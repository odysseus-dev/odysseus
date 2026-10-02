"""Microsoft (Outlook / Office 365) mail OAuth coverage.

Scopes, ID-token identity parsing, OAuth transport allowlists, refresh-token
rotation, and the XOAUTH2 wiring on the SMTP send path.
"""

import base64
import json

import pytest
from types import SimpleNamespace


def _jwt(claims):
    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none', 'typ': 'JWT'})}.{seg(claims)}.sig"


@pytest.fixture
def account_db(tmp_path, monkeypatch):
    from core import database as core_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounts.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    core_db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _make_account(factory, *, imap_user="info@craftale.it", provider="microsoft"):
    import uuid

    from core.database import EmailAccount
    from src.secret_storage import encrypt

    db = factory()
    try:
        row = EmailAccount(
            id=f"acc-{uuid.uuid4().hex[:12]}",
            owner="admin",
            name="Craftale",
            from_address=imap_user,
            imap_host="outlook.office365.com",
            imap_port=993,
            imap_starttls=False,
            imap_user=imap_user,
            oauth_provider=provider,
            oauth_refresh_token=encrypt("old-refresh"),
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


# --- Scopes -----------------------------------------------------------------


def test_scopes_contain_required_grants():
    from routes.email_helpers import MICROSOFT_OAUTH_SCOPES

    assert "https://outlook.office365.com/IMAP.AccessAsUser.All" in MICROSOFT_OAUTH_SCOPES
    assert "https://outlook.office365.com/SMTP.Send" in MICROSOFT_OAUTH_SCOPES
    assert "offline_access" in MICROSOFT_OAUTH_SCOPES
    # Identity claims ride along so the connect flow can verify the mailbox.
    assert "openid" in MICROSOFT_OAUTH_SCOPES and "email" in MICROSOFT_OAUTH_SCOPES


def test_tenant_defaults_to_common(monkeypatch):
    from routes.email_helpers import microsoft_oauth_tenant

    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT", raising=False)
    assert microsoft_oauth_tenant() == "common"
    monkeypatch.setenv("MICROSOFT_OAUTH_TENANT", "organizations")
    assert microsoft_oauth_tenant() == "organizations"


def test_configured_requires_client_id(monkeypatch):
    from routes.email_helpers import microsoft_oauth_configured

    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    assert microsoft_oauth_configured() is False
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "abc")
    assert microsoft_oauth_configured() is True


# --- Transport allowlists (routes module) -----------------------------------


def test_microsoft_transport_allowlists():
    from routes.email_routes import (
        _microsoft_oauth_imap_transport_allowed,
        _microsoft_oauth_smtp_transport_allowed,
    )

    assert _microsoft_oauth_imap_transport_allowed(993, False) is True
    assert _microsoft_oauth_imap_transport_allowed(143, True) is True
    assert _microsoft_oauth_imap_transport_allowed(993, True) is False
    assert _microsoft_oauth_imap_transport_allowed(587, False) is False
    # Exchange Online client submission is STARTTLS-only on 587.
    assert _microsoft_oauth_smtp_transport_allowed(587, "starttls") is True
    assert _microsoft_oauth_smtp_transport_allowed(465, "ssl") is False
    assert _microsoft_oauth_smtp_transport_allowed(587, "ssl") is False


def test_microsoft_oauth_hosts_are_pinned():
    from routes.email_routes import (
        _MICROSOFT_OAUTH_IMAP_HOSTS,
        _MICROSOFT_OAUTH_SMTP_HOSTS,
    )

    assert _MICROSOFT_OAUTH_IMAP_HOSTS == {"outlook.office365.com"}
    assert _MICROSOFT_OAUTH_SMTP_HOSTS == {"smtp.office365.com"}


# --- Refresh flow -----------------------------------------------------------


def test_refresh_rotates_tokens_and_persists(account_db, monkeypatch):
    import httpx
    from routes.email_helpers import _refresh_microsoft_token
    from core.database import EmailAccount
    from src.secret_storage import decrypt

    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "client-123")
    account_id = _make_account(account_db)

    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    token = _refresh_microsoft_token(account_id)

    assert token == "new-access"
    assert "login.microsoftonline.com/common/oauth2/v2.0/token" in captured["url"]
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "old-refresh"
    assert captured["data"]["scope"].startswith("https://outlook.office365.com/")

    db = account_db()
    try:
        row = db.get(EmailAccount, account_id)
        assert row.oauth_provider == "microsoft"
        assert decrypt(row.oauth_access_token) == "new-access"
        # v2 rotates refresh tokens — the new one must replace the old.
        assert decrypt(row.oauth_refresh_token) == "new-refresh"
    finally:
        db.close()


def test_refresh_ignores_non_microsoft_rows(account_db, monkeypatch):
    import httpx
    from routes.email_helpers import _refresh_microsoft_token

    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "client-123")
    account_id = _make_account(account_db, provider="google")

    def explode(*a, **kw):
        raise AssertionError("httpx must not be called for non-microsoft rows")

    monkeypatch.setattr(httpx, "post", explode)
    assert _refresh_microsoft_token(account_id) is None


def test_valid_token_uses_cache_without_refresh(account_db, monkeypatch):
    import httpx
    from routes.email_helpers import _get_valid_microsoft_token
    from src.secret_storage import encrypt

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no refresh expected")),
    )
    cfg = {
        "oauth_access_token": encrypt("cached-token"),
        "oauth_token_expiry": str(int(__import__("time").time()) + 600),
    }
    assert _get_valid_microsoft_token("any-account", cfg) == "cached-token"


# --- XOAUTH2 wiring on the send path ----------------------------------------


def test_smtp_send_authenticates_xoauth2_for_microsoft(monkeypatch):
    import smtplib
    from routes import email_helpers
    from routes.email_helpers import _send_smtp_message, _xoauth2_raw

    monkeypatch.setattr(
        email_helpers,
        "_get_valid_microsoft_token",
        lambda account_id, cfg: "ms-access-token",
    )

    calls = {"auth": None, "login": False, "starttls": False}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            calls["starttls"] = True

        def auth(self, mechanism, auth_cb, initial_response_ok=False):
            calls["auth"] = (mechanism, auth_cb)

        def login(self, user, password):
            calls["login"] = True

        def sendmail(self, from_addr, recipients, message):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    cfg = {
        "account_id": "acc-1",
        "oauth_provider": "microsoft",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_user": "info@craftale.it",
        "smtp_security": "starttls",
    }
    _send_smtp_message(cfg, "info@craftale.it", ["dest@example.com"], "body")

    assert calls["starttls"] is True
    assert calls["login"] is False
    mechanism, auth_cb = calls["auth"]
    assert mechanism == "XOAUTH2"
    assert auth_cb() == _xoauth2_raw("info@craftale.it", "ms-access-token")
