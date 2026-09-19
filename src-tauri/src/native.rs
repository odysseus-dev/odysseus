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

use crate::{get_odysseus_dir, run_system_command, PORT};

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
    Err(
        "Python 3.11+ was not found on PATH. Install it from https://www.python.org/downloads/ and retry."
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
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
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

    if !venv_python.exists() {
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

        let install_status = Command::new(&venv_python)
            .current_dir(target_dir)
            .args(["-m", "pip", "install", "-r", "requirements.txt"])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status()
            .map_err(|e| format!("Failed to execute pip install: {e}"))?;
        if !install_status.success() {
            return Err(format!("Failed to install dependencies: {install_status}"));
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
    let child = spawn_launcher(&target_dir)?;

    *NATIVE_PROCESS.lock().unwrap() = Some(child);
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

