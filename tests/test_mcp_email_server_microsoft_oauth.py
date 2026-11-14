"""Microsoft OAuth through the agent's MCP email server.

The browser routes authenticate Microsoft accounts with XOAUTH2; the MCP
server is a separate, long-lived process with its own account projection and
IMAP/SMTP connect functions. These tests drive the real ``call_tool`` entry
point against a real ``email_accounts`` table, with fake mail transports and a
fake token endpoint, so a Microsoft account connected in Settings is proven to
work for the agent too: provider + tokens are projected, an expired token is
refreshed (and the rotated grant persisted), XOAUTH2 is used instead of LOGIN,
and the provider host/TLS pin is enforced before any socket is opened.
"""

import asyncio
import time
import unittest.mock as mock
from types import SimpleNamespace

import httpx
import pytest

from mcp_servers import email_server as es
# Imported before any test patches imaplib: it subclasses the real IMAP4 classes.
import routes.email_helpers  # noqa: F401


@pytest.fixture
def ms_account(tmp_path, monkeypatch):
    """A real email_accounts table (SQLAlchemy schema) shared by the MCP
    server's sqlite reader and the refresh helper's SessionLocal."""
    from core import database as core_db
    from core.database import EmailAccount
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from src.secret_storage import encrypt

    path = tmp_path / "app.db"
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    core_db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    monkeypatch.setattr(es, "APP_DB", str(path))
    monkeypatch.setattr(es, "_ACCOUNT_CACHE", {})
    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: False)
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "client-123")

    db = factory()
    try:
        db.add(EmailAccount(
            id="acc-ms",
            owner="admin",
            name="Craftale",
            is_default=True,
            enabled=True,
            from_address="info@craftale.it",
            imap_host="outlook.office365.com",
            imap_port=993,
            imap_starttls=False,
            imap_user="info@craftale.it",
            smtp_host="smtp.office365.com",
            smtp_port=587,
            smtp_security="starttls",
            smtp_user="info@craftale.it",
            oauth_provider="microsoft",
            oauth_access_token=encrypt("expired-access"),
            oauth_refresh_token=encrypt("old-refresh"),
            oauth_token_expiry=str(int(time.time()) - 120),
        ))
        db.commit()
    finally:
        db.close()

    def set_row(**fields):
        db = factory()
        try:
            row = db.get(EmailAccount, "acc-ms")
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()
        finally:
            db.close()

    def get_row():
        db = factory()
        try:
            return db.get(EmailAccount, "acc-ms")
        finally:
            db.close()

    yield SimpleNamespace(set=set_row, get=get_row)
    engine.dispose()


@pytest.fixture
def token_endpoint(monkeypatch):
    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append({"url": url, "data": data})
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "access_token": f"fresh-access-{len(calls)}",
                "refresh_token": f"rotated-refresh-{len(calls)}",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


class _FakeImap:
    """Mailbox transport recording how it was authenticated."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.sock = None
        self.auth = []
        self.logins = []
        self.closed = False

    def authenticate(self, mechanism, responder):
        self.auth.append((mechanism, responder(b"")))
        return "OK", [b"done"]

    def login(self, user, password):
        self.logins.append((user, password))
        return "OK", [b"done"]

    def starttls(self):
        pass

    def list(self, *args):
        return "OK", [b'(\\HasNoChildren \\Sent) "/" "Sent Items"']

    def select(self, *args, **kwargs):
        return "OK", [b"0"]

    def uid(self, *args):
        return "OK", [b""]

    def search(self, *args):
        return "OK", [b""]

    def append(self, *args):
        return "OK", [b""]

    def shutdown(self):
        self.closed = True

    def logout(self):
        self.closed = True


def _call(name, arguments):
    return asyncio.run(es.call_tool(name, {**arguments, es._MCP_OWNER_ARG: "admin"}))


def _imap_factory(monkeypatch):
    opened = []

    def factory(*args, **kwargs):
        conn = _FakeImap(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(es.imaplib, "IMAP4_SSL", factory)
    monkeypatch.setattr(es.imaplib, "IMAP4", factory)
    return opened


def test_projection_carries_provider_and_tokens(ms_account):
    from src.secret_storage import decrypt

    (row,) = es._read_accounts_from_db()
    assert row["oauth_provider"] == "microsoft"

    with mock.patch.object(es, "_current_owner", return_value="admin"):
        cfg = es._load_config("acc-ms")
    assert cfg["oauth_provider"] == "microsoft"
    assert decrypt(cfg["oauth_refresh_token"]) == "old-refresh"
    assert cfg["oauth_token_expiry"]
    # No password is stored for an OAuth row, yet it must count as sendable.
    assert not cfg["smtp_password"]
    assert es._smtp_ready(cfg)


def test_mcp_list_emails_refreshes_and_uses_xoauth2(ms_account, token_endpoint, monkeypatch):
    from src.secret_storage import decrypt

    opened = _imap_factory(monkeypatch)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    result = _call("list_emails", {"account": "acc-ms"})

    assert not result[0].text.startswith("Error"), result[0].text
    assert opened, "list_emails never opened a mailbox connection"
    conn = opened[0]
    assert conn.args[0] == "outlook.office365.com"
    assert conn.logins == [], "Microsoft accounts must never fall back to LOGIN"
    assert conn.auth == [(
        "XOAUTH2",
        b"user=info@craftale.it\x01auth=Bearer fresh-access-1\x01\x01",
    )]

    # The expired token was refreshed once with the stored grant, and the
    # rotated refresh token replaced it in the row the browser routes use.
    assert len(token_endpoint) == 1
    assert token_endpoint[0]["data"]["refresh_token"] == "old-refresh"
    row = ms_account.get()
    assert decrypt(row.oauth_access_token) == "fresh-access-1"
    assert decrypt(row.oauth_refresh_token) == "rotated-refresh-1"


def test_refreshed_token_is_reused_by_the_next_mcp_call(ms_account, token_endpoint, monkeypatch):
    opened = _imap_factory(monkeypatch)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    _call("list_emails", {"account": "acc-ms"})
    _call("list_emails", {"account": "acc-ms"})

    # A cached config would still carry the expired expiry and refresh on
    # every call; the second call must pick up the persisted token instead.
    assert len(token_endpoint) == 1
    tokens = [conn.auth[0][1] for conn in opened]
    assert tokens and all(b"Bearer fresh-access-1" in t for t in tokens)


def test_mcp_sees_a_browser_reconnect(ms_account, token_endpoint, monkeypatch):
    from src.secret_storage import encrypt

    opened = _imap_factory(monkeypatch)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    _call("list_emails", {"account": "acc-ms"})
    ms_account.set(
        oauth_access_token=encrypt("reconnected-access"),
        oauth_token_expiry=str(int(time.time()) + 3600),
    )
    _call("list_emails", {"account": "acc-ms"})

    assert b"Bearer reconnected-access" in opened[-1].auth[0][1]


def test_mcp_send_email_uses_xoauth2_over_starttls(ms_account, token_endpoint, monkeypatch):
    _imap_factory(monkeypatch)
    smtp = mock.MagicMock()
    smtp_cls = mock.Mock(return_value=smtp)
    monkeypatch.setattr(es.smtplib, "SMTP", smtp_cls)
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", mock.Mock(side_effect=AssertionError("implicit TLS is not allowed")))

    result = _call("send_email", {"account": "acc-ms", "to": "x@example.com", "subject": "Hi", "body": "Hello"})

    assert result[0].text.startswith("Sent email"), result[0].text
    assert smtp_cls.call_args[0][:2] == ("smtp.office365.com", 587)
    smtp.starttls.assert_called_once()
    smtp.login.assert_not_called()
    mechanism, responder = smtp.auth.call_args[0][:2]
    assert mechanism == "XOAUTH2"
    assert responder() == "user=info@craftale.it\x01auth=Bearer fresh-access-1\x01\x01"
    smtp.send_message.assert_called_once()


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"imap_host": "imap.attacker.example"}, "outlook.office365.com"),
        ({"imap_port": 143, "imap_starttls": False}, "TLS on port 993"),
    ],
)
def test_mcp_imap_refuses_unpinned_endpoint_before_connecting(ms_account, token_endpoint, monkeypatch, fields, message):
    opened = _imap_factory(monkeypatch)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})
    ms_account.set(**fields)

    result = _call("list_emails", {"account": "acc-ms"})

    assert message in result[0].text
    assert opened == [], "no socket may be opened to an unpinned endpoint"
    assert token_endpoint == [], "no token may be fetched for an unpinned endpoint"


@pytest.mark.parametrize(
    "fields",
    [
        {"smtp_host": "smtp.attacker.example"},
        {"smtp_port": 465, "smtp_security": "ssl"},
        {"smtp_security": "none"},
    ],
)
def test_mcp_smtp_refuses_unpinned_endpoint_before_connecting(ms_account, token_endpoint, monkeypatch, fields):
    _imap_factory(monkeypatch)
    smtp_cls = mock.Mock()
    monkeypatch.setattr(es.smtplib, "SMTP", smtp_cls)
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", smtp_cls)
    ms_account.set(**fields)

    result = _call("send_email", {"account": "acc-ms", "to": "x@example.com", "subject": "Hi", "body": "Hello"})

    assert "Microsoft OAuth SMTP requires" in result[0].text
    smtp_cls.assert_not_called()
    assert token_endpoint == []


def test_mcp_reports_a_dead_grant_instead_of_logging_in(ms_account, monkeypatch):
    opened = _imap_factory(monkeypatch)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    def failing_post(url, data=None, timeout=None):
        raise httpx.HTTPError("invalid_grant")

    monkeypatch.setattr(httpx, "post", failing_post)

    result = _call("list_emails", {"account": "acc-ms"})

    assert "Microsoft OAuth token unavailable" in result[0].text
    assert opened and opened[0].logins == [] and opened[0].closed


def test_password_accounts_still_log_in(ms_account, monkeypatch):
    from src.secret_storage import encrypt

    ms_account.set(
        oauth_provider="",
        imap_host="imap.example.com",
        imap_password=encrypt("app-password"),
    )
    opened = _imap_factory(monkeypatch)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})

    _call("list_emails", {"account": "acc-ms"})

    assert opened[0].logins == [("info@craftale.it", "app-password")]
    assert opened[0].auth == []
