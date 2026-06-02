use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use log::info;
use reqwest::Client;
use tokio::time::sleep;

// Suppress console window for child processes on Windows
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// Result type for server operations.
pub type ServerResult<T> = Result<T, ServerError>;

#[derive(Debug, thiserror::Error)]
pub enum ServerError {
    #[error("Python not found: {0}")]
    PythonNotFound(String),
    #[error("Venv setup failed: {0}")]
    VenvSetupFailed(String),
    #[error("Server start failed: {0}")]
    StartFailed(String),
    #[error("Server health check failed after {0} retries")]
    HealthCheckTimeout(u32),
    #[error("Server crashed: {0}")]
    Crashed(String),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
}

/// Manages the Python uvicorn server lifecycle.
pub struct ServerManager {
    repo_dir: PathBuf,
    port: u16,
    python_path: Option<String>,
    child: Option<Child>,
    client: Client,
    pub is_running: Arc<AtomicBool>,
}

impl ServerManager {
    /// Create a new server manager.
    pub fn new(repo_dir: PathBuf, port: u16, python_path: Option<String>) -> Self {
        Self {
            repo_dir,
            port,
            python_path,
            child: None,
            client: Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .expect("Valid reqwest client"),
            is_running: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Resolve the Python executable path.
    pub fn find_python(&self) -> Option<PathBuf> {
        // Priority 1: explicit path from config
        if let Some(ref path) = self.python_path {
            let p = PathBuf::from(path);
            if p.exists() { return Some(p); }
        }

        // Priority 2: venv in repo
        let venv_python = if cfg!(target_os = "windows") {
            self.repo_dir.join("venv").join("Scripts").join("python.exe")
        } else {
            self.repo_dir.join("venv").join("bin").join("python3")
        };
        if venv_python.exists() {
            return Some(venv_python);
        }

        // Priority 3: .venv fallback
        let dot_venv_python = if cfg!(target_os = "windows") {
            self.repo_dir.join(".venv").join("Scripts").join("python.exe")
        } else {
            self.repo_dir.join(".venv").join("bin").join("python3")
        };
        if dot_venv_python.exists() {
            return Some(dot_venv_python);
        }

        // Priority 4: PATH python3/python/py
        for name in &["python3", "python"] {
            if let Ok(path) = which::which(name) {
                return Some(path);
            }
        }
        // Windows: py launcher
        #[cfg(target_os = "windows")]
        if let Ok(path) = which::which("py") {
            return Some(path);
        }

        None
    }

    /// Check if the repo has dependencies installed.
    pub fn check_venv(&self, python: &Path) -> bool {
        let requirements = self.repo_dir.join("requirements.txt");
        if !requirements.exists() {
            return false;
        }

        // Quick check: try importing fastapi
        let check = Command::new(python)
            .arg("-c")
            .arg("import fastapi; print('ok')")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .ok();
        matches!(check, Some(s) if s.success())
    }

    /// Run pip install to set up dependencies.
    pub fn install_deps(&self, python: &Path) -> ServerResult<()> {
        info!("Installing Python dependencies...");
        let status = Command::new(python)
            .arg("-m")
            .arg("pip")
            .arg("install")
            .arg("-r")
            .arg(self.repo_dir.join("requirements.txt"))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .status()?;

        if !status.success() {
            return Err(ServerError::VenvSetupFailed(
                "pip install failed. Check logs/desktop-server.log for details.".into(),
            ));
        }
        Ok(())
    }

    /// Run setup.py for first-time initialization.
    pub fn run_setup(&self, python: &Path) -> ServerResult<Option<String>> {
        info!("Running first-time setup...");
        let output = Command::new(python)
            .arg(self.repo_dir.join("setup.py"))
            .env("ODYSSEUS_ADMIN_USER", "admin")
            .env("ODYSSEUS_ADMIN_PASSWORD", &generate_password())
            .output()?;

        let stdout = String::from_utf8_lossy(&output.stdout);
        let password = extract_password(&stdout);

        if !output.status.success() {
            return Err(ServerError::VenvSetupFailed(
                "setup.py failed. Check logs for details.".into(),
            ));
        }
        Ok(password)
    }

    /// Start the uvicorn server.
    pub fn start(&mut self) -> ServerResult<()> {
        let python = self.find_python()
            .ok_or_else(|| ServerError::PythonNotFound(
                "Could not find Python 3.11+. Install Python from python.org".into()
            ))?;

        info!("Using Python: {}", python.display());

        // Ensure deps are installed
        if !self.check_venv(&python) {
            self.install_deps(&python)?;
        }

        // Run setup if .env doesn't exist
        let env_path = self.repo_dir.join(".env");
        if !env_path.exists() {
            let password = self.run_setup(&python)?;
            if let Some(pwd) = password {
                info!("First-time setup complete. Admin password: {}", pwd);
            }
        }

        // Start uvicorn
        info!("Starting uvicorn on port {}...", self.port);
        let log_file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.repo_dir.join("logs").join("desktop-server.log"))?;

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

        let child = cmd.spawn()?;

        self.child = Some(child);
        Ok(())
    }

    /// Wait for the server to respond to health checks.
    pub async fn wait_ready(&self, max_retries: u32) -> ServerResult<()> {
        let url = format!("http://127.0.0.1:{}/api/health", self.port);
        for i in 0..max_retries {
            match self.client.get(&url).send().await {
                Ok(resp) if resp.status().is_success() => {
                    self.is_running.store(true, Ordering::SeqCst);
                    info!("Server is ready (after ~{}s)", i / 2);
                    return Ok(());
                }
                _ => {
                    if i % 10 == 0 && i > 0 {
                        info!("Waiting for server... ({}/{} retries)", i, max_retries);
                    }
                    sleep(Duration::from_millis(500)).await;
                }
            }
        }
        self.is_running.store(false, Ordering::SeqCst);
        Err(ServerError::HealthCheckTimeout(max_retries))
    }

    /// Stop the server process.
    pub fn stop(&mut self) {
        if let Some(child) = self.child.take() {
            info!("Stopping server (PID {})...", child.id());
            #[cfg(target_os = "windows")]
            {
                // On Windows, send CTRL_BREAK via taskkill
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

    /// Check if the server process is still alive.
    pub fn is_process_alive(&self) -> bool {
        self.child.is_some()
    }

    /// Get the server URL.
    pub fn server_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }
}

impl Drop for ServerManager {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Generate a random 18-character admin password.
fn generate_password() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    // Simple alphanumeric generator (no external dep needed)
    let chars: Vec<char> = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        .chars().collect();
    let mut rng = seed;
    (0..18).map(|_| {
        rng = rng.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        chars[(rng as usize) % chars.len()]
    }).collect()
}

/// Extract the admin password from setup.py output.
fn extract_password(output: &str) -> Option<String> {
    for line in output.lines() {
        if line.contains("Temporary password:") || line.contains("password:") {
            let parts: Vec<&str> = line.split(':').collect();
            if parts.len() >= 2 {
                return Some(parts[1..].join(":").trim().to_string());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_password() {
        let output = "  [ok] Initial admin user created (admin)\n        Temporary password: abc123XYZ\n        ** Change it after first login.";
        assert_eq!(extract_password(output), Some("abc123XYZ".to_string()));
    }

    #[test]
    fn test_extract_password_missing() {
        let output = "  [skip] auth.json already exists";
        assert_eq!(extract_password(output), None);
    }

    #[test]
    fn test_generate_password_length() {
        let pwd = generate_password();
        assert_eq!(pwd.len(), 18);
        assert!(pwd.chars().all(|c| c.is_alphanumeric()));
    }

    #[test]
    fn test_generate_password_unique() {
        let a = generate_password();
        let b = generate_password();
        assert_ne!(a, b);
    }

    #[test]
    fn test_server_url() {
        let sm = ServerManager::new(PathBuf::from("/test"), 7000, None);
        assert_eq!(sm.server_url(), "http://127.0.0.1:7000");
    }
}
