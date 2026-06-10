"""Regression: manual chat compaction must resolve utility endpoints per owner."""

from types import SimpleNamespace

import pytest

import routes.history_routes as history_routes
import src.endpoint_resolver as endpoint_resolver
import src.llm_core as llm_core
import src.model_context as model_context
from core.models import ChatMessage


class _FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeDb:
    def query(self, *args, **kwargs):
        return _FakeQuery()

    def add(self, *args, **kwargs):
        pass

    def commit(self):
        pass

    def close(self):
        pass


class _FakeSessionManager:
    def __init__(self, session):
        self._session = session
        self.saved = False

    def get_session(self, session_id):
        return self._session

    def save_sessions(self):
        self.saved = True


@pytest.mark.asyncio
async def test_manual_compact_resolves_utility_endpoint_for_session_owner(monkeypatch):
    calls = []
    session = SimpleNamespace(
        owner="alice",
        endpoint_url="http://session/v1/chat/completions",
        model="session-model",
        headers={"Authorization": "Bearer session"},
        history=[
            ChatMessage(role="user", content=f"message {i}")
            for i in range(8)
        ],
    )
    session.get_context_messages = lambda: [
        msg.to_dict() for msg in session.history
    ]
    manager = _FakeSessionManager(session)

    def fake_resolve_endpoint(setting_prefix, *args, **kwargs):
        calls.append((setting_prefix, kwargs.get("owner")))
        return (
            "http://utility/v1/chat/completions",
            "utility-model",
            {"Authorization": "Bearer utility"},
        )

    async def fake_llm_call_async(*args, **kwargs):
        return "manual compact summary"

    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(history_routes, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(model_context, "get_context_length", lambda *a, **k: 4096)
    monkeypatch.setattr(model_context, "estimate_tokens", lambda messages: len(messages) * 10)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    router = history_routes.setup_history_routes(manager)
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/session/{session_id}/compact"
    )

    result = await endpoint(SimpleNamespace(), "session-1")

    assert calls == [("utility", "alice")]
    assert result["status"] == "ok"
    assert manager.saved is True
