use std::sync::Mutex;
use tauri::{
    AppHandle, Emitter, Manager,
    menu::{MenuBuilder, MenuItem, MenuItemBuilder, CheckMenuItemBuilder},
    tray::{TrayIcon, TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent},
    Wry,
};

/// Menu items that need runtime updates.
pub struct TrayState {
    pub server_status: MenuItem<Wry>,
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

    // Store server_status so we can update it later
    app.manage(Mutex::new(TrayState {
        server_status: server_status.clone(),
    }));

    let tray = TrayIconBuilder::new()
        .icon(icon)
        .menu(&menu)
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
