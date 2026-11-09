"""POST /api/v1/chat: a session started with a direct api_key takes the key
again on every turn, and the key is never written to the database.

Session headers live only in memory, and ``SessionManager.get_session()``
refreshes them from the DB row (which holds ``{}``), so before this fix a
resumed turn reached the provider with no key at all. These tests drive the
real SessionManager against a temporary SQLite database.
"""

import ipaddress
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as cdb
import core.models as core_models
import core.session_manager as sm

KEY = "sk-ant-test-resume-key"
ANTHROPIC_CHAT_URL = "https://api.anthropic.com/v1/messages"
DNS = {
    "api.anthropic.com": "93.184.216.34",
    "other.example.test": "93.184.216.35",
    "llm.example.test": "93.184.216.36",
}


class _WebhookManager:
    def fire_and_forget(self, event, payload):
        pass


@pytest.fixture
def chat(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.webhook import webhook_routes
    from src import llm_core, url_security

    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(sm, "SessionLocal", factory)
    monkeypatch.setattr(webhook_routes, "SessionLocal", factory)

    manager = sm.SessionManager.__new__(sm.SessionManager)
    manager.sessions = {}
    manager.upload_handler = None
    monkeypatch.setattr(core_models, "_SESSION_MANAGER_INSTANCE", manager)

    def _resolve(host):
        return [ipaddress.ip_address(DNS[host])]

    monkeypatch.setattr(url_security, "_resolve_hostname_ips", _resolve)

    calls = []

    async def _fake_llm_call_async(url, model, messages, **kwargs):
        calls.append({
            "url": url,
            "headers": dict(kwargs.get("headers") or {}),
            "message": messages[-1]["content"],
        })
        return f"reply {len(calls)}"

    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm_call_async)

    app = FastAPI()

    @app.middleware("http")
    async def _chat_scope_token(request, call_next):
        # What AuthMiddleware sets for a valid chat-scope bearer token.
        request.state.api_token = True
        request.state.api_token_scopes = ["chat"]
        request.state.api_token_owner = "alice"
        return await call_next(request)

    # The module-level router accumulates one route per setup call, so mount
    # the endpoint just registered rather than the whole router.
    router = webhook_routes.setup_webhook_routes(
        _WebhookManager(), auth_manager=None, session_manager=manager,
    )
    sync_chat = [r for r in router.routes if r.path == "/api/v1/chat"][-1]
    app.add_api_route("/api/v1/chat", sync_chat.endpoint, methods=["POST"])
    yield TestClient(app), calls, factory
    engine.dispose()


def _stored_headers(factory, session_id):
    with factory() as db:
        headers = db.get(cdb.Session, session_id).headers
    if isinstance(headers, str):
        headers = json.loads(headers or "{}")
    return headers or {}


def test_resume_with_api_key_sends_the_key_and_never_stores_it(chat):
    client, calls, factory = chat

    r1 = client.post("/api/v1/chat", json={
        "message": "turn one",
        "model": "claude-sonnet-4-6",
        "api_key": KEY,
        "base_url": "https://api.anthropic.com/v1",
    })
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["session_id"]
    assert calls[0]["url"] == ANTHROPIC_CHAT_URL
    assert calls[0]["headers"].get("x-api-key") == KEY

    # A base_url/provider on resume must not move the session elsewhere.
    r2 = client.post("/api/v1/chat", json={
        "message": "turn two",
        "session": session_id,
        "api_key": KEY,
        "base_url": "https://other.example.test/v1",
        "provider": "openai",
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["session_id"] == session_id
    assert calls[1]["url"] == ANTHROPIC_CHAT_URL
    assert calls[1]["headers"].get("x-api-key") == KEY
    assert calls[1]["headers"].get("anthropic-version") == "2023-06-01"
    assert "Authorization" not in calls[1]["headers"]

    # The key is used per request and never kept: a resume without it has none.
    r3 = client.post("/api/v1/chat", json={"message": "turn three", "session": session_id})
    assert r3.status_code == 200, r3.text
    assert "x-api-key" not in calls[2]["headers"]

    stored = _stored_headers(factory, session_id)
    assert stored == {}
    with factory() as db:
        assert db.query(cdb.Session).filter(cdb.Session.id != session_id).count() == 0


def test_resume_of_fallback_session_takes_the_configured_endpoint_key(chat):
    client, calls, factory = chat
    endpoint_key = "sk-admin-endpoint-key"
    with factory() as db:
        db.add(cdb.ModelEndpoint(
            id="ep-alice", name="Admin LLM", owner="alice", is_enabled=True,
            base_url="https://llm.example.test/v1", api_key=endpoint_key,
        ))
        db.commit()

    # No session and no api_key: the session starts from the configured endpoint.
    r1 = client.post("/api/v1/chat", json={"message": "turn one", "model": "test-model"})
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["session_id"]
    assert calls[0]["url"] == "https://llm.example.test/v1/chat/completions"
    assert calls[0]["headers"].get("Authorization") == f"Bearer {endpoint_key}"

    # Resume with session only: the key comes from that endpoint again.
    r2 = client.post("/api/v1/chat", json={"message": "turn two", "session": session_id})
    assert r2.status_code == 200, r2.text
    assert r2.json()["session_id"] == session_id
    assert calls[1]["url"] == calls[0]["url"]
    assert calls[1]["headers"].get("Authorization") == f"Bearer {endpoint_key}"

    assert _stored_headers(factory, session_id) == {}
    with factory() as db:
        assert db.get(cdb.Session, session_id).endpoint_id == "ep-alice"

    # The endpoint was deleted since: the resume fails instead of calling keyless.
    with factory() as db:
        db.delete(db.get(cdb.ModelEndpoint, "ep-alice"))
        db.commit()
    r3 = client.post("/api/v1/chat", json={"message": "turn three", "session": session_id})
    assert r3.status_code == 400, r3.text
    assert len(calls) == 2


def _cached_session(session_id):
    return core_models._SESSION_MANAGER_INSTANCE.sessions[session_id]


def _start_direct_key_session(client):
    r = client.post("/api/v1/chat", json={
        "message": "turn one",
        "model": "claude-sonnet-4-6",
        "api_key": KEY,
        "base_url": "https://api.anthropic.com/v1",
    })
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_key_is_never_left_on_the_cached_session(chat):
    # The cached Session is shared by every request on it, so a key sent for
    # one request must not stay on it.
    client, calls, _ = chat
    session_id = _start_direct_key_session(client)
    assert calls[0]["headers"].get("x-api-key") == KEY
    assert KEY not in json.dumps(_cached_session(session_id).headers or {})

    r2 = client.post("/api/v1/chat", json={
        "message": "turn two", "session": session_id, "api_key": KEY,
    })
    assert r2.status_code == 200, r2.text
    assert calls[1]["headers"].get("x-api-key") == KEY
    assert KEY not in json.dumps(_cached_session(session_id).headers or {})


def test_interleaved_resumes_each_send_their_own_key(chat, monkeypatch):
    client, calls, _ = chat
    session_id = _start_direct_key_session(client)
    key_a, key_b = "sk-ant-key-for-a", "sk-ant-key-for-b"

    # Resume B runs to completion inside resume A, after A has rebuilt its
    # headers and before A makes its outbound call.
    session_cls = type(_cached_session(session_id))
    original_add_message = session_cls.add_message
    nested = []

    def _add_message(self, message):
        original_add_message(self, message)
        if message.content == "turn A" and not nested:
            nested.append(client.post("/api/v1/chat", json={
                "message": "turn B", "session": session_id, "api_key": key_b,
            }))

    monkeypatch.setattr(session_cls, "add_message", _add_message)

    r_a = client.post("/api/v1/chat", json={
        "message": "turn A", "session": session_id, "api_key": key_a,
    })
    assert r_a.status_code == 200, r_a.text
    assert nested and nested[0].status_code == 200, nested[0].text
    # B's call is made first, inside A; A's own call follows.
    assert len(calls) == 3
    assert calls[1]["message"] == "turn B"
    assert calls[1]["headers"].get("x-api-key") == key_b
    assert calls[2]["headers"].get("x-api-key") == key_a


def test_resume_of_provider_auth_session_matches_the_runtime_base(chat, monkeypatch):
    # A provider-auth endpoint's first turn goes to the base URL its
    # credentials resolve to, which can differ from the endpoint's base_url.
    client, calls, factory = chat
    with factory() as db:
        db.add(cdb.ModelEndpoint(
            id="ep-sub", name="Subscription", owner="alice", is_enabled=True,
            base_url="https://llm.example.test/v1", provider_auth_id="auth-1",
        ))
        db.commit()
    resolved = []

    def _runtime_credentials(auth_id, owner=None, **kwargs):
        resolved.append((auth_id, owner))
        return {"base_url": "https://other.example.test/v1", "api_key": "sub-access-token"}

    monkeypatch.setattr("src.chatgpt_subscription.resolve_runtime_credentials", _runtime_credentials)

    r1 = client.post("/api/v1/chat", json={"message": "turn one", "model": "test-model"})
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["session_id"]
    assert calls[0]["url"] == "https://other.example.test/v1/chat/completions"
    assert calls[0]["headers"].get("Authorization") == "Bearer sub-access-token"

    r2 = client.post("/api/v1/chat", json={"message": "turn two", "session": session_id})
    assert r2.status_code == 200, r2.text
    assert calls[1]["url"] == calls[0]["url"]
    assert calls[1]["headers"].get("Authorization") == "Bearer sub-access-token"
    assert set(resolved) == {("auth-1", "alice")}
    assert _stored_headers(factory, session_id) == {}


def test_resume_of_provider_auth_session_on_a_private_runtime_base(chat, monkeypatch):
    # The credentials resolve to a host.docker.internal proxy, which a native
    # backend rewrites to 127.0.0.1. The server chose that URL from the
    # session's own bound endpoint, so the resume is not refused as private.
    client, calls, factory = chat
    with factory() as db:
        db.add(cdb.ModelEndpoint(
            id="ep-local", name="Local proxy", owner="alice", is_enabled=True,
            base_url="https://llm.example.test/v1", provider_auth_id="auth-local",
        ))
        db.commit()
    monkeypatch.setattr("src.endpoint_resolver._running_in_container", lambda: False)
    monkeypatch.setattr(
        "src.chatgpt_subscription.resolve_runtime_credentials",
        lambda auth_id, owner=None, **kwargs: {
            "base_url": "http://host.docker.internal:8317/v1", "api_key": "local-token",
        },
    )

    r1 = client.post("/api/v1/chat", json={"message": "turn one", "model": "test-model"})
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["session_id"]
    assert calls[0]["url"] == "http://127.0.0.1:8317/v1/chat/completions"

    r2 = client.post("/api/v1/chat", json={"message": "turn two", "session": session_id})
    assert r2.status_code == 200, r2.text
    assert calls[1]["url"] == calls[0]["url"]
    assert calls[1]["headers"].get("Authorization") == "Bearer local-token"


def test_resume_of_direct_key_session_now_pointing_private_is_refused(chat, monkeypatch):
    # A token-supplied base_url keeps the public-address check on resume, even
    # when one of the owner's provider-auth endpoints resolves to that same URL:
    # only the endpoint a session is bound to can vouch for it.
    client, calls, factory = chat
    with factory() as db:
        db.add(cdb.ModelEndpoint(
            id="ep-decoy", name="Subscription", owner="alice", is_enabled=True,
            base_url="https://other.example.test/v1", provider_auth_id="auth-decoy",
        ))
        db.commit()
    monkeypatch.setattr(
        "src.chatgpt_subscription.resolve_runtime_credentials",
        lambda auth_id, owner=None, **kwargs: {
            "base_url": "https://llm.example.test/v1", "api_key": "sub-token",
        },
    )

    r1 = client.post("/api/v1/chat", json={
        "message": "turn one", "model": "test-model",
        "api_key": KEY, "base_url": "https://llm.example.test/v1",
    })
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["session_id"]

    monkeypatch.setitem(DNS, "llm.example.test", "127.0.0.1")
    r2 = client.post("/api/v1/chat", json={
        "message": "turn two", "session": session_id, "api_key": KEY,
    })
    assert r2.status_code == 400, r2.text
    assert len(calls) == 1


def test_fallback_endpoint_key_is_never_left_on_the_cached_session(chat):
    # The first turn from a configured endpoint sends its key for that request
    # only; the cached Session is shared by every request on it.
    client, calls, factory = chat
    endpoint_key = "sk-admin-endpoint-key"
    with factory() as db:
        db.add(cdb.ModelEndpoint(
            id="ep-alice", name="Admin LLM", owner="alice", is_enabled=True,
            base_url="https://llm.example.test/v1", api_key=endpoint_key,
        ))
        db.commit()

    r1 = client.post("/api/v1/chat", json={"message": "turn one", "model": "test-model"})
    assert r1.status_code == 200, r1.text
    session_id = r1.json()["session_id"]
    assert calls[0]["headers"].get("Authorization") == f"Bearer {endpoint_key}"
    assert endpoint_key not in json.dumps(_cached_session(session_id).headers or {})
    assert _stored_headers(factory, session_id) == {}


# --- the bound endpoint must still be the caller's, enabled, and matching ---

ENDPOINT_KEY = "sk-bound-endpoint-key"
SUB_TOKEN = "sub-access-token"


def _start_bound_session(client, factory, monkeypatch, kind):
    """Start a session from a configured endpoint of `kind`: "key" (an
    api_key endpoint whose base_url the session uses) or "provider-auth"
    (credentials resolving to a runtime base other than its base_url).
    Returns (session_id, credential resolutions)."""
    with factory() as db:
        db.add(cdb.ModelEndpoint(
            id="ep-bound", name="Bound", owner="alice", is_enabled=True,
            base_url="https://llm.example.test/v1",
            api_key=ENDPOINT_KEY if kind == "key" else None,
            provider_auth_id="auth-bound" if kind == "provider-auth" else None,
        ))
        db.commit()
    resolved = []

    def _runtime_credentials(auth_id, owner=None, **kwargs):
        resolved.append((auth_id, owner))
        return {"base_url": "https://other.example.test/v1", "api_key": SUB_TOKEN}

    monkeypatch.setattr("src.chatgpt_subscription.resolve_runtime_credentials", _runtime_credentials)
    r1 = client.post("/api/v1/chat", json={"message": "turn one", "model": "test-model"})
    assert r1.status_code == 200, r1.text
    with factory() as db:
        assert db.get(cdb.Session, r1.json()["session_id"]).endpoint_id == "ep-bound"
    return r1.json()["session_id"], resolved


def _update_bound_endpoint(factory, **fields):
    with factory() as db:
        ep = db.get(cdb.ModelEndpoint, "ep-bound")
        for name, value in fields.items():
            setattr(ep, name, value)
        db.commit()


def _assert_refused_without_credentials(client, calls, resolved, session_id):
    resolutions_before = len(resolved)
    r2 = client.post("/api/v1/chat", json={"message": "turn two", "session": session_id})
    assert r2.status_code == 400, r2.text
    assert len(calls) == 1, "the resume must not reach the provider"
    assert len(resolved) == resolutions_before, "no credentials resolved for the refused resume"
    for secret in (ENDPOINT_KEY, SUB_TOKEN):
        assert secret not in r2.text


@pytest.mark.parametrize("kind", ["key", "provider-auth"])
def test_resume_refused_when_bound_endpoint_now_belongs_to_another_owner(chat, monkeypatch, kind):
    client, calls, factory = chat
    session_id, resolved = _start_bound_session(client, factory, monkeypatch, kind)

    _update_bound_endpoint(factory, owner="mallory")

    _assert_refused_without_credentials(client, calls, resolved, session_id)


@pytest.mark.parametrize("kind", ["key", "provider-auth"])
def test_resume_refused_when_bound_endpoint_is_disabled(chat, monkeypatch, kind):
    client, calls, factory = chat
    session_id, resolved = _start_bound_session(client, factory, monkeypatch, kind)

    _update_bound_endpoint(factory, is_enabled=False)

    _assert_refused_without_credentials(client, calls, resolved, session_id)


def test_resume_refused_when_bound_endpoint_without_provider_auth_differs_from_session_url(
        chat, monkeypatch):
    # The session went to the provider-auth runtime base (other.example.test).
    # Once the endpoint is a plain key endpoint, only its base_url
    # (llm.example.test) can match, so the session URL no longer does.
    client, calls, factory = chat
    session_id, resolved = _start_bound_session(client, factory, monkeypatch, "provider-auth")
    assert calls[0]["url"] == "https://other.example.test/v1/chat/completions"

    _update_bound_endpoint(factory, provider_auth_id=None, api_key=ENDPOINT_KEY)

    _assert_refused_without_credentials(client, calls, resolved, session_id)
