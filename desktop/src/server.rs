//! Python uvicorn server lifecycle management.
//!
//! Design decisions:
//! - Python is resolved from venv first, then system PATH — avoids accidentally
//!   picking up a different interpreter or one without odysseus deps installed.
//! - The child process inherits the parent's environment so that PATH,
//!   VIRTUAL_ENV, and other shell state carry through.
//! - Logs are written to `logs/desktop-server.log` in the repo directory so they
//!   live alongside existing Odysseus logs.
//! - A health-check poll against `/api/health` confirms the server is ready
//!   before the webview navigates to it; timeout surfaces failures clearly.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use log::info;
use reqwest::Client;
use tokio::time::sleep;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("Python not found: {0}")]
    PythonNotFound(String),
    #[error("Server start failed: {0}")]
    StartFailed(String),
    #[error("Health check timed out after {0} attempts (is the server starting?)")]
    HealthCheckTimeout(u32),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
}

pub struct ServerManager {
    repo_dir: PathBuf,
    port: u16,
    child: Option<Child>,
    client: Client,
    pub is_running: Arc<AtomicBool>,
}

impl ServerManager {
    pub fn new(repo_dir: PathBuf, port: u16) -> Self {
        Self {
            repo_dir,
            port,
            child: None,
            client: Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .expect("valid reqwest client"),
            is_running: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Find a Python interpreter that can run uvicorn.
    ///
    /// Resolution order:
    /// 1. `<repo>/venv/bin/python3` (or `Scripts/python.exe` on Windows)
    /// 2. `<repo>/.venv/bin/python3`
    /// 3. `python3` / `python` on PATH
    /// 4. Windows `py` launcher
    pub fn find_python(&self) -> Option<PathBuf> {
        let venv_python = if cfg!(target_os = "windows") {
            self.repo_dir.join("venv").join("Scripts").join("python.exe")
        } else {
            self.repo_dir.join("venv").join("bin").join("python3")
        };
        if venv_python.exists() {
            return Some(venv_python);
        }

        let dot_venv_python = if cfg!(target_os = "windows") {
            self.repo_dir.join(".venv").join("Scripts").join("python.exe")
        } else {
            self.repo_dir.join(".venv").join("bin").join("python3")
        };
        if dot_venv_python.exists() {
            return Some(dot_venv_python);
        }

        for name in &["python3", "python"] {
            if let Ok(path) = which::which(name) {
                return Some(path);
            }
        }

        #[cfg(target_os = "windows")]
        if let Ok(path) = which::which("py") {
            return Some(path);
        }

        None
    }

    /// Start the uvicorn server as a child process.
    pub fn start(&mut self) -> Result<()> {
        let python = self
            .find_python()
            .ok_or_else(|| Error::PythonNotFound(
                "Checked repo venv, .venv, and PATH. Install Python 3.11+ from python.org".into(),
            ))?;

        info!("Using Python: {}", python.display());

        // Ensure log directory exists
        let log_dir = self.repo_dir.join("logs");
        std::fs::create_dir_all(&log_dir)?;

        // Open log file (append mode so previous runs aren't lost)
        let log_file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_dir.join("desktop-server.log"))?;

        // Spawn uvicorn with stdout/stderr redirected to the log file.
        // On Windows, CREATE_NO_WINDOW prevents a console window from popping up
        // since python.exe is a console-subsystem binary.
        let mut cmd = Command::new(&python);
        cmd.arg("-m")
            .arg("uvicorn")
            .arg("app:app")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(self.port.to_string())
            .arg("--log-level")
            .arg("warning")
            .current_dir(&self.repo_dir)
            .stdout(Stdio::from(log_file.try_clone()?))
            .stderr(Stdio::from(log_file));

        #[cfg(target_os = "windows")]
        cmd.creation_flags(CREATE_NO_WINDOW);

        let child = cmd.spawn().map_err(|e| {
            Error::StartFailed(format!("Failed to spawn uvicorn: {e}"))
        })?;

        info!("uvicorn started (PID {})", child.id());
        self.child = Some(child);
        Ok(())
    }

    /// Poll `/api/health` until the server responds 200 or `max_retries` is hit.
    ///
    /// Each attempt is 500 ms apart, so `max_retries = 120` ≈ 60 s timeout.
    pub async fn wait_ready(&self, max_retries: u32) -> Result<()> {
        let url = format!("http://127.0.0.1:{}/api/health", self.port);
        for i in 0..max_retries {
            match self.client.get(&url).send().await {
                Ok(resp) if resp.status().is_success() => {
                    self.is_running.store(true, Ordering::SeqCst);
                    info!("Server ready after ~{}s", i / 2);
                    return Ok(());
                }
                _ => {
                    if i > 0 && i % 10 == 0 {
                        info!("Waiting for server… ({i}/{max_retries})");
                    }
                    sleep(Duration::from_millis(500)).await;
                }
            }
        }
        Err(Error::HealthCheckTimeout(max_retries))
    }

    pub fn server_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    /// Stop the server process (force-kill on all platforms for simplicity).
    pub fn stop(&mut self) {
        if let Some(child) = self.child.take() {
            info!("Stopping server (PID {})…", child.id());
            #[cfg(target_os = "windows")]
            {
                let _ = Command::new("taskkill")
                    .args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .status();
            }
            #[cfg(not(target_os = "windows"))]
            {
                let _ = child.kill();
                let _ = child.wait();
            }
            self.is_running.store(false, Ordering::SeqCst);
            info!("Server stopped");
        }
    }
}

impl Drop for ServerManager {
    fn drop(&mut self) {
        self.stop();
    }
}
