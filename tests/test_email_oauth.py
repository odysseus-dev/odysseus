"""Tests for the Google OAuth2 email helpers.

Covers the security-critical surface added for Google Workspace / .edu
IMAP/SMTP support:

- `make_oauth_state` / `verify_oauth_state` — HMAC-signed OAuth state so the
  callback can't be CSRF'd or have its account_id/owner tampered with.
- `_smtp_ready` — an OAuth account (no stored password) must still count as
  send-capable; a host+user-only account without password or OAuth must not.
- `_xoauth2_raw` / `_xoauth2_bytes` — SASL XOAUTH2 framing for SMTP/IMAP.
- `_refresh_google_token` — token refresh stores result encrypted; failure is
  silent (no token/secret in logs or return value).
- `_get_valid_google_token` — uses cached token when fresh; calls refresh when
  expired.
- `google_oauth_callback` (real route) — invalid/tampered/missing state and
  provider errors return generic redirects with no PII; owner mismatch refuses
  the token write; a valid owner writes encrypted tokens only to the intended
  account.
- `list_email_accounts` (real route) — exposes OAuth status but never token
  values.
- `_imap_connect` — password accounts use login(); OAuth accounts use XOAUTH2.

Route tests pull the live endpoint out of `setup_email_routes()` and call it
directly — they pin the real handler, not a re-implementation. The ASGI app is
not booted; outbound HTTP is mocked and the DB is an isolated in-memory SQLite.
"""

import base64
import json
import time
import unittest.mock as mock

import pytest


# ── OAuth state signing ──────────────────────────────────────────

def test_oauth_state_round_trips_account_and_owner():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    state = make_oauth_state("acct-123", "user@example.com")
    payload = verify_oauth_state(state)

    assert payload is not None
    assert payload["a"] == "acct-123"
    assert payload["o"] == "user@example.com"
    assert payload["n"]  # nonce present


def test_oauth_state_nonce_is_unique_per_call():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    a = verify_oauth_state(make_oauth_state("acct", "o"))
    b = verify_oauth_state(make_oauth_state("acct", "o"))
    assert a["n"] != b["n"]


def test_oauth_state_rejects_tampered_account_id():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    state = make_oauth_state("acct-123", "user@example.com")
    decoded = base64.urlsafe_b64decode(state.encode()).decode()
    payload_str, sig = decoded.rsplit("|", 1)
    payload = json.loads(payload_str)
    payload["a"] = "evil-acct"  # attacker swaps the target account
    forged = base64.urlsafe_b64encode(
        (json.dumps(payload, separators=(",", ":")) + "|" + sig).encode()
    ).decode()

    assert verify_oauth_state(forged) is None


def test_oauth_state_rejects_forged_signature():
    from routes.email_helpers import make_oauth_state, verify_oauth_state

    state = make_oauth_state("acct-123", "user@example.com")
    decoded = base64.urlsafe_b64decode(state.encode()).decode()
    payload_str, _ = decoded.rsplit("|", 1)
    forged = base64.urlsafe_b64encode((payload_str + "|" + "deadbeef" * 8).encode()).decode()

    assert verify_oauth_state(forged) is None


@pytest.mark.parametrize("garbage", ["", "not-base64-at-all", "###", "a|b|c"])
def test_oauth_state_rejects_garbage(garbage):
    from routes.email_helpers import verify_oauth_state

    assert verify_oauth_state(garbage) is None


# ── _smtp_ready: OAuth accounts have no password but can still send ──

def test_smtp_ready_true_for_oauth_account_without_password():
    from routes.email_routes import _smtp_ready

    cfg = {
        "smtp_host": "smtp.gmail.com",
        "smtp_user": "me@nyu.edu",
        "smtp_password": "",
        "oauth_provider": "google",
    }
    assert _smtp_ready(cfg) is True


def test_smtp_ready_true_for_password_account():
    from routes.email_routes import _smtp_ready

    cfg = {
        "smtp_host": "smtp.example.com",
        "smtp_user": "me@example.com",
        "smtp_password": "app-password",
        "oauth_provider": "",
    }
    assert _smtp_ready(cfg) is True


def test_smtp_ready_false_without_password_or_oauth():
    from routes.email_routes import _smtp_ready

    cfg = {
        "smtp_host": "smtp.example.com",
        "smtp_user": "me@example.com",
        "smtp_password": "",
        "oauth_provider": "",
    }
    assert _smtp_ready(cfg) is False


def test_smtp_ready_false_without_host():
    from routes.email_routes import _smtp_ready

    cfg = {"smtp_host": "", "smtp_user": "me@x.com", "oauth_provider": "google"}
    assert _smtp_ready(cfg) is False


# ── XOAUTH2 SASL framing ─────────────────────────────────────────

def test_xoauth2_raw_is_unencoded_sasl_frame():
    from routes.email_helpers import _xoauth2_raw

    assert _xoauth2_raw("me@nyu.edu", "tok123") == "user=me@nyu.edu\x01auth=Bearer tok123\x01\x01"


def test_xoauth2_bytes_is_raw_frame_encoded():
    from routes.email_helpers import _xoauth2_bytes

    assert _xoauth2_bytes("me@nyu.edu", "tok123") == b"user=me@nyu.edu\x01auth=Bearer tok123\x01\x01"


# ── Helpers for in-memory DB fixtures ────────────────────────────

def _make_db():
    """Return (Session, SessionFactory) backed by an isolated in-memory SQLite DB.

    Used to test DB-touching helpers without the real database.
    The factory lets tests open a fresh session after the helper closes its own.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine)
    return Factory(), Factory


def _make_account(session, account_id="acct-1", owner="alice", **kwargs):
    """Insert a minimal EmailAccount row and return it."""
    from core.database import EmailAccount
    row = EmailAccount(
        id=account_id,
        owner=owner,
        name=kwargs.get("name", "Test"),
        from_address=kwargs.get("from_address", "test@example.com"),
        imap_host=kwargs.get("imap_host", "imap.gmail.com"),
        imap_port=kwargs.get("imap_port", 993),
        imap_user=kwargs.get("imap_user", "test@example.com"),
        smtp_host=kwargs.get("smtp_host", "smtp.gmail.com"),
        smtp_port=kwargs.get("smtp_port", 587),
        smtp_user=kwargs.get("smtp_user", "test@example.com"),
    )
    for k, v in kwargs.items():
        if hasattr(row, k):
            setattr(row, k, v)
    session.add(row)
    session.commit()
    return row


# ── Token encryption at rest ─────────────────────────────────────

def test_refresh_token_stored_encrypted_not_raw():
    """_refresh_google_token must encrypt the new access token before writing it
    to the DB — storing the raw token string would expose credentials at rest."""
    from src.secret_storage import encrypt as _enc, decrypt as _dec
    from core.database import EmailAccount

    raw_token = "ya29.test_access_token_raw"

    db, Factory = _make_db()
    _make_account(db, account_id="acct-r", owner="bob",
                  oauth_refresh_token=_enc("refresh-tok-xyz"))
    db.close()

    fake_resp = mock.MagicMock()
    fake_resp.raise_for_status = mock.MagicMock()
    fake_resp.json.return_value = {"access_token": raw_token, "expires_in": 3600}

    with mock.patch("httpx.post", return_value=fake_resp), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=lambda k, d="": {
             "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "csec"
         }.get(k, d)):
        from routes.email_helpers import _refresh_google_token
        result = _refresh_google_token("acct-r")

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-r").first()
    stored = row.oauth_access_token
    verify_db.close()

    assert result == raw_token, "function should return the plain access token to callers"
    assert stored != raw_token, "raw token must not be stored directly in the DB"
    assert _dec(stored) == raw_token, "stored value must decrypt back to the raw token"


def test_refresh_stores_encrypted_expiry_not_token():
    """oauth_token_expiry stores only a timestamp, never the token value."""
    from src.secret_storage import encrypt as _enc
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-e", owner="bob",
                  oauth_refresh_token=_enc("ref-tok"))
    db.close()

    fake_resp = mock.MagicMock()
    fake_resp.raise_for_status = mock.MagicMock()
    fake_resp.json.return_value = {"access_token": "ya29.secret", "expires_in": 3600}

    with mock.patch("httpx.post", return_value=fake_resp), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=lambda k, d="": {
             "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "csec"
         }.get(k, d)):
        from routes.email_helpers import _refresh_google_token
        _refresh_google_token("acct-e")

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-e").first()
    expiry = row.oauth_token_expiry
    verify_db.close()

    assert "ya29" not in (expiry or ""), \
        "token_expiry must be a timestamp, not the token string"


# ── Real OAuth callback route ─────────────────────────────────────
#
# These pull the actual google_oauth_callback endpoint out of the router and
# invoke it — they pin the real route's behaviour, not a re-implementation, so
# they fail if the ownership/state guards are ever removed or weakened.

def _callback_endpoint():
    """Return the live google_oauth_callback endpoint from the email router."""
    from routes.email_routes import setup_email_routes
    router = setup_email_routes()
    for route in router.routes:
        if route.path == "/api/email/oauth/google/callback" and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("google_oauth_callback route not found")


class _FakeRequest:
    """Minimal stand-in for starlette Request — the callback only reads headers."""
    headers = {"host": "localhost:7000"}


def _location(resp):
    """Pull the redirect target out of a RedirectResponse."""
    return resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_missing_code_returns_generic_error():
    """No `code` query param → generic error redirect, with no account id, owner,
    or state echoed back into the URL."""
    from routes.email_helpers import make_oauth_state

    callback = _callback_endpoint()
    state = make_oauth_state("acct-1", "alice")
    resp = await callback(code=None, state=state, error=None, request=_FakeRequest())

    loc = _location(resp)
    assert "email_oauth_error=missing_code" in loc
    assert "acct-1" not in loc, "account id must not appear in redirect URL"
    assert "alice" not in loc, "owner must not appear in redirect URL"


@pytest.mark.asyncio
async def test_callback_provider_error_returns_generic_error():
    """An `error` from Google → generic error redirect, no raw provider text."""
    callback = _callback_endpoint()
    resp = await callback(code=None, state=None, error="access_denied", request=_FakeRequest())

    loc = _location(resp)
    assert "email_oauth_error=google_error" in loc
    assert "access_denied" not in loc, "raw provider error must not leak into redirect"


@pytest.mark.asyncio
async def test_callback_tampered_state_returns_generic_error_no_leak():
    """Tampered/invalid state → invalid_state redirect; the auth code and any
    token must never appear in the redirect URL."""
    callback = _callback_endpoint()
    resp = await callback(code="4/secret-auth-code", state="not-a-valid-state",
                          error=None, request=_FakeRequest())

    loc = _location(resp)
    assert "email_oauth_error=invalid_state" in loc
    assert "4/secret-auth-code" not in loc, "auth code must not leak into redirect"
    assert "token" not in loc


@pytest.mark.asyncio
async def test_callback_owner_mismatch_does_not_write_tokens():
    """A signed, valid state whose owner does not match the target account's
    owner must NOT write tokens — this blocks one authenticated user from
    binding their Google account onto another user's mailbox row.
    """
    from routes.email_helpers import make_oauth_state
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-x", owner="alice")
    db.close()

    # Token-exchange + userinfo would succeed — the point is the ownership gate
    # rejects the write *before* trusting them.
    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {"access_token": "ya29.attacker", "refresh_token": "r", "expires_in": 3600}
    userinfo_resp = mock.MagicMock()
    userinfo_resp.is_success = True
    userinfo_resp.json.return_value = {"email": "bob@evil.com", "name": "Bob"}

    # State is genuinely signed, but for owner "bob" — not the row owner "alice".
    state = make_oauth_state("acct-x", "bob")

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("httpx.get", return_value=userinfo_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        callback = _callback_endpoint()
        resp = await callback(code="4/code", state=state, error=None, request=_FakeRequest())

    loc = _location(resp)
    assert "email_oauth_error=ownership_error" in loc

    verify_db = Factory()
    row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-x").first()
    token_after = row.oauth_access_token
    verify_db.close()
    assert token_after is None, "no token may be written when ownership check fails"


@pytest.mark.asyncio
async def test_callback_valid_owner_writes_encrypted_tokens_to_intended_account():
    """A signed state whose owner matches the target account writes the tokens —
    and only to that account, stored encrypted (raw token never persisted)."""
    from routes.email_helpers import make_oauth_state
    from src.secret_storage import decrypt as _dec
    from core.database import EmailAccount

    db, Factory = _make_db()
    _make_account(db, account_id="acct-v", owner="alice", imap_host="", smtp_host="")
    _make_account(db, account_id="acct-other", owner="alice")  # must stay untouched
    db.close()

    raw_access = "ya29.legit_access_token"
    raw_refresh = "1//legit_refresh_token"
    token_resp = mock.MagicMock()
    token_resp.raise_for_status = mock.MagicMock()
    token_resp.json.return_value = {"access_token": raw_access, "refresh_token": raw_refresh, "expires_in": 3600}
    userinfo_resp = mock.MagicMock()
    userinfo_resp.is_success = True
    userinfo_resp.json.return_value = {"email": "alice@nyu.edu", "name": "Alice"}

    state = make_oauth_state("acct-v", "alice")

    with mock.patch("httpx.post", return_value=token_resp), \
         mock.patch("httpx.get", return_value=userinfo_resp), \
         mock.patch("core.database.SessionLocal", Factory):
        callback = _callback_endpoint()
        resp = await callback(code="4/code", state=state, error=None, request=_FakeRequest())

    assert "email_oauth_success=1" in _location(resp)

    verify_db = Factory()
    target = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-v").first()
    other = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-other").first()
    verify_db.close()

    assert target.oauth_provider == "google"
    assert target.oauth_access_token != raw_access, "access token must be stored encrypted"
    assert _dec(target.oauth_access_token) == raw_access
    assert _dec(target.oauth_refresh_token) == raw_refresh
    assert other.oauth_access_token is None, "tokens must only touch the intended account"


# ── Token refresh scenarios ───────────────────────────────────────

def test_get_valid_google_token_uses_cached_when_fresh():
    """_get_valid_google_token must NOT call refresh when the stored token is
    still valid (expiry - 60s buffer > now). Refresh is an outbound HTTP call
    that should only happen when genuinely needed."""
    from src.secret_storage import encrypt as _enc
    from routes.email_helpers import _get_valid_google_token

    future_expiry = str(int(time.time()) + 7200)  # 2 hours from now
    cfg = {
        "account_id": "acct-fresh",
        "oauth_access_token": _enc("ya29.fresh_token"),
        "oauth_token_expiry": future_expiry,
    }

    with mock.patch("routes.email_helpers._refresh_google_token") as mock_refresh:
        result = _get_valid_google_token("acct-fresh", cfg)

    assert result == "ya29.fresh_token"
    mock_refresh.assert_not_called()


def test_get_valid_google_token_refreshes_when_expired():
    """_get_valid_google_token must call refresh when the token is expired."""
    from src.secret_storage import encrypt as _enc
    from routes.email_helpers import _get_valid_google_token

    past_expiry = str(int(time.time()) - 10)  # already expired
    cfg = {
        "account_id": "acct-exp",
        "oauth_access_token": _enc("ya29.old_token"),
        "oauth_token_expiry": past_expiry,
    }

    with mock.patch("routes.email_helpers._refresh_google_token", return_value="ya29.new_token") as mock_refresh:
        result = _get_valid_google_token("acct-exp", cfg)

    mock_refresh.assert_called_once_with("acct-exp")
    assert result == "ya29.new_token"


def test_refresh_failure_returns_none_no_secret_raised():
    """When the refresh HTTP call fails, _refresh_google_token must return None
    silently. It must not raise an exception or surface token/secret details."""
    from src.secret_storage import encrypt as _enc

    db, Factory = _make_db()
    _make_account(db, account_id="acct-fail", owner="dave",
                  oauth_refresh_token=_enc("ref-tok"))
    db.close()

    failing_resp = mock.MagicMock()
    failing_resp.raise_for_status.side_effect = Exception("401 Unauthorized")

    with mock.patch("httpx.post", return_value=failing_resp), \
         mock.patch("core.database.SessionLocal", Factory), \
         mock.patch("routes.email_helpers.os.environ.get", side_effect=lambda k, d="": {
             "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "csec"
         }.get(k, d)):
        from routes.email_helpers import _refresh_google_token
        result = _refresh_google_token("acct-fail")

    assert result is None, "failed refresh must return None, not raise"


def test_refresh_without_credentials_returns_none():
    """_refresh_google_token must return None immediately when the OAuth client
    credentials are not configured — no DB query, no HTTP call."""
    with mock.patch("routes.email_helpers.os.environ.get", return_value=""):
        from routes.email_helpers import _refresh_google_token
        result = _refresh_google_token("acct-any")

    assert result is None


# ── Password-account regression ───────────────────────────────────

def test_imap_connect_uses_login_for_password_accounts():
    """Existing password-auth IMAP accounts must still call conn.login() and
    must NOT trigger the XOAUTH2 authenticate path."""
    from routes.email_helpers import _imap_connect

    mock_conn = mock.MagicMock()
    # _imap_connect calls _get_email_config internally — mock it to return our cfg.
    cfg = {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_starttls": False,
        "imap_user": "me@gmail.com",
        "imap_password": "app-password-xyz",
        "oauth_provider": "",
        "account_id": "acct-pw",
    }

    with mock.patch("routes.email_helpers._open_imap_connection", return_value=mock_conn), \
         mock.patch("routes.email_helpers._get_email_config", return_value=cfg):
        _imap_connect("acct-pw", owner="alice")

    mock_conn.login.assert_called_once_with("me@gmail.com", "app-password-xyz")
    mock_conn.authenticate.assert_not_called()


def test_imap_connect_uses_xoauth2_for_oauth_accounts():
    """OAuth accounts must call conn.authenticate('XOAUTH2', ...) and must NOT
    call conn.login() — which would fail without a password."""
    from routes.email_helpers import _imap_connect
    from src.secret_storage import encrypt as _enc

    mock_conn = mock.MagicMock()
    future_expiry = str(int(time.time()) + 7200)
    cfg = {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_starttls": False,
        "imap_user": "me@nyu.edu",
        "imap_password": "",
        "oauth_provider": "google",
        "account_id": "acct-oauth",
        "oauth_access_token": _enc("ya29.live_token"),
        "oauth_token_expiry": future_expiry,
    }

    with mock.patch("routes.email_helpers._open_imap_connection", return_value=mock_conn), \
         mock.patch("routes.email_helpers._get_email_config", return_value=cfg):
        _imap_connect("acct-oauth", owner="alice")

    mock_conn.authenticate.assert_called_once()
    assert mock_conn.authenticate.call_args[0][0] == "XOAUTH2"
    mock_conn.login.assert_not_called()


@pytest.mark.asyncio
async def test_account_list_response_does_not_expose_token_values():
    """The /accounts list route is the client-facing account inventory. It must
    expose `oauth_provider` (so the UI can show OAuth status) but never the
    access/refresh token values, encrypted or otherwise — only boolean
    has_*_password flags and the provider name."""
    from routes.email_routes import setup_email_routes
    from src.secret_storage import encrypt as _enc

    raw_access = "ya29.super_secret_access_token"
    raw_refresh = "1//super_secret_refresh_token"

    db, Factory = _make_db()
    _make_account(db, account_id="acct-list", owner="alice",
                  oauth_provider="google",
                  oauth_access_token=_enc(raw_access),
                  oauth_refresh_token=_enc(raw_refresh))
    db.close()

    router = setup_email_routes()
    list_accounts = None
    for route in router.routes:
        if route.path == "/api/email/accounts" and "GET" in getattr(route, "methods", set()):
            list_accounts = route.endpoint
            break
    assert list_accounts is not None, "accounts list route not found"

    with mock.patch("core.database.SessionLocal", Factory):
        result = await list_accounts(owner="alice")

    blob = json.dumps(result)
    assert raw_access not in blob, "raw access token must not appear in list response"
    assert raw_refresh not in blob, "raw refresh token must not appear in list response"
    assert _enc(raw_access) not in blob, "encrypted token must not be sent to the client either"

    acct = result["accounts"][0]
    assert acct["oauth_provider"] == "google"   # status is exposed
    assert "oauth_access_token" not in acct      # token value is not
    assert "oauth_refresh_token" not in acct
