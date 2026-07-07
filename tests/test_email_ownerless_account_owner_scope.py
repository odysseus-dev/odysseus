"""Cross-tenant access control for legacy owner-less email accounts.

`email_accounts` is the one owner-scoped table left out of the legacy-owner
migration backfill (core/database.py), so rows with owner NULL/"" persist on a
multi-user deploy — e.g. an account configured while auth was disabled, or an
imported legacy row. The HTTP route guards (`_assert_owns_account` and the
explicit-account_id path in `_get_email_config`) must scope such rows to a
mailbox match, exactly like the `_owner_or_matching_legacy_account` fallback and
the MCP `_account_visible_to_owner` gate. Otherwise any authenticated user can
read/send/update-credentials/delete another tenant's imported mailbox.
"""

from unittest import mock

import pytest
from fastapi import HTTPException


def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine)
    return Factory


def _make_account(Factory, account_id, owner, imap_user, from_address="", is_default=False):
    from core.database import EmailAccount
    db = Factory()
    row = EmailAccount(
        id=account_id,
        owner=owner,
        name="Test",
        enabled=True,
        is_default=is_default,
        imap_host="imap.example.com",
        imap_port=993,
        imap_user=imap_user,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user=imap_user,
        from_address=from_address or imap_user,
    )
    db.add(row)
    db.commit()
    db.close()


def test_assert_owns_account_rejects_ownerless_account_for_other_tenant():
    """The core regression: a legacy owner-less mailbox is NOT accessible to an
    authenticated caller whose own mailbox does not match it."""
    from routes.email_helpers import _assert_owns_account
    Factory = _make_db()
    # owner="" (created while auth was disabled); mailbox belongs to victim.
    _make_account(Factory, "acct-legacy", owner="", imap_user="victim@corp.com")

    with mock.patch("core.database.SessionLocal", Factory):
        with pytest.raises(HTTPException) as exc:
            _assert_owns_account("acct-legacy", "attacker")
    assert exc.value.status_code == 404


def test_assert_owns_account_allows_owned_account():
    from routes.email_helpers import _assert_owns_account
    Factory = _make_db()
    _make_account(Factory, "acct-bob", owner="bob", imap_user="bob@corp.com")
    with mock.patch("core.database.SessionLocal", Factory):
        _assert_owns_account("acct-bob", "bob")  # no raise


def test_assert_owns_account_allows_ownerless_account_on_mailbox_match():
    """Legacy-claim path stays intact: the user whose mailbox matches an
    owner-less account may still act on it (imap_user or from_address)."""
    from routes.email_helpers import _assert_owns_account
    Factory = _make_db()
    _make_account(Factory, "acct-legacy", owner="", imap_user="alice@corp.com")
    with mock.patch("core.database.SessionLocal", Factory):
        _assert_owns_account("acct-legacy", "alice@corp.com")  # no raise


def test_assert_owns_account_noop_for_single_user_mode():
    """owner == "" (unconfigured / single-user) accepts any account, unchanged."""
    from routes.email_helpers import _assert_owns_account
    Factory = _make_db()
    _make_account(Factory, "acct-legacy", owner="", imap_user="whoever@corp.com")
    with mock.patch("core.database.SessionLocal", Factory):
        _assert_owns_account("acct-legacy", "")  # no raise


def test_get_email_config_does_not_resolve_ownerless_account_for_other_tenant(monkeypatch):
    """`_get_email_config(account_id=..., owner=...)` must not serve an
    owner-less account (and its decrypted creds) to a non-matching tenant."""
    import routes.email_helpers as eh
    Factory = _make_db()
    _make_account(Factory, "acct-legacy", owner="", imap_user="victim@corp.com", is_default=True)

    # Make the settings.json / env fallback empty and deterministic.
    monkeypatch.setattr(eh, "_load_settings", lambda: {}, raising=False)
    for var in ("IMAP_HOST", "SMTP_HOST", "IMAP_USER", "SMTP_USER"):
        monkeypatch.delenv(var, raising=False)

    with mock.patch("core.database.SessionLocal", Factory):
        cfg = eh._get_email_config(account_id="acct-legacy", owner="attacker")

    assert cfg.get("account_id") != "acct-legacy"


def test_get_email_config_resolves_ownerless_account_on_mailbox_match():
    """The mailbox owner still resolves their claimable legacy account by id."""
    import routes.email_helpers as eh
    Factory = _make_db()
    _make_account(Factory, "acct-legacy", owner="", imap_user="alice@corp.com")
    with mock.patch("core.database.SessionLocal", Factory):
        cfg = eh._get_email_config(account_id="acct-legacy", owner="alice@corp.com")
    assert cfg.get("account_id") == "acct-legacy"
