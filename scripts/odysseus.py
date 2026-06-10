#!/usr/bin/env python3
"""odysseus.py — Python backend operations called by the Node.js CLI.

Subcommands:
  setup            Idempotent first-time bootstrap
  serve [--port]   Detect network, print URLs, start uvicorn
  status           Health check
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("odysseus")


# ── paths ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DIRS = [
    DATA_DIR,
    DATA_DIR / "uploads",
    DATA_DIR / "personal_docs",
    DATA_DIR / "personal_uploads",
    DATA_DIR / "tts_cache",
    DATA_DIR / "generated_images",
    DATA_DIR / "deep_research",
    DATA_DIR / "chroma",
    DATA_DIR / "rag",
    DATA_DIR / "memory_vectors",
    BASE_DIR / "logs",
]


# ── helpers ────────────────────────────────────────────
def default_port() -> int:
    """Return 7860 on macOS (AirPlay uses 7000), 7000 elsewhere."""
    return 7860 if sys.platform == "darwin" else 7000


def get_venv_python() -> str:
    """Return the venv python path, or system python if no venv."""
    for cand in (".venv", "venv"):
        venv = BASE_DIR / cand
        if venv.exists():
            if os.name == "nt":
                py = venv / "Scripts" / "python.exe"
            else:
                py = venv / "bin" / "python"
            if py.exists():
                return str(py)
    return sys.executable


# ── subcommand: setup ──────────────────────────────────
def cmd_setup(args: argparse.Namespace) -> int:
    """Idempotent bootstrap — dirs, .env, db, admin user."""
    # 1. dirs
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  + {d.relative_to(BASE_DIR)}/")

    # 2. .env from example
    env_path = BASE_DIR / ".env"
    example = BASE_DIR / ".env.example"
    if not env_path.exists() and example.exists():
        shutil.copy2(str(example), str(env_path))
        print(f"  + .env created from .env.example")
        print(f"    ** Edit .env with your LLM host and API keys **")
    elif not env_path.exists():
        print(f"  ! .env.example not found - create .env manually")

    # 3. database
    try:
        sys.path.insert(0, str(BASE_DIR))
        os.environ.setdefault("DATABASE_URL",
                              f"sqlite:///{DATA_DIR / 'app.db'}")
        from core.database import Base, engine
        Base.metadata.create_all(bind=engine)
        print(f"  + Database initialized")
    except Exception as e:
        print(f"  ! Database init failed: {e}")

    # 4. admin user
    try:
        auth_path = DATA_DIR / "auth.json"
        if not auth_path.exists():
            import bcrypt
            import json
            import secrets
            username = os.getenv("ODYSSEUS_ADMIN_USER", "admin").strip() or "admin"
            password = os.getenv("ODYSSEUS_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            auth_data = {"users": {username: {"password_hash": hashed, "is_admin": True}}}
            with open(str(auth_path), "w") as f:
                json.dump(auth_data, f, indent=2)
            print(f"  + Admin user created ({username})")
            print(f"    Temporary password: {password}")
            print(f"    ** Change it after first login **")
        else:
            print(f"  -> auth.json already exists")
    except ImportError:
        print(f"  ! bcrypt not installed - skipping admin user")
    except Exception as e:
        print(f"  ! Admin creation failed: {e}")

    print(f"\n  + Setup complete.")
    return 0


# ── subcommand: serve ──────────────────────────────────
def cmd_serve(args: argparse.Namespace) -> int:
    """Detect LAN IPs, detect Tailscale, start uvicorn."""
    port = args.port or default_port()

    # detect LAN IPs
    lan_ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if isinstance(ip, str) and ip.startswith(("192.168.", "10.", "172.")):
                lan_ips.append(ip)
    except Exception:
        pass

    # fallback: parse ifconfig/ipconfig
    if not lan_ips:
        try:
            if sys.platform == "darwin":
                cmd = ["ifconfig"]
            elif os.name == "nt":
                cmd = ["ipconfig"]
            else:
                cmd = ["ip", "addr"]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                for prefix in ("192.168.", "10.", "172."):
                    if prefix in line:
                        parts = line.strip().split()
                        for p in parts:
                            if p.startswith(prefix):
                                lan_ips.append(p.split("/")[0])
        except Exception:
            pass

    lan_ips = list(dict.fromkeys(lan_ips))  # dedupe, preserve order
    if not lan_ips:
        lan_ips.append("(detect failed - check network)")

    # detect Tailscale
    tailscale_ip = ""
    try:
        r = subprocess.run(["tailscale", "ip", "-4"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            tailscale_ip = r.stdout.strip()
    except Exception:
        pass

    # print URLs
    print(f"\n  Odysseus starting on port {port}...\n")
    print(f"    Local:     http://localhost:{port}")
    for ip in lan_ips[:3]:
        print(f"    LAN:       http://{ip}:{port}")
    if tailscale_ip:
        print(f"    Tailscale: http://{tailscale_ip}:{port}")
    print()

    # if --dry-run, just print and exit
    if args.dry_run:
        return 0

    # start uvicorn
    sys.path.insert(0, str(BASE_DIR))
    import uvicorn
    uvicorn.run(
        "app:app",
        host=args.host or "0.0.0.0",
        port=port,
        log_level="info",
    )
    return 0


# ── subcommand: status ─────────────────────────────────
def cmd_status(args: argparse.Namespace) -> int:
    """Health check."""
    port = args.port or default_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()

    if result == 0:
        print(f"  Server:     running on port {port}")
    else:
        print(f"  Server:     not running on port {port}")

    # check db
    db_path = DATA_DIR / "app.db"
    if db_path.exists():
        size = db_path.stat().st_size
        print(f"  Database:   present ({size / 1024:.0f} KB)")
    else:
        print(f"  Database:   not found")

    print(f"  Python:     {sys.executable}")
    print(f"  Data dir:   {DATA_DIR}")
    return 0


# ── main ───────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(prog="odysseus.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # setup
    sub.add_parser("setup", help="Idempotent first-time bootstrap").set_defaults(func=cmd_setup)

    # serve
    p_serve = sub.add_parser("serve", help="Start the server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind address")
    p_serve.add_argument("--port", type=int, default=0, help="Port (auto-detects 7860 on macOS)")
    p_serve.add_argument("--dry-run", action="store_true", help="Print URLs and exit")
    p_serve.set_defaults(func=cmd_serve)

    # status
    p_status = sub.add_parser("status", help="Health check")
    p_status.add_argument("--port", type=int, default=0, help="Port to check (auto-detects)")
    p_status.set_defaults(func=cmd_status)

    # args
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
