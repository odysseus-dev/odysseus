# launcher.py
"""Dedicated entrypoint for the standalone Windows portable launcher.

Runs the FastAPI server in a background thread and shows it in a genuine
native application window via pywebview (WebView2 on Windows) — not a
browser tab, and not an external browser process at all.
"""
import ctypes
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request

_log_path = os.path.join(os.path.expandvars(r"%LOCALAPPDATA%\Odysseus"), "desktop_launcher.log")
try:
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
except Exception:
    pass
# Use a uniquely-named, isolated logger (not "launcher" — that name is already
# used elsewhere in the app for DB-migration logging) with its own handler,
# so app.py's own logging setup can't swallow or redirect these messages.
log = logging.getLogger("odysseus_desktop_launcher")
log.setLevel(logging.INFO)
log.propagate = False
_handler = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
log.addHandler(_handler)


# PyInstaller multiprocessing children re-enter this executable with a private
# bootstrap argument. Consume it before splash/UI or application imports so a
# spawn-based worker does not relaunch the full desktop application.
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

# Define a dummy NullWriter to suppress standard stream crashes (isatty etc.) in GUI mode
class NullWriter:
    """Dummy stream used for sys.stdout/sys.stderr when running frozen with no
    console (both are None in that case).

    fileno() matters here: built-in MCP servers are spawned via subprocess with
    stderr defaulting to sys.stderr (see mcp.client.stdio.stdio_client), and
    Python's subprocess machinery calls .fileno() on whatever it's given to
    duplicate a real OS handle for the child. Without a working fileno(), that
    raises "'NullWriter' object has no attribute 'fileno'" and the subprocess
    is never spawned, so every built-in MCP server fails to connect. Lazily
    open os.devnull to hand back a real, if discarded, file descriptor.
    """
    def __init__(self):
        self._devnull_fd = None

    def write(self, text):
        pass
    def flush(self):
        pass
    def isatty(self):
        return False

    def fileno(self):
        if self._devnull_fd is None:
            self._devnull_fd = os.open(os.devnull, os.O_WRONLY)
        return self._devnull_fd

if sys.stdout is None:
    sys.stdout = NullWriter()
if sys.stderr is None:
    sys.stderr = NullWriter()


def wait_until_ready(url, timeout=60.0, interval=0.5):
    """Poll url until it responds (any HTTP status) or timeout elapses."""
    deadline = time.time() + timeout
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            urllib.request.urlopen(url, timeout=2)
            log.info("wait_until_ready: server ready after %d attempts", attempts)
            return True
        except urllib.error.HTTPError:
            # Any HTTP response (even 3xx/4xx) means the server is up.
            log.info("wait_until_ready: server ready (HTTPError response) after %d attempts", attempts)
            return True
        except Exception:
            time.sleep(interval)
    log.warning("wait_until_ready: timed out after %.1fs waiting for %s", timeout, url)
    return False


def run_server(fastapi_app, bind_host, bind_port):
    import uvicorn
    uvicorn.run(fastapi_app, host=bind_host, port=bind_port, log_level="info")


if __name__ == "__main__":
    # Re-entry mode: when frozen, built-in Python MCP servers (mcp_servers/*.py)
    # have no bundled python.exe to run as, so src/builtin_mcp.py spawns them by
    # re-invoking this same exe with a sentinel argv instead. Handle that here,
    # before the single-instance mutex and app/GUI imports below, so the child
    # actually runs the MCP server's stdio loop on this process's real stdio
    # rather than tripping the mutex check and exiting immediately (which the
    # parent sees as the subprocess closing its connection before any handshake).
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-mcp-server":
        import asyncio
        import importlib
        module = importlib.import_module(sys.argv[2])
        asyncio.run(module.run())
        sys.exit(0)

    from app import app as fastapi_app

    bind_host = os.getenv("APP_BIND", "127.0.0.1")
    bind_port = int(os.getenv("APP_PORT", "7000"))
    url = f"http://{bind_host}:{bind_port}"
    frozen = getattr(sys, 'frozen', False)
    log.info("launcher starting, frozen=%s, url=%s, log_path=%s", frozen, url, _log_path)

    if frozen:
        # Single-instance guard: if Odysseus is already running, exit
        # immediately rather than starting a second server + window.
        ERROR_ALREADY_EXISTS = 183
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\OdysseusDesktopSingleInstance")
        already_running = ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        log.info("single-instance check: already_running=%s", already_running)
        if already_running:
            log.info("another instance is already running; exiting without opening a new window")
            sys.exit(0)

        # Run the server in the background so the main thread is free for
        # the native window's GUI event loop (required by webview on Windows).
        threading.Thread(target=run_server, args=(fastapi_app, bind_host, bind_port), daemon=True).start()
        wait_until_ready(url, timeout=60.0)

        import webview
        webview.create_window("Odysseus", url, width=1400, height=900, min_size=(900, 600))
        log.info("opening native app window")
        webview.start()
        log.info("app window closed, exiting")
        os._exit(0)
    else:
        import uvicorn
        uvicorn.run(fastapi_app, host=bind_host, port=bind_port, log_level="info")
