"""Microsoft (Outlook / Office 365) mail OAuth coverage.

Scopes (against Microsoft's documented contract), OAuth host/transport policy
on every credential-bearing path, refresh-token rotation, the owner-scoped
device-code routes, and the XOAUTH2 wiring on the SMTP send path.
"""

import base64
import json

import httpx
import pytest
from types import SimpleNamespace

_REAL_ASYNC_CLIENT = httpx.AsyncClient


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


def _make_account(factory, *, imap_user="info@craftale.it", provider="microsoft", owner="admin"):
    import uuid

    from core.database import EmailAccount
    from src.secret_storage import encrypt

    db = factory()
    try:
        row = EmailAccount(
            id=f"acc-{uuid.uuid4().hex[:12]}",
            owner=owner,
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


# Microsoft documents the IMAP/SMTP OAuth scopes under the outlook.office.com
# resource: https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth
_DOCUMENTED_SCOPES = {
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
}


def test_scopes_contain_required_grants():
    from routes.email_helpers import MICROSOFT_OAUTH_SCOPES

    scopes = set(MICROSOFT_OAUTH_SCOPES.split())
    assert _DOCUMENTED_SCOPES <= scopes
    # The outlook.office365.com resource is not the documented one.
    assert not any("outlook.office365.com" in s for s in scopes)
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


# --- Transport allowlists ---------------------------------------------------


def test_microsoft_transport_allowlists():
    from routes.email_helpers import (
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
    from routes.email_helpers import (
        MICROSOFT_OAUTH_IMAP_HOSTS,
        MICROSOFT_OAUTH_SMTP_HOSTS,
    )

    assert MICROSOFT_OAUTH_IMAP_HOSTS == {"outlook.office365.com"}
    # smtp-mail.outlook.com is the submission host for personal accounts,
    # which the default 'common' tenant admits.
    assert MICROSOFT_OAUTH_SMTP_HOSTS == {"smtp.office365.com", "smtp-mail.outlook.com"}


@pytest.mark.parametrize(
    "host, port, starttls",
    [
        ("evil.example.com", 993, False),       # wrong host
        ("outlook.office365.com.evil.io", 993, False),
        ("outlook.office365.com", 143, False),  # plaintext IMAP
        ("outlook.office365.com", 993, True),
    ],
)
def test_imap_connect_rejects_bad_microsoft_endpoint_before_token(monkeypatch, host, port, starttls):
    from routes import email_helpers

    cfg = {
        "account_id": "acc-1",
        "oauth_provider": "microsoft",
        "imap_host": host,
        "imap_port": port,
        "imap_starttls": starttls,
        "imap_user": "me@contoso.com",
    }
    monkeypatch.setattr(email_helpers, "_get_email_config", lambda *a, **kw: dict(cfg))

    def no_token(*a, **kw):
        raise AssertionError("token must not be fetched for a disallowed endpoint")

    def no_connect(*a, **kw):
        raise AssertionError("must not connect to a disallowed endpoint")

    monkeypatch.setattr(email_helpers, "_get_valid_microsoft_token", no_token)
    monkeypatch.setattr(email_helpers, "_open_imap_connection", no_connect)
    with pytest.raises(email_helpers.OAuthTransportPolicyError):
        email_helpers._imap_connect("acc-1")


@pytest.mark.parametrize(
    "host, port, security",
    [
        ("evil.example.com", 587, "starttls"),
        ("smtp.office365.com", 25, "none"),
        ("smtp.office365.com", 587, "none"),
        ("smtp.office365.com", 465, "ssl"),
    ],
)
def test_smtp_send_rejects_bad_microsoft_endpoint_before_token(monkeypatch, host, port, security):
    from routes import email_helpers

    def no_token(*a, **kw):
        raise AssertionError("token must not be fetched for a disallowed endpoint")

    def no_connect(*a, **kw):
        raise AssertionError("must not connect to a disallowed endpoint")

    monkeypatch.setattr(email_helpers, "_get_valid_microsoft_token", no_token)
    monkeypatch.setattr(email_helpers, "_PolicySMTP", no_connect)
    monkeypatch.setattr(email_helpers, "_PolicySMTP_SSL", no_connect)
    cfg = {
        "account_id": "acc-1",
        "oauth_provider": "microsoft",
        "smtp_host": host,
        "smtp_port": port,
        "smtp_user": "me@contoso.com",
        "smtp_security": security,
    }
    with pytest.raises(email_helpers.OAuthTransportPolicyError):
        email_helpers._send_smtp_message(cfg, "me@contoso.com", ["x@example.com"], "body")


def test_password_accounts_skip_microsoft_policy():
    from routes.email_helpers import microsoft_oauth_smtp_policy_error

    # The policy is only consulted for oauth_provider == "microsoft"; it must
    # still accept the documented endpoints.
    assert microsoft_oauth_smtp_policy_error("smtp.office365.com", 587, "starttls") is None
    assert microsoft_oauth_smtp_policy_error("SMTP-MAIL.outlook.com.", 587, "starttls") is None


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
    assert _DOCUMENTED_SCOPES <= set(captured["data"]["scope"].split())

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
    from routes import email_helpers
    from routes.email_helpers import _send_smtp_message, _xoauth2_raw

    monkeypatch.setattr(
        email_helpers,
        "_get_valid_microsoft_token",
        lambda account_id, cfg: "ms-access-token",
    )

    calls = {"auth": None, "login": False, "starttls": False}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, block_private=None):
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

    # _send_smtp_message dials through the policy-checked subclass.
    monkeypatch.setattr(email_helpers, "_PolicySMTP", FakeSMTP)
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


# --- Device-code routes (owner-scoped, non-blocking) ------------------------
#
# These boot the real email router in a minimal FastAPI app. A tiny middleware
# stamps request.state.current_user from a test header, as the auth middleware
# does in production; nobody here is an admin.

_DEVICE = "/api/email/oauth/microsoft/device"


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; replies from a per-test script."""

    script: dict = {}
    calls: list = []
    timeouts: list = []
    gate = None

    def __init__(self, *args, timeout=None, **kwargs):
        type(self).timeouts.append(timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, **kwargs):
        import httpx

        type(self).calls.append((url, dict(data or {})))
        if type(self).gate is not None:
            await type(self).gate.wait()
        kind = "devicecode" if url.endswith("/devicecode") else "token"
        status, payload = type(self).script[kind]
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


@pytest.fixture
def device_app(account_db, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes.email_routes import setup_email_routes

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "client-123")
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT", raising=False)

    fake = type("FakeAsyncClient", (_FakeAsyncClient,), {
        "script": {
            "devicecode": (200, {
                "device_code": "dev-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "interval": 1,
                "expires_in": 900,
            }),
            "token": (400, {"error": "authorization_pending"}),
        },
        "calls": [],
        "timeouts": [],
        "gate": None,
    })
    monkeypatch.setattr(httpx, "AsyncClient", fake)

    def blocking_post(*a, **kw):
        raise AssertionError("device-flow handlers must not use blocking httpx.post")

    monkeypatch.setattr(httpx, "post", blocking_post)

    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: False)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(setup_email_routes())
    return SimpleNamespace(app=app, client=TestClient(app), fake=fake, db=account_db)


def _as(user):
    return {"x-test-user": user} if user else {}


def test_device_start_is_open_to_the_non_admin_account_owner(device_app):
    account_id = _make_account(device_app.db, owner="alice")

    r = device_app.client.post(f"{_DEVICE}/start", data={"account_id": account_id}, headers=_as("alice"))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["poll_id"] and body["user_code"] == "ABCD-EFGH"
    url, data = device_app.fake.calls[0]
    assert url == "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
    assert _DOCUMENTED_SCOPES <= set(data["scope"].split())
    # Every Microsoft call made from the event loop carries a bounded timeout.
    assert device_app.fake.timeouts and all(t and t <= 10 for t in device_app.fake.timeouts)


def test_device_start_requires_authentication(device_app):
    account_id = _make_account(device_app.db, owner="alice")
    r = device_app.client.post(f"{_DEVICE}/start", data={"account_id": account_id})
    assert r.status_code == 401
    assert device_app.fake.calls == []


def test_device_start_refuses_another_users_account(device_app):
    account_id = _make_account(device_app.db, owner="alice")
    r = device_app.client.post(f"{_DEVICE}/start", data={"account_id": account_id}, headers=_as("bob"))
    assert r.status_code == 404
    assert device_app.fake.calls == []


def test_device_poll_and_cancel_are_bound_to_the_initiator(device_app):
    account_id = _make_account(device_app.db, owner="alice")
    poll_id = device_app.client.post(
        f"{_DEVICE}/start", data={"account_id": account_id}, headers=_as("alice"),
    ).json()["poll_id"]

    assert device_app.client.post(f"{_DEVICE}/poll", data={"poll_id": poll_id}, headers=_as("bob")).status_code == 404
    assert device_app.client.post(f"{_DEVICE}/cancel", data={"poll_id": poll_id}, headers=_as("bob")).status_code == 404

    # Bob's attempts neither consumed nor cancelled Alice's flow.
    r = device_app.client.post(f"{_DEVICE}/poll", data={"poll_id": poll_id}, headers=_as("alice"))
    assert r.status_code == 200 and r.json()["status"] == "pending"


def test_device_poll_stores_tokens_for_the_matching_mailbox(device_app):
    from core.database import EmailAccount
    from src.secret_storage import decrypt

    account_id = _make_account(device_app.db, owner="alice", imap_user="alice@contoso.com")
    poll_id = device_app.client.post(
        f"{_DEVICE}/start", data={"account_id": account_id}, headers=_as("alice"),
    ).json()["poll_id"]
    device_app.fake.script["token"] = (200, {
        "access_token": "ms-access",
        "refresh_token": "ms-refresh",
        "expires_in": 3600,
        # UPN is what's trusted; a spoofable `email` claim must not win.
        "id_token": _jwt({"preferred_username": "Alice@Contoso.com", "email": "victim@example.com"}),
    })

    r = device_app.client.post(f"{_DEVICE}/poll", data={"poll_id": poll_id}, headers=_as("alice"))

    assert r.json() == {"status": "authorized", "endpoint": {"account_id": account_id, "email": "Alice@Contoso.com"}}
    url, data = device_app.fake.calls[-1]
    assert url.endswith("/common/oauth2/v2.0/token")
    assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert _DOCUMENTED_SCOPES <= set(data["scope"].split())
    db = device_app.db()
    try:
        row = db.get(EmailAccount, account_id)
        assert row.oauth_provider == "microsoft"
        assert decrypt(row.oauth_access_token) == "ms-access"
        assert decrypt(row.oauth_refresh_token) == "ms-refresh"
    finally:
        db.close()


async def test_slow_microsoft_endpoint_does_not_block_other_requests(device_app):
    import asyncio
    import httpx

    account_id = _make_account(device_app.db, owner="alice")
    device_app.fake.gate = asyncio.Event()
    transport = httpx.ASGITransport(app=device_app.app)
    # The fake replaces httpx.AsyncClient, so build the test client from the
    # real class captured before patching.
    async with _REAL_ASYNC_CLIENT(transport=transport, base_url="http://test") as client:
        slow = asyncio.create_task(client.post(
            f"{_DEVICE}/start", data={"account_id": account_id}, headers=_as("alice"),
        ))

        async def _microsoft_called():
            while not device_app.fake.calls:
                assert not slow.done(), (await slow).text
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_microsoft_called(), timeout=2)
        # Microsoft hasn't answered; an unrelated request must still be served.
        other = await asyncio.wait_for(
            client.post(f"{_DEVICE}/cancel", data={"poll_id": "nope"}, headers=_as("alice")),
            timeout=2,
        )
        assert other.status_code == 200
        assert not slow.done()
        device_app.fake.gate.set()
        assert (await slow).status_code == 200


# --- Settings UI: reconnect restores the provider ---------------------------


def _settings_js():
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "static" / "js" / "settings.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("form, select_id", [("eaf", "eaf-provider"), ("uf", "uf-email-provider")])
def test_edit_form_restores_oauth_provider_for_reconnect(form, select_id):
    import re

    src = _settings_js()
    # Saved oauth_provider → preset key, for both Google and Microsoft.
    assert src.count("const _OAUTH_PROVIDER_KEYS = { google: 'google_workspace', microsoft: 'outlook' };") == 2
    saved = f"_{form}SavedOauthKey"
    # The selector is restored from the saved account...
    assert re.search(rf"el\('{select_id}'\)\.value = {saved};", src)
    # ...and the Reconnect handler falls back to it if the selector is blank.
    assert re.search(
        rf"el\('{form}-oauth-btn'\)\.addEventListener\('click', async \(\) => \{{\s*"
        rf"const p = PROVIDERS\[el\('{select_id}'\)\.value\] \|\| PROVIDERS\[{saved}\];",
        src,
    )


def test_live_form_restores_provider_before_dropdown_label_is_drawn():
    src = _settings_js()
    restore = src.index("if (_ufSavedOauthKey) el('uf-email-provider').value = _ufSavedOauthKey;")
    dropdown = src.index("// Custom dropdown wire-up")
    assert restore < dropdown


def _ms_device_flow_blocks():
    src = _settings_js()
    marker = "async function _runMsDeviceFlow(accId) {"
    blocks, pos = [], 0
    while (pos := src.find(marker, pos)) != -1:
        blocks.append(src[pos:src.index("\n    }\n", pos)])
        pos += len(marker)
    return blocks


@pytest.mark.parametrize("form", ["eaf", "uf"])
def test_microsoft_sign_in_reuses_shared_device_flow_panel(form):
    blocks = [b for b in _ms_device_flow_blocks() if f"el('{form}-oauth-device')" in b]
    assert len(blocks) == 1
    block = blocks[0]
    # Same runner + panel as the Copilot / ChatGPT sign-in, not a bespoke one.
    assert "runProviderDeviceFlow('microsoft-mail'" in block
    assert "renderDeviceAuthWaitPanel(box," in block
    assert "openWindow: () => {}" in block
    assert "<a href" not in block


def test_outlook_note_keeps_most_accounts_wording():
    src = _settings_js()
    assert src.count("Most Outlook and Microsoft 365 accounts no longer accept passwords") == 2
    assert "devicelogin" not in src


# --- Settings UI: the sign-in-created row is the one the form keeps editing --
#
# Microsoft's sign-in stays on the form (Google's redirects away), so the row
# the OAuth button creates must be the one Save / Test use afterwards. Before
# this, Create POSTed a second, token-less copy and Test sent no account id.


def test_account_forms_never_choose_post_vs_put_from_isedit():
    src = _settings_js()
    assert "isEdit ? `/api/email/accounts/" not in src
    assert src.count("const url = savedId ? `/api/email/accounts/${savedId}` : '/api/email/accounts';") == 4
    assert src.count("const method = savedId ? 'PUT' : 'POST';") == 4


@pytest.mark.parametrize("init", [
    "let savedId = isEdit ? a.id : null;",       # legacy eaf form
    "let savedId = isEdit ? editId : null;",     # live uf form
])
def test_each_form_tracks_the_saved_row(init):
    assert _settings_js().count(init) == 1


def test_oauth_button_adopts_the_row_it_created():
    src = _settings_js()
    assert src.count("if (!savedId) savedId = d.id;") == 1          # eaf
    assert "if (!savedId) {\n        savedId = d.id;" in src          # uf
    assert src.count("const accId = savedId;") == 2


def test_live_form_test_uses_saved_row_and_blocks_unsigned_oauth():
    src = _settings_js()
    block = src[src.index("el('uf-email-test').addEventListener('click'"):]
    block = block[:block.index("btn.disabled = true;")]
    assert "if (savedId && !body.imap_password) body.account_id = savedId;" in block
    assert "'Sign in with Microsoft first'" in block
    # Gated on a completed sign-in, not merely on the row existing.
    assert "if (testProvider && testProvider.oauth && !oauthConnected) {" in block


def test_completed_sign_in_marks_the_form_connected():
    src = _settings_js()
    assert "let oauthConnected = !!(isEdit && a.oauth_provider);" in src
    assert "let oauthConnected = !!(existing && existing.oauth_provider);" in src
    for block in _ms_device_flow_blocks():
        authorized = block[block.index("if (result.status === 'authorized')"):block.index("} else if (result.status === 'expired')")]
        assert "oauthConnected = true;" in authorized


def test_successful_microsoft_sign_in_refreshes_integrations():
    for block in _ms_device_flow_blocks():
        authorized = block[block.index("if (result.status === 'authorized')"):block.index("} else if (result.status === 'expired')")]
        assert "renderList();" in authorized
        assert "notifyIntegrationsChanged();" in authorized


def test_outlook_note_is_one_sentence_without_env_var():
    src = _settings_js()
    note = 'Most Outlook and Microsoft 365 accounts no longer accept passwords for IMAP/SMTP — use "Sign in with Microsoft" below, or choose Custom if your Exchange server still allows them.'
    assert src.count(note) == 2
    # A missing client id is reported when sign-in starts, like Google's.
    for line in src.splitlines():
        if "no longer accept passwords" in line:
            assert "MICROSOFT_OAUTH_CLIENT_ID" not in line


def test_live_form_reads_existing_only_after_declaring_it():
    # `let` is in its temporal dead zone until declared: reading `existing`
    # earlier throws and the whole Add Email form fails to open.
    src = _settings_js()
    body = src[src.index("async function showEmailForm(editId) {"):]
    assert body.index("let existing = null;") < body.index("let oauthConnected = !!(existing")


def _poll_with_identity(device_app, account_id, upn):
    poll_id = device_app.client.post(
        f"{_DEVICE}/start", data={"account_id": account_id}, headers=_as("alice"),
    ).json()["poll_id"]
    device_app.fake.script["token"] = (200, {
        "access_token": "ms-access", "refresh_token": "ms-refresh", "expires_in": 3600,
        "id_token": _jwt({"preferred_username": upn}),
    })
    return device_app.client.post(f"{_DEVICE}/poll", data={"poll_id": poll_id}, headers=_as("alice")).json()


def test_device_poll_rejects_another_mailbox_even_with_blank_smtp_user(device_app):
    # Regression: the blank smtp_user used to be filled with the authorized
    # identity *before* the membership check, so any identity passed.
    from core.database import EmailAccount

    account_id = _make_account(device_app.db, owner="alice", imap_user="alice@contoso.com", provider=None)
    out = _poll_with_identity(device_app, account_id, "mallory@contoso.com")

    assert out["status"] == "failed" and "mallory@contoso.com" in out["error"]
    db = device_app.db()
    try:
        row = db.get(EmailAccount, account_id)
        assert row.oauth_provider is None
        assert not (row.smtp_user or "")
    finally:
        db.close()


def test_device_poll_adopts_identity_for_a_blank_account(device_app):
    from core.database import EmailAccount

    account_id = _make_account(device_app.db, owner="alice", imap_user="", provider=None)
    out = _poll_with_identity(device_app, account_id, "alice@contoso.com")

    assert out["status"] == "authorized"
    db = device_app.db()
    try:
        row = db.get(EmailAccount, account_id)
        assert row.imap_user == row.smtp_user == "alice@contoso.com"
        assert row.oauth_provider == "microsoft"
    finally:
        db.close()


# --- Saving an OAuth account must not erase the verified usernames ------------
#
# Regression: the sign-in filled the blank Username fields server-side, then
# the form's Save sent its still-blank fields and wiped them, leaving IMAP and
# SMTP to authenticate as "".


@pytest.mark.parametrize("provider, kept", [("microsoft", True), (None, False)])
def test_put_with_blank_usernames_keeps_them_only_on_oauth_rows(device_app, provider, kept):
    from core.database import EmailAccount

    account_id = _make_account(device_app.db, owner="alice", imap_user="alice@contoso.com", provider=provider)
    db = device_app.db()
    try:
        row = db.get(EmailAccount, account_id)
        row.smtp_user = "alice@contoso.com"
        db.commit()
    finally:
        db.close()

    r = device_app.client.put(
        f"/api/email/accounts/{account_id}",
        json={"name": "Renamed", "imap_user": "", "smtp_user": "", "imap_host": "outlook.office365.com"},
        headers=_as("alice"),
    )
    assert r.status_code == 200, r.text

    db = device_app.db()
    try:
        row = db.get(EmailAccount, account_id)
        assert row.name == "Renamed"
        expected = "alice@contoso.com" if kept else ""
        assert (row.imap_user or "") == expected
        assert (row.smtp_user or "") == expected
    finally:
        db.close()


def test_put_can_still_change_an_oauth_rows_username(device_app):
    from core.database import EmailAccount

    account_id = _make_account(device_app.db, owner="alice", imap_user="alice@contoso.com")
    device_app.client.put(f"/api/email/accounts/{account_id}", json={"imap_user": "bob@contoso.com"}, headers=_as("alice"))
    db = device_app.db()
    try:
        assert db.get(EmailAccount, account_id).imap_user == "bob@contoso.com"
    finally:
        db.close()


def test_successful_sign_in_fills_blank_usernames_in_the_form():
    for block in _ms_device_flow_blocks():
        authorized = block[block.index("if (result.status === 'authorized')"):block.index("} else if (result.status === 'expired')")]
        prefix = "eaf" if "el('eaf-imap-user')" in authorized else "uf"
        assert f"if (!el('{prefix}-imap-user').value.trim()) el('{prefix}-imap-user').value = email;" in authorized
        assert f"if (!el('{prefix}-smtp-user').value.trim()) el('{prefix}-smtp-user').value = email;" in authorized
