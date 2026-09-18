#!/usr/bin/env python3
"""Create/remove the switchable 'Demo' EmailAccount in Odysseus.

Mirrors the existing local-Dovecot account (localhost:31143, STARTTLS) but points
at the throwaway demo@odysseus.local mailbox. Password is stored Fernet-encrypted
via the app's own secret_storage, exactly like real accounts.

    python demo_account.py setup     # add (or update) the 'Demo' account
    python demo_account.py teardown  # remove it

Run from the repo root so the app's modules import cleanly.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Make repo root importable regardless of CWD.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.database import (  # noqa: E402
    Base,
    EmailAccount,
    SessionLocal,
    engine,
    lock_email_account_owner_mutations,
)
from sqlalchemy import or_  # noqa: E402
from src.secret_storage import encrypt  # noqa: E402

NAME = "Demo"
IMAP_USER = "demo@odysseus.local"
IMAP_PASSWORD = "demodemo"
# Owner empty-string => same list as the real Default account (switchable in the
# account dropdown).
OWNER = ""


def _owner_scope(query, owner: str):
    if owner:
        return query.filter(EmailAccount.owner == owner)
    return query.filter(or_(EmailAccount.owner == None, EmailAccount.owner == ""))  # noqa: E711


def _discover_demo_scopes() -> set[str]:
    db = SessionLocal()
    try:
        return {
            row.owner or ""
            for row in db.query(EmailAccount).filter(
                EmailAccount.name == NAME,
                EmailAccount.imap_user == IMAP_USER,
            ).all()
        }
    finally:
        db.close()


def _lock_and_load_demo_rows(db, scopes: set[str]):
    """Reload Demo rows under every observed owner lock."""
    scopes = set(scopes) or {OWNER}
    while True:
        lock_email_account_owner_mutations(db, *scopes)
        rows = (
            db.query(EmailAccount)
            .filter(
                EmailAccount.name == NAME,
                EmailAccount.imap_user == IMAP_USER,
            )
            .order_by(EmailAccount.created_at.asc(), EmailAccount.id.asc())
            .all()
        )
        current_scopes = {row.owner or "" for row in rows}
        if current_scopes.issubset(scopes) or db.get_bind().dialect.name == "sqlite":
            return rows
        db.rollback()
        scopes.update(current_scopes)


def _promote_oldest_enabled(db, owner: str, excluded_ids: list[str]) -> None:
    remaining = _owner_scope(
        db.query(EmailAccount).filter(
            EmailAccount.enabled == True,  # noqa: E712
            ~EmailAccount.id.in_(excluded_ids),
        ),
        owner,
    )
    if remaining.filter(EmailAccount.is_default == True).first() is not None:  # noqa: E712
        return
    promote = remaining.order_by(
        EmailAccount.created_at.asc(), EmailAccount.id.asc()
    ).first()
    if promote is not None:
        promote.is_default = True


def setup() -> int:
    Base.metadata.create_all(bind=engine)
    scopes = _discover_demo_scopes() | {OWNER}
    db = SessionLocal()
    try:
        rows = _lock_and_load_demo_rows(db, scopes)
        acct = rows[0] if rows else None
        if acct is None:
            acct = EmailAccount(id=uuid.uuid4().hex, name=NAME)
            db.add(acct)
        old_scope = acct.owner or ""
        was_default = bool(acct.is_default)
        if old_scope != OWNER:
            # Move a non-default row first so the unique index cannot see two
            # defaults transiently while SQLAlchemy flushes the owner move and
            # old-scope promotion in separate UPDATE statements.
            acct.is_default = False
            acct.owner = OWNER
            db.flush()
            if was_default:
                _promote_oldest_enabled(db, old_scope, [acct.id])

        target_default = _owner_scope(
            db.query(EmailAccount).filter(
                EmailAccount.id != acct.id,
                EmailAccount.is_default == True,  # noqa: E712
            ),
            OWNER,
        ).first()
        acct.owner = OWNER
        # Keep Demo non-default when a real default exists.  If it is the only
        # enabled account, it must be default to preserve normal create
        # semantics and avoid leaving the owner partition without one.
        acct.is_default = target_default is None
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
        state = "default" if acct.is_default else "non-default"
        print(f"'{NAME}' account ready (id={acct.id}, {state}, switchable).")
        return 0
    finally:
        db.close()


def teardown() -> int:
    scopes = _discover_demo_scopes()
    db = SessionLocal()
    try:
        rows = _lock_and_load_demo_rows(db, scopes)
        deleted_ids = [row.id for row in rows]
        default_scopes = {row.owner or "" for row in rows if row.is_default}
        for r in rows:
            db.delete(r)
        # Ensure the old default DELETE reaches the database before a
        # replacement UPDATE; the unique index is enforced per statement.
        db.flush()
        for owner in default_scopes:
            _promote_oldest_enabled(db, owner, deleted_ids)
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
