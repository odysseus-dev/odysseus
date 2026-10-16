use std::path::Path;
use std::process::Command;

use crate::run_system_command;

pub fn install_git() -> Result<String, String> {
    run_system_command(
        "winget",
        &[
            "install",
            "--id",
            "Git.Git",
            "-e",
            "--source",
            "winget",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ],
    )
}

pub fn launch_docker_desktop() -> Result<(), String> {
    let local_app_data = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| "C:\\".to_string());

    let candidate_paths = [
        format!(
            "{}\\Programs\\DockerDesktop\\Docker Desktop.exe",
            local_app_data
        ),
        format!(
            "{}\\Programs\\Docker\\Docker\\Docker Desktop.exe",
            local_app_data
        ),
        "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe".to_string(),
    ];

    let exe_path = candidate_paths
        .iter()
        .find(|path| Path::new(path).exists())
        .ok_or_else(|| {
            "Docker Desktop was not found. Install it from https://www.docker.com/products/docker-desktop/ and retry."
                .to_string()
        })?;

    Command::new(exe_path)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Failed to launch Docker Desktop: {e}"))
}
