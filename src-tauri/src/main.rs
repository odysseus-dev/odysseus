use std::{
    error::Error,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<CommandChild>>,
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let port = reserve_local_port()?;
            let app_data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&app_data_dir)?;

            let base_url = format!("http://127.0.0.1:{port}");
            let child = start_backend(app.handle(), port, app_data_dir)?;
            app.state::<BackendState>()
                .child
                .lock()
                .expect("backend child lock poisoned")
                .replace(child);

            wait_for_health(port, Duration::from_secs(120))?;

            let url = url::Url::parse(&base_url)?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Odysseus")
                .inner_size(1280.0, 860.0)
                .min_inner_size(960.0, 640.0)
                .build()?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let state = window.state::<BackendState>();
                if let Some(mut child) = state
                    .child
                    .lock()
                    .expect("backend child lock poisoned")
                    .take()
                {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Odysseus desktop app");
}

fn reserve_local_port() -> Result<u16, Box<dyn Error>> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn start_backend(
    app: &tauri::AppHandle,
    port: u16,
    app_data_dir: PathBuf,
) -> Result<CommandChild, Box<dyn Error>> {
    let data_dir = app_data_dir.join("data");
    std::fs::create_dir_all(&data_dir)?;

    let command = app
        .shell()
        .sidecar("odysseus-backend")?
        .current_dir(&app_data_dir)
        .env("ODYSSEUS_HOST", "127.0.0.1")
        .env("ODYSSEUS_PORT", port.to_string())
        .env("ODYSSEUS_DATA_DIR", data_dir.as_os_str())
        .env("DATA_DIR", data_dir.as_os_str())
        .env(
            "DATABASE_URL",
            format!("sqlite:///{}", data_dir.join("app.db").to_string_lossy()),
        )
        .env("ODYSSEUS_INPROCESS_TASKS", "0")
        .env("ODYSSEUS_INPROCESS_POLLERS", "0");

    let (_rx, child) = command.spawn()?;
    Ok(child)
}

fn wait_for_health(port: u16, timeout: Duration) -> Result<(), Box<dyn Error>> {
    let deadline = Instant::now() + timeout;
    let request = format!(
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );

    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            stream.set_read_timeout(Some(Duration::from_secs(2)))?;
            stream.write_all(request.as_bytes())?;
            let mut response = String::new();
            let _ = stream.read_to_string(&mut response);
            if response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200") {
                return Ok(());
            }
        }

        thread::sleep(Duration::from_millis(500));
    }

    Err(format!("backend did not become healthy on 127.0.0.1:{port}").into())
}
