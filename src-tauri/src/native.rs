//! Native (non-Docker) run support: delegates to the same per-OS launcher
//! scripts documented in docs/setup.md (launch-windows.ps1, start-macos.sh),
//! which already handle venv creation, dependency install, and first-run
//! setup idempotently. Linux has no dedicated script, so those steps are
//! replicated here for that one platform.

use std::net::{SocketAddr, TcpStream};
use std::path::Path;
#[cfg(target_os = "linux")]
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use crate::{
    explain_failure, forward_lines, get_odysseus_dir, run_system_command, OutputTail, PORT,
};

// Handle to the natively-spawned server process, so window close can stop it directly.
static NATIVE_PROCESS: LazyLock<Mutex<Option<Child>>> = LazyLock::new(|| Mutex::new(None));

// The actual uvicorn server is spawned as a *child* of the powershell/bash wrapper we launch, so
// killing just that wrapper orphans it. On Unix, put the wrapper in its own process group so we
// can later signal the whole group; on Windows, taskkill /T walks the process tree by ancestry
// instead, so no special spawn setup is needed there.
#[cfg(unix)]
fn new_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(not(unix))]
fn new_process_group(_command: &mut Command) {}

#[cfg(unix)]
fn kill_process_tree(child: &Child) {
    // Negative pid targets the whole process group we created at spawn time.
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
}

#[cfg(windows)]
fn kill_process_tree(child: &Child) {
    let _ = Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .output();
}

// Only used by the Linux fallback below — Windows/macOS delegate to their launcher scripts.
#[cfg(target_os = "linux")]
fn venv_python_path(target_dir: &Path) -> PathBuf {
    target_dir.join("venv").join("bin").join("python")
}

// Whichever system Python launcher is on PATH; used for the install-time preflight check.
pub fn find_python_command() -> Result<&'static str, String> {
    for candidate in ["python3", "python"] {
        if run_system_command(candidate, &["--version"]).is_ok() {
            return Ok(candidate);
        }
    }

    if run_system_command("winget", &["--version"]).is_ok() {
        if run_system_command(
            "winget",
            &[
                "install",
                "--id",
                "Python.Python.3.13",
                "--exact",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
        )
        .is_ok()
        {
            return Err(
                "Python was installed successfully. Please restart Odysseus and try again."
                    .to_string(),
            );
        }
    }

    Err(
        "Python 3.11+ was not found and could not be installed automatically. \
         Please install Python from https://www.python.org/downloads/ and retry."
            .to_string(),
    )
}

#[cfg(target_os = "windows")]
fn spawn_launcher(target_dir: &Path) -> Result<Child, String> {
    let script = target_dir.join("launch-windows.ps1");
    if !script.exists() {
        return Err(format!(
            "Native launcher script not found at {}. Reinstall Odysseus.",
            script.display()
        ));
    }

    Command::new("powershell")
        .current_dir(target_dir)
        .args([
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script.to_str().ok_or("Non-UTF8 launcher script path")?,
            "-Port",
            PORT.as_str(),
            "-BindHost",
            "127.0.0.1",
        ])
        // Windows auto-allocates a *new*, easy-to-miss console for this console
        // subprocess (unlike macOS/Linux, where a GUI parent's console-less child
        // just gets a non-tty stdin). Without this, setup.py sees an interactive
        // tty and blocks forever on `input()` for admin credentials in that
        // hidden window, which looks like the installer hung. Skip the prompt so
        // it falls back to an auto-generated password (printed to the console).
        .env("ODYSSEUS_SKIP_ADMIN_PROMPT", "1")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to launch launch-windows.ps1: {e}"))
}

#[cfg(target_os = "macos")]
fn spawn_launcher(target_dir: &Path) -> Result<Child, String> {
    let script = target_dir.join("start-macos.sh");
    if !script.exists() {
        return Err(format!(
            "Native launcher script not found at {}. Reinstall Odysseus.",
            script.display()
        ));
    }

    let mut command = Command::new("bash");
    command
        .current_dir(target_dir)
        .arg(&script)
        .env("ODYSSEUS_PORT", PORT.as_str())
        .env("ODYSSEUS_HOST", "127.0.0.1")
        .env("ODYSSEUS_NO_OPEN", "1")
        .stdout(Stdio::inherit())
        .stderr(Stdio::piped());
    new_process_group(&mut command);

    command
        .spawn()
        .map_err(|e| format!("Failed to launch start-macos.sh: {e}"))
}

// No native launcher script ships for Linux (docs/setup.md only documents the
// manual venv steps there), so replicate them directly before starting uvicorn.
#[cfg(target_os = "linux")]
fn spawn_launcher(target_dir: &Path) -> Result<Child, String> {
    let venv_python = venv_python_path(target_dir);
    let setup_done = target_dir.join("venv").join(".setup_done");

    if !setup_done.exists() {
        let python = find_python_command()?;

        let status = Command::new(python)
            .current_dir(target_dir)
            .args(["-m", "venv", "venv"])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status()
            .map_err(|e| format!("Failed to execute '{python} -m venv venv': {e}"))?;
        if !status.success() {
            return Err(format!("Failed to create virtual environment: {status}"));
        }

        let (install_status, install_output) = Command::new(&venv_python)
            .current_dir(target_dir)
            .args(["-m", "pip", "install", "-r", "requirements.txt"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .and_then(crate::wait_with_logged_output)
            .map_err(|e| format!("Failed to execute pip install: {e}"))?;
        if !install_status.success() {
            return Err(explain_failure(install_output.lines())
                .unwrap_or_else(|| format!("Failed to install dependencies: {install_status}")));
        }

        let setup_status = Command::new(&venv_python)
            .current_dir(target_dir)
            .arg("setup.py")
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status()
            .map_err(|e| format!("Failed to execute setup.py: {e}"))?;
        if !setup_status.success() {
            return Err(format!("setup.py failed: {setup_status}"));
        }
        let _ = std::fs::write(&setup_done, "");
    }

    let mut command = Command::new(&venv_python);
    command
        .current_dir(target_dir)
        .args([
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            PORT.as_str(),
        ])
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    new_process_group(&mut command);

    command
        .spawn()
        .map_err(|e| format!("Failed to launch the native Odysseus server: {e}"))
}

pub fn run_odysseus_native() -> Result<(), String> {
    let addr: SocketAddr = format!("127.0.0.1:{}", *PORT)
        .parse()
        .map_err(|e| format!("Invalid backend address: {e}"))?;

    if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
        println!("Odysseus is already running natively.");
        return Ok(());
    }

    let target_dir = get_odysseus_dir();
    println!("Starting Odysseus natively via the platform launcher script...");
    let mut child = spawn_launcher(&target_dir)?;

    // Only the macOS launcher pipes stderr, kept to explain a failed start.
    let errors = OutputTail::default();
    forward_lines(child.stderr.take(), std::io::stderr(), &errors);
    let explain = || explain_failure(errors.lock().unwrap().iter().map(String::as_str));

    *NATIVE_PROCESS.lock().unwrap() = Some(child);

    // Actively wait for the server to spin up (handling first-run pip installs)
    println!("Waiting for Odysseus server to start up (this may take a minute on first run)...");
    let start_time = std::time::Instant::now();
    let timeout = crate::BACKEND_STARTUP_TIMEOUT;

    loop {
        // Try connecting to the local port every 1 second
        if TcpStream::connect_timeout(&addr, Duration::from_secs(1)).is_ok() {
            println!("Odysseus server is up and responding!");
            break;
        }

        // Check if the spawned PowerShell/bash process crashed or exited early
        if let Some(child_process) = NATIVE_PROCESS.lock().unwrap().as_mut() {
            match child_process.try_wait() {
                Ok(Some(status)) => {
                    return Err(explain().unwrap_or_else(|| {
                        format!("Launcher script exited prematurely with status: {status}")
                    }));
                }
                Err(e) => {
                    return Err(format!("Error monitoring launcher process: {e}"));
                }
                Ok(None) => {} // Process is still alive, keep waiting for uvicorn
            }
        }

        if start_time.elapsed() > timeout {
            return Err(explain().unwrap_or_else(|| {
                "Timed out waiting for Odysseus server to start up.".to_string()
            }));
        }

        std::thread::sleep(Duration::from_secs(1));
    }

    Ok(())
}

pub fn stop_odysseus_native() {
    match NATIVE_PROCESS.lock().unwrap().take() {
        Some(mut child) => {
            println!("Stopping native Odysseus server (pid {})...", child.id());
            kill_process_tree(&child);
            let _ = child.wait();
            println!("Native Odysseus server stopped.");
        }
        None => println!("No native Odysseus process tracked; nothing to stop."),
    }
}
