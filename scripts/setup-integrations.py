#!/usr/bin/env python3
"""
Odysseus IaC integration setup — Gmail + Google Calendar + Google Drive MCP.

Reads credentials from .env and upserts them into the Odysseus SQLite database.
Idempotent: safe to re-run, won't create duplicates.

Usage:
    ./venv/bin/python scripts/setup-integrations.py
    ./venv/bin/python scripts/setup-integrations.py --dry-run

Environment variables (in .env):
    GMAIL_PERSONAL_USER          Primary Gmail address (e.g. maylortaylor@gmail.com)
    GMAIL_PERSONAL_APP_PASSWORD  16-char Google App Password for primary account
    GMAIL_SPM_USER               StPeteMusic Gmail address (e.g. theburgmusic@gmail.com)
    GMAIL_SPM_APP_PASSWORD       16-char Google App Password for StPeteMusic account

Legacy vars (auto-migrated on first run):
    GMAIL_USER         → GMAIL_PERSONAL_USER
    GMAIL_APP_PASSWORD → GMAIL_PERSONAL_APP_PASSWORD
"""
import json
import os
import sys
import uuid
from pathlib import Path

ENV_PATH = Path(__file__).parent.parent / ".env"


def _load_env(env_path: Path) -> None:
    """Load .env into os.environ; existing shell vars take priority."""
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def migrate_env_file(env_path: Path) -> None:
    """Rename legacy single-account GMAIL_USER vars to dual-account names in .env."""
    if not env_path.exists():
        return
    renames = {
        "GMAIL_USER": "GMAIL_PERSONAL_USER",
        "GMAIL_APP_PASSWORD": "GMAIL_PERSONAL_APP_PASSWORD",
    }
    lines = env_path.read_text().splitlines(keepends=True)
    changed = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in renames:
            new_key = renames[key]
            new_line = line.replace(key, new_key, 1)
            new_lines.append(new_line)
            print(f"  [migrate] renamed {key} → {new_key} in .env")
            # Propagate rename into the live environment
            if key in os.environ:
                os.environ[new_key] = os.environ.pop(key)
            changed = True
        else:
            new_lines.append(line)
    if changed:
        tmp = str(env_path) + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(new_lines)
        os.replace(tmp, str(env_path))


# Load .env → migrate legacy var names → reload so new names are visible
_load_env(ENV_PATH)
migrate_env_file(ENV_PATH)
_load_env(ENV_PATH)

def _best_password(*var_names: str) -> str:
    """Return the first env var that is a valid 16-char App Password; fall back to first set."""
    candidates = [(k, (os.getenv(k) or "").strip()) for k in var_names]
    for _, v in candidates:
        if len(v.replace("-", "").replace(" ", "")) == 16:
            return v
    for _, v in candidates:
        if v:
            return v
    return ""


PERSONAL_USER     = (os.getenv("GMAIL_PERSONAL_USER") or "").strip()
PERSONAL_PASSWORD = _best_password(
    "GMAIL_PERSONAL_APP_PASSWORD",
    "GMAIL_PERSONAL_PERSONAL_APP_PASSWORD",
)
SPM_USER     = (os.getenv("GMAIL_SPM_USER") or "").strip()
SPM_PASSWORD = _best_password(
    "GMAIL_SPM_APP_PASSWORD",
    "GMAIL_SPM_PERSONAL_APP_PASSWORD",
)


def _validate_account(user: str, password: str, label: str) -> list:
    errors = []
    if not user:
        errors.append(f"{label}_USER is not set in .env")
    elif "@" not in user or "." not in user.split("@")[-1]:
        errors.append(f"{label}_USER '{user}' does not look like a valid email")
    if not password:
        errors.append(f"{label}_APP_PASSWORD is not set in .env")
    elif len(password.replace("-", "").replace(" ", "")) != 16:
        errors.append(
            f"{label}_APP_PASSWORD looks wrong (expected 16 chars, got "
            f"{len(password.replace('-', '').replace(' ', ''))})"
        )
    return errors


def validate():
    """Return (errors, has_spm). Fatal errors fail the run; missing StPeteMusic warns only."""
    errors = _validate_account(PERSONAL_USER, PERSONAL_PASSWORD, "GMAIL_PERSONAL")
    has_spm = bool(SPM_USER or SPM_PASSWORD)
    if has_spm:
        errors += _validate_account(SPM_USER, SPM_PASSWORD, "GMAIL_SPM")
    return errors, has_spm


def setup_email_account(db, user: str, password: str, display_name: str, is_default: bool, dry_run: bool) -> None:
    """Upsert a Gmail IMAP/SMTP account. Updates name + password on existing records."""
    from core.database import EmailAccount
    from src.secret_storage import encrypt

    enc_password = encrypt(password) if not dry_run else "<encrypted>"

    if is_default and not dry_run:
        # Enforce single-default constraint before upsert
        db.query(EmailAccount).filter(
            EmailAccount.owner == "admin",
            EmailAccount.imap_user != user,
        ).update({"is_default": False})

    existing = db.query(EmailAccount).filter(EmailAccount.imap_user == user).first()

    if existing:
        print(f"  Updating email account: {user} → {display_name!r}")
        if not dry_run:
            existing.name = display_name
            existing.imap_password = enc_password
            existing.smtp_password = enc_password
            existing.is_default = is_default
            existing.enabled = True
    else:
        print(f"  Creating email account: {user} → {display_name!r}")
        if not dry_run:
            account = EmailAccount(
                id=str(uuid.uuid4()),
                owner="admin",
                name=display_name,
                is_default=is_default,
                enabled=True,
                imap_host="imap.gmail.com",
                imap_port=993,
                imap_user=user,
                imap_password=enc_password,
                imap_starttls=False,  # port 993 uses implicit SSL, not STARTTLS
                smtp_host="smtp.gmail.com",
                smtp_port=587,
                smtp_security="starttls",
                smtp_user=user,
                smtp_password=enc_password,
                from_address=user,
            )
            db.add(account)

    if not dry_run:
        db.commit()


def setup_caldav_account(prefs_path: Path, user: str, password: str, label: str, dry_run: bool) -> None:
    """Upsert a Google Calendar CalDAV account. Updates label + password on existing records."""
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        all_prefs = json.loads(prefs_path.read_text()) if prefs_path.exists() else {}
    except json.JSONDecodeError:
        all_prefs = {}

    all_prefs.setdefault("_users", {}).setdefault("admin", {})
    user_prefs = all_prefs["_users"]["admin"]
    accounts = user_prefs.get("caldav_accounts", [])

    # App Passwords use Basic Auth; only the legacy endpoint supports it.
    # apidata.googleusercontent.com requires OAuth2 Bearer tokens (→ 401).
    caldav_url = f"https://www.google.com/calendar/dav/{user}/user"
    existing = next((a for a in accounts if a.get("username") == user), None)

    if existing:
        print(f"  Updating CalDAV account: {user} → {label!r}")
        if not dry_run:
            existing["label"] = label
            existing["password"] = password
            existing["url"] = caldav_url
    else:
        print(f"  Creating CalDAV account: {user} → {label!r}")
        if not dry_run:
            accounts.append({
                "id": str(uuid.uuid4()),
                "label": label,
                "url": caldav_url,
                "username": user,
                "password": password,
            })

    if not dry_run:
        user_prefs["caldav_accounts"] = accounts
        all_prefs["_users"]["admin"] = user_prefs
        tmp = str(prefs_path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(all_prefs, f, indent=2)
        os.replace(tmp, str(prefs_path))


def setup_drive_mcp(db, dry_run: bool) -> None:
    """Create a Google Drive MCP server entry (disabled until authorized via Admin UI).

    If the entry already exists we leave it alone so existing OAuth tokens are preserved.
    The user authorizes once via Admin → MCP Servers → Google Drive → Enable/Authorize.
    One Drive entry is intentional: the gdrive MCP package manages its own OAuth token at a
    fixed path, so a second instance would conflict. Share StPeteMusic Drive content with the
    primary Google account for cross-account access.
    """
    from core.database import McpServer

    existing = db.query(McpServer).filter(McpServer.name == "Google Drive").first()
    if existing:
        print("  Google Drive MCP entry already exists — skipping (preserves auth tokens)")
        return

    print("  Creating Google Drive MCP entry (disabled — authorize via Admin → MCP Servers)")
    if not dry_run:
        srv = McpServer(
            id=str(uuid.uuid4()),
            name="Google Drive",
            transport="stdio",
            command="npx",
            args=json.dumps(["-y", "@modelcontextprotocol/server-gdrive"]),
            env=json.dumps({}),
            is_enabled=False,
        )
        db.add(srv)
        db.commit()


def setup_filesystem_mcp(db, dry_run: bool) -> None:
    """Create a Filesystem MCP server entry (enabled by default — no OAuth needed).

    Grants the AI read access to the Obsidian vault and Odysseus personal_docs.
    Idempotent: skips if already present so user-edited paths are preserved.
    """
    from core.database import McpServer

    existing = db.query(McpServer).filter(McpServer.name == "Filesystem").first()
    if existing:
        print("  Filesystem MCP entry already exists — skipping")
        return

    allowed_paths = [
        str(Path.home() / "Documents/_dev/maylortaylor/obsidian_vault"),
        str(Path.home() / "Documents/_dev/misc/odysseus/data/personal_docs"),
    ]

    print(f"  Creating Filesystem MCP entry (enabled, paths: {len(allowed_paths)} dirs)")
    if not dry_run:
        srv = McpServer(
            id=str(uuid.uuid4()),
            name="Filesystem",
            transport="stdio",
            command="npx",
            args=json.dumps(["-y", "@modelcontextprotocol/server-filesystem"] + allowed_paths),
            env=json.dumps({}),
            is_enabled=True,
        )
        db.add(srv)
        db.commit()


def print_next_steps(has_spm: bool) -> None:
    print()
    print("✓ Done. Next steps:")
    print("  1. Restart Odysseus:  ./start-macos.sh")
    print("  2. Email tab:  verify both accounts appear")
    print("  3. Calendar tab:  verify both calendars appear")
    print("  4. Authorize Google Drive (AI agent access):")
    print("     • Admin (gear icon) → MCP Servers → Google Drive → Enable / Authorize")
    print("     • Sign in with maylortaylor@gmail.com (primary account)")
    print("     • For StPeteMusic Drive content: share folders/docs with maylortaylor@gmail.com")
    print("  5. Filesystem MCP: active immediately — AI can read Obsidian vault + personal_docs")
    if not has_spm:
        print()
        print("  ⚠ StPeteMusic account skipped.")
        print("    Add GMAIL_SPM_USER and GMAIL_SPM_APP_PASSWORD to .env and re-run.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Set up Odysseus integrations from .env")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing")
    args = parser.parse_args()

    print("=== Odysseus Integration Setup ===")
    if args.dry_run:
        print("DRY RUN — no changes will be written\n")

    errors, has_spm = validate()
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  ✗ {e}")
        print("\nFix these in .env and re-run.")
        sys.exit(1)

    print(f"Personal:    {PERSONAL_USER}")
    if has_spm:
        print(f"StPeteMusic: {SPM_USER}")
    else:
        print("StPeteMusic: (not configured — add GMAIL_SPM_USER to .env)")
    print()

    # Add repo root to sys.path so project imports resolve
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))
    os.chdir(repo_root)

    from core.database import SessionLocal
    from src.constants import USER_PREFS_FILE

    prefs_path = Path(USER_PREFS_FILE)
    db = SessionLocal()
    try:
        print("→ Email (IMAP/SMTP)")
        setup_email_account(
            db, PERSONAL_USER, PERSONAL_PASSWORD,
            "Matt Taylor (personal)", is_default=True, dry_run=args.dry_run,
        )
        if has_spm:
            setup_email_account(
                db, SPM_USER, SPM_PASSWORD,
                "Matt Taylor (StPeteMusic)", is_default=False, dry_run=args.dry_run,
            )

        print("→ Calendar (CalDAV)")
        setup_caldav_account(
            prefs_path, PERSONAL_USER, PERSONAL_PASSWORD,
            "Google Calendar (Personal)", dry_run=args.dry_run,
        )
        if has_spm:
            setup_caldav_account(
                prefs_path, SPM_USER, SPM_PASSWORD,
                "Google Calendar (StPeteMusic)", dry_run=args.dry_run,
            )

        print("→ Google Drive MCP")
        setup_drive_mcp(db, dry_run=args.dry_run)

        print("→ Filesystem MCP")
        setup_filesystem_mcp(db, dry_run=args.dry_run)
    finally:
        db.close()

    print_next_steps(has_spm)
    if args.dry_run:
        print("\n(Re-run without --dry-run to apply changes)")


if __name__ == "__main__":
    main()
