use std::sync::Mutex;
use tauri::{
    AppHandle, Emitter, Manager,
    menu::{CheckMenuItem, MenuBuilder, MenuItem, MenuItemBuilder, CheckMenuItemBuilder},
    tray::{TrayIcon, TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent},
    Wry,
};

/// Menu items that need runtime updates.
pub struct TrayState {
    pub server_status: MenuItem<Wry>,
    pub auto_start: CheckMenuItem<Wry>,
    pub minimize_tray: CheckMenuItem<Wry>,
}

/// Build and return the system tray menu and icon.
pub fn build_tray(app: &AppHandle<Wry>) -> tauri::Result<TrayIcon<Wry>> {
    let show = MenuItemBuilder::with_id("show", "Show Odysseus")
        .accelerator("CmdOrCtrl+Shift+O")
        .build(app)?;

    let server_status = MenuItemBuilder::with_id("server_status", "Server: Starting...")
        .enabled(false)
        .build(app)?;

    let restart = MenuItemBuilder::with_id("restart", "Restart Server")
        .build(app)?;

    let open_logs = MenuItemBuilder::with_id("open_logs", "Open Server Logs")
        .build(app)?;

    let open_browser = MenuItemBuilder::with_id("open_browser", "Open in Browser")
        .build(app)?;

    let auto_start = CheckMenuItemBuilder::with_id("auto_start", "Auto-start on Login")
        .checked(false)
        .build(app)?;

    let minimize_tray = CheckMenuItemBuilder::with_id("minimize_tray", "Minimize to Tray on Close")
        .checked(true)
        .build(app)?;

    let about = MenuItemBuilder::with_id("about", "About Odysseus")
        .build(app)?;

    let quit = MenuItemBuilder::with_id("quit", "Quit")
        .accelerator("CmdOrCtrl+Q")
        .build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&show)
        .separator()
        .item(&server_status)
        .item(&restart)
        .item(&open_logs)
        .separator()
        .item(&open_browser)
        .separator()
        .item(&auto_start)
        .item(&minimize_tray)
        .separator()
        .item(&about)
        .item(&quit)
        .build()?;

    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/32x32.png"))
        .expect("Tray icon must be 32x32 PNG");

    // Store menu items so we can update them at runtime
    app.manage(Mutex::new(TrayState {
        server_status: server_status.clone(),
        auto_start: auto_start.clone(),
        minimize_tray: minimize_tray.clone(),
    }));

    let tray = TrayIconBuilder::new()
        .icon(icon)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("Odysseus")
        .on_menu_event(|app, event| {
            log::info!("Tray menu event: {}", event.id().as_ref());
            match event.id().as_ref() {
                "show" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
                "quit" => {
                    let _ = app.emit("app-quit", ());
                    std::process::exit(0);
                }
                "restart" => {
                    let _ = app.emit("server-restart", ());
                }
                "open_logs" => {
                    let repo_dir = app.state::<Mutex<super::AppConfig>>()
                        .lock().unwrap()
                        .repo_dir.clone();
                    let log_path = repo_dir.join("logs").join("desktop-server.log");
                    if log_path.exists() {
                        let _ = open::that(log_path);
                    }
                }
                "open_browser" => {
                    let port = app.state::<Mutex<super::AppConfig>>()
                        .lock().unwrap()
                        .port;
                    let _ = open::that(format!("http://127.0.0.1:{}", port));
                }
                "auto_start" => {
                    use tauri_plugin_autostart::ManagerExt;

                    // Toggle the checkbox
                    let new_checked = {
                        let state = app.state::<Mutex<TrayState>>();
                        let guard = state.lock().unwrap();
                        let current = guard.auto_start.is_checked().unwrap_or(false);
                        let new_val = !current;
                        guard.auto_start.set_checked(new_val).ok();
                        new_val
                    };

                    // Enable/disable OS-level auto-launch
                    if new_checked {
                        app.autolaunch().enable().ok();
                    } else {
                        app.autolaunch().disable().ok();
                    }

                    // Update in-memory config
                    if let Some(cfg) = app.try_state::<Mutex<super::AppConfig>>() {
                        cfg.lock().unwrap().auto_start = new_checked;
                    }

                    // Persist config to disk
                    let config_path = super::get_config_path();
                    if let Some(cfg) = app.try_state::<Mutex<super::AppConfig>>() {
                        let guard = cfg.lock().unwrap();
                        let saved = super::SavedConfig {
                            port: Some(guard.port),
                            python_path: guard.python_path.clone(),
                            minimize_to_tray: Some(guard.minimize_to_tray),
                            auto_start: Some(guard.auto_start),
                        };
                        drop(guard);
                        super::save_config(&config_path, &saved);
                    }
                }
                "minimize_tray" => {
                    let new_checked = {
                        let state = app.state::<Mutex<TrayState>>();
                        let guard = state.lock().unwrap();
                        let current = guard.minimize_tray.is_checked().unwrap_or(false);
                        let new_val = !current;
                        guard.minimize_tray.set_checked(new_val).ok();
                        new_val
                    };

                    // Update in-memory config
                    if let Some(cfg) = app.try_state::<Mutex<super::AppConfig>>() {
                        cfg.lock().unwrap().minimize_to_tray = new_checked;
                    }

                    // Persist config to disk
                    let config_path = super::get_config_path();
                    if let Some(cfg) = app.try_state::<Mutex<super::AppConfig>>() {
                        let guard = cfg.lock().unwrap();
                        let saved = super::SavedConfig {
                            port: Some(guard.port),
                            python_path: guard.python_path.clone(),
                            minimize_to_tray: Some(guard.minimize_to_tray),
                            auto_start: Some(guard.auto_start),
                        };
                        drop(guard);
                        super::save_config(&config_path, &saved);
                    }
                }
                "about" => {
                    let _ = app.emit("show-about", ());
                }
                _ => {}
            }
        })
        .on_tray_icon_event(|tray, event| {
            match event {
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } => {
                    let app = tray.app_handle();
                    if let Some(window) = app.get_webview_window("main") {
                        if window.is_visible().unwrap_or(false) {
                            let _ = window.hide();
                        } else {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                }
                _ => {}
            }
        })
        .build(app)?;

    Ok(tray)
}

/// Update the server status menu item text.
pub fn set_server_status(app: &AppHandle<Wry>, running: bool) {
    if let Some(state) = app.try_state::<Mutex<TrayState>>() {
        let item = state.lock().unwrap();
        let text = if running {
            "Server: ● Running"
        } else {
            "Server: ● Stopped"
        };
        let _ = item.server_status.set_text(text);
    }
}

/// Set the auto-start checkbox checked state.
pub fn set_auto_start_checked(app: &AppHandle<Wry>, checked: bool) {
    if let Some(state) = app.try_state::<Mutex<TrayState>>() {
        let guard = state.lock().unwrap();
        let _ = guard.auto_start.set_checked(checked);
    }
}

/// Set the minimize-to-tray checkbox checked state.
pub fn set_minimize_tray_checked(app: &AppHandle<Wry>, checked: bool) {
    if let Some(state) = app.try_state::<Mutex<TrayState>>() {
        let guard = state.lock().unwrap();
        let _ = guard.minimize_tray.set_checked(checked);
    }
}
