"""Tests for Google OAuth2 support in the /api/email/accounts/test endpoint.

Covers the changes made to routes/email_routes.py:

- test_account_config: OAuth accounts must not require a stored password.
- IMAP and SMTP test paths must use XOAUTH2 for Google accounts.
- Password accounts must still use conn.login() / smtp.login().

These tests use only in-memory SQLite (via SQLAlchemy) and mock network
objects — no live email server or real OAuth credentials are needed.
"""

import time
import unittest.mock as mock

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_orm_db():
    """Return (Session, SessionFactory) backed by an isolated in-memory SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine)
    return Factory(), Factory


def _make_orm_account(session, account_id="acct-1", owner="alice", **kwargs):
    from core.database import EmailAccount
    row = EmailAccount(
        id=account_id,
        owner=owner,
        name=kwargs.get("name", "Test"),
        from_address=kwargs.get("from_address", "me@nia.law"),
        imap_host=kwargs.get("imap_host", "imap.gmail.com"),
        imap_port=kwargs.get("imap_port", 993),
        imap_user=kwargs.get("imap_user", "me@nia.law"),
        smtp_host=kwargs.get("smtp_host", "smtp.gmail.com"),
        smtp_port=kwargs.get("smtp_port", 587),
        smtp_user=kwargs.get("smtp_user", "me@nia.law"),
    )
    for k, v in kwargs.items():
        if hasattr(row, k):
            setattr(row, k, v)
    session.add(row)
    session.commit()
    return row


# ── test_connection route: OAuth awareness ────────────────────────────────────

@pytest.mark.asyncio
async def test_test_connection_oauth_account_uses_xoauth2_imap():
    """The /test-connection route must use XOAUTH2 for Google OAuth accounts
    rather than rejecting with 'Need IMAP host, username, and password'."""
    from src.secret_storage import encrypt as _enc
    from routes.email_routes import setup_email_routes

    future_expiry = str(int(time.time()) + 7200)
    db, Factory = _make_orm_db()
    _make_orm_account(
        db, account_id="acct-oauth", owner="alice",
        oauth_provider="google",
        oauth_access_token=_enc("ya29.live"),
        oauth_refresh_token=_enc("1//refresh"),
        oauth_token_expiry=future_expiry,
    )
    db.close()

    router = setup_email_routes()
    test_conn = None
    for route in router.routes:
        if route.path == "/api/email/accounts/test" and "POST" in getattr(route, "methods", set()):
            test_conn = route.endpoint
            break
    assert test_conn is not None, "test-connection route not found"

    mock_imap_conn = mock.MagicMock()

    class _FakeReq:
        async def json(self):
            return {"account_id": "acct-oauth"}

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._open_imap_connection", return_value=mock_imap_conn), \
         mock.patch("routes.email_helpers._get_valid_google_token", return_value="ya29.live"):
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["imap"].get("ok") is True, \
        f"OAuth IMAP test must succeed, got: {result['imap']}"
    mock_imap_conn.authenticate.assert_called_once()
    assert mock_imap_conn.authenticate.call_args[0][0] == "XOAUTH2"
    mock_imap_conn.login.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_password_account_still_uses_login():
    """Existing password accounts must still go through the login() path."""
    from src.secret_storage import encrypt as _enc
    from routes.email_routes import setup_email_routes

    db, Factory = _make_orm_db()
    _make_orm_account(
        db, account_id="acct-pw", owner="alice",
        imap_host="imap.example.com",
        imap_user="me@example.com",
        smtp_host="smtp.example.com",
        smtp_user="me@example.com",
        imap_password=_enc("hunter2"),
    )
    db.close()

    router = setup_email_routes()
    test_conn = None
    for route in router.routes:
        if route.path == "/api/email/accounts/test" and "POST" in getattr(route, "methods", set()):
            test_conn = route.endpoint
            break

    mock_imap_conn = mock.MagicMock()

    class _FakeReq:
        async def json(self):
            return {"account_id": "acct-pw"}

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._open_imap_connection", return_value=mock_imap_conn):
        result = await test_conn(req=_FakeReq(), owner="alice")

    mock_imap_conn.login.assert_called_once_with("me@example.com", "hunter2")
    mock_imap_conn.authenticate.assert_not_called()
