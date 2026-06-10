#!/usr/bin/env python3
"""bootstrap.py — Odysseus first-launch setup for the portable macOS .app.

Runs once, gated by $DATA_DIR/.bootstrapped flag written by launcher.sh.

Environment variables set by launcher.sh:
  ODYSSEUS_DATA_DIR    — ~/Library/Application Support/Odysseus
  ODYSSEUS_ENV_EXAMPLE — path to .env.example inside the bundle
  ODYSSEUS_PORT        — port Odysseus will listen on
  SEARXNG_PORT         — port SearXNG will listen on
  PYTHONPATH           — set to odysseus_app/_internal for passlib import

Produces (in ODYSSEUS_DATA_DIR):
  .env          — user config, pre-filled with correct settings
  auth.json     — initial admin user (600 permissions)
  logs/         — log directory

The temp admin password is printed to stdout (shown in dialog by launcher)
but is NOT written to bootstrap.log (security: P1-4).
"""

import os
import sys
import json
import secrets
from pathlib import Path

# ── Resolve paths ──────────────────────────────────────────────────────────────
DATA_DIR      = Path(os.environ.get("ODYSSEUS_DATA_DIR",
                     Path.home() / "Library" / "Application Support" / "Odysseus"))
ENV_EXAMPLE   = Path(os.environ.get("ODYSSEUS_ENV_EXAMPLE", ""))
ODYSSEUS_PORT = os.environ.get("ODYSSEUS_PORT", "7860")
SEARXNG_PORT  = os.environ.get("SEARXNG_PORT",  "8080")
BUNDLE_DIR    = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

def out(msg: str = "") -> None:
    print(msg, flush=True)

# ── 1. Create runtime directories ─────────────────────────────────────────────
SUBDIRS = [
    "", "uploads", "personal_docs", "personal_uploads", "tts_cache",
    "generated_images", "deep_research", "chroma", "rag",
    "memory_vectors", "logs", "skills",
]

out("=== Odysseus — First Launch Setup ===")
out()
out("Creating data directories...")
for sub in SUBDIRS:
    (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
out(f"  Data directory: {DATA_DIR}")

# ── 2. Write .env ──────────────────────────────────────────────────────────────
env_path = DATA_DIR / ".env"
if not env_path.exists():
    out()
    out("Writing .env...")
    content = ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else ""

    # These values are always set correctly for the bundled app.
    # CHROMADB_HOST/PORT are intentionally absent — app uses embedded Chroma
    # via CHROMA_DB_PATH (P0-4 fix).
    overrides = {
        "SEARXNG_INSTANCE": f"http://127.0.0.1:{SEARXNG_PORT}",
        "DATABASE_URL":     f"sqlite:///{DATA_DIR}/app.db",
        "CHROMA_DB_PATH":   str(DATA_DIR / "chroma"),
        "AUTH_ENABLED":     "true",
        "LOCALHOST_BYPASS": "false",
        "APP_PORT":         ODYSSEUS_PORT,
    }

    result_lines = []
    handled: set = set()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            result_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].lstrip("#").strip()
        if key in overrides:
            result_lines.append(f"{key}={overrides[key]}")
            handled.add(key)
        elif key in ("CHROMADB_HOST", "CHROMADB_PORT"):
            # Remove stale ChromaDB server config — use embedded Chroma
            result_lines.append(f"# {line.strip()}  # removed: using embedded ChromaDB")
        else:
            result_lines.append(line)

    for key, val in overrides.items():
        if key not in handled:
            result_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(result_lines) + "\n")
    os.chmod(env_path, 0o600)
    out(f"  .env written to {env_path}")
else:
    out()
    out(f"  .env already exists — skipping")

# ── 3. Initialize SQLite database ─────────────────────────────────────────────
db_path = DATA_DIR / "app.db"
out()
out("Initializing database...")
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

# Add bundle to path so core.database is importable
_internal = str(BUNDLE_DIR / "_internal")
if _internal not in sys.path:
    sys.path.insert(0, _internal)
if str(BUNDLE_DIR) not in sys.path:
    sys.path.insert(0, str(BUNDLE_DIR))

try:
    from core.database import Base, engine
    Base.metadata.create_all(bind=engine)
    out(f"  Database initialized: {db_path}")
except Exception as exc:
    out(f"  Note: DB pre-init skipped ({type(exc).__name__}) — app will init on first start")

# ── 4. Create initial admin user ──────────────────────────────────────────────
auth_path = DATA_DIR / "auth.json"
out()
out("Creating admin user...")

temp_password: str | None = None

if auth_path.exists():
    out("  auth.json already exists — skipping")
else:
    username = (os.environ.get("ODYSSEUS_ADMIN_USER", "") or "admin").strip()
    temp_password = secrets.token_urlsafe(18)

    # Hash using passlib/bcrypt from the frozen bundle (P1-5)
    hashed: str
    try:
        from passlib.context import CryptContext
        _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed = _ctx.hash(temp_password)
        out("  Password hashed with bcrypt")
    except Exception as _e:
        # Should not happen — passlib is in the frozen bundle.
        # Fail loudly rather than silently storing an incompatible hash (P1-5).
        out(f"  ERROR: Could not hash password — {_e}")
        out("  Bootstrap cannot continue safely.")
        sys.exit(1)

    auth_data = {
        "users": {
            username: {
                "password_hash": hashed,
                "is_admin": True,
            }
        }
    }
    auth_path.write_text(json.dumps(auth_data, indent=2))
    os.chmod(auth_path, 0o600)  # owner read/write only (P1-4)
    out(f"  Admin user created: {username}")

# ── 5. Summary ────────────────────────────────────────────────────────────────
out()
out("══════════════════════════════════════════")
out("  Setup complete!")
out()
out(f"  Open:  http://127.0.0.1:{ODYSSEUS_PORT}")
if temp_password:
    out(f"  Login: admin / {temp_password}")
    out()
    out("  ⚠  Change your password after first login.")
    out("     Settings → Account → Change Password")
out("══════════════════════════════════════════")
out()
out("Odysseus is starting — this window will close.")
