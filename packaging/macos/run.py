"""run.py — PyInstaller entry point for Odysseus.

Two modes (set via ODYSSEUS_MODE env var):
  server — headless uvicorn only (no PyWebView import)
  gui    — single PyWebView window only (no app/uvicorn import)

The launcher starts server first (via raw binary), waits for port 7860,
then starts gui via OdysseusGUI.app mini-bundle (correct Dock icon/name).
"""

import os
import sys
import pathlib

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Env + paths — safe in all modes, no heavy imports
# ═══════════════════════════════════════════════════════════════════════════════

def _load_env_file(path: str) -> None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_data_dir = os.environ.get(
    "ODYSSEUS_DATA",
    os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Odysseus"),
)

_load_env_file(os.path.join(_data_dir, ".env"))

# Add Homebrew to PATH so bundled app can find tmux, llama-server, etc.
# The frozen app inherits a minimal PATH that excludes /opt/homebrew/bin.
_brew_paths = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
_current_path = os.environ.get("PATH", "")
_extra = ":".join(p for p in _brew_paths if p not in _current_path)
if _extra:
    os.environ["PATH"] = _extra + ":" + _current_path

os.environ["DATABASE_URL"]          = f"sqlite:///{_data_dir}/app.db"
os.environ.setdefault("ODYSSEUS_DATA",     _data_dir)
os.environ.setdefault("CHROMA_DB_PATH",    os.path.join(_data_dir, "chroma"))
os.environ.setdefault("UPLOAD_DIR",        os.path.join(_data_dir, "uploads"))
os.environ.setdefault("PERSONAL_DOCS_DIR", os.path.join(_data_dir, "personal_docs"))
os.environ.setdefault("TTS_CACHE_DIR",     os.path.join(_data_dir, "tts_cache"))
os.environ.setdefault("MEMORY_VECTOR_DIR", os.path.join(_data_dir, "memory_vectors"))
os.environ.pop("CHROMADB_HOST", None)
os.environ.pop("CHROMADB_PORT", None)

if getattr(sys, "frozen", False):
    _bundle = sys._MEIPASS
    os.chdir(_bundle)
    if _bundle not in sys.path:
        sys.path.insert(0, _bundle)

_internal_data = pathlib.Path(os.getcwd()) / "data"
_real_data = pathlib.Path(_data_dir)
if not _internal_data.exists() and not _internal_data.is_symlink():
    try:
        _internal_data.symlink_to(_real_data)
    except OSError:
        pass

for _sub in [
    "deep_research", "tts_cache", "uploads", "personal_docs",
    "personal_uploads", "chroma", "memory_vectors", "logs",
    "generated_images", "rag", "skills",
]:
    (_real_data / _sub).mkdir(parents=True, exist_ok=True)

port = int(os.environ.get("APP_PORT", os.environ.get("ODYSSEUS_PORT", "7860")))
host = os.environ.get("APP_BIND", os.environ.get("ODYSSEUS_HOST", "127.0.0.1"))
_mode = os.environ.get("ODYSSEUS_MODE", "server").strip().lower()

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Multiprocessing child guard (server mode only)
# ═══════════════════════════════════════════════════════════════════════════════

def _is_pyinstaller_child() -> bool:
    """True when this is a multiprocessing/PyInstaller re-exec child."""
    import multiprocessing as _mp
    if _mp.parent_process() is not None:
        return True
    if len(sys.argv) >= 2:
        if "--multiprocessing-fork" in sys.argv[1:]:
            return True
        if len(sys.argv) >= 3 and sys.argv[-2] == "-c":
            cmd = sys.argv[-1]
            if any(cmd.startswith(p) for p in (
                "from multiprocessing.resource_tracker import main",
                "from multiprocessing.semaphore_tracker import main",
                "from multiprocessing.forkserver import main",
            )):
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Server mode — uvicorn only, NO webview
# ═══════════════════════════════════════════════════════════════════════════════

def _run_server() -> None:
    import multiprocessing
    multiprocessing.freeze_support()
    if _is_pyinstaller_child():
        sys.exit(0)

    import uvicorn
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        log_level="info",
        workers=1,
        reload=False,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: GUI mode — PyWebView only, NO app/uvicorn import
# ═══════════════════════════════════════════════════════════════════════════════

def _acquire_gui_singleton() -> None:
    """Atomic O_EXCL lock — not flock (flock inherited by forked children)."""
    os.makedirs(os.path.join(_data_dir, "logs"), exist_ok=True)
    pid_file = os.path.join(_data_dir, "logs", ".gui.pid")

    for _ in range(3):
        try:
            fd = os.open(pid_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                with open(pid_file) as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)
                sys.exit(0)  # already running
            except (ProcessLookupError, ValueError, PermissionError):
                try:
                    os.remove(pid_file)
                except OSError:
                    sys.exit(0)
            except OSError:
                sys.exit(0)


def _run_gui() -> None:
    import time
    import socket
    import webview

    # Load launcher-written .gui.env so ODYSSEUS_DATA, APP_PORT, APP_NAME
    # are available even when launched via PyInstaller BUNDLE (LSEnvironment
    # only sets ODYSSEUS_MODE; other vars come from this file).
    _gui_env = os.path.join(
        os.path.expanduser("~"), "Library", "Application Support",
        "Odysseus", ".gui.env"
    )
    _load_env_file(_gui_env)

    # Re-read port/host after env load (may have been updated by .gui.env)
    global port, host
    port = int(os.environ.get("APP_PORT", os.environ.get("ODYSSEUS_PORT", "7860")))
    host = os.environ.get("APP_BIND", os.environ.get("ODYSSEUS_HOST", "127.0.0.1"))

    _acquire_gui_singleton()

    url = f"http://{host}:{port}"

    for _ in range(180):
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except OSError:
            time.sleep(1)
    else:
        log_path = os.path.join(_data_dir, "logs", "odysseus.log")
        url = (
            "data:text/html,<html><body style='font-family:sans-serif;padding:40px'>"
            "<h2>Odysseus failed to start</h2>"
            f"<p>Check logs at:<br><code>{log_path}</code></p>"
            "</body></html>"
        )

    # Set process name so Dock shows "Odysseus" not "OdysseusGUI"
    app_title = os.environ.get("ODYSSEUS_APP_NAME", "Odysseus")
    try:
        from AppKit import NSApplication, NSBundle
        from Foundation import NSProcessInfo
        # Set the process name shown in Dock + menu bar
        NSProcessInfo.processInfo().setValue_forKey_(app_title, "processName")
        # Also patch the bundle display name
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = app_title
        info["CFBundleDisplayName"] = app_title
        # Ensure app appears in Dock (not as background agent)
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        app.activateIgnoringOtherApps_(True)
    except Exception:
        pass

    window = webview.create_window(
        title=app_title,
        url=url,
        width=1280,
        height=820,
        min_size=(800, 600),
        resizable=True,
        text_select=True,
    )

    def on_loaded():
        try:
            result = window.evaluate_js(
                'document.body ? document.body.innerText : ""'
            )
            if result and "failed" in str(result).lower():
                time.sleep(2)
                window.load_url(url)
        except Exception:
            pass

    window.events.loaded += on_loaded
    webview.start(gui="cocoa", debug=False)

    try:
        os.remove(os.path.join(_data_dir, "logs", ".gui.pid"))
    except OSError:
        pass

    sys.exit(0)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if _mode == "gui":
        _run_gui()
    elif _mode == "server":
        _run_server()
    else:
        print(f"Unknown ODYSSEUS_MODE={_mode!r}. Use 'server' or 'gui'.", file=sys.stderr)
        sys.exit(1)
