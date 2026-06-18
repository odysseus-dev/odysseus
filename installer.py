import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
import json

EXTENSION_ID = "odysseus"
EXTENSION_NAME = "Odysseus"
LOG_FILENAME = "odysseus-installer.log"
SIMPLE_SIGNAL_EXE = "Simple-Signal-Desktop.exe"
VENV_DIR_NAME = "venv"
EXTENSION_PORT = int(os.environ.get("ODYSSEUS_EXTENSION_PORT", "7017"))

ELECTRON_CACHE_DIRS = [
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "ShaderCache",
    "blob_storage",
    "Service Worker\\CacheStorage",
    "Network\\Cache",
]

FILES_TO_COPY = [
    ".env.example",
    "app.py",
    "core",
    "routes",
    "src",
    "services",
    "config",
    "static",
    "mcp_servers",
    "integrations",
    "companion",
    "scripts",
    "launch-windows.ps1",
    "requirements.txt",
    "setup.py"
]

def get_windows_creation_flags() -> int:
    flags = 0
    for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
        flags |= getattr(subprocess, name, 0)
    return flags

def append_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write(message.rstrip() + "\n")

def show_windows_message(title: str, message: str, error: bool = False) -> None:
    try:
        import ctypes
        icon = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, icon)
    except Exception:
        pass

def wait_before_close(prompt: str = "Press Enter to close setup...") -> None:
    try:
        input(prompt)
    except Exception:
        print("Setup window will close in 30 seconds...")
        time.sleep(30)

def run_logged(command: list[str], log_path: Path, check: bool = False) -> subprocess.CompletedProcess:
    append_log(log_path, f"\n> {' '.join(command)}")
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        return subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=check,
            creationflags=get_windows_creation_flags(),
        )

def run_capture(command: list[str], log_path: Path) -> str:
    append_log(log_path, f"\n> {' '.join(command)}")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        creationflags=get_windows_creation_flags(),
    )
    if completed.stdout:
        append_log(log_path, completed.stdout)
    if completed.stderr:
        append_log(log_path, completed.stderr)
    return (completed.stdout or "").strip()

def get_user_extensions_dir() -> Path:
    configured = os.environ.get("SIMPLE_SIGNAL_EXTENSIONS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "SimpleSignal" / "extensions"

    return Path.home() / ".simple-signal" / "extensions"

def get_simple_signal_user_data_dir() -> Path | None:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        return None
    return Path(app_data) / "simple-signal-desktop"

def get_source_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

def copy_extension_files(source_dir: Path, extension_dir: Path) -> None:
    extension_dir.mkdir(parents=True, exist_ok=True)

    for filename in FILES_TO_COPY:
        src = source_dir / filename
        if not src.exists():
            continue

        target = extension_dir / filename
        if src.is_dir():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(src, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        print(f" -> Copied {filename}")

    # Generate required extension files
    manifest_path = extension_dir / "manifest.json"
    manifest_path.write_text(json.dumps({
        "name": EXTENSION_NAME,
        "description": "Comprehensive AI chat with memory, research, and multi-modal capabilities",
        "version": "1.0.0"
    }, indent=2), encoding="utf-8")

    target_url = f"http://127.0.0.1:{EXTENSION_PORT}/"
    index_path = extension_dir / "index.html"
    index_path.write_text(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Opening Odysseus</title>
  <style>
    html, body {
      width: 100%;
      height: 100%;
      margin: 0;
      background: #111;
      color: #f4f4f5;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body {
      display: grid;
      place-items: center;
    }
    main {
      width: min(420px, calc(100% - 48px));
      text-align: center;
      line-height: 1.5;
    }
    a {
      color: #e06c75;
    }
    .hint {
      opacity: 0.72;
      font-size: 0.92rem;
    }
  </style>
</head>
<body>
  <main>
    <h1>Opening Odysseus...</h1>
    <p id="status">Starting the local Odysseus server.</p>
    <p class="hint">This can take a few seconds after Simple Signal starts.</p>
    <p><a id="open-now" href="__ODYSSEUS_TARGET_URL__">Open manually</a></p>
  </main>
  <script>
    (function () {
      var target = "__ODYSSEUS_TARGET_URL__";
      var health = target + "api/health";
      var status = document.getElementById("status");
      var attempts = 0;
      var navigated = false;

      function setStatus(text) {
        if (status) status.textContent = text;
      }

      function navigate() {
        if (navigated) return;
        navigated = true;
        setStatus("Opening Odysseus now.");
        window.location.replace(target);
      }

      function retry() {
        if (navigated) return;
        attempts += 1;
        setStatus(attempts > 30
          ? "Still waiting for Odysseus. You can use the manual link once localhost is ready."
          : "Starting the local Odysseus server.");
        setTimeout(openWhenReady, 1000);
      }

      function openWhenReady() {
        if (navigated) return;
        fetch(health, { cache: "no-store", mode: "no-cors" })
          .then(navigate)
          .catch(retry);
      }

      var manual = document.getElementById("open-now");
      if (manual) manual.addEventListener("click", function () { navigated = true; });
      setTimeout(navigate, 8000);
      openWhenReady();
    })();
  </script>
</body>
</html>""".replace("__ODYSSEUS_TARGET_URL__", target_url),
        encoding="utf-8"
    )

    router_path = extension_dir / "router.py"
    router_content = """import os
import socket
import subprocess
import time
from fastapi import APIRouter

router = APIRouter()

install_dir = os.path.dirname(os.path.abspath(__file__))
ps1_path = os.path.join(install_dir, "launch-windows.ps1")
lock_path = os.path.join(install_dir, ".odysseus-launch.lock")
port = "__ODYSSEUS_EXTENSION_PORT__"

def _is_port_open(value):
    try:
        with socket.create_connection(("127.0.0.1", int(value)), timeout=0.35):
            return True
    except OSError:
        return False

def _launch_lock_recent(max_age=45):
    try:
        return time.time() - os.path.getmtime(lock_path) < max_age
    except OSError:
        return False

if os.path.exists(ps1_path) and not _is_port_open(port) and not _launch_lock_recent():
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass
    env = os.environ.copy()
    env["ODYSSEUS_ALLOW_EMBED"] = "1"
    env["APP_PORT"] = port
    env["ODYSSEUS_INTERNAL_BASE"] = "http://127.0.0.1:" + port
    subprocess.Popen(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1_path, "-Port", port],
        cwd=install_dir,
        env=env
    )
""".replace("__ODYSSEUS_EXTENSION_PORT__", str(EXTENSION_PORT))
    router_path.write_text(router_content, encoding="utf-8")

def get_python_commands() -> list[list[str]]:
    commands = []
    seen = set()

    def add_command(executable: str | None, prefix_args: list[str] | None = None) -> None:
        if not executable:
            return
        prefix_args = prefix_args or []
        key = (executable.lower(), tuple(prefix_args))
        if key in seen:
            return
        seen.add(key)
        commands.append([executable, *prefix_args])

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        for version in ("Python312", "Python311", "Python310"):
            candidate = Path(local_app_data) / "Programs" / "Python" / version / "python.exe"
            if candidate.exists():
                add_command(str(candidate))

    add_command(shutil.which("python"))
    add_command(shutil.which("python3"))

    py_launcher = shutil.which("py")
    if py_launcher:
        add_command(py_launcher, ["-3"])

    return commands

def get_venv_python(extension_dir: Path) -> Path:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return extension_dir / VENV_DIR_NAME / scripts_dir / executable

def create_extension_venv(extension_dir: Path, log_path: Path) -> Path:
    venv_dir = extension_dir / VENV_DIR_NAME
    venv_python = get_venv_python(extension_dir)
    if venv_python.exists():
        print(f" -> Using existing virtual environment: {venv_dir}")
        return venv_python

    commands = get_python_commands()

    if not commands:
        print("[!] Python was not found on PATH.")
        print(f"    Create the venv manually: python -m venv {venv_dir}")
        raise RuntimeError("Python was not found; cannot create the extension virtual environment.")

    print(f" -> Creating virtual environment: {venv_dir}")
    for command in commands:
        try:
            run_logged([*command, "-m", "venv", str(venv_dir)], log_path, check=True)
            if venv_python.exists():
                return venv_python
            append_log(log_path, f"Venv command completed but Python was not found at: {venv_python}")
        except Exception as exc:
            print(f"[!] Venv creation failed with {' '.join(command)}. See log: {log_path}")
            append_log(log_path, f"Venv creation failed: {exc}")

    raise RuntimeError(f"Could not create virtual environment at {venv_dir}.")

def install_dependencies(extension_dir: Path, log_path: Path) -> None:
    venv_python = create_extension_venv(extension_dir, log_path)

    print(f" -> Installing dependencies into: {venv_python.parent.parent}")
    print(" -> (Pip output will be shown below so you can see the progress)")
    print("-" * 40)
    subprocess.run([str(venv_python), "-m", "ensurepip", "--upgrade"], check=False)
    subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=False)
    subprocess.run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    print("-" * 40)
    print(" -> Dependencies installed successfully in the extension virtual environment.")

def run_setup(extension_dir: Path) -> None:
    venv_python = get_venv_python(extension_dir)
    print("\n[3/4] Running First-Time Setup...")
    print(" -> You will be prompted to create an admin account.")
    subprocess.run([str(venv_python), "setup.py"], check=True)

def get_running_simple_signal_path(log_path: Path) -> Path | None:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-CimInstance Win32_Process -Filter \"Name='Simple-Signal-Desktop.exe'\" | Select-Object -First 1 -ExpandProperty ExecutablePath)",
    ]
    output = run_capture(command, log_path)
    if output:
        path = Path(output.splitlines()[0].strip())
        if path.exists():
            return path
    return None

def find_simple_signal_app(log_path: Path) -> Path | None:
    running_path = get_running_simple_signal_path(log_path)
    if running_path:
        return running_path

    common_paths = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Simple-Signal-Desktop" / SIMPLE_SIGNAL_EXE,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Simple-Signal-Desktop" / SIMPLE_SIGNAL_EXE,
        Path("C:\\Program Files\\Simple-Signal-Desktop") / SIMPLE_SIGNAL_EXE,
        Path("C:\\Program Files (x86)\\Simple-Signal-Desktop") / SIMPLE_SIGNAL_EXE,
    ]

    for path in common_paths:
        if path.exists() and (path.parent / "resources.pak").exists():
            return path

    return None

def stop_stale_simple_signal_server(log_path: Path) -> None:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$targets = Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match '^(python|python3|py)\\.exe$' -and "
            "$_.CommandLine -match 'web_server\\.py' -and "
            "($_.CommandLine -match 'Simple-Signal-Desktop' -or $_.CommandLine -match 'simple-signal-cli') "
            "}; "
            "foreach ($p in $targets) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }"
        ),
    ]
    run_logged(command, log_path)

def clear_simple_signal_electron_cache(log_path: Path) -> None:
    user_data_dir = get_simple_signal_user_data_dir()
    if not user_data_dir or not user_data_dir.exists():
        append_log(log_path, "Electron cache clear skipped: Simple Signal user data directory was not found.")
        print(" -> Electron cache directory was not found; skipping cache clear.")
        return

    root = user_data_dir.resolve()
    cleared = 0

    for relative_name in ELECTRON_CACHE_DIRS:
        target = (user_data_dir / relative_name).resolve()
        if root != target and root not in target.parents:
            append_log(log_path, f"Skipped unsafe cache path: {target}")
            continue
        if not target.exists():
            continue

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            cleared += 1
            append_log(log_path, f"Cleared Electron cache path: {target}")
        except Exception as exc:
            append_log(log_path, f"Failed to clear Electron cache path {target}: {exc}")

    if cleared:
        print(f" -> Cleared {cleared} Electron cache folder(s).")
    else:
        print(" -> No Electron cache folders needed clearing.")

def start_simple_signal_background(app_path: Path) -> None:
    subprocess.Popen(
        [str(app_path)],
        cwd=str(app_path.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=get_windows_creation_flags(),
    )

def restart_simple_signal(log_path: Path) -> None:
    print("[4/4] Restarting Simple Signal...")
    app_path = find_simple_signal_app(log_path)

    print(" -> Closing running Simple Signal windows...")
    run_logged(["taskkill", "/IM", SIMPLE_SIGNAL_EXE, "/T", "/F"], log_path)

    print(" -> Stopping stale Simple Signal backend server...")
    stop_stale_simple_signal_server(log_path)

    print(" -> Clearing Electron cache for extension assets...")
    clear_simple_signal_electron_cache(log_path)
    time.sleep(2)

    if app_path and app_path.exists():
        print(" -> Starting Simple Signal in the background.")
        try:
            start_simple_signal_background(app_path)
            return
        except Exception as exc:
            print(f"[!] Failed to restart automatically: {exc}")
            append_log(log_path, f"Failed to restart automatically: {exc}")
    else:
        print("[!] Could not find Simple Signal executable to restart automatically.")

    print("    Please start Simple Signal manually.")

def main():
    print("=" * 60)
    print(f"{EXTENSION_NAME} - Installer for Simple Signal")
    print("=" * 60)
    print("")
    print("This installer does not modify Simple Signal itself.")
    print("It installs the extension into the user extension folder.")
    print("")

    source_dir = get_source_dir()
    extensions_dir = get_user_extensions_dir()
    extension_dir = extensions_dir / EXTENSION_ID
    extension_dir.mkdir(parents=True, exist_ok=True)
    log_path = extension_dir / LOG_FILENAME
    log_path.write_text(f"{EXTENSION_NAME} installer log\n", encoding="utf-8")

    print(f"[1/4] Installing extension files in: {extension_dir}")
    copy_extension_files(source_dir, extension_dir)
    print(f" -> Installer log: {log_path}")

    print("\n[2/4] Installing Python dependencies...")
    # Change working directory so pip install -r requirements.txt works
    os.chdir(extension_dir)
    install_dependencies(extension_dir, log_path)

    run_setup(extension_dir)

    restart_simple_signal(log_path)

    print("")
    print("=" * 60)
    print(f"{EXTENSION_NAME} installed successfully.")
    print("Simple Signal was restarted so the extension server can refresh.")
    print("=" * 60)
    print("")
    show_windows_message(
        f"{EXTENSION_NAME} installed",
        f"{EXTENSION_NAME} installed successfully.\n\nSimple Signal was restarted so the extension server can refresh.",
        error=False,
    )

    wait_before_close()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fallback_dir = get_user_extensions_dir() / EXTENSION_ID
        fallback_dir.mkdir(parents=True, exist_ok=True)
        log_path = fallback_dir / LOG_FILENAME
        append_log(log_path, "Installer failed:")
        append_log(log_path, traceback.format_exc())
        print("")
        print("=" * 60)
        print(f"{EXTENSION_NAME} installer failed.")
        print(f"Error: {exc}")
        print(f"Log: {log_path}")
        print("=" * 60)
        show_windows_message(
            f"{EXTENSION_NAME} installer failed",
            f"{EXTENSION_NAME} installer failed:\n\n{exc}\n\nLog:\n{log_path}",
            error=True,
        )
        wait_before_close()
