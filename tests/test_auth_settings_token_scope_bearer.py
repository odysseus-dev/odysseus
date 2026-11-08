"""Three auth-boundary regressions, one per fix:

1. ``GET /api/auth/settings`` is auth-exempt. A caller with no session must get
   only the public allowlist (keybinds + TTS prefs), not the reminder ntfy topic
   or the reminder email address; admins still see everything.
2. ``GET /api/models`` on a chat-scope bearer token minted by an admin must get
   the owner-scoped view, not every user's endpoints (the delegated-credential
   policy in ``src/auth_helpers.is_delegated_credential``).
3. Bearer tokens are bcrypt-verified in a worker thread, not on the event loop,
   and repeated failed bearer attempts get 429 per (client, token prefix): a
   prefix no token has costs no bcrypt and is never counted, so junk headers
   (all from 127.0.0.1 behind a tunnel) cannot lock out other tokens.
"""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import bcrypt
import pytest
from starlette.requests import Request


# ---------------------------------------------------------------------------
# 1. /api/auth/settings for callers without a session
# ---------------------------------------------------------------------------

_SEEDED = {
    "reminder_ntfy_topic": "private-topic-7f3a",
    "reminder_email_to": "owner@example.com",
    "default_model": "gpt-4o",
    "brave_api_key": "sk-secret-brave",
    "keybinds": {"search": "ctrl+k"},
    "tts_voice": "alloy",
    "tts_enabled": True,
}


class _SettingsAuthManager:
    is_configured = True
    signup_enabled = False

    def get_username_for_token(self, token):
        return {"admin-token": "admin", "user-token": "user"}.get(token)

    def is_admin(self, user):
        return user == "admin"


@pytest.fixture
def settings_app(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from routes import auth_routes

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(_SEEDED))
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    app = fastapi.FastAPI()
    app.include_router(auth_routes.setup_auth_routes(_SettingsAuthManager()))
    return app


def test_unauthenticated_settings_get_only_public_allowlist(settings_app):
    from fastapi.testclient import TestClient

    r = TestClient(settings_app).get("/api/auth/settings")
    assert r.status_code == 200
    data = r.json()
    assert "reminder_ntfy_topic" not in data
    assert "reminder_email_to" not in data
    assert "private-topic-7f3a" not in r.text
    assert "owner@example.com" not in r.text
    assert "default_model" not in data
    # What the endpoint is auth-exempt for stays readable.
    assert data["keybinds"] == {"search": "ctrl+k"}
    assert data["tts_voice"] == "alloy"


def test_admin_settings_still_include_reminder_targets(settings_app):
    from fastapi.testclient import TestClient

    c = TestClient(settings_app)
    c.cookies.set("odysseus_session", "admin-token")
    data = c.get("/api/auth/settings").json()
    assert data["reminder_ntfy_topic"] == "private-topic-7f3a"
    assert data["reminder_email_to"] == "owner@example.com"
    assert data["brave_api_key"] == "sk-secret-brave"


def test_logged_in_non_admin_keeps_scrubbed_view(settings_app):
    from fastapi.testclient import TestClient

    c = TestClient(settings_app)
    c.cookies.set("odysseus_session", "user-token")
    data = c.get("/api/auth/settings").json()
    assert data["default_model"] == "gpt-4o"
    assert data["brave_api_key"] == ""


def test_localhost_bypass_loopback_keeps_scrubbed_view(settings_app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LOCALHOST_BYPASS", "true")
    c = TestClient(settings_app, client=("127.0.0.1", 50000))
    data = c.get("/api/auth/settings").json()
    assert data["default_model"] == "gpt-4o"
    assert data["brave_api_key"] == ""


# ---------------------------------------------------------------------------
# 2. /api/models on a delegated (bearer) credential
# ---------------------------------------------------------------------------

def _endpoint(ep_id, name, owner):
    return SimpleNamespace(
        id=ep_id, name=name, base_url=f"http://{name}.invalid:8000/v1",
        api_key=None, is_enabled=True, hidden_models=None,
        cached_models=json.dumps([f"{name}-model"]), pinned_models=None,
        model_type="llm", supports_tools=None, endpoint_kind="auto",
        model_refresh_mode="auto", model_refresh_interval=None,
        model_refresh_timeout=None, owner=owner, created_at=None, updated_at=None,
    )


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


def _models_request(*, api_token, user, owner=None, scopes=None):
    scope = {"type": "http", "method": "GET", "path": "/api/models",
             "headers": [], "app": MagicMock(), "client": ("192.0.2.5", 1234)}
    req = Request(scope)
    auth_mgr = MagicMock()
    auth_mgr.is_configured = True
    auth_mgr.is_admin.side_effect = lambda u: u == "adminuser"
    req.app.state.auth_manager = auth_mgr
    req.state.current_user = user
    req.state.api_token = api_token
    if api_token:
        req.state.api_token_owner = owner
        req.state.api_token_scopes = scopes or []
    return req


@pytest.fixture
def models_endpoint(monkeypatch):
    from routes import model_routes

    rows = [_endpoint("ep-admin", "adminbox", "adminuser"),
            _endpoint("ep-bob", "bobprivate", "bob")]
    db = MagicMock()
    db.query.return_value = _Query(rows)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "_disable_stale_cookbook_local_endpoints", lambda _db: 0)
    monkeypatch.setattr(
        model_routes, "owner_filter",
        lambda q, _model, owner: _Query(r for r in q.rows if r.owner in (owner, None)),
    )
    router = model_routes.setup_model_routes(model_discovery=None)
    return next(r.endpoint for r in router.routes
                if getattr(r, "path", "") == "/api/models" and "GET" in r.methods)


def test_chat_scope_token_of_admin_does_not_list_other_users_endpoints(models_endpoint):
    req = _models_request(api_token=True, user="api", owner="adminuser", scopes=["chat"])
    body = json.dumps(models_endpoint(req, refresh=False, background=False, wait_refresh=False),
                      default=str)
    assert "ep-admin" in body
    assert "ep-bob" not in body
    assert "bobprivate" not in body


def test_admin_cookie_session_still_lists_every_endpoint(models_endpoint):
    req = _models_request(api_token=False, user="adminuser")
    body = json.dumps(models_endpoint(req, refresh=False, background=False, wait_refresh=False),
                      default=str)
    assert "ep-admin" in body
    assert "ep-bob" in body


# ---------------------------------------------------------------------------
# 3. Bearer verification off the event loop, failed attempts limited
# ---------------------------------------------------------------------------

_TOKEN = "ody_" + "A" * 43
_OTHER_TOKEN = "ody_" + "B" * 43


@pytest.fixture
def bearer_client(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delitem(sys.modules, "app", raising=False)
    import app as app_mod  # noqa: E402

    monkeypatch.setattr(app_mod, "auth_manager",
                        SimpleNamespace(is_configured=True, validate_token=lambda _t: False))
    monkeypatch.setattr(app_mod, "SessionLocal", MagicMock())
    def _hash(token):
        return bcrypt.hashpw(token.encode(), bcrypt.gensalt(rounds=4)).decode()

    monkeypatch.setattr(app_mod, "_token_cache", {
        _TOKEN[:8]: [("tid-1", _hash(_TOKEN), "adminuser", ["chat"])],
        _OTHER_TOKEN[:8]: [("tid-2", _hash(_OTHER_TOKEN), "bob", ["chat"])],
    })
    app_mod.app.state._token_cache_dirty = False

    async def probe(request):
        return JSONResponse({"owner": getattr(request.state, "api_token_owner", None)})

    downstream = Starlette(routes=[Route("/api/probe", probe)])
    return app_mod, TestClient(app_mod.AuthMiddleware(downstream))


def test_bearer_bcrypt_runs_off_the_event_loop(bearer_client, monkeypatch):
    _app_mod, client = bearer_client
    real_checkpw = bcrypt.checkpw
    on_event_loop = []

    def recording_checkpw(password, hashed):
        try:
            asyncio.get_running_loop()
            on_event_loop.append(True)
        except RuntimeError:
            on_event_loop.append(False)
        return real_checkpw(password, hashed)

    monkeypatch.setattr(bcrypt, "checkpw", recording_checkpw)
    r = client.get("/api/probe", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    assert r.json() == {"owner": "adminuser"}
    assert on_event_loop == [False]


def test_repeated_failed_bearer_attempts_get_429(bearer_client):
    _app_mod, client = bearer_client
    attempts = 30  # comfortably above the limit

    # Successful auth is not counted toward the failure limit.
    for _ in range(attempts):
        ok = client.get("/api/probe", headers={"Authorization": f"Bearer {_TOKEN}"})
        assert ok.status_code == 200

    bad = {"Authorization": "Bearer " + _TOKEN[:-1] + "B"}  # same prefix, wrong secret
    statuses = [client.get("/api/probe", headers=bad).status_code for _ in range(attempts)]
    assert statuses[0] == 401
    assert statuses[-1] == 429
    first_429 = statuses.index(429)
    assert set(statuses[:first_429]) == {401}
    assert set(statuses[first_429:]) == {429}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _junk_token(i):
    """A well-formed token whose prefix no cached token has."""
    return f"ody_{i:04d}" + "x" * 39


def test_junk_prefixes_do_not_lock_out_a_valid_token(bearer_client):
    _app_mod, client = bearer_client
    for i in range(20):
        assert client.get("/api/probe", headers=_bearer(_junk_token(i))).status_code == 401

    ok = client.get("/api/probe", headers=_bearer(_TOKEN))
    assert ok.status_code == 200
    assert ok.json() == {"owner": "adminuser"}


def test_junk_prefix_never_runs_bcrypt_and_is_never_counted(bearer_client, monkeypatch):
    _app_mod, client = bearer_client
    calls = []
    real_checkpw = bcrypt.checkpw

    def spy_checkpw(password, hashed):
        calls.append(password)
        return real_checkpw(password, hashed)

    monkeypatch.setattr(bcrypt, "checkpw", spy_checkpw)
    statuses = [client.get("/api/probe", headers=_bearer(_junk_token(7))).status_code
                for _ in range(30)]
    assert statuses == [401] * 30
    assert calls == []


def test_failures_on_one_prefix_throttle_only_that_client_and_prefix(bearer_client):
    from fastapi.testclient import TestClient

    _app_mod, client = bearer_client
    wrong = _TOKEN[:-1] + "B"  # existing prefix, wrong secret
    statuses = [client.get("/api/probe", headers=_bearer(wrong)).status_code for _ in range(15)]
    assert statuses[0] == 401
    assert statuses[-1] == 429

    # Another token from the same client is not refused for those failures.
    other = client.get("/api/probe", headers=_bearer(_OTHER_TOKEN))
    assert other.status_code == 200
    assert other.json() == {"owner": "bob"}

    # The attacked prefix stays limited for this client, valid token included:
    # verifying it first would give the attacker unbounded bcrypt work again.
    assert client.get("/api/probe", headers=_bearer(_TOKEN)).status_code == 429

    # A different client is not affected by this client's failures.
    elsewhere = TestClient(client.app, client=("198.51.100.9", 4321))
    assert elsewhere.get("/api/probe", headers=_bearer(_TOKEN)).status_code == 200
