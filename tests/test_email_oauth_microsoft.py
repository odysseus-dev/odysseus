"""Tests for the Microsoft (Outlook / Office 365) OAuth2 email support.

Microsoft turned off basic authentication for Outlook/Microsoft 365 IMAP and
SMTP, so these accounts can only be added through OAuth. This covers the
security-critical surface of that flow:

- `_microsoft_oauth_tenant` — the tenant segment is validated before it is
  interpolated into the authority URL, so a bad env value can't repoint the
  flow at another host.
- `_refresh_microsoft_token` — stores the new access token encrypted, persists
  Microsoft's *rotated* refresh token, and fails silently (no secrets in logs
  or return values).
- `_get_valid_microsoft_token` / `_get_valid_oauth_token` — cached-while-fresh,
  refresh when expired, and provider dispatch that refuses unknown providers.
- `microsoft_oauth_callback` (real route) — missing/tampered state and provider
  errors return generic redirects; a token for a different mailbox is refused;
  a valid owner gets encrypted tokens written to the intended account only.
- `microsoft_oauth_authorize` (real route) — requires a configured client id
  and derives the redirect URI from the request scheme.
- `_imap_connect` / `_send_smtp_message` — Microsoft accounts authenticate with
  XOAUTH2, never login().

Route tests pull the live endpoint out of `setup_email_routes()` and call it
directly. The ASGI app is not booted; outbound HTTP is mocked and the DB is an
isolated in-memory SQLite.
"""

import base64
import json
import time
import unittest.mock as mock
from types import SimpleNamespace

import pytest


# ── Helpers ───────────────────────────────────────────────────────

def _make_db():
    """Return (Session, SessionFactory) backed by an isolated in-memory SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine)
    return Factory(), Factory


def _make_account(session, account_id="acct-1", owner="alice", **kwargs):
    """Insert a minimal Outlook-shaped EmailAccount row and return it."""
    from core.database import EmailAccount
    row = EmailAccount(
        id=account_id,
        owner=owner,
        name=kwargs.get("name", "Work"),
        from_address=kwargs.get("from_address", "alice@contoso.com"),
        imap_host=kwargs.get("imap_host", "outlook.office365.com"),
        imap_port=kwargs.get("imap_port", 993),
        imap_user=kwargs.get("imap_user", "alice@contoso.com"),
        imap_starttls=kwargs.get("imap_starttls", False),
        smtp_host=kwargs.get("smtp_host", "smtp.office365.com"),
        smtp_port=kwargs.get("smtp_port", 587),
        smtp_security=kwargs.get("smtp_security", "starttls"),
        smtp_user=kwargs.get("smtp_user", "alice@contoso.com"),
    )
    for k, v in kwargs.items():
        if hasattr(row, k):
            setattr(row, k, v)
    session.add(row)
    session.commit()
    return row


def _id_token(claims: dict) -> str:
    """Build an unsigned JWT-shaped id_token carrying `claims`."""
    def _seg(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{_seg({'alg': 'RS256'})}.{_seg(claims)}.signature"


class _FakeRequest:
    """Minimal stand-in for a starlette Request — the routes read the Host
    header and the request scheme."""

    def __init__(self, scheme="http", host="localhost:7000"):
        self.headers = {"host": host}
        self.url = SimpleNamespace(scheme=scheme)


def _location(resp):
    return resp.headers["location"]


def _route(path, method="GET"):
    """Return the live endpoint registered at `path`."""
    from routes.email_routes import setup_email_routes
    router = setup_email_routes()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _callback_endpoint():
    return _route("/api/email/oauth/microsoft/callback")


def _authorize_endpoint():
    return _route("/api/email/oauth/microsoft/authorize")


# ── Tenant validation ─────────────────────────────────────────────

@pytest.mark.parametrize("tenant", ["common", "organizations", "consumers",
                                    "contoso.onmicrosoft.com",
                                    "72f988bf-86f1-41af-91ab-2d7cd011db47"])
def test_tenant_accepts_real_tenant_forms(tenant, monkeypatch):
    from routes.email_helpers import _microsoft_oauth_tenant

    monkeypatch.setenv("MICROSOFT_OAUTH_TENANT_ID", tenant)
    assert _microsoft_oauth_tenant() == tenant


@pytest.mark.parametrize("tenant", [
    "evil.example.com/../../authorize",
    "common@evil.example.com",
    "https://evil.example.com",
    "common/oauth2",
    "..",
    " ",
])
def test_tenant_rejects_url_punctuation(tenant, monkeypatch):
    """A tenant value must never be able to repoint the authority URL."""
    from routes.email_helpers import _microsoft_oauth_tenant

    monkeypatch.setenv("MICROSOFT_OAUTH_TENANT_ID", tenant)
    assert _microsoft_oauth_tenant() == "common"


def test_tenant_defaults_to_common_when_unset(monkeypatch):
    from routes.email_helpers import _microsoft_oauth_tenant

    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)
    assert _microsoft_oauth_tenant() == "common"


def test_authority_urls_stay_on_microsoft_host(monkeypatch):
    from routes.email_helpers import (
        microsoft_oauth_authorize_url, microsoft_oauth_token_url,
    )

    monkeypatch.setenv("MICROSOFT_OAUTH_TENANT_ID", "https://evil.example.com")
    assert microsoft_oauth_authorize_url() == (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )
    assert microsoft_oauth_token_url() == (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )


# ── Token refresh ─────────────────────────────────────────────────

def _env(**overrides):
    base = {
        "MICROSOFT_OAUTH_CLIENT_ID": "cid",
        "MICROSOFT_OAUTH_CLIENT_SECRET": "csec",
    }
    base.update(overrides)
    return lambda k, d="": base.get(k, d)


def test_refresh_stores_access_token_encrypted():
    from src.secret_storage import encrypt as _enc, decrypt as _dec
    from core.database import EmailAccount

    raw_token = "eyJ0.microsoft_access_token"

    db, Factory = _make_db()
    _make_account(db, account_id="acct-r", owner="bob",
                  oauth_provider="microsoft",
                  oauth_refresh_token=_enc("refresh-tok-xyz"))
    db.close()

    resp = mock.MagicMock()
    resp.raise_for_status = mock.MagicMock()
    resp.json.return_value = {"access_token": raw_token, "expires_in": 3600}

    with mock.patch("httpx.post", return_value=resp), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=_env()):
        from routes.email_helpers import _refresh_microsoft_token
        result = _refresh_microsoft_token("acct-r")

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-r").first()
    stored, expiry = row.oauth_access_token, row.oauth_token_expiry
    verify_db.close()

    assert result == raw_token
    assert stored != raw_token, "raw token must not be stored directly"
    assert _dec(stored) == raw_token
    assert raw_token not in (expiry or ""), "expiry must be a timestamp, not a token"


def test_refresh_persists_microsofts_rotated_refresh_token():
    """Microsoft retires the sent refresh token and returns a new one. Dropping
    it would break the account on the *next* refresh, an hour later."""
    from src.secret_storage import encrypt as _enc, decrypt as _dec
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-rot", owner="bob",
                  oauth_provider="microsoft",
                  oauth_refresh_token=_enc("old-refresh"))
    db.close()

    resp = mock.MagicMock()
    resp.raise_for_status = mock.MagicMock()
    resp.json.return_value = {
        "access_token": "new-access",
        "refresh_token": "rotated-refresh",
        "expires_in": 3600,
    }

    with mock.patch("httpx.post", return_value=resp), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=_env()):
        from routes.email_helpers import _refresh_microsoft_token
        _refresh_microsoft_token("acct-rot")

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-rot").first()
    stored_refresh = row.oauth_refresh_token
    verify_db.close()

    assert _dec(stored_refresh) == "rotated-refresh"


def test_refresh_keeps_existing_token_when_none_is_returned():
    """A response without a rotated token must not blank the stored one."""
    from src.secret_storage import encrypt as _enc, decrypt as _dec
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-keep", owner="bob",
                  oauth_provider="microsoft",
                  oauth_refresh_token=_enc("still-valid"))
    db.close()

    resp = mock.MagicMock()
    resp.raise_for_status = mock.MagicMock()
    resp.json.return_value = {"access_token": "new-access", "expires_in": 3600}

    with mock.patch("httpx.post", return_value=resp), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=_env()):
        from routes.email_helpers import _refresh_microsoft_token
        _refresh_microsoft_token("acct-keep")

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-keep").first()
    stored_refresh = row.oauth_refresh_token
    verify_db.close()

    assert _dec(stored_refresh) == "still-valid"


def test_refresh_posts_to_the_microsoft_token_endpoint():
    from src.secret_storage import encrypt as _enc

    db, Factory = _make_db()
    _make_account(db, account_id="acct-url", owner="bob",
                  oauth_provider="microsoft",
                  oauth_refresh_token=_enc("refresh"))
    db.close()

    resp = mock.MagicMock()
    resp.raise_for_status = mock.MagicMock()
    resp.json.return_value = {"access_token": "tok", "expires_in": 3600}

    with mock.patch("httpx.post", return_value=resp) as post, \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=_env()):
        from routes.email_helpers import _refresh_microsoft_token
        _refresh_microsoft_token("acct-url")

    url = post.call_args[0][0]
    form = post.call_args[1]["data"]
    assert url.startswith("https://login.microsoftonline.com/")
    assert url.endswith("/oauth2/v2.0/token")
    assert form["grant_type"] == "refresh_token"
    assert "IMAP.AccessAsUser.All" in form["scope"]
    assert "SMTP.Send" in form["scope"]


def test_refresh_without_credentials_returns_none():
    from src.secret_storage import encrypt as _enc

    db, Factory = _make_db()
    _make_account(db, account_id="acct-nc", owner="bob",
                  oauth_provider="microsoft",
                  oauth_refresh_token=_enc("refresh"))
    db.close()

    with mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get",
                    side_effect=_env(MICROSOFT_OAUTH_CLIENT_ID="")), \
         mock.patch("httpx.post") as post:
        from routes.email_helpers import _refresh_microsoft_token
        assert _refresh_microsoft_token("acct-nc") is None
    post.assert_not_called()


def test_refresh_failure_returns_none_without_raising_secrets():
    from src.secret_storage import encrypt as _enc

    db, Factory = _make_db()
    _make_account(db, account_id="acct-f", owner="bob",
                  oauth_provider="microsoft",
                  oauth_refresh_token=_enc("super-secret-refresh"))
    db.close()

    with mock.patch("httpx.post", side_effect=RuntimeError("network down")), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=_env()):
        from routes.email_helpers import _refresh_microsoft_token
        assert _refresh_microsoft_token("acct-f") is None


# ── Token freshness + provider dispatch ───────────────────────────

def test_cached_token_is_reused_while_fresh():
    from src.secret_storage import encrypt as _enc
    from routes.email_helpers import _get_valid_microsoft_token

    cfg = {
        "oauth_access_token": _enc("cached-token"),
        "oauth_token_expiry": str(int(time.time()) + 3600),
    }
    with mock.patch("routes.email_helpers._refresh_microsoft_token") as refresh:
        assert _get_valid_microsoft_token("acct", cfg) == "cached-token"
    refresh.assert_not_called()


def test_expired_token_triggers_refresh():
    from src.secret_storage import encrypt as _enc
    from routes.email_helpers import _get_valid_microsoft_token

    cfg = {
        "oauth_access_token": _enc("stale-token"),
        "oauth_token_expiry": str(int(time.time()) - 10),
    }
    with mock.patch("routes.email_helpers._refresh_microsoft_token",
                    return_value="fresh-token") as refresh:
        assert _get_valid_microsoft_token("acct", cfg) == "fresh-token"
    refresh.assert_called_once_with("acct")


def test_oauth_dispatch_routes_each_provider_to_its_own_getter():
    from routes.email_helpers import _get_valid_oauth_token

    with mock.patch("routes.email_helpers._get_valid_microsoft_token",
                    return_value="ms-token") as ms, \
         mock.patch("routes.email_helpers._get_valid_google_token",
                    return_value="g-token") as goog:
        assert _get_valid_oauth_token("a", {"oauth_provider": "microsoft"}) == "ms-token"
        assert _get_valid_oauth_token("a", {"oauth_provider": "google"}) == "g-token"
    ms.assert_called_once()
    goog.assert_called_once()


@pytest.mark.parametrize("provider", ["", "yahoo", "MICROSOFT ", "graph"])
def test_oauth_dispatch_refuses_unknown_providers(provider):
    """An unrecognized provider yields no token, so callers surface
    "reconnect the account" instead of silently falling back to a password."""
    from routes.email_helpers import _get_valid_oauth_token

    assert _get_valid_oauth_token("a", {"oauth_provider": provider}) is None


# ── id_token identity extraction ──────────────────────────────────

@pytest.mark.parametrize("claims,expected", [
    ({"email": "alice@contoso.com"}, "alice@contoso.com"),
    ({"preferred_username": "alice@contoso.com"}, "alice@contoso.com"),
    ({"upn": "alice@contoso.com"}, "alice@contoso.com"),
    # `email` wins when several are present.
    ({"email": "alice@contoso.com", "upn": "other@contoso.com"}, "alice@contoso.com"),
    # A non-mailbox preferred_username is skipped in favour of a real address.
    ({"preferred_username": "alice", "upn": "alice@contoso.com"}, "alice@contoso.com"),
])
def test_identity_claims_are_read_from_the_id_token(claims, expected):
    from routes.email_routes import _microsoft_identity_from_id_token

    email_addr, _ = _microsoft_identity_from_id_token(_id_token(claims))
    assert email_addr == expected


def test_identity_reads_display_name():
    from routes.email_routes import _microsoft_identity_from_id_token

    _, name = _microsoft_identity_from_id_token(
        _id_token({"email": "alice@contoso.com", "name": "Alice Example"})
    )
    assert name == "Alice Example"


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.!!!.c",
                                   "a." + base64.urlsafe_b64encode(b"[]").decode() + ".c"])
def test_identity_is_empty_for_unusable_tokens(token):
    from routes.email_routes import _microsoft_identity_from_id_token

    assert _microsoft_identity_from_id_token(token) == ("", "")


# ── Callback route ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_callback_provider_error_surfaces_code_and_aadsts():
    """Microsoft's refusal must reach the operator: the OAuth2 code says what
    was refused, the AADSTS number says why. Both are fixed-vocabulary tokens."""
    resp = await _callback_endpoint()(
        code=None, state=None, error="access_denied",
        error_description="AADSTS65001: The user or administrator has not consented.",
        request=_FakeRequest(),
    )
    location = _location(resp)
    assert "email_oauth_error=microsoft_error" in location
    assert "email_oauth_provider=microsoft" in location
    assert "email_oauth_code=access_denied" in location
    assert "email_oauth_aadsts=AADSTS65001" in location
    # The provider's prose never rides along — only the two codes.
    assert "consented" not in location
    assert "administrator" not in location


@pytest.mark.asyncio
async def test_callback_missing_code_returns_generic_error():
    from routes.email_helpers import make_oauth_state

    resp = await _callback_endpoint()(
        code=None, state=make_oauth_state("acct-1", "alice"), error=None,
        request=_FakeRequest(),
    )
    location = _location(resp)
    assert "email_oauth_error=missing_code" in location
    assert "acct-1" not in location and "alice" not in location


@pytest.mark.asyncio
async def test_callback_tampered_state_is_refused_before_token_exchange():
    from routes.email_helpers import make_oauth_state

    state = make_oauth_state("acct-1", "alice")
    decoded = base64.urlsafe_b64decode(state.encode()).decode()
    payload_str, sig = decoded.rsplit("|", 1)
    payload = json.loads(payload_str)
    payload["a"] = "victim-acct"
    forged = base64.urlsafe_b64encode(
        (json.dumps(payload, separators=(",", ":")) + "|" + sig).encode()
    ).decode()

    with mock.patch("httpx.post") as post:
        resp = await _callback_endpoint()(
            code="code", state=forged, error=None, request=_FakeRequest()
        )

    assert "email_oauth_error=invalid_state" in _location(resp)
    # State is verified before any token exchange.
    post.assert_not_called()


@pytest.mark.asyncio
async def test_callback_without_refresh_token_is_refused():
    """No refresh token means `offline_access` was not granted and the mailbox
    would stop working an hour later — refuse rather than store it."""
    from routes.email_helpers import make_oauth_state
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-nr", owner="alice")
    db.close()

    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": "access-only",
        "expires_in": 3600,
        "id_token": _id_token({"email": "alice@contoso.com"}),
    }

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        resp = await _callback_endpoint()(
            code="code", state=make_oauth_state("acct-nr", "alice"),
            error=None, request=_FakeRequest(),
        )

    assert "email_oauth_error=missing_refresh_token" in _location(resp)

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-nr").first()
    provider = row.oauth_provider
    verify_db.close()
    assert not provider, "no partial credentials may be written"


@pytest.mark.asyncio
async def test_callback_owner_mismatch_does_not_write_tokens():
    from routes.email_helpers import make_oauth_state
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-victim", owner="victim",
                  imap_user="victim@contoso.com", smtp_user="victim@contoso.com")
    db.close()

    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": "attacker-access",
        "refresh_token": "attacker-refresh",
        "expires_in": 3600,
        "id_token": _id_token({"email": "attacker@contoso.com"}),
    }

    # A signed state the attacker legitimately owns, pointed at someone
    # else's account row.
    state = make_oauth_state("acct-victim", "attacker")

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        resp = await _callback_endpoint()(
            code="code", state=state, error=None, request=_FakeRequest()
        )

    assert "email_oauth_error=ownership_error" in _location(resp)

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-victim").first()
    token = row.oauth_access_token
    verify_db.close()
    assert not token, "tokens must never be written to another owner's account"


@pytest.mark.asyncio
async def test_callback_rejects_token_for_a_different_mailbox():
    """Authorizing a different mailbox than the row is configured for would
    pair the saved IMAP/SMTP usernames with another identity's credentials."""
    from routes.email_helpers import make_oauth_state
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-m", owner="alice",
                  imap_user="alice@contoso.com", smtp_user="alice@contoso.com")
    db.close()

    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": "other-access",
        "refresh_token": "other-refresh",
        "expires_in": 3600,
        "id_token": _id_token({"email": "someone.else@contoso.com"}),
    }

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        resp = await _callback_endpoint()(
            code="code", state=make_oauth_state("acct-m", "alice"),
            error=None, request=_FakeRequest(),
        )

    assert "email_oauth_error=identity_verification_failed" in _location(resp)

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-m").first()
    token = row.oauth_access_token
    verify_db.close()
    assert not token


@pytest.mark.asyncio
async def test_callback_without_verifiable_identity_is_refused():
    """An id_token that carries no mailbox address can't be checked against the
    configured account, so the connect must fail closed."""
    from routes.email_helpers import make_oauth_state
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-ni", owner="alice")
    db.close()

    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 3600,
        "id_token": _id_token({"sub": "no-mailbox-claim"}),
    }

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        resp = await _callback_endpoint()(
            code="code", state=make_oauth_state("acct-ni", "alice"),
            error=None, request=_FakeRequest(),
        )

    assert "email_oauth_error=identity_verification_failed" in _location(resp)

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-ni").first()
    token = row.oauth_access_token
    verify_db.close()
    assert not token


@pytest.mark.asyncio
async def test_callback_writes_encrypted_tokens_and_office365_defaults():
    from routes.email_helpers import make_oauth_state
    from src.secret_storage import decrypt as _dec
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(
        db, account_id="acct-ok", owner="alice",
        imap_host="", smtp_host="",
        imap_user="alice@contoso.com", smtp_user="ALICE@CONTOSO.COM",
        display_name="",
    )
    _make_account(db, account_id="acct-other", owner="alice")  # must stay untouched
    db.close()

    raw_access = "eyJ0.access"
    raw_refresh = "0.AR8A.refresh"
    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": raw_access,
        "refresh_token": raw_refresh,
        "expires_in": 3600,
        "id_token": _id_token({"email": "alice@contoso.com", "name": "Alice"}),
    }

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        resp = await _callback_endpoint()(
            code="code", state=make_oauth_state("acct-ok", "alice"),
            error=None, request=_FakeRequest(),
        )

    assert "email_oauth_success=1" in _location(resp)

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-ok").first()
    other = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-other").first()
    fields = {
        "provider": row.oauth_provider,
        "access": row.oauth_access_token,
        "refresh": row.oauth_refresh_token,
        "expiry": row.oauth_token_expiry,
        "imap_host": row.imap_host,
        "imap_port": row.imap_port,
        "imap_starttls": row.imap_starttls,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_security": row.smtp_security,
        "display_name": row.display_name,
    }
    other_token = other.oauth_access_token
    verify_db.close()

    assert fields["provider"] == "microsoft"
    assert fields["access"] != raw_access, "access token must be stored encrypted"
    assert _dec(fields["access"]) == raw_access
    assert _dec(fields["refresh"]) == raw_refresh
    assert raw_access not in (fields["expiry"] or "")
    # Office 365 transport defaults are filled in for an otherwise blank row.
    assert fields["imap_host"] == "outlook.office365.com"
    assert fields["imap_port"] == 993
    assert fields["imap_starttls"] is False
    assert fields["smtp_host"] == "smtp.office365.com"
    assert fields["smtp_port"] == 587
    assert fields["smtp_security"] == "starttls", \
        "Microsoft 365 SMTP is STARTTLS on 587 — an implicit-TLS default cannot connect"
    assert fields["display_name"] == "Alice"
    assert not other_token, "tokens must only touch the intended account"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_callback_redirect_uri_follows_the_request_scheme(scheme, monkeypatch):
    """Microsoft rejects the exchange unless redirect_uri matches the authorize
    step byte for byte, so it has to track the scheme behind a TLS front."""
    from routes.email_helpers import make_oauth_state

    monkeypatch.delenv("MICROSOFT_OAUTH_REDIRECT_URI", raising=False)
    db, Factory = _make_db()
    _make_account(db, account_id="acct-s", owner="alice")
    db.close()

    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": "a", "refresh_token": "r", "expires_in": 3600,
        "id_token": _id_token({"email": "alice@contoso.com"}),
    }

    with mock.patch("httpx.post", return_value=token_resp) as post, \
         mock.patch("core.database.SessionLocal", Factory):
        await _callback_endpoint()(
            code="code", state=make_oauth_state("acct-s", "alice"), error=None,
            request=_FakeRequest(scheme=scheme, host="mail.example.com"),
        )

    assert post.call_args[1]["data"]["redirect_uri"] == (
        f"{scheme}://mail.example.com/api/email/oauth/microsoft/callback"
    )


@pytest.mark.asyncio
async def test_callback_redirect_uri_env_override_wins(monkeypatch):
    from routes.email_helpers import make_oauth_state

    pinned = "https://pinned.example.com/api/email/oauth/microsoft/callback"
    monkeypatch.setenv("MICROSOFT_OAUTH_REDIRECT_URI", pinned)
    db, Factory = _make_db()
    _make_account(db, account_id="acct-p", owner="alice")
    db.close()

    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {
        "access_token": "a", "refresh_token": "r", "expires_in": 3600,
        "id_token": _id_token({"email": "alice@contoso.com"}),
    }

    with mock.patch("httpx.post", return_value=token_resp) as post, \
         mock.patch("core.database.SessionLocal", Factory):
        await _callback_endpoint()(
            code="code", state=make_oauth_state("acct-p", "alice"), error=None,
            request=_FakeRequest(scheme="http", host="localhost:7000"),
        )

    assert post.call_args[1]["data"]["redirect_uri"] == pinned


# ── Authorize route ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_requires_a_configured_client_id(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    with mock.patch("routes.email_routes._assert_owns_account"):
        with pytest.raises(HTTPException) as exc:
            await _authorize_endpoint()(
                account_id="acct-1", request=_FakeRequest(), owner="alice"
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_authorize_checks_account_ownership(monkeypatch):
    """Ownership is asserted before anything else, so a signed state can never
    be minted for someone else's account."""
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "cid")
    with mock.patch("routes.email_routes._assert_owns_account",
                    side_effect=AssertionError("not yours")) as guard:
        with pytest.raises(AssertionError):
            await _authorize_endpoint()(
                account_id="acct-x", request=_FakeRequest(), owner="alice"
            )
    guard.assert_called_once_with("acct-x", "alice")


@pytest.mark.asyncio
async def test_authorize_redirects_to_microsoft_with_mail_scopes(monkeypatch):
    from urllib.parse import urlparse, parse_qs
    from routes.email_helpers import verify_oauth_state

    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "cid")
    monkeypatch.delenv("MICROSOFT_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    with mock.patch("routes.email_routes._assert_owns_account"):
        resp = await _authorize_endpoint()(
            account_id="acct-1",
            request=_FakeRequest(scheme="https", host="mail.example.com"),
            owner="alice",
        )

    target = urlparse(_location(resp))
    params = parse_qs(target.query)
    assert target.hostname == "login.microsoftonline.com"
    assert target.path == "/common/oauth2/v2.0/authorize"
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == [
        "https://mail.example.com/api/email/oauth/microsoft/callback"
    ]
    scope = params["scope"][0]
    assert "offline_access" in scope, "a refresh token is required to stay connected"
    assert "https://outlook.office.com/IMAP.AccessAsUser.All" in scope
    assert "https://outlook.office.com/SMTP.Send" in scope
    # State must be signed and carry the account + owner it was minted for.
    payload = verify_oauth_state(params["state"][0])
    assert payload["a"] == "acct-1" and payload["o"] == "alice"


# ── IMAP / SMTP use XOAUTH2 for Microsoft accounts ────────────────

def test_imap_connect_uses_xoauth2_for_microsoft_accounts():
    from routes import email_helpers

    cfg = {
        "account_id": "acct-ms",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_user": "alice@contoso.com",
        "imap_password": "",
        "imap_starttls": False,
        "oauth_provider": "microsoft",
    }
    conn = mock.MagicMock()

    with mock.patch.object(email_helpers, "_get_email_config", return_value=cfg), \
         mock.patch.object(email_helpers, "_open_imap_connection", return_value=conn), \
         mock.patch.object(email_helpers, "_get_valid_oauth_token", return_value="ms-token"):
        email_helpers._imap_connect("acct-ms", owner="alice")

    conn.login.assert_not_called()
    conn.authenticate.assert_called_once()
    assert conn.authenticate.call_args[0][0] == "XOAUTH2"
    # The SASL callback must produce the raw (unencoded) XOAUTH2 frame.
    assert conn.authenticate.call_args[0][1](b"") == (
        b"user=alice@contoso.com\x01auth=Bearer ms-token\x01\x01"
    )


def test_imap_connect_reports_reconnect_when_token_is_unavailable():
    from routes import email_helpers

    cfg = {
        "account_id": "acct-ms",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_user": "alice@contoso.com",
        "imap_starttls": False,
        "oauth_provider": "microsoft",
    }
    conn = mock.MagicMock()

    with mock.patch.object(email_helpers, "_get_email_config", return_value=cfg), \
         mock.patch.object(email_helpers, "_open_imap_connection", return_value=conn), \
         mock.patch.object(email_helpers, "_get_valid_oauth_token", return_value=None):
        with pytest.raises(RuntimeError, match="Microsoft"):
            email_helpers._imap_connect("acct-ms", owner="alice")

    conn.login.assert_not_called()
    # A failed auth must not orphan the already-connected socket.
    conn.shutdown.assert_called_once()


def test_smtp_send_uses_xoauth2_for_microsoft_accounts():
    from routes import email_helpers

    cfg = {
        "account_id": "acct-ms",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": "alice@contoso.com",
        "smtp_password": "",
        "oauth_provider": "microsoft",
    }
    smtp = mock.MagicMock()
    smtp.__enter__ = mock.MagicMock(return_value=smtp)
    smtp.__exit__ = mock.MagicMock(return_value=False)

    with mock.patch.object(email_helpers.smtplib, "SMTP", return_value=smtp), \
         mock.patch.object(email_helpers, "_get_valid_oauth_token", return_value="ms-token"):
        email_helpers._send_smtp_message(
            cfg, "alice@contoso.com", ["bob@example.com"], "Subject: hi\r\n\r\nbody"
        )

    smtp.starttls.assert_called_once()
    smtp.login.assert_not_called()
    smtp.auth.assert_called_once()
    assert smtp.auth.call_args[0][0] == "XOAUTH2"
    assert smtp.auth.call_args[0][1]() == (
        "user=alice@contoso.com\x01auth=Bearer ms-token\x01\x01"
    )
    smtp.sendmail.assert_called_once()


# ── Diagnostics are surfaced, but only in bounded shapes ──────────
#
# The callback is an unauthenticated endpoint: anyone can craft a request to it
# with any `error` / `error_description`. Those strings reach a server log line
# and the browser's URL, so the sanitizer is the only thing standing between a
# crafted callback and log injection or a forged message in the UI.

@pytest.mark.parametrize("hostile", [
    "<script>alert(1)</script>",
    "access denied",                       # space is not in the vocabulary
    "Access_Denied",                       # uppercase is not either
    "error\nWARNING: fake log line",       # log injection via newline
    "error\r\nSet-Cookie: a=b",            # header/response splitting shape
    "user@example.com",                    # a mailbox address must never ride along
    "a" * 41,                              # longer than the bounded length
    "../../etc/passwd",
    "&email_oauth_error=forged",           # parameter smuggling
])
def test_hostile_error_codes_are_dropped_entirely(hostile):
    from routes.email_routes import _oauth_failure_hints

    code, aadsts = _oauth_failure_hints(hostile, None)
    assert code == "", f"{hostile!r} must not pass the allow-list"
    assert aadsts == ""


@pytest.mark.parametrize("code", [
    "access_denied", "consent_required", "invalid_scope",
    "interaction_required", "invalid_client", "a", "a" * 40,
])
def test_real_oauth_error_codes_pass_through(code):
    from routes.email_routes import _oauth_failure_hints

    assert _oauth_failure_hints(code, None)[0] == code


@pytest.mark.parametrize("description,expected", [
    ("AADSTS65001: The user or administrator has not consented", "AADSTS65001"),
    ("Something AADSTS7000215 something", "AADSTS7000215"),
    ("no code here at all", ""),
    ("AADSTS12: too short to be a real code", ""),
    ("AADSTS123456789: too long", "AADSTS1234567"),
])
def test_aadsts_number_is_extracted_from_free_text(description, expected):
    """Only the AADSTS token is lifted out; the surrounding prose is discarded."""
    from routes.email_routes import _oauth_failure_hints

    assert _oauth_failure_hints(None, description)[1] == expected


def test_hostile_description_cannot_smuggle_text_alongside_an_aadsts_code():
    from routes.email_routes import _oauth_failure_hints

    code, aadsts = _oauth_failure_hints(
        None, "AADSTS65001 <img src=x onerror=alert(1)> user@example.com"
    )
    assert code == ""
    assert aadsts == "AADSTS65001", "only the bounded token survives"


def test_redirect_carries_no_unsanitized_provider_text():
    """End to end: a hostile callback produces a redirect with nothing in it
    beyond Odysseus's own codes."""
    from routes.email_routes import _oauth_failure_hints, _oauth_result_redirect

    code, aadsts = _oauth_failure_hints(
        "<script>x</script>", "totally free text with user@example.com"
    )
    location = _oauth_result_redirect("microsoft_error", "microsoft", code, aadsts)

    assert location == "/?section=integrations&email_oauth_error=microsoft_error&email_oauth_provider=microsoft"
    assert "script" not in location
    assert "@" not in location
