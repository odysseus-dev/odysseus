from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.api_token_routes import ALLOWED_SCOPES, TOKEN_PROFILES
from routes.openclaw_bridge_routes import (
    OpenClawAskRequest,
    _allowed_workflows,
    _sanitize_ticket_payload,
    _scope_owner,
    _workflow_allowed,
    openclaw_session_id,
    setup_openclaw_bridge_routes,
)
from core.models import Session


def _request(*, api_token=True, scopes=None, owner="alice", current_user="browser-user"):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    return SimpleNamespace(state=state, app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))


def test_openclaw_bridge_token_profile_is_read_only_by_default():
    assert "chat" in TOKEN_PROFILES["openclaw_bridge"]
    assert "converge:read" in TOKEN_PROFILES["openclaw_bridge"]
    assert "workflows:trigger" not in TOKEN_PROFILES["openclaw_bridge"]
    assert {
        "chat",
        "converge:read",
        "workflows:trigger",
        "web:read",
        "research:run",
        "tools:use",
        "memory:read",
        "memory:write",
    }.issubset(ALLOWED_SCOPES)


def test_scope_owner_uses_api_token_owner_when_scope_matches():
    req = _request(scopes=["chat"], owner="bridge-owner")
    assert _scope_owner(req, {"chat"}) == "bridge-owner"


def test_scope_owner_rejects_missing_scope():
    req = _request(scopes=["chat"], owner="bridge-owner")
    with pytest.raises(HTTPException) as exc:
        _scope_owner(req, {"workflows:trigger"})
    assert exc.value.status_code == 403


def test_openclaw_session_id_is_predictable_and_sanitized():
    assert openclaw_session_id("C123", "1700000000.000100") == "openclaw:slack:C123:1700000000.000100"
    assert openclaw_session_id("C 123", "thread/with spaces") == "openclaw:slack:C-123:thread-with-spaces"
    assert openclaw_session_id(session_id="custom:session") == "custom:session"


def _endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}
        self.saved = False

    def get_session(self, session_id):
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    def create_session(self, session_id, name, endpoint_url, model, rag, owner):
        sess = Session(
            id=session_id,
            name=name,
            endpoint_url=endpoint_url,
            model=model,
            rag=rag,
            owner=owner,
            headers={},
        )
        self.sessions[session_id] = sess
        return sess

    def save_sessions(self):
        self.saved = True


class FakeChatHandler:
    def __init__(self, memory_response=None):
        self.memory_response = memory_response
        self.memory_calls = 0

    async def handle_memory_command(self, sess, message):
        self.memory_calls += 1
        return self.memory_response

    async def preprocess_message(self, message, att_ids, sess, auto_opened_docs=None, allow_tool_preprocessing=True):
        return message, message, message, [], []

    def validate_and_extract_preset(self, preset_id):
        return 0.1, 100, None, None

    def update_session_name_if_needed(self, sess, text):
        return None


class FakeChatProcessor:
    def build_context_preface(self, **kwargs):
        self.kwargs = kwargs
        return [], [], []


class FakeResearchHandler:
    async def call_research_service(self, *args, **kwargs):
        raise RuntimeError("research offline")


def _bridge_request(scopes, owner="alice"):
    return _request(scopes=scopes, owner=owner, current_user="api")


def _router(chat_handler=None, research_handler=None):
    return setup_openclaw_bridge_routes(
        FakeSessionManager(),
        chat_handler or FakeChatHandler(),
        FakeChatProcessor(),
        memory_manager=None,
        research_handler=research_handler or FakeResearchHandler(),
    )


@pytest.mark.asyncio
async def test_health_does_not_include_converge_smoke_checks(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    router = _router()
    health = _endpoint(router, "/api/openclaw/health", "GET")
    data = await health(_bridge_request(["chat"]))
    assert data["status"] == "ok"
    assert "converge" not in data
    assert _endpoint(router, "/api/openclaw/converge/health", "GET")


@pytest.mark.asyncio
async def test_ask_rejects_web_without_web_scope(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)
    ask = _endpoint(_router(), "/api/openclaw/ask", "POST")
    with pytest.raises(HTTPException) as exc:
        await ask(_bridge_request(["chat"]), OpenClawAskRequest(message="search this", use_web=True))
    assert exc.value.status_code == 403
    assert "web:read" in exc.value.detail


@pytest.mark.asyncio
async def test_ask_disables_memory_commands_without_memory_write(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)

    async def fake_llm(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("routes.openclaw_bridge_routes.llm_call_async", fake_llm)

    async def fake_context(sess, request, chat_handler, chat_processor, message, session_id, **kwargs):
        sess.history.append(SimpleNamespace(role="user", content=message, metadata=None, to_dict=lambda: {"role": "user", "content": message}))
        return SimpleNamespace(
            messages=[{"role": "user", "content": message}],
            preface=[],
            preset=SimpleNamespace(temperature=0.1, max_tokens=100, character_name=None),
            uprefs={},
            user="alice",
        )

    monkeypatch.setattr("routes.openclaw_bridge_routes.build_chat_context", fake_context)
    handler = FakeChatHandler(memory_response="memory updated")
    ask = _endpoint(_router(chat_handler=handler), "/api/openclaw/ask", "POST")
    data = await ask(_bridge_request(["chat"]), OpenClawAskRequest(message="/remember x"))
    assert data["message"] == "ok"
    assert handler.memory_calls == 0


@pytest.mark.asyncio
async def test_ask_gates_tool_preprocessing_on_tools_scope(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)

    async def fake_llm(*args, **kwargs):
        return "ok"

    seen = []

    async def fake_context(*args, **kwargs):
        seen.append(kwargs["allow_tool_preprocessing"])
        return SimpleNamespace(
            messages=[{"role": "user", "content": "question"}],
            preface=[],
            preset=SimpleNamespace(temperature=0.1, max_tokens=100, character_name=None),
            uprefs={},
            user="alice",
        )

    monkeypatch.setattr("routes.openclaw_bridge_routes.llm_call_async", fake_llm)
    monkeypatch.setattr("routes.openclaw_bridge_routes.build_chat_context", fake_context)
    ask = _endpoint(_router(), "/api/openclaw/ask", "POST")

    await ask(_bridge_request(["chat"]), OpenClawAskRequest(message="question"))
    await ask(_bridge_request(["chat", "tools:use"]), OpenClawAskRequest(message="question"))

    assert seen == [False, True]


@pytest.mark.asyncio
async def test_ask_without_memory_read_passes_no_memory_true(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)

    async def fake_llm(*args, **kwargs):
        return "ok"

    seen = []

    async def fake_context(*args, **kwargs):
        seen.append(kwargs["no_memory"])
        return SimpleNamespace(
            messages=[{"role": "user", "content": "question"}],
            preface=[],
            preset=SimpleNamespace(temperature=0.1, max_tokens=100, character_name=None),
            uprefs={},
            user="alice",
        )

    monkeypatch.setattr("routes.openclaw_bridge_routes.llm_call_async", fake_llm)
    monkeypatch.setattr("routes.openclaw_bridge_routes.build_chat_context", fake_context)
    ask = _endpoint(_router(), "/api/openclaw/ask", "POST")

    await ask(_bridge_request(["chat"]), OpenClawAskRequest(message="question"))

    assert seen == [True]


@pytest.mark.asyncio
async def test_ask_with_memory_read_passes_no_memory_false(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)

    async def fake_llm(*args, **kwargs):
        return "ok"

    seen = []

    async def fake_context(*args, **kwargs):
        seen.append(kwargs["no_memory"])
        return SimpleNamespace(
            messages=[{"role": "user", "content": "question"}],
            preface=[],
            preset=SimpleNamespace(temperature=0.1, max_tokens=100, character_name=None),
            uprefs={},
            user="alice",
        )

    monkeypatch.setattr("routes.openclaw_bridge_routes.llm_call_async", fake_llm)
    monkeypatch.setattr("routes.openclaw_bridge_routes.build_chat_context", fake_context)
    ask = _endpoint(_router(), "/api/openclaw/ask", "POST")

    await ask(_bridge_request(["chat", "memory:read"]), OpenClawAskRequest(message="question"))

    assert seen == [False]


@pytest.mark.asyncio
async def test_ask_allows_memory_commands_with_memory_write(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)
    handler = FakeChatHandler(memory_response="memory updated")
    ask = _endpoint(_router(chat_handler=handler), "/api/openclaw/ask", "POST")
    data = await ask(_bridge_request(["chat", "memory:write"]), OpenClawAskRequest(message="/remember x"))
    assert data["message"] == "memory updated"
    assert handler.memory_calls == 1


@pytest.mark.asyncio
async def test_ask_returns_research_warning(monkeypatch):
    monkeypatch.setattr("routes.openclaw_bridge_routes.resolve_endpoint", lambda *a, **k: ("http://llm", "model", {}))
    monkeypatch.setattr("routes.openclaw_bridge_routes._clear_orphaned_session_endpoint", lambda *a, **k: False)

    async def fake_llm(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("routes.openclaw_bridge_routes.llm_call_async", fake_llm)

    async def fake_context(*args, **kwargs):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "question"}],
            preface=[],
            preset=SimpleNamespace(temperature=0.1, max_tokens=100, character_name=None),
            uprefs={},
            user="alice",
        )

    monkeypatch.setattr("routes.openclaw_bridge_routes.build_chat_context", fake_context)
    monkeypatch.setattr("routes.research_routes._resolve_research_endpoint", lambda sess: ("http://research", "model", {}))
    ask = _endpoint(_router(research_handler=FakeResearchHandler()), "/api/openclaw/ask", "POST")
    data = await ask(_bridge_request(["chat", "research:run"]), OpenClawAskRequest(message="question", use_research=True))
    assert data["message"] == "ok"
    assert data["warnings"]
    assert "Research context unavailable" in data["warnings"][0]


def test_workflow_allowlist_defaults_open_and_filters_when_configured(monkeypatch):
    monkeypatch.delenv("OPENCLAW_ALLOWED_WORKFLOWS", raising=False)
    assert _allowed_workflows() is None
    assert _workflow_allowed("daily-summary", task_id="task-1", task_name="Daily Summary")

    monkeypatch.setenv("OPENCLAW_ALLOWED_WORKFLOWS", "daily-summary,task-2")
    assert _workflow_allowed("daily-summary", task_id="task-1", task_name="Daily Summary")
    assert _workflow_allowed("other", task_id="task-2", task_name="Other")
    assert not _workflow_allowed("other", task_id="task-3", task_name="Other")


def test_sanitize_ticket_payload_removes_secret_and_unknown_top_level_fields():
    ticket = {
        "id": 123,
        "subject": "VPN down",
        "api_key": "secret",
        "internal_token": "secret",
        "raw_redmine_payload": {"password": "secret", "subject": "nested ok"},
        "journals": [{"notes": "safe", "authorization": "bearer secret"}],
    }
    sanitized = _sanitize_ticket_payload(ticket)
    assert sanitized["id"] == 123
    assert sanitized["subject"] == "VPN down"
    assert "api_key" not in sanitized
    assert "internal_token" not in sanitized
    assert "raw_redmine_payload" not in sanitized
    assert "authorization" not in sanitized["journals"][0]


@pytest.mark.asyncio
async def test_build_chat_context_saves_user_message_before_assistant(monkeypatch):
    from routes import chat_helpers

    sess = Session(id="s1", name="OpenClaw", endpoint_url="http://llm", model="model", headers={})
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda user: {})
    monkeypatch.setattr(chat_helpers, "normalize_model_id", lambda *a, **k: None)

    async def fake_compact(sess, endpoint_url, model, messages, headers, owner=None):
        return messages, 42, False

    monkeypatch.setattr(chat_helpers, "maybe_compact", fake_compact)
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda messages, context_length: messages)
    ctx = await chat_helpers.build_chat_context(
        sess,
        request,
        FakeChatHandler(),
        FakeChatProcessor(),
        message="hello bridge",
        session_id="s1",
        att_ids=[],
        use_web=False,
        no_memory=True,
        webhook_manager=None,
        allow_tool_preprocessing=False,
    )
    assert sess.history[0].role == "user"
    assert sess.history[0].content == "hello bridge"
    assert any(msg.get("role") == "user" and msg.get("content") == "hello bridge" for msg in ctx.messages)
