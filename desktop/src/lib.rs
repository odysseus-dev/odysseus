use std::path::PathBuf;
use std::sync::Mutex;
use log::info;
use tauri::{AppHandle, Manager};
use tokio::sync::oneshot;

pub mod server;
pub mod tray;

/// Shared application configuration.
pub struct AppConfig {
    pub repo_dir: PathBuf,
    pub port: u16,
    pub python_path: Option<String>,
    pub minimize_to_tray: bool,
    pub auto_start: bool,
}

impl AppConfig {
    pub fn detect_repo_dir() -> PathBuf {
        // Priority 1: resolve relative to the executable location
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));

        // Check if we're inside a macOS .app bundle
        let macos_bundle = exe_dir.parent()
            .and_then(|p| p.parent())
            .and_then(|p| {
                if p.join("Resources").exists() {
                    Some(p.to_path_buf())
                } else {
                    None
                }
            });

        // Walk up from exe/bundle looking for app.py
        let mut check = macos_bundle.unwrap_or_else(|| exe_dir.clone());
        loop {
            if check.join("app.py").exists() {
                return check;
            }
            if !check.pop() {
                break;
            }
        }

        // Priority 2: home-directory clone (~/odysseus/)
        if let Some(home) = dirs::home_dir() {
            let home_repo = home.join("odysseus");
            if home_repo.join("app.py").exists() {
                return home_repo;
            }
        }

        // Priority 3: current working directory
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    }
}

/// Tauri command: start the server.
#[tauri::command]
async fn start_server(app: AppHandle) -> Result<String, String> {
    let config = {
        let state = app.state::<Mutex<AppConfig>>();
        let guard = state.lock().unwrap();
        (guard.repo_dir.clone(), guard.port, guard.python_path.clone())
    };

    let (tx, rx) = oneshot::channel();

    // Spawn server management in a background task
    let app_clone = app.clone();
    tokio::spawn(async move {
        let mut sm = server::ServerManager::new(config.0, config.1, config.2);
        match sm.start() {
            Ok(()) => {
                info!("Server process started, waiting for readiness...");
                tray::set_server_status(&app_clone, false);
                match sm.wait_ready(120).await {
                    Ok(()) => {
                        info!("Server is ready!");
                        tray::set_server_status(&app_clone, true);
                        // Navigate the webview to the server
                        let url = sm.server_url();
                        if let Ok(parsed) = url::Url::parse(&url) {
                            if let Some(window) = app_clone.get_webview_window("main") {
                                let _ = window.navigate(parsed);
                            }
                        }
                        let _ = tx.send(Ok(url));
                    }
                    Err(e) => {
                        tray::set_server_status(&app_clone, false);
                        let _ = tx.send(Err(e.to_string()));
                    }
                }
            }
            Err(e) => {
                let _ = tx.send(Err(e.to_string()));
            }
        }
        // Keep sm alive until app exits
        app_clone.manage(Mutex::new(sm));
    });

    rx.await.map_err(|e| e.to_string())?
}

/// Tauri command: restart the server.
#[tauri::command]
async fn restart_server(app: AppHandle) -> Result<String, String> {
    info!("Restarting server...");
    let config = {
        let state = app.state::<Mutex<AppConfig>>();
        let guard = state.lock().unwrap();
        (guard.repo_dir.clone(), guard.port, guard.python_path.clone())
    };

    // Drop old server manager (stops the process)
    // Create new one and start
    let mut sm = server::ServerManager::new(config.0, config.1, config.2);
    sm.start().map_err(|e| e.to_string())?;
    sm.wait_ready(120).await.map_err(|e| e.to_string())?;
    tray::set_server_status(&app, true);
    let url = sm.server_url();
    if let Ok(parsed) = url::Url::parse(&url) {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.navigate(parsed);
        }
    }
    app.manage(Mutex::new(sm));
    Ok(url)
}

/// Tauri command: get server status.
#[tauri::command]
fn get_server_status(app: AppHandle) -> bool {
    app.try_state::<Mutex<server::ServerManager>>()
        .map(|sm| sm.lock().unwrap().is_running.load(std::sync::atomic::Ordering::Relaxed))
        .unwrap_or(false)
}

/// Tauri command: toggle auto-start.
#[tauri::command]
async fn toggle_auto_start(app: AppHandle, enabled: bool) -> Result<(), String> {
    use tauri_plugin_autostart::ManagerExt;
    let auto_launch = app.autolaunch();
    if enabled {
        auto_launch.enable().map_err(|e| e.to_string())?;
    } else {
        auto_launch.disable().map_err(|e| e.to_string())?;
    }
    // Update config
    if let Some(state) = app.try_state::<Mutex<AppConfig>>() {
        state.lock().unwrap().auto_start = enabled;
    }
    Ok(())
}

/// Tauri command: get app version.
#[tauri::command]
fn get_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Initialize all Tauri commands and state.
pub fn run() {
    let repo_dir = AppConfig::detect_repo_dir();
    info!("Repository directory: {}", repo_dir.display());

    // Load persisted config
    let config_path = get_config_path();
    let saved_config = load_config(&config_path);

    let port = saved_config.as_ref().and_then(|c| c.port).unwrap_or(7000);
    let python_path = saved_config.as_ref().and_then(|c| c.python_path.clone());
    let minimize_to_tray = saved_config.as_ref().and_then(|c| c.minimize_to_tray).unwrap_or(true);
    let auto_start = saved_config.as_ref().and_then(|c| c.auto_start).unwrap_or(false);

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::Builder::new().build())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Mutex::new(AppConfig {
            repo_dir,
            port,
            python_path,
            minimize_to_tray,
            auto_start,
        }))
        .invoke_handler(tauri::generate_handler![
            start_server,
            restart_server,
            get_server_status,
            toggle_auto_start,
            get_version,
        ])
        .setup(|app| {
            // Build tray
            let _tray = tray::build_tray(app.handle())?;

            // Restore checkbox states from saved config
            let auto_start = app.state::<Mutex<AppConfig>>()
                .lock().unwrap().auto_start;
            let minimize_tray = app.state::<Mutex<AppConfig>>()
                .lock().unwrap().minimize_to_tray;
            tray::set_auto_start_checked(app.handle(), auto_start);
            tray::set_minimize_tray_checked(app.handle(), minimize_tray);

            // Register global hotkey
            use tauri_plugin_global_shortcut::GlobalShortcutExt;
            app.global_shortcut().on_shortcut("Ctrl+Shift+O", |app, _shortcut, _event| {
                if let Some(window) = app.get_webview_window("main") {
                    if window.is_visible().unwrap_or(false) {
                        let _ = window.hide();
                    } else {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
            })?;

            // Auto-start the server
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let _ = start_server(app_handle).await;
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                let minimize = app.state::<Mutex<AppConfig>>()
                    .lock().unwrap()
                    .minimize_to_tray;
                if minimize {
                    api.prevent_close();
                    let _ = window.hide();
                }
                // If not minimizing, the window closes and the app exits
                // The server is cleaned up via Drop on ServerManager
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Path to the persisted config file.
pub(crate) fn get_config_path() -> PathBuf {
    let config_dir = dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("com.odysseus.desktop");
    std::fs::create_dir_all(&config_dir).ok();
    config_dir.join("config.json")
}

/// Persisted config structure.
#[derive(serde::Serialize, serde::Deserialize, Default)]
pub(crate) struct SavedConfig {
    port: Option<u16>,
    python_path: Option<String>,
    minimize_to_tray: Option<bool>,
    auto_start: Option<bool>,
}

/// Load config from disk.
fn load_config(path: &PathBuf) -> Option<SavedConfig> {
    std::fs::read_to_string(path).ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}

/// Save config to disk (called on config changes).
pub(crate) fn save_config(path: &PathBuf, config: &SavedConfig) {
    if let Ok(json) = serde_json::to_string_pretty(config) {
        let _ = std::fs::write(path, json);
    }
}