use crate::run_system_command;

// Covers the most common distro package managers — extend with zypper/apk/etc. as needed.
pub fn install_git() -> Result<String, String> {
    let package_managers: [(&str, &[&str]); 3] = [
        ("apt-get", &["install", "-y", "git"]),
        ("dnf", &["install", "-y", "git"]),
        ("pacman", &["-S", "--noconfirm", "git"]),
    ];

    for (manager, install_args) in package_managers {
        if run_system_command("which", &[manager]).is_ok() {
            // -n fails fast instead of hanging forever if a password is actually required.
            let mut args = vec!["-n", manager];
            args.extend_from_slice(install_args);

            return run_system_command("sudo", &args).map_err(|_| {
                format!(
                    "Installing git requires a password we can't supply here. Run `sudo {manager} {}` in a terminal, then retry.",
                    install_args.join(" ")
                )
            });
        }
    }

    Err(
        "Git is not installed and no supported package manager (apt-get, dnf, pacman) was found. Install Git manually and retry."
            .to_string(),
    )
}

pub fn launch_docker_desktop() -> Result<(), String> {
    if run_system_command("which", &["docker"]).is_err() {
        return Err(
            "Docker was not found. Install Docker Engine for your distro and retry.".to_string(),
        );
    }

    // -n fails fast instead of hanging forever if a password is actually required.
    run_system_command("sudo", &["-n", "systemctl", "start", "docker"]).map_err(|_| {
        "Starting the Docker service requires a password we can't supply here. Run `sudo systemctl start docker` in a terminal, then retry."
            .to_string()
    })?;

    Ok(())
}
