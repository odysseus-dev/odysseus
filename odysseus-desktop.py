"""
Odysseus Desktop — native Windows harness for the Odysseus web UI.

Embeds the existing web UI (localhost:7000) in a native window via pywebview,
with a system tray for start/stop/quit control. The backend runs as a managed
subprocess — auto-starts, health-checks, and restarts on crash.

Requires: pywebview, pystray, pillow (all installed in the Odysseus venv).

Usage from the repo root:
    venv\Scripts\python.exe odysseus-desktop.py

Packaging (single .exe):
    venv\Scripts\python.exe -m PyInstaller odysseus-desktop.spec
"""

from __future__ import annotations

import os
import sys
import time
import json
import signal
import atexit
import logging
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / "venv" / "Scripts" / "python.exe"
BACKEND_HOST = os.environ.get("ODYSSEUS_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("ODYSSEUS_PORT", "7000"))
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
HEALTH_URL = f"{BACKEND_URL}/api/health"
LOG_DIR = REPO_ROOT / "logs"

# Timeouts (seconds)
HEALTH_CHECK_INTERVAL = 2
HEALTH_STARTUP_TIMEOUT = 30
HEALTH_RESTART_DELAY = 3
MAX_RESTARTS = 5

log = logging.getLogger("odysseus-desktop")


# ---------------------------------------------------------------------------
# Backend process manager
# ---------------------------------------------------------------------------

class BackendManager:
    """Spawns and monitors the Odysseus backend (uvicorn) as a subprocess."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._restart_count = 0
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Start the backend. Returns True on success."""
        with self._lock:
            if self.is_running:
                log.info("Backend already running")
                return True

            if not VENV_PYTHON.exists():
                log.error(f"Python venv not found at {VENV_PYTHON}")
                return False

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / "backend.log"

            log.info(f"Starting backend: {VENV_PYTHON} -m uvicorn app:app --host {BACKEND_HOST} --port {BACKEND_PORT}")
            try:
                with open(log_path, "a") as lf:
                    lf.write(f"\n--- Backend started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                    self._process = subprocess.Popen(
                        [str(VENV_PYTHON), "-m", "uvicorn", "app:app",
                         "--host", BACKEND_HOST,
                         "--port", str(BACKEND_PORT)],
                        cwd=str(REPO_ROOT),
                        stdout=lf,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
            except Exception as e:
                log.error(f"Failed to start backend: {e}")
                return False

            # Wait for health check
            self._running = True
            if self._wait_healthy():
                self._restart_count = 0
                # Start monitor thread
                threading.Thread(target=self._monitor, daemon=True, name="backend-monitor").start()
                return True
            else:
                log.error("Backend failed health check on startup")
                self.stop()
                return False

    def stop(self) -> None:
        """Gracefully stop the backend."""
        with self._lock:
            self._running = False
            proc = self._process
            self._process = None
            if proc:
                log.info("Stopping backend...")
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        log.warning("Backend didn't exit, killing...")
                        proc.kill()
                        proc.wait(timeout=5)
                except Exception as e:
                    log.error(f"Error stopping backend: {e}")

    def _wait_healthy(self) -> bool:
        """Poll the health endpoint until it responds or timeout."""
        deadline = time.time() + HEALTH_STARTUP_TIMEOUT
        last_error = None
        while time.time() < deadline:
            if self._process and self._process.poll() is not None:
                log.error(f"Backend exited early with code {self._process.returncode}")
                return False
            try:
                req = urllib.request.Request(HEALTH_URL)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        log.info("Backend healthy")
                        return True
            except Exception as e:
                last_error = e
            time.sleep(HEALTH_CHECK_INTERVAL)
        log.error(f"Health check timed out after {HEALTH_STARTUP_TIMEOUT}s. Last error: {last_error}")
        return False

    def _monitor(self) -> None:
        """Background thread: watch the process and restart on crash."""
        while self._running:
            proc = self._process
            if proc is None:
                break
            rc = proc.wait()
            if not self._running:
                break
            self._restart_count += 1
            log.warning(f"Backend exited with code {rc} (restart {self._restart_count}/{MAX_RESTARTS})")
            if self._restart_count > MAX_RESTARTS:
                log.error("Max restarts exceeded — not restarting")
                self._running = False
                break
            time.sleep(HEALTH_RESTART_DELAY)
            self.start()


# ---------------------------------------------------------------------------
# System tray (pystray)
# ---------------------------------------------------------------------------

def _make_tray_icon():
    """Create the Odysseus boat icon (32x32) for the tray."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = (224, 108, 117, 255)       # Odysseus red
    accent_dim = (224, 108, 117, 153)   # 60% opacity

    # Left sail (full opacity)
    draw.polygon([(16, 4), (6, 22), (16, 22)], fill=accent)

    # Right sail (60% opacity)
    draw.polygon([(16, 8), (24, 22), (16, 22)], fill=accent_dim)

    # Hull: two quad beziers  M4 24 Q10 20 16 24 Q22 28 28 24
    def quad_bezier(p0, p1, p2, steps=12):
        pts = []
        for i in range(steps + 1):
            t = i / steps
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
            pts.append((x, y))
        return pts

    hull = quad_bezier((4, 24), (10, 20), (16, 24)) + quad_bezier((16, 24), (22, 28), (28, 24))
    draw.line(hull, fill=accent, width=3, joint="curve")

    return img


class TrayController:
    """System tray icon with start/stop/open/quit."""

    def __init__(self, backend: BackendManager) -> None:
        self.backend = backend
        self._webview_window = None
        self._tray_thread: Optional[threading.Thread] = None

    def set_window(self, window) -> None:
        self._webview_window = window

    def run(self) -> None:
        """Run the tray icon (blocking). Call in a thread."""
        import pystray
        tray = pystray.Icon(
            "Odysseus",
            _make_tray_icon(),
            "Odysseus AI Workspace",
            menu=pystray.Menu(
                pystray.MenuItem("Open Odysseus", self._action_open, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start Backend", self._action_start),
                pystray.MenuItem("Stop Backend", self._action_stop),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Reset Account (First-Run Setup)", self._action_reset_auth),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._action_quit),
            ),
        )
        self._tray = tray
        self._update_tray_state()
        tray.run()

    def stop(self) -> None:
        if hasattr(self, "_tray") and self._tray:
            self._tray.stop()

    # -- menu actions --

    def _action_open(self, icon, item) -> None:
        if self._webview_window:
            # Bring to front — pywebview doesn't have a direct API for this,
            # but we can try to toggle minimize/restore.
            try:
                import pywebview
                if hasattr(pywebview, "windows") and pywebview.windows:
                    win = pywebview.windows[0]
                    win.show()
                    win.restore()
            except Exception:
                pass

    def _action_start(self, icon, item) -> None:
        def _run():
            self.backend.start()
            self._update_tray_state()
        threading.Thread(target=_run, daemon=True).start()

    def _action_stop(self, icon, item) -> None:
        def _run():
            self.backend.stop()
            self._update_tray_state()
        threading.Thread(target=_run, daemon=True).start()

    def _action_quit(self, icon, item) -> None:
        self.backend.stop()
        self.stop()
        # Shut down the webview window
        import pywebview
        if pywebview.windows:
            pywebview.windows[0].destroy()
        os._exit(0)

    def _action_reset_auth(self, icon, item) -> None:
        """Delete the stored admin account and restart the backend so the
        web UI falls back to first-run setup (create-admin-account screen)
        on next load. Does not touch sessions, chats, or other data."""
        def _run():
            self.backend.stop()
            auth_path = REPO_ROOT / "data" / "auth.json"
            try:
                if auth_path.exists():
                    auth_path.unlink()
                    log.info(f"Removed {auth_path} — next launch will show first-run setup")
            except Exception as e:
                log.error(f"Failed to remove {auth_path}: {e}")
            self.backend.start()
            self._update_tray_state()
            self._action_open(icon, item)
            # Force the webview to reload — the DOM is still showing the
            # pre-reset page (e.g. the chat UI) otherwise.
            try:
                import pywebview
                if pywebview.windows:
                    pywebview.windows[0].load_url(BACKEND_URL)
            except Exception as e:
                log.error(f"Failed to reload webview after reset: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _update_tray_state(self) -> None:
        """Update tray title to reflect backend state."""
        if self.backend.is_running:
            self._tray.title = f"Odysseus — Running"
        else:
            self._tray.title = f"Odysseus — Stopped"


# ---------------------------------------------------------------------------
# WebView window
# ---------------------------------------------------------------------------

class WebViewApp:
    """Embeds the Odysseus web UI in a native window."""

    def __init__(self, backend: BackendManager, tray: TrayController) -> None:
        self.backend = backend
        self.tray = tray

    def run(self) -> None:
        import webview

        # Start tray in background thread
        tray_thread = threading.Thread(target=self.tray.run, daemon=True, name="tray")
        tray_thread.start()

        # Start backend
        self.backend.start()

        # Create the window
        window = webview.create_window(
            title="Odysseus AI Workspace",
            url=BACKEND_URL,
            width=1280,
            height=800,
            min_size=(800, 600),
            confirm_close=False,  # We handle close via tray
        )
        self.tray.set_window(window)

        # Override close — minimize to tray instead of quitting
        window.events.closing += self._on_closing

        # Set the taskbar icon to the Odysseus boat
        def _set_taskbar_icon() -> None:
            import ctypes
            import time
            # Brief delay so the native window is fully realized
            time.sleep(0.5)
            hwnd = ctypes.windll.user32.FindWindowW(None, "Odysseus AI Workspace")
            if not hwnd:
                return
            icon = str(REPO_ROOT / "odysseus.ico")
            hicon = ctypes.windll.user32.LoadImageW(
                0, icon, 1, 0, 0, 0x0010,  # IMAGE_ICON, LR_LOADFROMFILE
            )
            if hicon:
                WM_SETICON = 0x0080
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)  # ICON_SMALL
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)  # ICON_BIG

        window.events.shown += _set_taskbar_icon

        webview.start(debug=False)

    def _on_closing(self) -> None:
        """Minimize to tray instead of closing."""
        import webview
        if webview.windows:
            webview.windows[0].hide()
        return False  # Cancel the close event


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "desktop.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    setup_logging()
    log.info("=== Odysseus Desktop starting ===")

    # Ensure we're in the repo root
    os.chdir(str(REPO_ROOT))

    # Save the boat icon as .ico for use by the shortcut
    icon_path = REPO_ROOT / "odysseus.ico"
    if not icon_path.exists():
        _make_tray_icon().save(icon_path, format="ICO")

    backend = BackendManager()
    tray = TrayController(backend)

    # Cleanup on exit
    def cleanup():
        log.info("Shutting down...")
        backend.stop()
    atexit.register(cleanup)

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: os._exit(0))
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))

    app = WebViewApp(backend, tray)
    app.run()


if __name__ == "__main__":
    main()
