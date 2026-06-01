#!/usr/bin/env python3
"""Odysseus — first-time setup script.

Creates data directories, initializes the database, and sets up an
initial admin user. Safe to re-run (skips what already exists).
"""

import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

DIRS = [
    DATA_DIR,
    os.path.join(DATA_DIR, "uploads"),
    os.path.join(DATA_DIR, "personal_docs"),
    os.path.join(DATA_DIR, "personal_uploads"),
    os.path.join(DATA_DIR, "tts_cache"),
    os.path.join(DATA_DIR, "generated_images"),
    os.path.join(DATA_DIR, "deep_research"),
    os.path.join(DATA_DIR, "chroma"),
    os.path.join(DATA_DIR, "rag"),
    os.path.join(DATA_DIR, "memory_vectors"),
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


def create_default_admin():
    """Create an initial admin user if none exists."""
    auth_path = os.path.join(DATA_DIR, "auth.json")
    if os.path.exists(auth_path):
        print("  [skip] auth.json already exists")
        return "exists"

    try:
        import bcrypt
        import json

        username = os.getenv("ODYSSEUS_ADMIN_USER", "admin").strip() or "admin"
        password = os.getenv("ODYSSEUS_ADMIN_PASSWORD") or __import__("secrets").token_urlsafe(18)
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
        print(f"  [ok] Initial admin user created ({username})")
        print(f"        Temporary password: {password}")
        print(f"        ** Change it after first login. Set ODYSSEUS_ADMIN_PASSWORD to choose your own. **")
        return "created"
    except ImportError:
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


def main():
    print("\n=== Odysseus Setup ===\n")

    print("1. Creating directories...")
    create_dirs()

    print("\n2. Environment file...")
    create_env()

    print("\n3. Checking dependencies...")
    check_deps()

    print("\n4. Initializing database...")
    try:
        init_database()
    except Exception as e:
        print(f"  [warn] Database init failed: {e}")
        print("         This is OK if dependencies aren't installed yet.")

    print("\n5. Creating initial admin...")

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
        print(f"\nStart the server with:")
        print(f"  python -m uvicorn app:app --host 127.0.0.1 --port 7000")
        print(f"\nThen open http://localhost:7000")

    # Cleaned, action-focused final instruction strings
    if admin_status == "created":
        print("Login with the admin username and temporary password printed above.\n")
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
