"""Regression tests for the dual-default bug (#5046).

When a user has a legacy owner-less account whose mailbox matches their
username *and* an owned account, switching the default (or creating/deleting
defaults) must keep the one-default invariant. Previously the set-default /
create / delete mutation paths scoped their `is_default` clear/promote to
`owner == user` only, leaving the matching legacy row flagged default — so
`_get_email_config` (whose `.first()` happens to return the legacy row) kept
serving the old account and "switching the default email" silently failed.

These tests drive the actual route handlers against an in-memory DB to cover
the real code path, not just the extracted helper.
"""
import unittest.mock as mock

import pytest


def _make_db():
    """Return (Session, SessionFactory) on an isolated in-memory SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine)
    return Factory(), Factory


def _make_account(session, account_id, owner=None, is_default=False, imap_user="u@example.com",
                  from_address="u@example.com", name="Acc"):
    from core.database import EmailAccount
    row = EmailAccount(
        id=account_id, owner=owner, name=name,
        is_default=is_default, enabled=True,
        from_address=from_address, imap_user=imap_user,
        imap_host="imap.example.com", imap_port=993,
        smtp_host="smtp.example.com", smtp_port=465, smtp_user=imap_user,
    )
    session.add(row)
    session.commit()
    return row


def _default_ids(Factory):
    """Return the ids of all rows with is_default=True (across every owner)."""
    from core.database import EmailAccount
    db = Factory()
    try:
        return [r.id for r in db.query(EmailAccount).filter(EmailAccount.is_default == True).all()]  # noqa: E712
    finally:
        db.close()


def _endpoint(path, methods=None):
    """Look up a route handler by path (+ optional method set) without using
    next()/generator expressions — those raise StopIteration inside a coroutine
    and crash an async test."""
    from routes.email_routes import setup_email_routes
    router = setup_email_routes()
    for route in router.routes:
        if getattr(route, "path", None) != path:
            continue
        if methods is not None and getattr(route, "methods", None) != methods:
            continue
        return route.endpoint
    raise AssertionError(f"no route matched path={path!r} methods={methods!r}")


# ── set-default: the headline bug ──────────────────────────────────

@pytest.mark.asyncio
async def test_set_default_clears_matching_legacy_account():
    """Switching default to an owned account must clear the matching legacy
    owner-less row's is_default, leaving exactly one default."""
    db, Factory = _make_db()
    # Legacy owner-less row whose mailbox IS alice's — this is what list /
    # _get_email_config treat as hers, so the default switch must too.
    _make_account(db, "legacy", owner=None, is_default=True,
                  imap_user="alice", from_address="alice@example.com", name="Legacy")
    _make_account(db, "owned", owner="alice", is_default=False,
                  imap_user="alice@example.com", from_address="alice2@example.com", name="Owned")
    db.close()

    with mock.patch("core.database.SessionLocal", Factory):
        set_default = _endpoint("/api/email/accounts/{account_id}/set-default")
        await set_default("owned", owner="alice")

    assert _default_ids(Factory) == ["owned"], "legacy row must be cleared so only 'owned' is default"


@pytest.mark.asyncio
async def test_set_default_does_not_touch_other_users():
    """Scoping must not clear another (real) user's default — only the caller's
    owned rows + their matching legacy rows."""
    db, Factory = _make_db()
    _make_account(db, "alice-owned", owner="alice", is_default=True,
                  imap_user="alice@example.com", name="Alice")
    _make_account(db, "bob-owned", owner="bob", is_default=True,
                  imap_user="bob@example.com", name="Bob")
    db.close()

    with mock.patch("core.database.SessionLocal", Factory):
        set_default = _endpoint("/api/email/accounts/{account_id}/set-default")
        # Re-assert alice's own default; bob's must be preserved.
        await set_default("alice-owned", owner="alice")

    defaults = _default_ids(Factory)
    assert "bob-owned" in defaults, "another user's default must be preserved"
    assert "alice-owned" in defaults


# ── create: sibling-clear must use the shared scope ────────────────

@pytest.mark.asyncio
async def test_create_default_clears_matching_legacy_account():
    """Creating a new default account must clear a matching legacy owner-less
    row, not leave two defaults."""
    db, Factory = _make_db()
    _make_account(db, "legacy", owner=None, is_default=True,
                  imap_user="alice", from_address="alice@example.com", name="Legacy")
    db.close()

    payload = {
        "name": "New", "is_default": True,
        "imap_user": "alice@example.com", "from_address": "alice@example.com",
        "imap_host": "imap.example.com", "imap_port": 993,
        "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_user": "alice@example.com",
    }
    with mock.patch("core.database.SessionLocal", Factory):
        create = _endpoint("/api/email/accounts", methods={"POST"})
        result = await create(payload, owner="alice")

    new_id = result["id"]
    assert _default_ids(Factory) == [new_id], "legacy default must be cleared by the create"


# ── delete: promote-next must use the shared scope ─────────────────

@pytest.mark.asyncio
async def test_delete_default_promotes_within_shared_scope():
    """Deleting the default must promote the next account within the shared
    scope (owned OR matching legacy), including a legacy row."""
    db, Factory = _make_db()
    _make_account(db, "owned", owner="alice", is_default=True,
                  imap_user="alice@example.com", name="Owned")
    # Legacy row that matches alice — should be promoted to default after delete.
    _make_account(db, "legacy", owner=None, is_default=False,
                  imap_user="alice", from_address="alice@example.com", name="Legacy")
    db.close()

    with mock.patch("core.database.SessionLocal", Factory):
        delete = _endpoint("/api/email/accounts/{account_id}", methods={"DELETE"})
        await delete("owned", owner="alice")

    # owned is gone; legacy should have been promoted.
    assert _default_ids(Factory) == ["legacy"]
