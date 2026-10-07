use std::collections::VecDeque;
use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{Arc, LazyLock, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, Url};

mod native;
mod platform;

const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(600);
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
pub fn run(is_installed: bool, is_native: bool) {
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

            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            let html = {
                let checkbox = r#"id="native-install-checkbox""#;
                if html.contains(checkbox) && run_system_command("brew", &["--version"]).is_ok() {
                    html.replace(checkbox, &format!("{checkbox} checked"))
                } else {
                    html
                }
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
                    let start_result = if is_native {
                        native::run_odysseus_native()
                    } else {
                        run_odysseus()
                    };

                    if let Err(error) = start_result {
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
        .on_window_event(move |window, event| match event {
            tauri::WindowEvent::CloseRequested { api: _, .. } => {
                println!(
                    "User clicked X on window: {}. Shutting down...",
                    window.label()
                );
                // is_native is read at startup, so it is still false right after a native install.
                native::stop_odysseus_native();
                if !is_native {
                    close_odysseus();
                }
                println!("Teardown complete. Goodbye!");
            }
            _ => {}
        })
        // Point the handler to the module namespace
        .invoke_handler(tauri::generate_handler![commands::installation_script])
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(move |_app, _event| {
            // Cmd+Q on macOS quits without CloseRequested, so also tear down on exit.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Exit = _event {
                native::stop_odysseus_native();
                if !is_native {
                    close_odysseus();
                }
            }
        });
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
    pub async fn installation_script(native: bool) -> (String, bool) {
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

        if native {
            #[cfg(target_os = "macos")]
            if run_system_command("brew", &["--version"]).is_err() {
                return (
                    "The native install needs Homebrew, a free tool from https://brew.sh. Install it, then click Install again, or uncheck \"Install natively\" to use Docker Desktop instead."
                        .to_string(),
                    false,
                );
            }

            if let Err(e) = crate::native::find_python_command() {
                return (e, false);
            }
            println!("Python found. Skipping Docker checks for native install.");
        } else {
            match run_system_command("docker", &["info"]) {
                Ok(_) => println!("Docker CLI found and Engine is running."),
                Err(_) => {
                    return (
                        "Docker is not running. Please open Docker Desktop and try again."
                            .to_string(),
                        false,
                    );
                }
            }
        }

        let target_dir = get_odysseus_dir();

        if target_dir.exists() {
            if !installer_marker(&target_dir).is_file() {
                return (
                    format!(
                        "There is already an Odysseus folder at {}, and Odysseus will not overwrite it. Move or rename it, then click Install again.",
                        target_dir.display()
                    ),
                    false,
                );
            }
            println!(
                "Resuming the unfinished installation in {}",
                target_dir.display()
            );
            native::stop_odysseus_native();
        } else {
            let clone_result = Command::new("git")
                .args([
                    "clone",
                    "https://github.com/bitboody/odysseus.git",
                    "--branch",
                    "tauri",
                ])
                .arg(&target_dir)
                .output();

            match clone_result {
                Ok(output) if output.status.success() => {
                    println!(
                        "Git repository cloned successfully: {}",
                        String::from_utf8_lossy(&output.stdout).trim()
                    );
                    let _ = std::fs::write(installer_marker(&target_dir), "");
                }
                Ok(output) => {
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    return (
                        explain_failure(stderr.lines()).unwrap_or_else(|| {
                            format!("Failed to clone repository: {}", stderr.trim())
                        }),
                        false,
                    );
                }
                Err(e) => return (format!("Failed to execute git clone: {e}"), false),
            }
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

            // AirPlay Receiver holds port 7000 on macOS, so use 7860 like start-macos.sh.
            #[cfg(target_os = "macos")]
            if let Err(e) = std::fs::read_to_string(&env_path).and_then(|env| {
                let env = env.replace("\nAPP_PORT=7000\n", "\nAPP_PORT=7860\n");
                std::fs::write(&env_path, env)
            }) {
                return (
                    format!("Could not set the macOS port in the Odysseus environment file: {e}"),
                    false,
                );
            }
        }

        if native {
            println!("Native install: skipping Docker build/up entirely.");
        } else {
            // Piped only to explain failures, Windows keeps docker's output as before.
            #[cfg(target_os = "windows")]
            let docker_output = Stdio::inherit;
            #[cfg(not(target_os = "windows"))]
            let docker_output = Stdio::piped;

            println!("Building Odysseus with optional extras (this will take a while)...");
            let build_status = Command::new("docker")
                .current_dir(&target_dir)
                .args(["compose", "build", "--build-arg", "INSTALL_OPTIONAL=true"])
                .stdout(docker_output())
                .stderr(docker_output())
                .spawn()
                .and_then(wait_with_logged_output);

            match build_status {
                Ok((status, _)) if status.success() => println!("Build successful!"),
                Ok((status, output)) => {
                    return (
                        explain_failure(output.lines()).unwrap_or_else(|| {
                            format!("Docker build failed with status: {}", status)
                        }),
                        false,
                    )
                }
                Err(e) => return (format!("Failed to execute docker build: {}", e), false),
            }

            println!("Starting Odysseus containers...");
            let up_status = Command::new("docker")
                .current_dir(&target_dir)
                .args(["compose", "up", "-d", "--build"])
                .stdout(docker_output())
                .stderr(docker_output())
                .spawn()
                .and_then(wait_with_logged_output);

            match up_status {
                Ok((status, _)) if status.success() => {
                    println!("Docker compose up executed successfully!")
                }
                Ok((status, output)) => {
                    return (
                        explain_failure(output.lines()).unwrap_or_else(|| {
                            format!("Docker compose up failed with status: {}", status)
                        }),
                        false,
                    )
                }
                Err(e) => return (format!("Failed to execute docker up command: {}", e), false),
            }
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
        let config_content = format!("{{\n  \"installed\": true,\n  \"is_native\": {native}\n}}\n");

        if let Err(e) = std::fs::write(&config_path, config_content) {
            return (
                format!("Could not create the installation marker: {e}"),
                false,
            );
        }
        println!("Successfully created config.json at {:?}", config_path);

        if native {
            println!("Launching native Odysseus and waiting for it to finish...");
            match native::run_odysseus_native() {
                Ok(_) => println!("Native Odysseus process exited cleanly."),
                Err(e) => {
                    return (e, false);
                }
            }
        }

        println!("Odysseus installation script executed successfully.");
        (
            "Odysseus installation script executed successfully.".to_string(),
            true,
        )
    }
}

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

// ---------------------------------------------------------
// HELPER FUNCTIONS
// ---------------------------------------------------------

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

fn installer_marker(dir: &std::path::Path) -> PathBuf {
    dir.join(".git").join("odysseus-installer")
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

pub(crate) type OutputTail = Arc<Mutex<VecDeque<String>>>;

pub(crate) fn wait_with_logged_output(mut child: Child) -> std::io::Result<(ExitStatus, String)> {
    let tail = OutputTail::default();
    let readers = [
        forward_lines(child.stdout.take(), std::io::stdout(), &tail),
        forward_lines(child.stderr.take(), std::io::stderr(), &tail),
    ];
    for reader in readers.into_iter().flatten() {
        let _ = reader.join();
    }
    let lines: Vec<String> = tail.lock().unwrap().drain(..).collect();
    Ok((child.wait()?, lines.join("\n")))
}

pub(crate) fn forward_lines(
    input: Option<impl Read + Send + 'static>,
    mut console: impl Write + Send + 'static,
    tail: &OutputTail,
) -> Option<thread::JoinHandle<()>> {
    let input = input?;
    let tail = tail.clone();
    Some(thread::spawn(move || {
        for line in BufReader::new(input).split(b'\n').map_while(Result::ok) {
            let line = String::from_utf8_lossy(&line).trim_end().to_string();
            let _ = writeln!(console, "{line}");
            let mut tail = tail.lock().unwrap();
            if tail.len() == 100 {
                tail.pop_front();
            }
            tail.push_back(line);
        }
    }))
}

const NETWORK_ERRORS: [&str; 10] = [
    "unable to connect",
    "failed to fetch",
    "temporary failure resolving",
    "could not resolve",
    "failed to connect to",
    "after connection broken by",
    "failed to do request",
    "dial tcp",
    "i/o timeout",
    "tls handshake timeout",
];

const NETWORK_PROBLEM: &str = "Odysseus could not download the files it needs. Check your internet connection (and pause any VPN or ad blocker), then click Install again.";
const DISK_FULL: &str = "There is not enough free disk space to install Odysseus. Free up some space, then click Install again.";

pub(crate) fn explain_failure<'a>(
    lines: impl DoubleEndedIterator<Item = &'a str>,
) -> Option<String> {
    for line in lines.rev() {
        let lower = line.to_lowercase();
        if lower.contains("no space left on device") {
            return Some(DISK_FULL.to_string());
        }
        if NETWORK_ERRORS.iter().any(|error| lower.contains(error)) {
            return Some(NETWORK_PROBLEM.to_string());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::{explain_failure, DISK_FULL, NETWORK_PROBLEM};

    const NETWORK_LINES: [&str; 9] = [
        "#8 481.6 E: Failed to fetch http://deb.debian.org/debian/pool/main/t/tmux/tmux_3.5a-3_arm64.deb  Unable to connect to deb.debian.org:http: [IP: 140.248.138.132 80]",
        "W: Failed to fetch http://deb.debian.org/debian/dists/trixie/InRelease  Temporary failure resolving 'deb.debian.org'",
        "fatal: unable to access 'https://github.invalid/odysseus-dev/odysseus.git/': Could not resolve host: github.invalid",
        "fatal: unable to access 'https://127.0.0.1:9/odysseus-dev/odysseus.git/': Failed to connect to 127.0.0.1 port 9 after 0 ms: Couldn't connect to server",
        r#"#6 0.850 WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='pypi.org', port=443): Failed to resolve 'pypi.org' ([Errno -3] Temporary failure in name resolution)")': /simple/requests/"#,
        r#"WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='127.0.0.1', port=9): Failed to establish a new connection: [Errno 61] Connection refused")': /simple/requests/"#,
        r#"Error response from daemon: failed to resolve reference "registry.invalid/odysseus/probe:latest": failed to do request: Head "https://registry.invalid/v2/odysseus/probe/manifests/latest": dialing registry.invalid:443 container via direct connection because Docker Desktop has no HTTPS proxy: connecting to registry.invalid:443: dial tcp: lookup registry.invalid: no such host"#,
        r#"Error response from daemon: failed to resolve reference "10.255.255.1/odysseus/probe:latest": failed to do request: Head "https://10.255.255.1/v2/odysseus/probe/manifests/latest": context deadline exceeded"#,
        r#"Error response from daemon: failed to resolve reference "host.docker.internal:9999/odysseus/probe:latest": failed to do request: Head "https://host.docker.internal:9999/v2/odysseus/probe/manifests/latest": EOF"#,
    ];

    #[test]
    fn explains_network_errors() {
        for line in NETWORK_LINES {
            assert_eq!(
                explain_failure(line.lines()).as_deref(),
                Some(NETWORK_PROBLEM),
                "{line}"
            );
        }
    }

    #[test]
    fn explains_a_failed_docker_build() {
        let output = "#8 481.6 E: Failed to fetch http://deb.debian.org/debian/pool/main/n/npm/npm_9.2.0%7eds1-3_all.deb  Unable to connect to deb.debian.org:http: [IP: 140.248.138.132 80]\n\
            481.6 E: Failed to fetch http://deb.debian.org/debian/pool/main/t/tmux/tmux_3.5a-3_arm64.deb  Unable to connect to deb.debian.org:http: [IP: 140.248.138.132 80]\n\
            481.6 E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?\n\
            failed to solve: process \"/bin/sh -c apt-get update && apt-get install -y --no-install-recommends tmux\" did not complete successfully: exit code: 100";
        assert_eq!(
            explain_failure(output.lines()).as_deref(),
            Some(NETWORK_PROBLEM)
        );
    }

    #[test]
    fn explains_a_full_disk_and_nothing_else() {
        let disk = explain_failure(
            "head: error writing 'standard output': No space left on device".lines(),
        );
        assert_eq!(disk.as_deref(), Some(DISK_FULL));
        let other = "ERROR: No matching distribution found for requests\nfailed to solve: process \"/bin/sh -c pip install -r requirements.txt\" did not complete successfully: exit code: 1";
        assert_eq!(explain_failure(other.lines()), None);
    }
}
