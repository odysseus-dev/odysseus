"""Tests for Google OAuth2 support in the MCP email server.

Covers the changes made to mcp_servers/email_server.py:

- _read_accounts_from_db: must include oauth_provider and token columns so the
  MCP server can distinguish OAuth accounts from password accounts.
- _load_config: must populate oauth fields from the DB row into the cfg dict.
- _imap_connect: OAuth accounts must use XOAUTH2; password accounts must use
  conn.login(); a missing/expired token must raise, not silently fail.
- _smtp_connect: OAuth accounts must use smtp.auth("XOAUTH2"); password accounts
  must use smtp.login(); no-token error must propagate.
- test_connection route (email_routes.py): OAuth accounts must not require a
  stored password; IMAP and SMTP tests must use XOAUTH2.

These tests use only in-memory SQLite and mock network/IMAP/SMTP objects — no
live email server or real OAuth credentials are needed.
"""

import sqlite3
import time
import unittest.mock as mock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_sqlite_db_with_oauth_account(
    account_id="acct-1",
    owner="alice",
    imap_user="me@nia.law",
    oauth_provider="google",
    access_token_raw="ya29.test_access",
    refresh_token_raw="1//test_refresh",
    expiry_offset=7200,
) -> "sqlite3.Connection":
    """Create an in-memory SQLite DB with one OAuth email_accounts row.

    Returns the connection (kept open so :memory: survives).
    """
    from src.secret_storage import encrypt as _enc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE email_accounts (
            id TEXT PRIMARY KEY,
            owner TEXT,
            name TEXT,
            is_default INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            imap_host TEXT,
            imap_port INTEGER DEFAULT 993,
            imap_user TEXT,
            imap_password TEXT,
            imap_starttls INTEGER DEFAULT 0,
            smtp_host TEXT,
            smtp_port INTEGER DEFAULT 587,
            smtp_security TEXT DEFAULT '',
            smtp_user TEXT,
            smtp_password TEXT,
            from_address TEXT,
            oauth_provider TEXT DEFAULT '',
            oauth_access_token TEXT DEFAULT '',
            oauth_refresh_token TEXT DEFAULT '',
            oauth_token_expiry TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    expiry = str(int(time.time()) + expiry_offset)
    conn.execute("""
        INSERT INTO email_accounts
            (id, owner, name, is_default, enabled,
             imap_host, imap_port, imap_user, imap_password, imap_starttls,
             smtp_host, smtp_port, smtp_security, smtp_user, smtp_password, from_address,
             oauth_provider, oauth_access_token, oauth_refresh_token, oauth_token_expiry)
        VALUES (?,?,?,1,1, 'imap.gmail.com',993,?,NULL,0,
                'smtp.gmail.com',587,'starttls',?,NULL,?,
                ?,?,?,?)
    """, (
        account_id, owner, "NIA",
        imap_user, imap_user, imap_user,
        oauth_provider, _enc(access_token_raw), _enc(refresh_token_raw), expiry,
    ))
    conn.commit()
    return conn


def _make_sqlite_db_password_account(
    account_id="acct-pw",
    owner="alice",
    imap_user="me@example.com",
    password="app-password",
) -> sqlite3.Connection:
    from src.secret_storage import encrypt as _enc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE email_accounts (
            id TEXT PRIMARY KEY, owner TEXT, name TEXT,
            is_default INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
            imap_host TEXT, imap_port INTEGER DEFAULT 993,
            imap_user TEXT, imap_password TEXT, imap_starttls INTEGER DEFAULT 0,
            smtp_host TEXT, smtp_port INTEGER DEFAULT 587,
            smtp_security TEXT DEFAULT '', smtp_user TEXT, smtp_password TEXT,
            from_address TEXT, oauth_provider TEXT DEFAULT '',
            oauth_access_token TEXT DEFAULT '', oauth_refresh_token TEXT DEFAULT '',
            oauth_token_expiry TEXT DEFAULT '', created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        INSERT INTO email_accounts
            (id, owner, name, is_default, enabled,
             imap_host, imap_port, imap_user, imap_password, imap_starttls,
             smtp_host, smtp_port, smtp_security, smtp_user, smtp_password, from_address,
             oauth_provider)
        VALUES (?,?,?,1,1,'imap.example.com',993,?,?,0,
                'smtp.example.com',587,'starttls',?,?,?,
                '')
    """, (account_id, owner, "Test", imap_user, _enc(password), imap_user, _enc(password), imap_user))
    conn.commit()
    return conn


# ── _read_accounts_from_db: OAuth columns are included ───────────────────────

def test_read_accounts_includes_oauth_provider_column():
    """_read_accounts_from_db must return oauth_provider in each row dict so
    _load_config can detect Google OAuth accounts."""
    db_conn = _make_sqlite_db_with_oauth_account()

    import mcp_servers.email_server as es
    with mock.patch.object(es, "_db_path") as mock_path:
        # Patch _db_path to return a sentinel; patch sqlite3.connect to return our connection
        mock_path.return_value = mock.MagicMock()
        mock_path.return_value.exists.return_value = True
        with mock.patch("mcp_servers.email_server.sqlite3.connect", return_value=db_conn):
            rows = es._read_accounts_from_db()

    assert rows, "should have returned at least one account row"
    row = rows[0]
    assert "oauth_provider" in row, "oauth_provider column missing from row"
    assert row["oauth_provider"] == "google"


def test_read_accounts_includes_oauth_token_columns():
    """All four OAuth columns must be present in the returned rows."""
    db_conn = _make_sqlite_db_with_oauth_account()

    import mcp_servers.email_server as es
    with mock.patch.object(es, "_db_path") as mock_path:
        mock_path.return_value = mock.MagicMock()
        mock_path.return_value.exists.return_value = True
        with mock.patch("mcp_servers.email_server.sqlite3.connect", return_value=db_conn):
            rows = es._read_accounts_from_db()

    row = rows[0]
    for col in ("oauth_provider", "oauth_access_token", "oauth_refresh_token", "oauth_token_expiry"):
        assert col in row, f"Column {col!r} missing from _read_accounts_from_db row"


def test_read_accounts_still_works_without_oauth_columns():
    """If the DB predates the oauth columns, _read_accounts_from_db must not
    crash — it should return empty strings for missing columns (backward compat)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE email_accounts (
            id TEXT PRIMARY KEY, owner TEXT, name TEXT,
            is_default INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
            imap_host TEXT, imap_port INTEGER DEFAULT 993,
            imap_user TEXT, imap_password TEXT, imap_starttls INTEGER DEFAULT 0,
            smtp_host TEXT, smtp_port INTEGER DEFAULT 465,
            smtp_security TEXT DEFAULT '', smtp_user TEXT, smtp_password TEXT,
            from_address TEXT, created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        INSERT INTO email_accounts
            (id, owner, name, is_default, enabled, imap_host, imap_port, imap_user,
             smtp_host, smtp_port, from_address)
        VALUES ('acct-old','alice','Old',1,1,'imap.example.com',993,'u@example.com',
                'smtp.example.com',465,'u@example.com')
    """)
    conn.commit()

    import mcp_servers.email_server as es
    with mock.patch.object(es, "_db_path") as mock_path:
        mock_path.return_value = mock.MagicMock()
        mock_path.return_value.exists.return_value = True
        with mock.patch("mcp_servers.email_server.sqlite3.connect", return_value=conn):
            rows = es._read_accounts_from_db()

    assert rows, "should still return the account row"
    row = rows[0]
    # Columns fall back to empty string via the PRAGMA guard
    assert row.get("oauth_provider", "") == ""


# ── _load_config: OAuth fields propagated into cfg ───────────────────────────

def test_load_config_populates_oauth_provider_for_google_account():
    """_load_config must copy oauth_provider from the DB row into the returned cfg."""
    db_conn = _make_sqlite_db_with_oauth_account()

    import mcp_servers.email_server as es
    with mock.patch.object(es, "_db_path") as mock_path, \
         mock.patch.object(es, "_ACCOUNT_CACHE", {}), \
         mock.patch.object(es, "_current_owner", return_value=None):
        mock_path.return_value = mock.MagicMock()
        mock_path.return_value.exists.return_value = True
        with mock.patch("mcp_servers.email_server.sqlite3.connect", return_value=db_conn):
            cfg = es._load_config("acct-1")

    assert cfg["oauth_provider"] == "google"


def test_load_config_populates_all_oauth_fields():
    """All four OAuth fields must be in cfg after _load_config."""
    db_conn = _make_sqlite_db_with_oauth_account()

    import mcp_servers.email_server as es
    with mock.patch.object(es, "_db_path") as mock_path, \
         mock.patch.object(es, "_ACCOUNT_CACHE", {}), \
         mock.patch.object(es, "_current_owner", return_value=None):
        mock_path.return_value = mock.MagicMock()
        mock_path.return_value.exists.return_value = True
        with mock.patch("mcp_servers.email_server.sqlite3.connect", return_value=db_conn):
            cfg = es._load_config("acct-1")

    for field in ("oauth_provider", "oauth_access_token", "oauth_refresh_token", "oauth_token_expiry"):
        assert field in cfg, f"cfg missing {field!r} after _load_config"
        assert cfg[field] != "" or field == "oauth_refresh_token"  # at minimum not missing


def test_load_config_oauth_provider_empty_for_password_account():
    """Password accounts must have oauth_provider == '' in cfg."""
    db_conn = _make_sqlite_db_password_account()

    import mcp_servers.email_server as es
    with mock.patch.object(es, "_db_path") as mock_path, \
         mock.patch.object(es, "_ACCOUNT_CACHE", {}), \
         mock.patch.object(es, "_current_owner", return_value=None):
        mock_path.return_value = mock.MagicMock()
        mock_path.return_value.exists.return_value = True
        with mock.patch("mcp_servers.email_server.sqlite3.connect", return_value=db_conn):
            cfg = es._load_config("acct-pw")

    assert cfg.get("oauth_provider", "") == ""


# ── _imap_connect: XOAUTH2 vs login ──────────────────────────────────────────

def test_mcp_imap_connect_uses_xoauth2_for_google_account():
    """Google OAuth accounts must use conn.authenticate('XOAUTH2') not conn.login()."""
    import mcp_servers.email_server as es
    from src.secret_storage import encrypt as _enc

    future_expiry = str(int(time.time()) + 7200)
    cfg = {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_starttls": False,
        "imap_ssl": True,
        "imap_user": "me@nia.law",
        "imap_password": "",
        "oauth_provider": "google",
        "account_id": "acct-1",
        "oauth_access_token": _enc("ya29.live_token"),
        "oauth_token_expiry": future_expiry,
    }

    mock_conn = mock.MagicMock()
    with mock.patch("mcp_servers.email_server.imaplib.IMAP4_SSL", return_value=mock_conn), \
         mock.patch("mcp_servers.email_server._load_config", return_value=cfg):
        es._imap_connect("acct-1")

    mock_conn.authenticate.assert_called_once()
    assert mock_conn.authenticate.call_args[0][0] == "XOAUTH2", \
        "IMAP auth method must be XOAUTH2 for Google OAuth accounts"
    mock_conn.login.assert_not_called()


def test_mcp_imap_connect_uses_login_for_password_account():
    """Password accounts must still use conn.login(), not XOAUTH2."""
    import mcp_servers.email_server as es

    cfg = {
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_starttls": False,
        "imap_ssl": True,
        "imap_user": "me@example.com",
        "imap_password": "app-password",
        "oauth_provider": "",
        "account_id": "acct-pw",
    }

    mock_conn = mock.MagicMock()
    with mock.patch("mcp_servers.email_server.imaplib.IMAP4_SSL", return_value=mock_conn), \
         mock.patch("mcp_servers.email_server._load_config", return_value=cfg):
        es._imap_connect("acct-pw")

    mock_conn.login.assert_called_once_with("me@example.com", "app-password")
    mock_conn.authenticate.assert_not_called()


def test_mcp_imap_connect_raises_when_token_unavailable():
    """If the OAuth token can't be fetched, _imap_connect must raise and close
    the socket — not silently connect without auth."""
    import mcp_servers.email_server as es

    cfg = {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_starttls": False,
        "imap_ssl": True,
        "imap_user": "me@nia.law",
        "imap_password": "",
        "oauth_provider": "google",
        "account_id": "acct-1",
        "oauth_access_token": "",
        "oauth_token_expiry": "",
        "oauth_refresh_token": "",
    }

    mock_conn = mock.MagicMock()
    with mock.patch("mcp_servers.email_server.imaplib.IMAP4_SSL", return_value=mock_conn), \
         mock.patch("mcp_servers.email_server._load_config", return_value=cfg), \
         mock.patch("routes.email_helpers._get_valid_google_token", return_value=None):
        with pytest.raises(RuntimeError, match="OAuth token unavailable"):
            es._imap_connect("acct-1")

    # Socket must be closed on failure
    mock_conn.shutdown.assert_called()
    mock_conn.login.assert_not_called()


# ── _smtp_connect: XOAUTH2 vs login ──────────────────────────────────────────

def test_mcp_smtp_connect_uses_xoauth2_for_google_account():
    """Google OAuth accounts must use smtp.auth('XOAUTH2'), not smtp.login()."""
    import mcp_servers.email_server as es
    from src.secret_storage import encrypt as _enc

    future_expiry = str(int(time.time()) + 7200)
    cfg = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": "me@nia.law",
        "smtp_password": "",
        "oauth_provider": "google",
        "account_id": "acct-1",
        "oauth_access_token": _enc("ya29.live_token"),
        "oauth_token_expiry": future_expiry,
        "from_address": "me@nia.law",
    }

    mock_smtp = mock.MagicMock()
    with mock.patch("mcp_servers.email_server.smtplib.SMTP", return_value=mock_smtp), \
         mock.patch("mcp_servers.email_server._load_config", return_value=cfg), \
         mock.patch("mcp_servers.email_server._smtp_ready", return_value=True):
        es._smtp_connect(cfg=cfg)

    mock_smtp.auth.assert_called_once()
    assert mock_smtp.auth.call_args[0][0] == "XOAUTH2", \
        "SMTP auth method must be XOAUTH2 for Google OAuth accounts"
    mock_smtp.login.assert_not_called()


def test_mcp_smtp_connect_uses_login_for_password_account():
    """Password accounts must still use smtp.login(), not XOAUTH2."""
    import mcp_servers.email_server as es

    cfg = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": "me@example.com",
        "smtp_password": "app-password",
        "oauth_provider": "",
        "account_id": "acct-pw",
        "from_address": "me@example.com",
    }

    mock_smtp = mock.MagicMock()
    with mock.patch("mcp_servers.email_server.smtplib.SMTP", return_value=mock_smtp), \
         mock.patch("mcp_servers.email_server._load_config", return_value=cfg), \
         mock.patch("mcp_servers.email_server._smtp_ready", return_value=True):
        es._smtp_connect(cfg=cfg)

    mock_smtp.login.assert_called_once_with("me@example.com", "app-password")
    mock_smtp.auth.assert_not_called()


def test_mcp_smtp_connect_raises_and_closes_when_token_unavailable():
    """Missing OAuth token must raise and close the socket — no partial connection."""
    import mcp_servers.email_server as es

    cfg = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": "me@nia.law",
        "smtp_password": "",
        "oauth_provider": "google",
        "account_id": "acct-1",
        "oauth_access_token": "",
        "oauth_token_expiry": "",
        "oauth_refresh_token": "",
        "from_address": "me@nia.law",
    }

    mock_smtp = mock.MagicMock()
    with mock.patch("mcp_servers.email_server.smtplib.SMTP", return_value=mock_smtp), \
         mock.patch("mcp_servers.email_server._load_config", return_value=cfg), \
         mock.patch("mcp_servers.email_server._smtp_ready", return_value=True), \
         mock.patch("routes.email_helpers._get_valid_google_token", return_value=None):
        with pytest.raises(RuntimeError, match="OAuth token unavailable"):
            es._smtp_connect(cfg=cfg)

    mock_smtp.close.assert_called()
    mock_smtp.login.assert_not_called()


# ── test_connection route: OAuth awareness ────────────────────────────────────

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
