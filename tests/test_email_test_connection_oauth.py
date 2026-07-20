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
        imap_starttls=kwargs.get("imap_starttls", False),
        smtp_host=kwargs.get("smtp_host", "smtp.gmail.com"),
        smtp_port=kwargs.get("smtp_port", 587),
        smtp_security=kwargs.get("smtp_security", "starttls"),
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
async def test_test_connection_oauth_account_uses_xoauth2_for_imap_and_smtp():
    """The saved-account test must use XOAUTH2 for both mail protocols."""
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
    mock_smtp_conn = mock.MagicMock()

    class _FakeReq:
        async def json(self):
            return {"account_id": "acct-oauth"}

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._open_imap_connection", return_value=mock_imap_conn), \
         mock.patch("routes.email_routes.smtplib.SMTP", return_value=mock_smtp_conn), \
         mock.patch("routes.email_routes.smtplib.SMTP_SSL", return_value=mock_smtp_conn), \
         mock.patch("routes.email_routes._get_valid_google_token", return_value="ya29.live") as token_getter:
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is True
    assert result["imap"].get("ok") is True, \
        f"OAuth IMAP test must succeed, got: {result['imap']}"
    assert result["smtp"].get("ok") is True, \
        f"OAuth SMTP test must succeed, got: {result['smtp']}"
    mock_imap_conn.authenticate.assert_called_once()
    assert mock_imap_conn.authenticate.call_args[0][0] == "XOAUTH2"
    mock_imap_conn.login.assert_not_called()
    mock_smtp_conn.auth.assert_called_once()
    assert mock_smtp_conn.auth.call_args[0][0] == "XOAUTH2"
    mock_smtp_conn.login.assert_not_called()
    token_getter.assert_called_once()


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
    mock_smtp_conn = mock.MagicMock()

    class _FakeReq:
        async def json(self):
            return {"account_id": "acct-pw"}

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._open_imap_connection", return_value=mock_imap_conn), \
         mock.patch("routes.email_routes.smtplib.SMTP", return_value=mock_smtp_conn), \
         mock.patch("routes.email_routes.smtplib.SMTP_SSL", return_value=mock_smtp_conn):
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is True
    mock_imap_conn.login.assert_called_once_with("me@example.com", "hunter2")
    mock_imap_conn.authenticate.assert_not_called()
    mock_smtp_conn.login.assert_called_once_with("me@example.com", "hunter2")
    mock_smtp_conn.auth.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_rejects_non_google_hosts_before_oauth_auth():
    """Saved Google tokens must never be routed to edited custom hosts."""
    from src.secret_storage import encrypt as _enc
    from routes.email_routes import setup_email_routes

    db, Factory = _make_orm_db()
    _make_orm_account(
        db,
        account_id="acct-oauth",
        owner="alice",
        oauth_provider="google",
        oauth_access_token=_enc("ya29.live"),
        oauth_refresh_token=_enc("1//refresh"),
        oauth_token_expiry=str(int(time.time()) + 7200),
    )
    db.close()

    router = setup_email_routes()
    test_conn = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/email/accounts/test" and "POST" in getattr(route, "methods", set())
    )

    class _FakeReq:
        async def json(self):
            return {
                "account_id": "acct-oauth",
                "imap_host": "collector.invalid",
                "smtp_host": "collector.invalid",
            }

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._open_imap_connection") as open_imap, \
         mock.patch("routes.email_routes.smtplib.SMTP") as open_smtp, \
         mock.patch("routes.email_routes.smtplib.SMTP_SSL") as open_smtp_ssl, \
         mock.patch("routes.email_routes._get_valid_google_token") as token_getter:
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is False
    assert "imap.gmail.com" in result["imap"]["error"]
    assert "smtp.gmail.com" in result["smtp"]["error"]
    open_imap.assert_not_called()
    open_smtp.assert_not_called()
    open_smtp_ssl.assert_not_called()
    token_getter.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_rejects_insecure_oauth_transports_before_auth():
    """Google OAuth credentials must not be tested over plaintext transports."""
    from src.secret_storage import encrypt as _enc
    from routes.email_routes import setup_email_routes

    db, Factory = _make_orm_db()
    _make_orm_account(
        db,
        account_id="acct-oauth",
        owner="alice",
        oauth_provider="google",
        oauth_access_token=_enc("ya29.live"),
        oauth_refresh_token=_enc("1//refresh"),
        oauth_token_expiry=str(int(time.time()) + 7200),
    )
    db.close()

    router = setup_email_routes()
    test_conn = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/email/accounts/test" and "POST" in getattr(route, "methods", set())
    )

    class _FakeReq:
        async def json(self):
            return {
                "account_id": "acct-oauth",
                "imap_port": 143,
                "imap_starttls": False,
                "smtp_port": 587,
                "smtp_security": "none",
            }

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_routes._open_imap_connection") as open_imap, \
         mock.patch("routes.email_routes.smtplib.SMTP") as open_smtp, \
         mock.patch("routes.email_routes.smtplib.SMTP_SSL") as open_smtp_ssl, \
         mock.patch("routes.email_routes._get_valid_google_token") as token_getter:
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is False
    assert "TLS" in result["imap"]["error"]
    assert "TLS" in result["smtp"]["error"]
    open_imap.assert_not_called()
    open_smtp.assert_not_called()
    open_smtp_ssl.assert_not_called()
    token_getter.assert_not_called()


@pytest.mark.asyncio
async def test_test_connection_does_not_accept_inline_oauth_state():
    """Only an owner-checked saved account may select the OAuth branch."""
    from routes.email_routes import setup_email_routes

    router = setup_email_routes()
    test_conn = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/email/accounts/test" and "POST" in getattr(route, "methods", set())
    )

    class _FakeReq:
        async def json(self):
            return {
                "imap_host": "imap.gmail.com",
                "imap_user": "me@example.com",
                "oauth_provider": "google",
                "oauth_access_token": "client-supplied-token",
            }

    with mock.patch("routes.email_routes._open_imap_connection") as open_imap, \
         mock.patch("routes.email_routes._get_valid_google_token") as token_getter:
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is False
    assert result["imap"]["error"] == "Need IMAP host, username, and password"
    open_imap.assert_not_called()
    token_getter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("imap_starttls", "imap_port"),
    [(False, 993), (True, 143)],
)
async def test_test_connection_verifies_imap_tls_before_loading_oauth_token(
    imap_starttls,
    imap_port,
):
    """Both Google IMAP TLS modes receive a certificate-verifying context;
    certificate rejection happens before the bearer token is loaded."""
    import ssl

    from routes.email_routes import setup_email_routes
    from src.secret_storage import encrypt as _enc

    db, Factory = _make_orm_db()
    _make_orm_account(
        db,
        account_id="acct-imap-tls",
        owner="alice",
        imap_port=imap_port,
        imap_starttls=imap_starttls,
        smtp_host="",
        oauth_provider="google",
        oauth_access_token=_enc("ya29.live"),
        oauth_refresh_token=_enc("1//refresh"),
        oauth_token_expiry=str(int(time.time()) + 7200),
    )
    db.close()

    router = setup_email_routes()
    test_conn = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/email/accounts/test"
        and "POST" in getattr(route, "methods", set())
    )

    class _FakeReq:
        async def json(self):
            return {"account_id": "acct-imap-tls"}

    context = ssl.create_default_context()
    starttls_conn = mock.MagicMock()
    starttls_conn.starttls.side_effect = ssl.SSLCertVerificationError(
        "untrusted certificate"
    )
    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch(
             "routes.email_routes.ssl.create_default_context",
             return_value=context,
         ), mock.patch(
             "routes.email_helpers.imaplib.IMAP4",
             return_value=starttls_conn,
         ) as imap_cls, mock.patch(
             "routes.email_helpers.imaplib.IMAP4_SSL",
             side_effect=ssl.SSLCertVerificationError("untrusted certificate"),
         ) as imap_ssl_cls, mock.patch(
             "routes.email_routes._get_valid_google_token"
         ) as token_getter:
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is False
    assert result["imap"]["ok"] is False
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    token_getter.assert_not_called()
    if imap_starttls:
        assert starttls_conn.starttls.call_args.kwargs["ssl_context"] is context
        imap_ssl_cls.assert_not_called()
    else:
        assert imap_ssl_cls.call_args.kwargs["ssl_context"] is context
        imap_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("smtp_security", "smtp_port"),
    [("ssl", 465), ("starttls", 587)],
)
async def test_test_connection_verifies_smtp_tls_before_loading_oauth_token(
    smtp_security,
    smtp_port,
):
    """Both Google SMTP TLS modes reject an invalid certificate before any
    XOAUTH2 credential is obtained or sent."""
    import ssl

    from routes.email_routes import setup_email_routes
    from src.secret_storage import encrypt as _enc

    db, Factory = _make_orm_db()
    _make_orm_account(
        db,
        account_id="acct-smtp-tls",
        owner="alice",
        imap_host="",
        smtp_port=smtp_port,
        smtp_security=smtp_security,
        oauth_provider="google",
        oauth_access_token=_enc("ya29.live"),
        oauth_refresh_token=_enc("1//refresh"),
        oauth_token_expiry=str(int(time.time()) + 7200),
    )
    db.close()

    router = setup_email_routes()
    test_conn = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/email/accounts/test"
        and "POST" in getattr(route, "methods", set())
    )

    class _FakeReq:
        async def json(self):
            return {"account_id": "acct-smtp-tls"}

    context = ssl.create_default_context()
    starttls_smtp = mock.MagicMock()
    starttls_smtp.starttls.side_effect = ssl.SSLCertVerificationError(
        "untrusted certificate"
    )
    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch(
             "routes.email_routes.ssl.create_default_context",
             return_value=context,
         ), mock.patch(
             "routes.email_routes.smtplib.SMTP",
             return_value=starttls_smtp,
         ) as smtp_cls, mock.patch(
             "routes.email_routes.smtplib.SMTP_SSL",
             side_effect=ssl.SSLCertVerificationError("untrusted certificate"),
         ) as smtp_ssl_cls, mock.patch(
             "routes.email_routes._get_valid_google_token"
         ) as token_getter:
        result = await test_conn(req=_FakeReq(), owner="alice")

    assert result["ok"] is False
    assert result["smtp"]["ok"] is False
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    token_getter.assert_not_called()
    if smtp_security == "starttls":
        assert starttls_smtp.starttls.call_args.kwargs["context"] is context
        assert starttls_smtp.close.called
        smtp_ssl_cls.assert_not_called()
    else:
        assert smtp_ssl_cls.call_args.kwargs["context"] is context
        smtp_cls.assert_not_called()
