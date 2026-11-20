use std::path::Path;
use std::process::Command;

use crate::run_system_command;

pub fn install_git() -> Result<String, String> {
    // Most Macs already have git via the Xcode Command Line Tools.
    if run_system_command("xcode-select", &["-p"]).is_ok() {
        return Ok("Xcode Command Line Tools (includes git) are already installed.".to_string());
    }

    if run_system_command("brew", &["--version"]).is_ok() {
        return run_system_command("brew", &["install", "git"]);
    }

    // xcode-select --install opens a non-blocking GUI installer, so we can't wait for it to finish here.
    Command::new("xcode-select")
        .arg("--install")
        .spawn()
        .map_err(|e| {
            format!("Git is not installed and the Xcode Command Line Tools installer could not be launched: {e}")
        })?;

    Err(
        "Launched the Xcode Command Line Tools installer. Finish the on-screen setup, then retry."
            .to_string(),
    )
}

pub fn launch_docker_desktop() -> Result<(), String> {
    if !Path::new("/Applications/Docker.app").exists() {
        return Err(
            "Docker Desktop was not found in /Applications. Install it from https://www.docker.com/products/docker-desktop/ and retry."
                .to_string(),
        );
    }

    Command::new("open")
        .arg("-a")
        .arg("Docker")
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Failed to launch Docker Desktop: {e}"))
}
