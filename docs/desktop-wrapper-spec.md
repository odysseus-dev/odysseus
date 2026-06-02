# Odysseus Desktop Wrapper (Tauri v2)

**Status:** Spec  
**Date:** 2026-06-02  
**Author:** OpenCode  
**Repository:** [pewdiepie-archdaemon/odysseus](https://github.com/pewdiepie-archdaemon/odysseus)

## Overview

A lightweight Tauri v2 desktop wrapper for the existing Odysseus web application. It launches the Python/FastAPI backend as a child process, embeds the SPA in a native webview window, and adds system tray integration, global hotkeys, auto-start, and auto-update — all without modifying a single line of the existing Python or JavaScript code.

## Goals

- Provide a native desktop experience for Odysseus on Windows, macOS, and Linux
- Zero changes to the existing Python backend or static SPA frontend
- Self-contained `desktop/` directory that integrates with the existing build and CI
- Minimal barrier to entry: users still need Python + deps installed separately

## Non-Goals

- Bundling Python or pip dependencies inside the installer
- Replacing the web UI with native UI components
- Adding Tauri-specific APIs to the SPA frontend
- Modifying the existing server code, routes, or configuration

---

## 1. Project Structure

```
desktop/
├── src/
│   ├── main.rs            # Entry point: app builder, window, event loop
│   ├── lib.rs             # Tauri command handlers, module re-exports
│   ├── server.rs          # Python process detection, spawn, health-check, shutdown
│   └── tray.rs            # System tray icon, menu, event handlers
├── icons/
│   ├── icon.png           # 1024×1024 source (generated from docs/odysseus.jpg)
│   ├── icon.ico           # Windows
│   ├── icon.icns          # macOS
│   └── 32x32.png          # Linux / tray
├── tauri.conf.json        # Tauri configuration
├── capabilities/
│   └── desktop.json       # Tauri v2 capability permissions
├── Cargo.toml             # Rust dependencies
├── build.rs              # Tauri build script
└── .github/workflows/    # CI (shared with root, filtered to desktop/ paths)
```

**Key constraint:** `desktop/` is a completely standalone Tauri project. It does not share Cargo workspace or npm dependencies with the root. The only integration point is the CI workflow file.

---

## 2. Python Server Management (`server.rs`)

### 2.1 Python Detection

On startup, probe the following locations in order:

| Platform | Priority | Command |
|----------|----------|---------|
| All | 1 | `$REPO_DIR/venv/bin/python3` (macOS/Linux) or `$REPO_DIR/venv\Scripts\python.exe` (Windows) |
| All | 2 | `$REPO_DIR/.venv/bin/python3` / `.venv\Scripts\python.exe` |
| Windows | 3 | `py -3.13`, `py -3.12`, `py -3.11` |
| macOS | 4 | `python3` (from PATH) |
| Linux | 5 | `python3` (from PATH) |

The repo directory is resolved relative to the Tauri executable. On macOS, the app bundle is placed adjacent to the cloned repo (or the repo is found via a stored path in app config).

### 2.2 First-Run Setup

If `$REPO_DIR/.env` does not exist:
1. Spawn `$PYTHON setup.py` with environment variables `ODYSSEUS_ADMIN_USER=admin` and `ODYSSEUS_ADMIN_PASSWORD=<random>`
2. Capture the random password from setup.py output
3. Store it in the Tauri app config for first-login display
4. Show a one-time dialog: "Odysseus is ready. Admin password: <random>. Change it after login."

### 2.3 Server Lifecycle

```
App Start
  │
  ├─► Find Python executable
  │     └─► Not found → show error dialog with install instructions
  │
  ├─► Check venv exists
  │     └─► Missing → run `python -m venv venv` + `pip install -r requirements.txt`
  │
  ├─► Spawn: `$PYTHON -m uvicorn app:app --host 127.0.0.1 --port $PORT`
  │     ├─► stdout/stderr → captured log file (logs/desktop-server.log)
  │     └─► PID stored for lifecycle management
  │
  ├─► Health check loop (every 500ms, max 120 retries ≈ 60s)
  │     ├─► GET http://127.0.0.1:$PORT/api/health → 200 → READY
  │     └─► Timeout → show error, offer Retry / Open Logs / Quit
  │
  ├─► Webview navigates to http://127.0.0.1:$PORT
  │
  └─► On app quit:
        ├─► Send SIGTERM (Unix) / CTRL_BREAK_EVENT (Windows)
        ├─► Wait 5s for graceful shutdown
        └─► Force kill if still running
```

### 2.4 Crash Recovery

If the health-check fails after the server was previously healthy:
- Show system notification: "Odysseus server has stopped unexpectedly."
- Tray menu shows: `Server: ● Stopped` (red indicator)
- Offer: `Restart Server` from tray menu
- On restart, replay the spawn → health-check flow

---

## 3. Window

**Tauri configuration (`tauri.conf.json`):**

```json
{
  "app": {
    "windows": [
      {
        "title": "Odysseus",
        "width": 1200,
        "height": 800,
        "minWidth": 800,
        "minHeight": 600,
        "resizable": true,
        "decorations": true,
        "center": true
      }
    ],
    "security": {
      "csp": "default-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*; style-src 'self' 'unsafe-inline'; img-src 'self' data: http://127.0.0.1:*;"
    }
  },
  "bundle": {
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/icon.ico",
      "icons/icon.icns"
    ]
  }
}
```

**Important:** `decorations: true` — use native window chrome. No custom title bar. This keeps the app looking native on each platform with zero frontend changes.

**URL loading strategy:**
1. Show a built-in loading page (embedded HTML string in Tauri) centered on "Starting Odysseus…" with the app icon
2. On health-check success, call `window.location.href = "http://127.0.0.1:PORT"` from the loading page
3. The SPA takes over entirely from there

The loading page is deliberately minimal. We do not inject Tauri APIs into the SPA window context — the SPA runs exactly as it would in a browser.

---

## 4. System Tray (`tray.rs`)

### 4.1 Tray Icon

The 32×32 app icon displayed in the system tray. On macOS this goes in the menu bar; on Windows/Linux in the notification area.

### 4.2 Menu Structure

```
Show Odysseus                  → Bring window to front (or create if hidden)
───────────────
Server: ● Running              → Status indicator (disabled menu item)
   Restart Server              → Kill + re-spawn Python process
   Open Server Logs            → Open logs/desktop-server.log in default text editor
───────────────
Open in Browser                → Open http://127.0.0.1:$PORT in default browser
───────────────
✓ Auto-start on Login          → Toggle (Tauri auto-launch plugin)
✓ Minimize to Tray on Close    → Toggle (config preference)
───────────────
About Odysseus                 → Version info dialog
Quit                           → Kill Python → exit
```

### 4.3 Behavior

| Action | Result |
|--------|--------|
| Close window | If "Minimize to Tray" enabled → hide window (not quit). If disabled → quit entirely. |
| Click tray icon (Windows) | Toggle window visibility |
| Click tray icon (macOS) | Open menu (macOS standard) |
| Double-click tray icon (Windows) | Show window |

---

## 5. Global Hotkey

- **Default binding:** `Ctrl+Shift+O` (Windows/Linux) / `Cmd+Shift+O` (macOS)
- **Action:** Bring the Odysseus window to front. If minimized/hidden, restore it.
- **Configurable** via the app config file
- **Implementation:** Tauri `global-shortcut` plugin v2

---

## 6. Auto-start on Login

- **Implementation:** Tauri `auto-launch` plugin v2
- **Behavior:** When enabled, registers the app with the OS startup mechanism:
  - Windows: Registry `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
  - macOS: `~/Library/LaunchAgents/`
  - Linux: `~/.config/autostart/`
- The auto-start launches the Tauri app, which finds/detects Python and starts the server.
- **Visual feedback:** Checkmark in tray menu reflects current state.

---

## 7. Auto-update

- **Implementation:** Tauri `updater` plugin v2
- **Update source:** GitHub Releases on the `pewdiepie-archdaemon/odysseus` repo
- **Release tag convention:** `desktop-v1.0.0`, `desktop-v1.1.0`, etc.
- **Update flow:**
  1. App checks for updates on startup + every 6 hours
  2. If available, downloads in background
  3. On Windows: NSIS installer replaces the app
  4. On macOS: replaces .app bundle
  5. On Linux: replaces AppImage file
- The updater only replaces the Tauri wrapper, not the Python code

---

## 8. Configuration Storage

Stored in the platform's standard app-data directory (`$APPDATA/com.odysseus.desktop/config.json` or equivalent):

```json
{
  "port": 7000,
  "pythonPath": null,
  "hotkey": "Ctrl+Shift+O",
  "minimizeToTray": true,
  "autoStart": false,
  "repoDir": null,
  "firstRunPassword": null
}
```

- `repoDir` is auto-detected on first launch (relative to executable location)
- `firstRunPassword` is set once, cleared after user logs in
- Users can override port via CLI: `Odysseus --port 8080`

---

## 9. Build & CI

### 9.1 Local Build

```bash
# Prerequisites: Rust 1.77+, Node.js 18+
cd desktop

# Development mode (hot-reload webview)
cargo tauri dev

# Release build
cargo tauri build
```

The first `cargo tauri build` downloads a prebuilt webview runtime. On Windows, this is the WebView2 runtime (included in Windows 11, available as a redistributable on Windows 10).

### 9.2 Release Artifacts

| Platform | Format |
|----------|--------|
| Windows | `.msi` (WiX) or `.exe` (NSIS) |
| macOS | `.dmg` + `.tar.gz` |
| Linux | `.AppImage` + `.deb` |

### 9.3 GitHub Actions Workflow

**File:** `.github/workflows/desktop-build.yml`

```yaml
name: Build Desktop App

on:
  push:
    paths:
      - 'desktop/**'
    tags:
      - 'desktop-v*'

jobs:
  build:
    strategy:
      matrix:
        os: [windows-latest, macos-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Install dependencies (Linux)
        if: runner.os == 'Linux'
        run: sudo apt-get install -y libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf
      - name: Build
        run: cargo tauri build
        working-directory: desktop
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          path: desktop/target/release/bundle/*
```

---

## 10. Dependencies (Cargo.toml)

```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
tauri-plugin-shell = "2"
tauri-plugin-process = "2"
tauri-plugin-global-shortcut = "2"
tauri-plugin-auto-launch = "2"
tauri-plugin-updater = "2"
tauri-plugin-log = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
reqwest = { version = "0.12", features = ["json"] }
tokio = { version = "1", features = ["full"] }

[build-dependencies]
tauri-build = { version = "2", features = [] }
```

---

## 11. Frontend Changes Required

**None.** The SPA at `static/` is served by the Python backend exactly as it is now. The Tauri webview is a standard Chromium-based browser — it renders HTML/CSS/JS identically to Chrome or Edge. The only difference is the absence of browser chrome (address bar, tabs, bookmarks).

The loading screen is two files inside `desktop/src/`:
- `loading.html` — HTML string embedded in Rust (or read from a `desktop/loading/` directory)

---

## 12. Manual Test Plan

1. **First run (no venv):** Verify Tauri creates venv, installs deps, runs setup.py, shows password dialog, loads SPA.
2. **Second run (existing venv):** Verify server starts quickly, skips setup, loads SPA.
3. **Server crash:** Kill Python process → verify tray notification appears, menu shows stopped status, "Restart Server" works.
4. **Tray minimize:** Close window → verify app minimizes to tray, click tray icon → restore window.
5. **Global hotkey:** Press `Ctrl+Shift+O` from another app → verify Odysseus window comes to front.
6. **Auto-start:** Toggle on → verify OS startup entry created. Log out/in → app launches automatically.
7. **Auto-update:** Publish a fake release → verify update notification appears, download + replace works.
8. **Cross-platform:** Repeat on Windows 11, macOS 14+, Ubuntu 24.04.

---

## 13. Future Possibilities (Out of Scope)

- Bundling a portable Python runtime inside the installer (would increase size by ~50MB but remove the Python dependency)
- Custom native title bar / traffic light controls
- Pushing notifications from the Tauri side (e.g., desktop alerts for scheduled task triggers)
- Deep-link protocol handler (`odysseus://`) for URL-based task creation
