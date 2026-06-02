// GUI subsystem in every build so no console window ever appears (this is an
// end-user app, not a dev tool). Because there's no console, all diagnostics go
// to log files under <backend>/logs/ so an AI or a person can read what failed.
#![windows_subsystem = "windows"]

use std::fs::OpenOptions;
use std::io::Write as _;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 7000;

/// Data directories the backend expects to exist (SQLite won't mkdir; ChromaDB
/// needs data/chroma; several features write into these).
const DATA_SUBDIRS: &[&str] = &[
    "data",
    "data/uploads",
    "data/personal_docs",
    "data/personal_uploads",
    "data/tts_cache",
    "data/generated_images",
    "data/deep_research",
    "data/chroma",
    "data/rag",
    "data/memory_vectors",
    "logs",
];

/// Injected on every page load to add the frameless custom title bar and offset
/// the app's top-pinned fixed elements (toast notifications, hamburger) below it.
const BOOT_JS: &str = r#"(function () {
  if (!document.body || document.getElementById('__od_titlebar')) return;
  var isLogin = location.pathname === '/login' || !!document.getElementById('authForm');
  var H = 32;
  var s = document.createElement('style');
  s.id = '__od_tb_css';
  s.textContent =
    (isLogin ? '' : 'body{box-sizing:border-box!important;padding-top:' + H + 'px!important;}') +
    '#__od_titlebar{position:fixed;top:0;left:0;right:0;height:' + H + 'px;display:flex;align-items:center;' +
    'z-index:2147483647;background:var(--bg,#0f1115);border-bottom:1px solid var(--border,rgba(255,255,255,.07));' +
    'font-family:var(--font-family,system-ui,sans-serif);user-select:none;-webkit-user-select:none;}' +
    '#__od_titlebar .od-drag{flex:1;height:100%;display:flex;align-items:center;gap:7px;padding-left:12px;' +
    'font-size:12px;letter-spacing:.3px;color:var(--fg,#cdd2da);opacity:.7;}' +
    '#__od_titlebar .od-dot{color:var(--red,#e06c75);font-size:13px;line-height:1;}' +
    '#__od_titlebar .od-btns{display:flex;height:100%;}' +
    '#__od_titlebar .od-btn{width:46px;height:100%;border:0;background:transparent;color:var(--fg,#cdd2da);' +
    'opacity:.85;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;}' +
    '#__od_titlebar .od-btn:hover{background:rgba(255,255,255,.09);opacity:1;}' +
    '#__od_titlebar .od-close:hover{background:#e81123;color:#fff;}' +
    '#toast{margin-top:' + H + 'px!important;}' +
    '.hamburger-btn{margin-top:' + H + 'px!important;}';
  (document.head || document.documentElement).appendChild(s);
  var bar = document.createElement('div');
  bar.id = '__od_titlebar';
  var drag = document.createElement('div');
  drag.className = 'od-drag';
  drag.setAttribute('data-tauri-drag-region', '');
  drag.innerHTML = '<span class="od-dot" data-tauri-drag-region>&#x25B2;</span><span data-tauri-drag-region>Odysseus</span>';
  var btns = document.createElement('div');
  btns.className = 'od-btns';
  function mk(c, h) { var b = document.createElement('button'); b.className = 'od-btn ' + c; b.innerHTML = h; b.tabIndex = -1; return b; }
  var bn = mk('od-min', '&#x2013;'), bx = mk('od-max', '&#x25A1;'), bc = mk('od-close', '&#x2715;');
  btns.appendChild(bn); btns.appendChild(bx); btns.appendChild(bc);
  bar.appendChild(drag); bar.appendChild(btns);
  document.body.appendChild(bar);
  function W() { try { return window.__TAURI__.window.getCurrentWindow(); } catch (e) { return null; } }
  bn.onclick = function () { var w = W(); if (w) w.minimize(); };
  bx.onclick = function () { var w = W(); if (w) w.toggleMaximize(); };
  bc.onclick = function () { var w = W(); if (w) w.close(); };
})();"#;

/// Tracks the backend processes (ChromaDB + uvicorn) for teardown on exit.
struct Backend {
    children: Mutex<Vec<Child>>,
    started_by_us: Mutex<bool>,
}

/// Machine readiness, surfaced to the first-run setup screen.
#[derive(serde::Serialize)]
struct EnvInfo {
    virtualization: bool,
    wsl2: bool,
    ollama: bool,
    gpus: Vec<String>,
}

// ── Logging ─────────────────────────────────────────────────────────────────

/// Open a log file under <cwd>/logs/. `append=false` truncates it per launch
/// (good for the noisy backend/chroma logs — keeps the *current* run readable);
/// `append=true` keeps history (desktop/updater logs are small).
fn open_log(cwd: &Path, name: &str, append: bool) -> Option<std::fs::File> {
    let dir = cwd.join("logs");
    let _ = std::fs::create_dir_all(&dir);
    let mut opts = OpenOptions::new();
    opts.create(true);
    if append {
        opts.append(true);
    } else {
        opts.write(true).truncate(true);
    }
    opts.open(dir.join(name)).ok()
}

/// Append a timestamped line to <cwd>/logs/desktop.log (the wrapper's own log).
fn log_line(cwd: &Path, msg: &str) {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Some(mut f) = open_log(cwd, "desktop.log", true) {
        let _ = writeln!(f, "[{ts}] {msg}");
    }
}

/// Set a command's stdout+stderr to the given log file (both streams interleave).
fn redirect_to(cmd: &mut Command, cwd: &Path, log_name: &str) {
    if let Some(out) = open_log(cwd, log_name, false) {
        if let Ok(err) = out.try_clone() {
            cmd.stdout(Stdio::from(out)).stderr(Stdio::from(err));
        }
    }
}

fn port_open() -> bool {
    let Ok(mut addrs) = (BACKEND_HOST, BACKEND_PORT).to_socket_addrs() else {
        return false;
    };
    match addrs.next() {
        Some(addr) => TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok(),
        None => false,
    }
}

/// (python_exe, working_dir). Prefers the bundled runtime (installed app); the
/// dev fallback is the ODYSSEUS_HOME env var so `cargo run` works on a dev box
/// without any machine-specific path hard-coded here.
fn resolve_backend(app: &tauri::AppHandle) -> Option<(PathBuf, PathBuf)> {
    if let Ok(res) = app.path().resource_dir() {
        let cwd = res.join("backend");
        let py = cwd.join("runtime").join("python.exe");
        if py.exists() {
            return Some((py, cwd));
        }
    }
    if let Ok(home) = std::env::var("ODYSSEUS_HOME") {
        let home = PathBuf::from(home.trim());
        let py = home.join("venv").join("Scripts").join("python.exe");
        if py.exists() {
            return Some((py, home));
        }
    }
    None
}

fn ensure_data_dirs(cwd: &Path) {
    for d in DATA_SUBDIRS {
        let _ = std::fs::create_dir_all(cwd.join(d));
    }
}

fn augmented_path(cwd: &Path, runtime_dir: &Path) -> std::ffi::OsString {
    let mut dirs: Vec<PathBuf> = Vec::new();
    for p in [cwd.join("git").join("bin"), cwd.join("git").join("cmd")] {
        if p.exists() {
            dirs.push(p);
        }
    }
    dirs.push(runtime_dir.to_path_buf());
    dirs.push(runtime_dir.join("Scripts"));
    if let Some(existing) = std::env::var_os("PATH") {
        dirs.extend(std::env::split_paths(&existing));
    }
    std::env::join_paths(dirs).unwrap_or_else(|_| std::env::var_os("PATH").unwrap_or_default())
}

#[cfg(windows)]
fn no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
}
#[cfg(not(windows))]
fn no_window(_cmd: &mut Command) {}

fn start_backend(app: &tauri::AppHandle, children: &mut Vec<Child>) {
    let Some((python, cwd)) = resolve_backend(app) else {
        return; // nothing we can log to yet; resolve only fails with no bundle + no ODYSSEUS_HOME
    };
    ensure_data_dirs(&cwd);
    log_line(&cwd, "launch: starting backend");
    let runtime_dir = python
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| cwd.clone());
    let path_env = augmented_path(&cwd, &runtime_dir);

    // Self-update: apply any staged update before the backend starts (fast if
    // none), then kick a weekly background check. Output → logs/updater.log.
    let updater = cwd.join("updater.py");
    if updater.exists() {
        let mut ap = Command::new(&python);
        ap.arg(&updater).arg("--apply").current_dir(&cwd).env("PATH", &path_env);
        redirect_to(&mut ap, &cwd, "updater.log");
        no_window(&mut ap);
        let _ = ap.status();
        let mut ck = Command::new(&python);
        ck.arg(&updater).arg("--check").current_dir(&cwd).env("PATH", &path_env);
        redirect_to(&mut ck, &cwd, "updater.log");
        no_window(&mut ck);
        let _ = ck.spawn();
    }

    // 1. ChromaDB server (vector memory). Output → logs/chroma.log.
    let mut chroma = Command::new(&python);
    chroma
        .arg(cwd.join("chroma_server.py"))
        .args(["run", "--host", "127.0.0.1", "--port", "8100", "--path"])
        .arg(cwd.join("data").join("chroma"))
        .current_dir(&cwd)
        .env("PATH", &path_env);
    redirect_to(&mut chroma, &cwd, "chroma.log");
    no_window(&mut chroma);
    match chroma.spawn() {
        Ok(ch) => {
            log_line(&cwd, &format!("chromadb started, pid {}", ch.id()));
            children.push(ch);
        }
        Err(e) => log_line(&cwd, &format!("chromadb FAILED (vector memory off): {e}")),
    }

    // 2. The app server. Output (uvicorn + the whole app log) → logs/backend.log.
    let mut uvicorn = Command::new(&python);
    uvicorn
        .args(["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "7000"])
        .current_dir(&cwd)
        .env("PATH", &path_env);
    redirect_to(&mut uvicorn, &cwd, "backend.log");
    no_window(&mut uvicorn);
    match uvicorn.spawn() {
        Ok(ch) => {
            log_line(&cwd, &format!("backend started, pid {}", ch.id()));
            children.push(ch);
        }
        Err(e) => log_line(&cwd, &format!("backend FAILED to start: {e}")),
    }
}

fn kill_tree(pid: u32) {
    #[cfg(windows)]
    {
        let mut k = Command::new("taskkill");
        k.args(["/F", "/T", "/PID", &pid.to_string()]);
        no_window(&mut k);
        let _ = k.status();
    }
}

// ── First-run setup helpers + commands ──────────────────────────────────────

fn ps_capture(cmd: &str) -> String {
    let mut c = Command::new("powershell");
    c.args(["-NoProfile", "-NonInteractive", "-Command", cmd]);
    no_window(&mut c);
    match c.output() {
        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        Err(_) => String::new(),
    }
}

fn marker_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path()
        .app_local_data_dir()
        .ok()
        .map(|d| d.join("setup_done"))
}

#[tauri::command]
fn detect_env() -> EnvInfo {
    let virtualization = ps_capture("(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled")
        .to_lowercase()
        .contains("true");
    let wsl2 = Path::new(r"C:\Windows\System32\wsl.exe").exists()
        || Path::new(r"C:\Windows\Sysnative\wsl.exe").exists();
    let ollama = {
        let port = "127.0.0.1:11434"
            .to_socket_addrs()
            .ok()
            .and_then(|mut a| a.next())
            .map(|a| TcpStream::connect_timeout(&a, Duration::from_millis(300)).is_ok())
            .unwrap_or(false);
        let exe = std::env::var_os("LOCALAPPDATA")
            .map(|l| Path::new(&l).join("Programs").join("Ollama").join("ollama.exe").exists())
            .unwrap_or(false);
        port || exe
    };
    let gpus = ps_capture("(Get-CimInstance Win32_VideoController).Name -join '||'")
        .split("||")
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    EnvInfo { virtualization, wsl2, ollama, gpus }
}

#[tauri::command]
fn is_first_run(app: tauri::AppHandle) -> bool {
    match marker_path(&app) {
        Some(p) => !p.exists(),
        None => false,
    }
}

#[tauri::command]
fn finish_setup(app: tauri::AppHandle) {
    if let Some(p) = marker_path(&app) {
        if let Some(parent) = p.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(&p, b"1");
    }
}

/// Open an http(s) URL in the default browser. Uses explorer.exe (no shell
/// parsing — avoids any `&`/metacharacter command-injection) and only accepts
/// http/https.
#[tauri::command]
fn open_url(url: String) {
    let ok = (url.starts_with("http://") || url.starts_with("https://"))
        && !url.contains(['"', '\'', '\n', '\r']);
    if ok {
        let mut c = Command::new("explorer");
        c.arg(&url);
        no_window(&mut c);
        let _ = c.spawn();
    }
}

#[tauri::command]
fn install_wsl2() -> Result<(), String> {
    let mut c = Command::new("powershell");
    c.args([
        "-NoProfile",
        "-Command",
        "Start-Process wsl -ArgumentList '--install' -Verb RunAs",
    ]);
    no_window(&mut c);
    c.spawn().map(|_| ()).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            detect_env,
            is_first_run,
            finish_setup,
            open_url,
            install_wsl2
        ])
        .on_page_load(|webview, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                let _ = webview.eval(BOOT_JS);
            }
        })
        .manage(Backend {
            children: Mutex::new(Vec::new()),
            started_by_us: Mutex::new(false),
        })
        .setup(|app| {
            let first_run = marker_path(app.handle()).map(|p| !p.exists()).unwrap_or(false);
            let handle = app.handle().clone();
            // Everything heavy happens off the main thread so the window paints
            // the splash immediately: apply pending self-update → start ChromaDB
            // + uvicorn → wait for the port → navigate to the app.
            std::thread::spawn(move || {
                if !port_open() {
                    let state = handle.state::<Backend>();
                    let mut children = state.children.lock().unwrap();
                    start_backend(&handle, &mut children);
                    if !children.is_empty() {
                        *state.started_by_us.lock().unwrap() = true;
                    }
                }

                let mut ready = false;
                for _ in 0..240 {
                    std::thread::sleep(Duration::from_millis(500));
                    if port_open() {
                        ready = true;
                        break;
                    }
                }
                if first_run {
                    return; // index.html sends the window to the setup screen
                }
                if let Some(window) = handle.get_webview_window("main") {
                    if ready {
                        let _ = window.eval("window.location.replace('http://127.0.0.1:7000/')");
                    } else {
                        let _ = window.eval(
                            "document.body.innerText = 'Could not start the Odysseus backend — see logs/backend.log';",
                        );
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Odysseus desktop app")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                let state = app_handle.state::<Backend>();
                let started_by_us = *state.started_by_us.lock().unwrap();
                if started_by_us {
                    for mut ch in state.children.lock().unwrap().drain(..) {
                        kill_tree(ch.id());
                        let _ = ch.kill();
                    }
                }
            }
        });
}
