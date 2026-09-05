use std::env;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::LazyLock;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, Url};

mod platform;

const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(300);
pub static PORT: LazyLock<String> = LazyLock::new(|| {
    dotenv::from_path(get_odysseus_dir().join(".env")).ok();
    env::var("APP_PORT").unwrap_or_else(|_| "7000".to_string())
});

fn status_url(page: &str) -> Url {
    #[cfg(target_os = "windows")]
    let prefix = "http://odysseus.localhost/";

    #[cfg(not(target_os = "windows"))]
    let prefix = "odysseus://localhost/";

    Url::parse(&format!("{prefix}{page}")).expect("status page URL must be valid")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run(is_installed: bool) {
    tauri::Builder::default()
        .register_uri_scheme_protocol("odysseus", move |_app, request| {
            let path = request.uri().path();
            let fallback_css = include_str!("../assets/tauri-style.css");

            let html = if path.contains("installer")
                || (!is_installed && (path == "/" || path.is_empty()))
            {
                include_str!("../assets/tauri-installer.html").replace(
                    "</head>",
                    &format!("<style>\n{fallback_css}\n</style>\n</head>"),
                )
            } else if path.contains("loading") || (is_installed && (path == "/" || path.is_empty()))
            {
                include_str!("../assets/tauri-loading.html").replace(
                    "</head>",
                    &format!("<style>\n{fallback_css}\n</style>\n</head>"),
                )
            } else {
                include_str!("../assets/tauri-404.html").replace(
                    "</head>",
                    &format!("<style>\n{fallback_css}\n</style>\n</head>"),
                )
            };

            tauri::http::Response::builder()
                .status(200)
                .header("Content-Type", "text/html; charset=utf-8")
                .header("Access-Control-Allow-Origin", "*")
                .body(html.into_bytes())
                .unwrap()
        })
        .setup(move |app| {
            let app_handle = app.handle().clone();
            let window = app_handle.get_webview_window("main").unwrap();

            let initial_page = if is_installed { "loading" } else { "installer" };
            let _ = window.navigate(status_url(initial_page));

            if is_installed {
                println!("Odysseus is installed. Spinning up background services...");

                std::thread::spawn(move || {
                    if let Err(error) = run_odysseus() {
                        println!("Could not start Odysseus: {error}");
                        if let Some(thread_window) =
                            app_handle.get_webview_window("main")
                        {
                            let _ = thread_window.navigate(status_url("error"));
                        }
                        return;
                    }

                    println!("Polling for localhost:{} to serve HTTP 200...", *PORT);
                    let addr: SocketAddr = format!("127.0.0.1:{}", *PORT).parse().unwrap();
                    let deadline = Instant::now() + BACKEND_STARTUP_TIMEOUT;
                    let mut backend_ready = false;

                    while Instant::now() < deadline {
                        if let Ok(mut stream) =
                            TcpStream::connect_timeout(&addr, Duration::from_millis(500))
                        {
                            let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));

                            let request =
                                "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";

                            if stream.write_all(request.as_bytes()).is_ok() {
                                let mut buffer = [0; 1024];

                                if let Ok(bytes_read) = stream.read(&mut buffer) {
                                    let response = String::from_utf8_lossy(&buffer[..bytes_read]);

                                    let first_line = response.lines().next().unwrap_or("Empty Response");
                                    println!("Pinged localhost:{}. Server replied: {}", *PORT, first_line);

                                    if first_line.starts_with("HTTP/1.1 20")
                                        || first_line.starts_with("HTTP/1.0 20")
                                        || first_line.starts_with("HTTP/1.1 30")
                                        || first_line.starts_with("HTTP/1.0 30")
                                    {
                                        println!(
                                            "Server is fully booted and responsive! Navigating to live app..."
                                        );

                                        if let Some(thread_window) =
                                            app_handle.get_webview_window("main")
                                        {
                                            let _ = thread_window.navigate(
                                                Url::parse(&format!("http://localhost:{}", *PORT))
                                                    .unwrap(),
                                            );
                                        }

                                        backend_ready = true;
                                        break;
                                    }
                                } else {
                                    println!("TCP connected, but timed out waiting for the HTTP response.");
                                }
                            }
                        } else {
                            println!("Waiting for port {} to open...", *PORT);
                        }

                        thread::sleep(Duration::from_millis(1000));
                    }

                    if !backend_ready {
                        println!(
                            "Odysseus did not become responsive within {:?}.",
                            BACKEND_STARTUP_TIMEOUT
                        );
                        if let Some(thread_window) =
                            app_handle.get_webview_window("main")
                        {
                            let _ = thread_window.navigate(status_url("error"));
                        }
                    }

                });
            } else {
                println!("Odysseus is not installed. Waiting for user in installer UI...");
            }

            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api: _, .. } => {
                println!(
                    "User clicked X on window: {}. Shutting down containers...",
                    window.label()
                );
                close_odysseus();
                println!("Teardown complete. Goodbye!");
            }
            _ => {}
        })
        // Point the handler to the module namespace
        .invoke_handler(tauri::generate_handler![commands::installation_script])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

pub mod commands {
    use tauri::utils::config;

    use super::*;

    #[tauri::command]
    pub fn check_installation_status() -> bool {
        let config_path = get_config_dir().join("config.json");
        config_path.exists()
    }

    #[tauri::command]
    pub async fn installation_script() -> (String, bool) {
        match run_system_command("git", &["--version"]) {
            Ok(output) => println!("Found Git: {}", output.trim()),
            Err(_) => match platform::install_git() {
                Ok(_) => println!("Git installed successfully."),
                Err(e) => {
                    return (
                        format!("Git is not installed, and automated installation failed: {e}"),
                        false,
                    );
                }
            },
        }

        match run_system_command("docker", &["info"]) {
            Ok(_) => println!("Docker CLI found and Engine is running."),
            Err(_) => {
                return (
                    "Docker is not running. Please open Docker Desktop and try again.".to_string(),
                    false,
                );
            }
        }

        let target_dir = get_odysseus_dir();

        if target_dir.exists() {
            return (
                format!(
                    "Installation directory already exists: {}. Refusing to run Docker Compose from it.",
                    target_dir.display()
                ),
                false,
            );
        }

        let clone_result = Command::new("git")
            .args(["clone", "https://github.com/odysseus-dev/odysseus.git"])
            .arg(&target_dir)
            .output();

        match clone_result {
            Ok(output) if output.status.success() => println!(
                "Git repository cloned successfully: {}",
                String::from_utf8_lossy(&output.stdout).trim()
            ),
            Ok(output) => {
                return (
                    format!(
                        "Failed to clone repository: {}",
                        String::from_utf8_lossy(&output.stderr).trim()
                    ),
                    false,
                )
            }
            Err(e) => return (format!("Failed to execute git clone: {e}"), false),
        }

        let env_example_path = target_dir.join(".env.example-desktop");
        let env_path = target_dir.join(".env");
        if !env_path.exists() {
            match std::fs::copy(&env_example_path, &env_path) {
                Ok(_) => println!("Successfully created .env file."),
                Err(e) => {
                    return (
                        format!("Could not create the Odysseus environment file: {e}"),
                        false,
                    )
                }
            }
        }

        println!("Building Odysseus with optional extras (this will take a while)...");
        let build_status = Command::new("docker")
            .current_dir(&target_dir)
            .args(["compose", "build", "--build-arg", "INSTALL_OPTIONAL=true"])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status();

        match build_status {
            Ok(status) if status.success() => println!("Build successful!"),
            Ok(status) => {
                return (
                    format!("Docker build failed with status: {}", status),
                    false,
                )
            }
            Err(e) => return (format!("Failed to execute docker build: {}", e), false),
        }

        println!("Starting Odysseus containers...");
        let up_status = Command::new("docker")
            .current_dir(&target_dir)
            .args(["compose", "up", "-d", "--build"])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status();

        match up_status {
            Ok(status) if status.success() => println!("Docker compose up executed successfully!"),
            Ok(status) => {
                return (
                    format!("Docker compose up failed with status: {}", status),
                    false,
                )
            }
            Err(e) => return (format!("Failed to execute docker up command: {}", e), false),
        }

        // Write config.json into Documents/Odysseus Desktop/config.json
        let config_dir = get_config_dir();
        if let Err(e) = std::fs::create_dir_all(&config_dir) {
            return (
                format!("Could not create the installation marker directory: {e}"),
                false,
            );
        }

        let config_path = config_dir.join("config.json");
        let config_content = "{\n  \"installed\": true\n}\n";

        if let Err(e) = std::fs::write(&config_path, config_content) {
            return (
                format!("Could not create the installation marker: {e}"),
                false,
            );
        }
        println!("Successfully created config.json at {:?}", config_path);

        println!("Odysseus installation script executed successfully.");
        (
            "Odysseus installation script executed successfully.".to_string(),
            true,
        )
    }
}

// ---------------------------------------------------------
// HELPER FUNCTIONS
// ---------------------------------------------------------

fn ensure_docker_is_running() -> Result<(), String> {
    if run_system_command("docker", &["info"]).is_ok() {
        return Ok(());
    }

    println!("Docker daemon is offline. Attempting to launch Docker Desktop...");

    platform::launch_docker_desktop()
        .map_err(|e| format!("Could not launch Docker automatically: {}", e))?;

    let max_retries = 30;
    for attempt in 1..=max_retries {
        if run_system_command("docker", &["info"]).is_ok() {
            println!("Docker daemon is now online and ready!");
            return Ok(());
        }
        println!(
            "Waiting for Docker engine to initialize... (Attempt {}/{})",
            attempt, max_retries
        );
        thread::sleep(Duration::from_secs(2));
    }

    Err("Docker daemon took too long to start. Please check Docker Desktop manually.".to_string())
}

fn run_odysseus() -> Result<(), String> {
    ensure_docker_is_running()?;

    match run_system_command("docker", &["ps"]) {
        Ok(output) => {
            if output.contains("odysseus") {
                println!("Odysseus is already running.");
            } else {
                println!("Starting Odysseus...");
                let target_dir = get_odysseus_dir();

                let result = Command::new("docker")
                    .current_dir(&target_dir)
                    .args(["compose", "up", "-d"])
                    .output();

                match result {
                    Ok(out) if out.status.success() => println!("Odysseus started successfully."),
                    Ok(out) => {
                        return Err(format!(
                            "Failed to start Odysseus: {}",
                            String::from_utf8_lossy(&out.stderr).trim()
                        ))
                    }
                    Err(e) => return Err(format!("Failed to execute docker command: {e}")),
                }
            }
        }
        Err(e) => return Err(format!("Could not inspect Docker containers: {e}")),
    }

    Ok(())
}

fn close_odysseus() {
    if run_system_command("docker", &["info"]).is_err() {
        println!("Docker is offline. Assuming Odysseus is already stopped.");
        return;
    }

    match run_system_command("docker", &["ps"]) {
        Ok(output) => {
            if !output.contains("odysseus") {
                println!("Odysseus is not running.");
            } else {
                println!("Closing Odysseus...");
                let target_dir = get_odysseus_dir();

                let result = Command::new("docker")
                    .current_dir(&target_dir)
                    .args(["compose", "stop"])
                    .output();

                match result {
                    Ok(out) if out.status.success() => println!("Odysseus stopped successfully."),
                    Ok(out) => println!(
                        "Failed to stop Odysseus: {}",
                        String::from_utf8_lossy(&out.stderr)
                    ),
                    Err(e) => println!("Failed to execute docker command: {}", e),
                }
            }
        }
        Err(_) => println!("Unexpected error checking docker ps."),
    }
}

fn get_documents_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    let base_dir = std::env::var_os("USERPROFILE").unwrap_or_else(|| "C:\\".into());

    #[cfg(not(target_os = "windows"))]
    let base_dir = std::env::var_os("HOME").unwrap_or_else(|| "/".into());

    PathBuf::from(base_dir).join("Documents")
}

fn get_odysseus_dir() -> PathBuf {
    get_documents_dir().join("Odysseus")
}

fn get_config_dir() -> PathBuf {
    get_documents_dir().join("Odysseus Desktop")
}

pub(crate) fn run_system_command(cmd: &str, args: &[&str]) -> Result<String, String> {
    let output = Command::new(cmd)
        .args(args)
        .output()
        .map_err(|e| format!("Failed to execute command '{cmd}': {e}"))?;

    if output.status.success() {
        String::from_utf8(output.stdout)
            .map_err(|e| format!("Command output was not valid UTF-8: {e}"))
    } else {
        let error_message = String::from_utf8_lossy(&output.stderr).to_string();
        Err(format!(
            "Command failed with exit code {:?}: {}",
            output.status.code(),
            error_message
        ))
    }
}
