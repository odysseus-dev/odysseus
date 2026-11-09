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
        calls.append({"url": url, "headers": dict(kwargs.get("headers") or {})})
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
