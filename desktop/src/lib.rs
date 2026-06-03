//! Desktop wrapper for Odysseus.
//!
//! This is a minimal proof-of-concept that:
//! 1. Detects the Odysseus repo directory
//! 2. Finds and starts the Python uvicorn server
//! 3. Opens a native webview window pointing at the server
//! 4. Cleans up the server process when the window closes

use std::path::PathBuf;
use std::sync::Mutex;

use log::info;
use tauri::{AppHandle, Manager};
use tokio::sync::oneshot;

pub mod server;

/// Resolve the Odysseus repo root directory.
///
/// Detection order:
/// 1. Walk up from the executable's directory looking for `app.py`
///    (covers development builds and macOS .app bundles)
/// 2. Check `~/odysseus/` (common quickstart clone location)
/// 3. Fall back to the current working directory
fn detect_repo_dir() -> PathBuf {
    // Walk up from exe location
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    // macOS .app bundle: Odysseus.app/Contents/MacOS/ → Odysseus.app/Contents/Resources/
    let macos_bundle = exe_dir
        .parent()
        .and_then(|p| p.parent())
        .filter(|p| p.join("Resources").exists())
        .map(|p| p.to_path_buf());

    let mut check = macos_bundle.unwrap_or(exe_dir);
    loop {
        if check.join("app.py").exists() {
            info!("Found repo at {}", check.display());
            return check;
        }
        if !check.pop() {
            break;
        }
    }

    // Common quickstart location
    if let Some(home) = dirs::home_dir() {
        let candidate = home.join("odysseus");
        if candidate.join("app.py").exists() {
            info!("Found repo at {}", candidate.display());
            return candidate;
        }
    }

    let fallback = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    info!("Using current directory as repo root: {}", fallback.display());
    fallback
}

/// Tauri command: start the server and navigate the webview.
#[tauri::command]
async fn start_server(app: AppHandle) -> Result<String, String> {
    let repo_dir = app.state::<Mutex<PathBuf>>()
        .lock().unwrap()
        .clone();

    let (tx, rx) = oneshot::channel();

    let app_clone = app.clone();
    tokio::spawn(async move {
        let mut sm = server::ServerManager::new(repo_dir, 7000);
        match sm.start() {
            Ok(()) => {
                info!("Server process started, waiting for readiness…");
                match sm.wait_ready(120).await {
                    Ok(()) => {
                        let url = sm.server_url();
                        info!("Server ready at {url}");
                        // Navigate the webview to the server
                        if let Ok(parsed) = url::Url::parse(&url) {
                            if let Some(window) = app_clone.get_webview_window("main") {
                                let _ = window.navigate(parsed);
                            }
                        }
                        let _ = tx.send(Ok(url));
                    }
                    Err(e) => {
                        let _ = tx.send(Err(format!(
                            "Server did not become ready: {e}. Check logs/desktop-server.log for details."
                        )));
                    }
                }
            }
            Err(e) => {
                let _ = tx.send(Err(format!("Failed to start server: {e}")));
            }
        }
        // Keep ServerManager alive until the app exits (Drop handles cleanup)
        app_clone.manage(Mutex::new(sm));
    });

    rx.await.map_err(|e| e.to_string())?
}

pub fn run() {
    let repo_dir = detect_repo_dir();

    tauri::Builder::default()
        .manage(Mutex::new(repo_dir))
        .invoke_handler(tauri::generate_handler![start_server])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // Auto-start the server on launch
            tauri::async_runtime::spawn(async move {
                match start_server(app_handle).await {
                    Ok(url) => info!("App ready at {url}"),
                    Err(e) => log::error!("{e}"),
                }
            });
            Ok(())
        })
        .on_window_event(|_window, event| {
            // Window closed → app exits → ServerManager drops → server stops
            if let tauri::WindowEvent::Destroyed = event {
                info!("Window closed, shutting down…");
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
