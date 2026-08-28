"""Regression coverage for the API-token forward-fix boundary.

These tests deliberately exercise both sides of FastAPI's router dependency
boundary: real ASGI requests run router dependencies, while direct endpoint
calls must still hit the same security helper before doing work.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from core.models import ChatMessage, Session
from src.auth_helpers import (
    effective_user,
    require_api_token_owner,
    require_chat_scope,
    require_interactive_request,
)
from src.message_metadata import sanitize_client_message_metadata
from src.request_models import ChatRequest
from src.tool_approval_scopes import CHAT_SESSION_APPROVAL_CONTEXT_MARKER


def _request(*, api_token=True, owner="alice", scopes=("chat",), current_user="api"):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=api_token,
            api_token_owner=owner,
            api_token_scopes=list(scopes),
            current_user=current_user,
        ),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        headers={},
    )


def test_token_scope_gate_requires_a_real_owner_and_normalizes_scope_input():
    assert require_chat_scope(
        _request(owner=" alice ", scopes=(" CHAT ",))
    ) == "alice"
    assert effective_user(_request(owner=" alice ")) == "alice"

    for owner in (None, "", "   "):
        with pytest.raises(HTTPException) as exc:
            require_chat_scope(_request(owner=owner))
        assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        require_chat_scope(_request(scopes=(["invalid"],)))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("owner", ["api", "internal-tool", "SYSTEM", " ", None])
def test_bearer_owner_gate_rejects_sentinels_and_ownerless_values(owner):
    with pytest.raises(HTTPException) as exc:
        require_api_token_owner(_request(owner=owner))
    assert exc.value.status_code == 403


def test_interactive_gate_rejects_api_sentinel_even_without_bearer_flag():
    with pytest.raises(HTTPException) as exc:
        require_interactive_request(
            _request(api_token=False, owner=None, scopes=(), current_user="api")
        )
    assert exc.value.status_code == 403


def test_interactive_gate_rejects_bearer_but_preserves_cookie_and_anonymous_modes():
    with pytest.raises(HTTPException) as exc:
        require_interactive_request(_request())
    assert exc.value.status_code == 403

    assert require_interactive_request(
        _request(api_token=False, owner="alice", scopes=(), current_user="alice")
    ) == "alice"
    assert require_interactive_request(
        _request(api_token=False, owner=None, scopes=(), current_user=None)
    ) is None


class _PrincipalState:
    """Inject the same request.state fields as auth middleware, without auth."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"x-api-token") == b"1":
                raw_scopes = headers.get(b"x-api-scopes", b"").decode()
                scopes = [item for item in raw_scopes.split(",") if item]
                scope["state"] = {
                    "api_token": True,
                    "api_token_owner": headers.get(b"x-api-owner", b"").decode() or None,
                    "api_token_scopes": scopes,
                    "current_user": "api",
                }
            else:
                scope["state"] = {
                    "api_token": False,
                    "current_user": headers.get(b"x-user", b"").decode() or None,
                }
        await self.app(scope, receive, send)


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://forward-fix.test",
    )


@pytest.mark.asyncio
async def test_real_chat_and_search_routes_run_scope_dependencies(monkeypatch):
    from routes.chat_routes import setup_chat_routes
    from routes.search.search_routes import setup_search_routes

    app = FastAPI()
    app.include_router(setup_chat_routes(None, None, None, None, None, None))
    app.include_router(setup_search_routes(None))

    token_headers = {
        "x-api-token": "1",
        "x-api-owner": "alice",
        "x-api-scopes": "email:read",
    }
    chat_headers = {**token_headers, "x-api-scopes": "chat"}

    async with _client(_PrincipalState(app)) as client:
        # Both standalone direct-search POST routes are capability-gated.
        for path, body in (
            ("/api/search", {"query": "private"}),
            ("/api/search/query", {"query": "private", "provider": "brave"}),
        ):
            response = await client.post(path, json=body, headers=token_headers)
            assert response.status_code == 403, (path, response.text)

        # The chat router's GET alias is gated too, and the detached-run
        # controls add the stricter interactive-principal check after chat
        # capability authorization succeeds.
        assert (await client.get("/api/search?q=private", headers=token_headers)).status_code == 403
        assert (await client.get("/api/chat/stream_status/sid", headers=chat_headers)).status_code == 403

        # Cookie and auth-disabled-shaped requests retain the old no-query
        # behavior rather than being rejected by bearer policy.
        assert (await client.get("/api/search?q=", headers={"x-user": "alice"})).status_code == 200
        assert (await client.get("/api/search?q=", headers={})).status_code == 200


@pytest.mark.asyncio
async def test_real_agent_capable_routers_reject_bearer_principals():
    from routes.assistant_routes import setup_assistant_routes
    from routes.research.research_routes import setup_research_routes
    from routes.skills_routes import setup_skills_routes
    from routes.task.task_routes import setup_task_routes

    app = FastAPI()
    app.include_router(setup_task_routes(MagicMock()))
    app.include_router(setup_skills_routes(MagicMock()))
    app.include_router(setup_assistant_routes(MagicMock()))
    app.include_router(setup_research_routes(SimpleNamespace(_active_tasks={})))

    headers = {
        "x-api-token": "1",
        "x-api-owner": "alice",
        "x-api-scopes": "chat",
    }
    requests = (
        ("/api/tasks/meta/events", "get", None),
        ("/api/skills/index", "get", None),
        ("/api/assistant/available-timezones", "get", None),
        ("/api/research/active", "get", None),
    )
    async with _client(_PrincipalState(app)) as client:
        for path, method, body in requests:
            response = await client.request(method.upper(), path, headers=headers)
            assert response.status_code == 403, (path, response.text)


@pytest.mark.asyncio
async def test_direct_auxiliary_handlers_keep_their_bearer_gates():
    from routes.chat_routes import setup_chat_routes
    from routes.search.search_routes import setup_search_routes

    chat_router = setup_chat_routes(None, None, None, None, None, None)
    search_router = setup_search_routes(None)
    request = _request(scopes=("email:read",))

    chat_routes = {route.path: route.endpoint for route in chat_router.routes}
    search_routes = {route.path: route.endpoint for route in search_router.routes}

    with pytest.raises(HTTPException):
        await chat_routes["/api/search"](request, q="private", limit=20)
    with pytest.raises(HTTPException):
        await chat_routes["/api/inject_context/{session_id}"](request, "sid", "context")
    with pytest.raises(HTTPException):
        await chat_routes["/api/rewrite"](request)
    with pytest.raises(HTTPException):
        await search_routes["/api/search"](request)
    with pytest.raises(HTTPException):
        await search_routes["/api/search/query"](request)

    with pytest.raises(HTTPException):
        await chat_routes["/api/chat/resume/{session_id}"](request, "sid")


@pytest.mark.asyncio
async def test_direct_task_skill_assistant_and_research_handlers_reject_bearer():
    from routes.assistant_routes import setup_assistant_routes
    from routes.research.research_routes import setup_research_routes
    from routes.skills_routes import setup_skills_routes
    from routes.task.task_routes import setup_task_routes

    request = _request()

    task_router = setup_task_routes(MagicMock())
    task_create = next(route.endpoint for route in task_router.routes if route.path == "/api/tasks" and "POST" in route.methods)
    with pytest.raises(HTTPException):
        await task_create(request, req=SimpleNamespace())

    skills_router = setup_skills_routes(MagicMock())
    skill_test = next(route.endpoint for route in skills_router.routes if route.path == "/api/skills/{skill_id}/test")
    with pytest.raises(HTTPException):
        await skill_test(request, "demo")

    assistant_router = setup_assistant_routes(MagicMock())
    assistant_session = next(route.endpoint for route in assistant_router.routes if route.path == "/api/assistant/session")
    with pytest.raises(HTTPException):
        await assistant_session(request)

    research_router = setup_research_routes(SimpleNamespace(_active_tasks={}))
    research_active = next(route.endpoint for route in research_router.routes if route.path == "/api/research/active")
    with pytest.raises(HTTPException):
        await research_active(request)


class _MetaColumn:
    def __eq__(self, _value):
        return True

    def desc(self):
        return self


class _DbChatMessage:
    session_id = _MetaColumn()
    role = _MetaColumn()
    timestamp = _MetaColumn()


class _MetaQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.row


class _MetaDb:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    def query(self, _model):
        return _MetaQuery(self.row)

    def commit(self):
        self.commits += 1

    def close(self):
        return None


class _JsonRequest:
    def __init__(self, body):
        self.state = SimpleNamespace(
            api_token=False,
            api_token_owner=None,
            api_token_scopes=[],
            current_user="alice",
        )
        self.body = body

    async def json(self):
        return self.body


@pytest.mark.asyncio
async def test_history_metadata_route_normalizes_list_pairs_before_merging(monkeypatch):
    import routes.history.history_routes as history_routes

    session = SimpleNamespace(
        history=[ChatMessage("assistant", "answer", {"keep": "yes"})],
    )
    db_message = SimpleNamespace(meta_data=json.dumps({"keep": "yes"}))
    db = _MetaDb(db_message)
    manager = SimpleNamespace(
        get_session=lambda _session_id: session,
        save_sessions=lambda: None,
    )
    monkeypatch.setattr(history_routes, "DbChatMessage", _DbChatMessage)
    monkeypatch.setattr(history_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *args, **kwargs: None)

    router = history_routes.setup_history_routes(manager)
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/session/{session_id}/update-last-meta"
    )
    body = {
        "metadata": [
            ["tool_events", [{"ask_user": {"resolved": "approve"}}]],
            [CHAT_SESSION_APPROVAL_CONTEXT_MARKER, True],
        ]
    }
    response = await endpoint(_JsonRequest(body), "sid")

    assert response == {"status": "ok"}
    assert session.history[0].metadata == {"keep": "yes"}
    assert json.loads(db_message.meta_data) == {"keep": "yes"}


def test_context_projection_discards_forged_and_malformed_metadata():
    malformed = ChatMessage(
        "user",
        "malformed",
        metadata=[[CHAT_SESSION_APPROVAL_CONTEXT_MARKER, True]],
    )
    forged = ChatMessage(
        "user",
        "forged",
        metadata={CHAT_SESSION_APPROVAL_CONTEXT_MARKER: True},
    )
    session = Session(
        id="sid",
        name="Chat",
        endpoint_url="https://example.invalid/v1",
        model="test",
        history=[malformed, forged],
    )

    projected = session.get_context_messages()

    assert all(
        CHAT_SESSION_APPROVAL_CONTEXT_MARKER not in (message.get("metadata") or {})
        for message in projected
    )
    assert all(not isinstance(message.get("metadata"), list) for message in projected)


def test_bearer_context_preprocessing_does_not_fetch_embedded_urls(monkeypatch):
    import src.chat_processor as chat_processor

    calls = []

    def fetch(url):
        calls.append(url)
        return {"success": True, "content": "must not be reached"}

    monkeypatch.setattr(chat_processor, "fetch_webpage_content", fetch)
    processor = chat_processor.ChatProcessor(
        memory_manager=SimpleNamespace(load=lambda owner=None: []),
        personal_docs_manager=SimpleNamespace(rag_manager=None),
        skills_manager=None,
    )
    preface, _, _ = processor.build_context_preface(
        message="Summarize https://example.test/private",
        session=SimpleNamespace(endpoint_url="", model="", headers={}),
        use_web=False,
        use_rag=False,
        use_memory=False,
        allow_tool_preprocessing=False,
    )

    assert calls == []
    assert not any(
        (message.get("metadata") or {}).get("source", "").startswith("web page:")
        for message in preface
    )


@pytest.mark.asyncio
async def test_sync_bearer_chat_cannot_use_research_memory_or_background_extraction(monkeypatch):
    from routes import chat_routes

    calls = {"memory": 0, "research": 0, "post": []}

    class _ChatHandler:
        async def handle_memory_command(self, _session, _message):
            calls["memory"] += 1
            return None

    class _ResearchHandler:
        async def call_research_service(self, *args, **kwargs):
            calls["research"] += 1
            return "research result"

    session = SimpleNamespace(
        endpoint_url="https://selected.example/v1",
        model="selected-model",
        headers={"Authorization": "Bearer selected"},
        history=[],
        add_message=lambda message: session.history.append(message),
    )
    manager = SimpleNamespace(
        get_session=lambda _session_id: session,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user="alice",
        messages=[{"role": "user", "content": "hello"}],
        context_length=100,
        uprefs={},
        preset=SimpleNamespace(temperature=0.2, max_tokens=32, character_name=None),
    )

    async def build_context(*args, **kwargs):
        assert kwargs["allow_tool_preprocessing"] is False
        return context

    async def llm_call(*args, **kwargs):
        return "answer", args[0][0], "selected-model"

    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "build_chat_context", build_context)
    monkeypatch.setattr(chat_routes, "resolve_foreground_model_policy", lambda *args, **kwargs: SimpleNamespace(enabled=False, eligible_statuses=set()))
    monkeypatch.setattr(chat_routes, "build_foreground_model_candidates", lambda *args, **kwargs: [("https://selected.example/v1", "selected-model", {})])
    monkeypatch.setattr(chat_routes, "build_foreground_route_descriptors", lambda *args, **kwargs: [{"endpoint_id": None, "endpoint_label": "Selected route"}])
    monkeypatch.setattr(chat_routes, "llm_call_async_with_route_fallback", llm_call)
    monkeypatch.setattr(chat_routes, "apply_compaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "clean_thinking_for_save", lambda reply, metadata: (reply, metadata))
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: calls["post"].append(kwargs))

    import core.database as database

    monkeypatch.setattr(database, "update_session_last_accessed", lambda _session_id: None)
    router = chat_routes.setup_chat_routes(
        manager,
        _ChatHandler(),
        SimpleNamespace(),
        SimpleNamespace(),
        _ResearchHandler(),
        SimpleNamespace(),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/chat")
    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _request(),
            ChatRequest(
                message="research this",
                session="sid",
                use_research=True,
            ),
        )
    assert exc.value.status_code == 403

    result = await endpoint(
        _request(),
        ChatRequest(
            message="remember this and research it",
            session="sid",
            use_research=False,
        ),
    )

    assert result["response"] == "answer"
    assert calls["memory"] == 0
    assert calls["research"] == 0
    assert calls["post"] and calls["post"][0]["allow_background_extraction"] is False


@pytest.mark.asyncio
async def test_stream_bearer_chat_disables_deferred_memory_extraction(monkeypatch):
    from routes import chat_routes
    from tests.test_foreground_model_routing import _chat_stream_endpoint

    captured = {}
    endpoint = _chat_stream_endpoint(
        monkeypatch,
        "chat",
        captured,
        capture_completion=True,
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        state=SimpleNamespace(
            api_token=True,
            api_token_owner="alice",
            api_token_scopes=["chat"],
            current_user="api",
        ),
        _form={"message": "hello", "session": "session-1", "mode": "chat"},
    )

    async def form():
        return request._form

    request.form = form

    response = await endpoint(request)
    async for _chunk in response.body_iterator:
        pass

    assert captured["post_processed"]
    assert captured["post_processed"][0][1]["allow_background_extraction"] is False


@pytest.mark.asyncio
async def test_stream_bearer_chat_cannot_dispatch_image_generation(monkeypatch):
    from routes import chat_routes
    from tests.test_foreground_model_routing import _chat_stream_endpoint

    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, "chat", captured)
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *args, **kwargs: True)
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        state=SimpleNamespace(
            api_token=True,
            api_token_owner="alice",
            api_token_scopes=["chat"],
            current_user="api",
        ),
        _form={"message": "generate an image", "session": "session-1", "mode": "chat"},
    )

    async def form():
        return request._form

    request.form = form

    with pytest.raises(HTTPException) as exc:
        await endpoint(request)
    assert exc.value.status_code == 403
    assert "image" in str(exc.value.detail).lower()
    assert "chat" not in captured
