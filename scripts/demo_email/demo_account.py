#!/usr/bin/env python3
"""Create/remove the switchable, non-default 'Demo' EmailAccount in Odysseus.

Mirrors the existing local-Dovecot account (localhost:31143, STARTTLS) but points
at the throwaway demo@odysseus.local mailbox. Password is stored Fernet-encrypted
via the app's own secret_storage, exactly like real accounts.

    python demo_account.py setup     # add (or update) the 'Demo' account
    python demo_account.py teardown  # remove it

Run from the repo root so the app's modules import cleanly.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Make repo root importable regardless of CWD.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.database import SessionLocal, EmailAccount, Base, engine  # noqa: E402
from src.secret_storage import encrypt  # noqa: E402

NAME = "Demo"
IMAP_USER = "demo@odysseus.local"
IMAP_PASSWORD = os.environ.get("DEMO_IMAP_PASSWORD", "")
if not IMAP_PASSWORD:
    print("ERROR: set DEMO_IMAP_PASSWORD env var before running demo account setup.", file=sys.stderr)
    raise SystemExit(1)
# Owner empty-string => same list as the real Default account (switchable in the
# account dropdown).
OWNER = ""


def setup() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        acct = db.query(EmailAccount).filter(
            EmailAccount.name == NAME, EmailAccount.imap_user == IMAP_USER
        ).first()
        if acct is None:
            acct = EmailAccount(id=uuid.uuid4().hex, name=NAME)
            db.add(acct)
        acct.owner = OWNER
        acct.is_default = False          # never default — user switches to it
        acct.enabled = True
        acct.imap_host = "localhost"
        acct.imap_port = 31143
        acct.imap_user = IMAP_USER
        acct.imap_password = encrypt(IMAP_PASSWORD)
        acct.imap_starttls = True
        # Local-only: no real SMTP. Point at a dead local port so an accidental
        # "Send" during the demo fails locally instead of mailing anyone.
        acct.smtp_host = "localhost"
        acct.smtp_port = 2525
        acct.smtp_user = IMAP_USER
        acct.smtp_password = encrypt(IMAP_PASSWORD)
        acct.from_address = IMAP_USER
        db.commit()
        print(f"'{NAME}' account ready (id={acct.id}, non-default, switchable).")
        return 0
    finally:
        db.close()


def teardown() -> int:
    db = SessionLocal()
    try:
        rows = db.query(EmailAccount).filter(
            EmailAccount.name == NAME, EmailAccount.imap_user == IMAP_USER
        ).all()
        for r in rows:
            db.delete(r)
        db.commit()
        print(f"removed {len(rows)} '{NAME}' account row(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        raise SystemExit(setup())
    if cmd == "teardown":
        raise SystemExit(teardown())
    print(__doc__)
    raise SystemExit(2)
