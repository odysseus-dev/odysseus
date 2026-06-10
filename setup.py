#!/usr/bin/env python3
"""Odysseus — first-time setup script.

Creates data directories, initializes the database, and sets up an
initial admin user. Safe to re-run (skips what already exists).
"""

import os
import platform
import re
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from src.constants import (
    DATA_DIR, AUTH_FILE, UPLOAD_DIR, PERSONAL_DIR, PERSONAL_UPLOADS_DIR,
    TTS_CACHE_DIR, GENERATED_IMAGES_DIR, DEEP_RESEARCH_DIR, CHROMA_DIR,
    RAG_DIR, MEMORY_VECTORS_DIR,
)

DIRS = [
    DATA_DIR,
    UPLOAD_DIR,
    PERSONAL_DIR,
    PERSONAL_UPLOADS_DIR,
    TTS_CACHE_DIR,
    GENERATED_IMAGES_DIR,
    DEEP_RESEARCH_DIR,
    CHROMA_DIR,
    RAG_DIR,
    MEMORY_VECTORS_DIR,
    os.path.join(BASE_DIR, "logs"),
]


def create_dirs():
    for d in DIRS:
        os.makedirs(d, exist_ok=True)
        print(f"  [ok] {os.path.relpath(d, BASE_DIR)}/")


def init_database():
    """Create all SQLAlchemy tables."""
    sys.path.insert(0, BASE_DIR)
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}")

    from core.database import Base, engine
    Base.metadata.create_all(bind=engine)
    print("  [ok] Database initialized")


def _prompt_admin_credentials():
    """Interactively ask for admin username and password when running in a terminal."""
    import getpass

    print()
    print("  Set up your admin account:")
    print("  (Press Enter to accept defaults)")
    print()

    username = input("  Username [admin]: ").strip().lower()
    if not username:
        username = "admin"

    while True:
        password = getpass.getpass("  Password: ")
        if not password:
            print("  Password cannot be empty.")
            continue
        confirm = getpass.getpass("  Confirm password: ")
        if password != confirm:
            print("  Passwords don't match. Try again.")
            continue
        break

    return username, password


def create_default_admin():
    """Create an initial admin user if none exists."""
    auth_path = AUTH_FILE
    if os.path.exists(auth_path):
        print("  [skip] auth.json already exists")
        return "exists"

    try:
        import bcrypt
        import json

        # Priority: env vars > interactive prompt > random password
        username = os.getenv("ODYSSEUS_ADMIN_USER", "").strip().lower()
        password = os.getenv("ODYSSEUS_ADMIN_PASSWORD", "").strip()

        if username and password:
            # Both provided via env — use them directly
            pass
        elif sys.stdin.isatty() and not os.getenv("ODYSSEUS_SKIP_ADMIN_PROMPT"):
            # Interactive terminal — ask the user
            username, password = _prompt_admin_credentials()
        else:
            # Non-interactive (Docker, CI) — fall back to generated password
            username = username or "admin"
            password = password or __import__("secrets").token_urlsafe(18)

        username = username or "admin"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        auth_data = {
            "users": {
                username: {
                    "password_hash": hashed,
                    "is_admin": True,
                }
            }
        }
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump(auth_data, f, indent=2)

        if sys.stdin.isatty() and not os.getenv("ODYSSEUS_ADMIN_PASSWORD"):
            print(f"  [ok] Admin account created ({username})")
        else:
            print(f"  [ok] Initial admin user created ({username})")
            if not os.getenv("ODYSSEUS_ADMIN_PASSWORD"):
                print(f"        Temporary password: {password}")
                print(f"        ** Change it after first login. Set ODYSSEUS_ADMIN_PASSWORD to choose your own. **")
        return "created"
    except ImportError as e:
        if "incompatible architecture" in str(e).lower():
            # bcrypt is present but built for the wrong CPU architecture — the
            # same Apple Silicon mismatch check_arch() guards against, caught here
            # for the rarer case of an x86 wheel inside an arm64 venv.
            print("  [error] bcrypt loaded with the wrong CPU architecture.")
            print("          Rebuild the venv with an arm64 Python:")
            print("            rm -rf venv && /opt/homebrew/bin/python3.11 -m venv venv")
            print("            ./venv/bin/pip install -r requirements.txt")
            return "skipped"
        print("  [warn] bcrypt not installed — skipping admin user creation")
        print("         Run: pip install bcrypt")
        return "skipped"


def create_env():
    """Copy .env.example to .env if it doesn't exist."""
    env_path = os.path.join(BASE_DIR, ".env")
    example_path = os.path.join(BASE_DIR, ".env.example")
    if os.path.exists(env_path):
        print("  [skip] .env already exists")
        return
    if os.path.exists(example_path):
        import shutil
        shutil.copy2(example_path, env_path)
        print("  [ok] .env created from .env.example")
        print("        ** Edit .env with your LLM host and API keys **")
    else:
        print("  [warn] .env.example not found — create .env manually")


def _prompt_choice(title, choices, default):
    """Prompt for one choice from a small menu."""
    print()
    print(f"  {title}")
    for idx, (key, label) in enumerate(choices, start=1):
        suffix = " [default]" if key == default else ""
        print(f"    {idx}. {label}{suffix}")

    valid_numbers = {str(i): key for i, (key, _label) in enumerate(choices, start=1)}
    valid_keys = {}
    for key, label in choices:
        valid_keys[key.lower()] = key
        valid_keys[re.sub(r"[^a-z0-9]+", "", key.lower())] = key
        valid_keys[re.sub(r"[^a-z0-9]+", "", label.lower())] = key
    while True:
        raw = input("  Choose: ").strip().lower()
        raw = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw).strip()
        raw = raw.rstrip(".):")
        if not raw:
            return default
        if raw in valid_numbers:
            return valid_numbers[raw]
        if raw and raw[0] in valid_numbers and raw[1:].strip() in {"", ".", ")", ":"}:
            return valid_numbers[raw[0]]
        if raw in valid_keys:
            return valid_keys[raw]
        compact = re.sub(r"[^a-z0-9]+", "", raw)
        if compact in valid_keys:
            return valid_keys[compact]
        print("  Please enter one of the listed numbers or names.")


def _detect_render_gid():
    """Return the host render group id used by the AMD Docker overlay."""
    try:
        result = subprocess.run(
            ["getent", "group", "render"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        return parts[2]
    return None


def _set_env_value(text, key, value):
    """Set an env assignment, replacing commented template values too."""
    pattern = re.compile(rf"^(?P<prefix>\s*#?\s*){re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    new_text, count = pattern.subn(replacement, text, count=1)
    if count:
        return new_text
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return f"{new_text}{replacement}\n"


def _optional_requirements(path=None):
    """Return installable requirement lines from requirements-optional.txt."""
    path = path or os.path.join(BASE_DIR, "requirements-optional.txt")
    if not os.path.exists(path):
        return []
    packages = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            packages.append(line)
    return packages


def configure_optional_requirements(enabled, install_target, env_path=None):
    """Persist or install optional dependencies for the chosen install target."""
    env_path = env_path or os.path.join(BASE_DIR, ".env")
    if install_target == "docker":
        if not os.path.exists(env_path):
            print("  [warn] .env not found — skipping optional Docker build setting")
            return "missing-env"
        text = open(env_path, "r", encoding="utf-8").read()
        text = _set_env_value(text, "INSTALL_OPTIONAL", "true" if enabled else "false")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(text)
        state = "enabled" if enabled else "disabled"
        print(f"  [ok] Optional Docker dependencies {state} in .env")
        return "updated"

    if not enabled:
        print("  [skip] Optional Python dependencies not selected")
        return "skipped"

    optional_path = os.path.join(BASE_DIR, "requirements-optional.txt")
    if not os.path.exists(optional_path):
        print("  [warn] requirements-optional.txt not found — skipping optional install")
        return "missing-file"

    print("  Installing optional Python dependencies...")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", optional_path], check=False)
    if result.returncode != 0:
        print("  [warn] Optional dependency install failed; see pip output above.")
        return "failed"
    print("  [ok] Optional Python dependencies installed")
    return "installed"


def configure_docker_target(gpu_target, os_target, env_path=None, render_gid=None):
    """Update .env for the selected Docker Compose GPU target.

    CPU is intentionally a no-op: leaving GPU settings commented keeps the
    default CPU-only Compose behavior, and avoids deleting a user's manual edits.
    """
    if gpu_target == "cpu":
        print("  [skip] CPU-only selected; .env GPU settings unchanged")
        return "skipped"

    env_path = env_path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        print("  [warn] .env not found — skipping Docker GPU target configuration")
        return "missing-env"

    if gpu_target == "nvidia":
        compose_file = (
            "docker-compose.yml;docker/gpu.nvidia.yml"
            if os_target == "windows"
            else "docker-compose.yml:docker/gpu.nvidia.yml"
        )
        text = open(env_path, "r", encoding="utf-8").read()
        text = _set_env_value(text, "COMPOSE_FILE", compose_file)
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  [ok] NVIDIA Docker overlay enabled ({os_target})")
        return "updated"

    if gpu_target == "amd":
        if os_target == "windows":
            print("  [warn] AMD Docker GPU overlay is Linux/WSL2-oriented; using Linux Compose syntax.")
        render_gid = render_gid or _detect_render_gid()
        if not render_gid:
            print("  [warn] Could not detect render group GID with `getent group render`.")
            print("         Leaving RENDER_GID at the template value; update it manually if needed.")
            render_gid = "989"

        text = open(env_path, "r", encoding="utf-8").read()
        text = _set_env_value(text, "COMPOSE_FILE", "docker-compose.yml:docker/gpu.amd.yml")
        text = _set_env_value(text, "RENDER_GID", render_gid)
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(text)
        print("  [ok] AMD Docker overlay enabled")
        print(f"       RENDER_GID={render_gid}")
        return "updated"

    print(f"  [warn] Unknown GPU target: {gpu_target}")
    return "skipped"


def _default_os_target():
    return "windows" if os.name == "nt" else "linux"


def choose_setup_profile():
    """Prompt for OS, install mode, and hardware target."""
    default_os = _default_os_target()
    profile = {
        "os": default_os,
        "install": "docker",
        "gpu": "cpu",
        "optional": False,
    }

    if not sys.stdin.isatty() or os.getenv("ODYSSEUS_SKIP_SETUP_PROFILE_PROMPT"):
        print("  [skip] Setup profile prompt skipped; defaulting to Docker CPU")
        configure_docker_target(profile["gpu"], profile["os"])
        configure_optional_requirements(profile["optional"], profile["install"])
        return profile

    os_target = _prompt_choice(
        "Operating system:",
        [
            ("linux", "Linux / WSL2"),
            ("windows", "Windows PowerShell / cmd"),
        ],
        default_os,
    )
    install_choices = (
        [
            ("docker", "Docker Compose"),
            ("venv", "Python venv"),
        ]
        if os_target == "linux"
        else [
            ("docker", "Docker Desktop / Compose"),
            ("local", "Local Windows launcher"),
        ]
    )
    install_target = _prompt_choice("Install method:", install_choices, "docker")
    gpu_target = _prompt_choice(
        "Hardware target:",
        [
            ("cpu", "CPU only"),
            ("nvidia", "CPU + NVIDIA GPU"),
            ("amd", "CPU + AMD GPU"),
        ],
        "cpu",
    )
    optional_packages = _optional_requirements()
    if optional_packages:
        print()
        print("  Optional dependencies available:")
        for package in optional_packages:
            print(f"    - {package}")
        optional_target = _prompt_choice(
            "Install optional dependencies too?",
            [
                ("no", "No"),
                ("yes", "Yes"),
            ],
            "no",
        )
        install_optional = optional_target == "yes"
    else:
        print("  [warn] No optional requirements were found to list")
        install_optional = False

    profile = {
        "os": os_target,
        "install": install_target,
        "gpu": gpu_target,
        "optional": install_optional,
    }
    if install_target == "docker":
        configure_docker_target(gpu_target, os_target)
        configure_optional_requirements(install_optional, install_target)
    elif gpu_target != "cpu":
        print("  [info] GPU selection only changes Docker Compose .env targets.")
        print("         Native/local installs configure GPU runtimes outside setup.py.")
        configure_optional_requirements(install_optional, install_target)
    else:
        print("  [skip] Native/local CPU setup selected; .env GPU settings unchanged")
        configure_optional_requirements(install_optional, install_target)
    return profile


def check_deps():
    """Check for common missing dependencies."""
    missing = []
    for mod in ["fastapi", "uvicorn", "sqlalchemy", "bcrypt", "httpx", "dotenv"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"\n  [warn] Missing packages: {', '.join(missing)}")
        print(f"         Run: pip install -r requirements.txt")
    else:
        print("  [ok] All core dependencies installed")

    if os.name != "nt" and shutil.which("tmux") is None:
        print("\n  [warn] tmux not found")
        print("         Cookbook uses tmux for background downloads and model serves.")
        print("         Install it with your OS package manager, for example:")
        if sys.platform == "darwin":
            print("           brew install tmux")
        else:
            print("           sudo apt install tmux")
            print("           sudo pacman -S tmux")
            print("           sudo dnf install tmux")
    elif os.name != "nt":
        print("  [ok] tmux installed")


def check_arch():
    """Stop early, with guidance, if we're on Apple Silicon but running an
    Intel (x86_64) Python through Rosetta.

    A venv built with such an interpreter installs and loads compiled packages
    (bcrypt, pydantic-core, onnxruntime, …) for the wrong CPU architecture, then
    dies deep inside an import with a cryptic
    "(mach-o file, but is an incompatible architecture)" error. Catching it here
    turns that into one clear, actionable message.
    """
    if sys.platform != "darwin" or platform.machine() == "arm64":
        return  # Not macOS, or already an arm64-native interpreter — nothing to do.

    # platform.machine() == "x86_64": either a genuine Intel Mac (fine) or an x86
    # interpreter running under Rosetta on Apple Silicon (the case we must catch).
    try:
        translated = subprocess.run(
            ["sysctl", "-n", "sysctl.proc_translated"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        translated = ""
    if translated != "1":
        return  # Genuine Intel Mac — carry on.

    print("\n  [error] This is an Apple Silicon Mac, but setup is running under an")
    print("          Intel (x86_64) Python through Rosetta. Compiled packages would")
    print('          load as the wrong architecture and crash with "incompatible')
    print('          architecture" later on.')
    print("\n          Rebuild the environment with Homebrew's arm64 Python:")
    print("            brew install python@3.11          # if you don't have it yet")
    print("            rm -rf venv")
    print("            /opt/homebrew/bin/python3.11 -m venv venv")
    print("            ./venv/bin/pip install -r requirements.txt")
    print("            ./venv/bin/python setup.py")
    print("\n          Tip: ./start-macos.sh does all of this with the right Python.\n")
    sys.exit(1)


def _print_command_block(title, commands):
    print(f"\n{title}")
    for command in commands:
        print(f"  {command}")


def _print_docker_install_notes(os_target):
    print("\nDocker installation notes:")
    print("  Recommended: install the latest Docker stable release from Docker's official docs.")
    if os_target == "windows":
        print("  Windows: Docker Desktop per-user install is recommended for most users.")
        print("  Download/install: https://docs.docker.com/desktop/setup/install/windows-install/")
        print("  Docker Desktop uses WSL 2 for the recommended per-user mode.")
    else:
        print("  Linux: install Docker Engine from Docker's official repository for your distro.")
        print("  Install guide: https://docs.docker.com/engine/install/")
        print("  Use Docker Engine's stable channel unless you intentionally need pre-release builds.")


def _print_docker_cleanup_notes():
    print("\nDocker cleanup commands:")
    print("  docker compose down")
    print("    Stops and removes this project's containers/network; keeps named volumes and images.")
    print("  docker compose down -v")
    print("    Also removes this project's named volumes, including Chroma/SearXNG/ntfy volumes.")
    print("    It does not delete bind-mounted ./data or ./logs.")
    print("  docker builder prune")
    print("    Removes unused Docker build cache. Useful when rebuilds have eaten disk space.")
    print("  docker image prune -a")
    print("    Removes unused images, including old Odysseus image layers not used by containers.")
    print("  docker system prune -a --volumes")
    print("    Most aggressive cleanup: removes unused containers, networks, images, build cache,")
    print("    and unused Docker volumes across Docker, not just Odysseus.")


def print_next_steps(profile=None):
    """Show common run/update commands after setup finishes."""
    profile = profile or {
        "os": _default_os_target(),
        "install": "docker",
        "gpu": "cpu",
        "optional": False,
    }
    os_target = profile.get("os", _default_os_target())
    install_target = profile.get("install", "docker")
    install_optional = bool(profile.get("optional"))

    print("\nNext steps:")
    print("  Check .env before starting if you want custom install-time choices:")
    print("    ODYSSEUS_DATA_DIR moves app data/settings/uploads to another folder.")
    print("    INSTALL_OPTIONAL controls requirements-optional.txt for Docker builds.")
    if install_target == "docker":
        print("  First run is always: build/start Docker, then open localhost.")
        if install_optional:
            print("  Optional dependencies are enabled by INSTALL_OPTIONAL=true in .env.")
        _print_docker_install_notes(os_target)
        _print_command_block(
            "Docker first run / rebuild:",
            [
                "docker compose build",
                "docker compose up -d",
                "docker compose logs -f odysseus",
            ],
        )
        print("  Then open http://localhost:7000 in your browser.")
        _print_command_block(
            "Docker update an existing checkout:",
            [
                "git pull",
                "docker compose build",
                "docker compose up -d",
            ],
        )
        print("  Then open http://localhost:7000 in your browser.")
        _print_command_block("Docker stop active containers:", ["docker compose down"])
        _print_docker_cleanup_notes()
        return

    if os_target == "windows":
        _print_command_block(
            "Native Windows local setup:",
            [
                r"powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1",
            ],
        )
        if install_optional:
            print("  Optional dependencies are installed by setup.py when selected.")
        print("  Then open http://localhost:7000 in your browser.")
        _print_command_block(
            "Native Windows server start if venv already exists:",
            [
                r"venv\Scripts\Activate.ps1",
                "python -m uvicorn app:app --host 127.0.0.1 --port 7000",
            ],
        )
    else:
        linux_commands = [
            "python3 -m venv venv",
            "source venv/bin/activate",
            "pip install -r requirements.txt",
        ]
        if install_optional:
            linux_commands.append("pip install -r requirements-optional.txt")
        linux_commands.extend(
            [
                "python setup.py",
                "python -m uvicorn app:app --host 127.0.0.1 --port 7000",
            ]
        )
        _print_command_block(
            "Native Linux venv setup:",
            linux_commands,
        )
        print("  Then open http://localhost:7000 in your browser.")
    print("\nSystem Python install is possible, but a venv/local launcher is recommended so")
    print("dependencies stay isolated from your OS Python packages.")


def main():
    print("\n=== Odysseus Setup ===\n")

    # Fail fast with a clear message if the CPU architecture is wrong (Apple
    # Silicon under an x86/Rosetta Python) before importing anything native.
    check_arch()

    print("1. Creating directories...")
    create_dirs()

    print("\n2. Environment file...")
    create_env()

    print("\n3. Setup profile...")
    setup_profile = choose_setup_profile()

    print("\n4. Checking dependencies...")
    check_deps()

    print("\n5. Initializing database...")
    try:
        init_database()
    except Exception as e:
        print(f"  [warn] Database init failed: {e}")
        print("         This is OK if dependencies aren't installed yet.")

    print("\n6. Creating initial admin...")

    admin_status = "failed"

    try:
        admin_status = create_default_admin()
    except Exception as e:
        print(f"  [warn] Admin creation failed: {e}")
        admin_status = "failed"

    print("\n=== Setup complete ===")
    # start-macos.sh launches the server itself (on its own port) right after
    # this, so suppress the manual hint there to avoid a contradictory URL.
    if not os.getenv("ODYSSEUS_SKIP_RUN_HINT"):
        print_next_steps(setup_profile)

    # Cleaned, action-focused final instruction strings
    if admin_status == "created":
        print("Login with your admin credentials.\n")
    elif admin_status == "exists":
        print("Login with your existing admin credentials.\n")
    elif admin_status == "skipped":
        print("Admin creation did not happen: dependencies are missing.\nRun 'pip install bcrypt' and rerun setup.\n")
    elif admin_status == "failed":
        print("Admin creation did not happen: a system or file error occurred.\nCheck write permissions for the 'data' directory and rerun setup.\n")
    else:  # handling "failed" or any unhandled edge case
        print("Admin creation did not happen: a system or file error occurred.\nCheck write permissions for the 'data' directory and rerun setup.\n")


if __name__ == "__main__":
    main()
