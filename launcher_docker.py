"""Windows launcher for the Docker-based Odysseus setup."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


APP_PORT = int(os.getenv("APP_PORT", "7000"))
APP_URL = f"http://127.0.0.1:{APP_PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent
APP_ICON = PROJECT_ROOT / "static" / "icon.ico"

EDGE_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
]

try:
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Odysseus.Docker")
except Exception:
    pass


def _is_ready(url: str) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=3) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except Exception:
        return False


def _docker_compose_cmd() -> list[str]:
    # The installed app carries its own source bundle. Rebuild on launch so a
    # newly installed UI or backend feature cannot be masked by an older image.
    # Docker reuses unchanged layers, making ordinary launches fast.
    return ["docker", "compose", "up", "-d", "--build"]


def _start_docker_desktop() -> bool:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Docker" / "Docker" / "Docker Desktop.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            subprocess.Popen([str(candidate)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


def _wait_for_docker(timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Docker did not become ready in time.")


def _wait_for_app(timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_ready(APP_URL):
            return
        time.sleep(2)
    raise RuntimeError(f"Odysseus did not respond at {APP_URL} in time.")


def _open_standalone_window() -> bool:
    for candidate in EDGE_CANDIDATES:
        if candidate and candidate.exists():
            args = [
                str(candidate),
                "--app=" + APP_URL,
                "--new-window",
                "--window-size=1400,960",
                "--window-position=80,40",
                "--force-device-scale-factor=1",
                "--disable-pinch",
            ]
            if APP_ICON.exists():
                args.append("--app-id=odysseus-docker")
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


def main() -> int:
    os.chdir(PROJECT_ROOT)
    _start_docker_desktop()
    _wait_for_docker()
    subprocess.run(_docker_compose_cmd(), check=True)
    _wait_for_app()

    if not _open_standalone_window():
        try:
            import webview

            webview.create_window(
                "Odysseus",
                APP_URL,
                width=1400,
                height=960,
                resizable=True,
                fullscreen=False,
                frameless=False,
                easy_drag=False,
                icon=str(APP_ICON) if APP_ICON.exists() else None,
            )
            webview.start(debug=False)
        except Exception:
            import webbrowser

            webbrowser.open(APP_URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
