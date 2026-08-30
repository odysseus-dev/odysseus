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

    calls = {"memory": 0, "research": 0, "post": [], "recovery": []}

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
    def recover(*args, **kwargs):
        calls["recovery"].append(kwargs)
        return False

    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", recover)
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
    assert calls["recovery"] == [{"owner": "alice", "allow_live_probes": False}]


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
    recovery_calls = []

    def recover(*args, **kwargs):
        recovery_calls.append(kwargs)
        return False

    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", recover)
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
    assert recovery_calls == [{"owner": "alice", "allow_live_probes": False}]


class _RecoveryPredicate:
    def __or__(self, _other):
        return self


class _RecoveryColumn:
    def __eq__(self, _value):
        return _RecoveryPredicate()


class _RecoveryEndpointModel:
    is_enabled = _RecoveryColumn()
    owner = _RecoveryColumn()


class _RecoverySessionModel:
    id = _RecoveryColumn()
    owner = _RecoveryColumn()


class _RecoveryQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        if self.model is _RecoveryEndpointModel:
            return [self.db.endpoint]
        return []

    def first(self):
        if self.model is _RecoverySessionModel:
            return self.db.session_row
        return None


class _RecoveryDb:
    def __init__(self, endpoint, session_row):
        self.endpoint = endpoint
        self.session_row = session_row
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return _RecoveryQuery(self, model)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        return None


def _recovery_harness(monkeypatch, cached_models):
    from routes import chat_routes
    from src import chatgpt_subscription

    endpoint = SimpleNamespace(
        id="endpoint-1",
        base_url="https://chatgpt.com",
        cached_models=json.dumps(cached_models),
        hidden_models=None,
        provider_auth_id="provider-auth-1",
    )
    session_row = SimpleNamespace(
        id="session-1",
        owner="alice",
        model="",
        updated_at=None,
    )
    db = _RecoveryDb(endpoint, session_row)
    sess = SimpleNamespace(
        id="session-1",
        endpoint_url="https://chatgpt.com/backend-api/codex",
        model="",
        headers={},
    )

    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat_routes, "ModelEndpoint", _RecoveryEndpointModel)
    monkeypatch.setattr(chat_routes, "DBSession", _RecoverySessionModel)
    monkeypatch.setattr(chat_routes, "_session_url_matches_endpoint", lambda *args: True)
    monkeypatch.setattr(
        chatgpt_subscription,
        "is_chatgpt_subscription_base",
        lambda _url: True,
    )
    return chat_routes, db, endpoint, session_row, sess


def test_bearer_empty_model_recovery_fails_without_cache_or_live_probe(monkeypatch):
    chat_routes, db, endpoint, session_row, sess = _recovery_harness(monkeypatch, [])
    from src import chatgpt_subscription, endpoint_resolver

    def forbidden(*args, **kwargs):
        raise AssertionError("bearer recovery must not resolve credentials or fetch models")

    monkeypatch.setattr(chatgpt_subscription, "fetch_available_models", forbidden)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", forbidden)

    assert chat_routes._recover_empty_session_model(
        sess,
        "session-1",
        owner="alice",
        allow_live_probes=False,
    ) is False
    assert sess.model == ""
    assert session_row.model == ""
    assert endpoint.cached_models == "[]"
    assert db.commits == 0
    assert db.rollbacks == 0


def test_bearer_model_recovery_uses_cache_without_endpoint_or_session_writes(monkeypatch):
    chat_routes, db, endpoint, session_row, sess = _recovery_harness(
        monkeypatch,
        ["cached-model"],
    )
    from src import chatgpt_subscription, endpoint_resolver

    def forbidden(*args, **kwargs):
        raise AssertionError("bearer recovery must not resolve credentials or fetch models")

    monkeypatch.setattr(chatgpt_subscription, "fetch_available_models", forbidden)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", forbidden)

    assert chat_routes._recover_empty_session_model(
        sess,
        "session-1",
        owner="alice",
        allow_live_probes=False,
    ) is True
    assert sess.model == "cached-model"
    assert session_row.model == ""
    assert endpoint.cached_models == '["cached-model"]'
    assert db.commits == 0
    assert db.rollbacks == 0


def test_interactive_model_recovery_retains_live_catalog_and_persistence(monkeypatch):
    chat_routes, db, endpoint, session_row, sess = _recovery_harness(monkeypatch, [])
    from src import chatgpt_subscription, endpoint_resolver

    seen = {}

    def resolve(ep, owner=None):
        seen["resolve"] = (ep, owner)
        return ep.base_url, "owner-secret"

    def fetch(api_key):
        seen["fetch"] = api_key
        return ["gpt-live"]

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", resolve)
    monkeypatch.setattr(chatgpt_subscription, "fetch_available_models", fetch)

    # Interactive callers retain the helper's live-probe default.
    assert chat_routes._recover_empty_session_model(
        sess,
        "session-1",
        owner="alice",
    ) is True
    assert seen["resolve"] == (endpoint, "alice")
    assert seen["fetch"] == "owner-secret"
    assert sess.model == "gpt-live"
    assert session_row.model == "gpt-live"
    assert json.loads(endpoint.cached_models) == ["gpt-live"]
    assert db.commits == 2
    assert db.rollbacks == 0


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


def test_nested_legacy_approval_shapes_are_stripped_on_ingress_and_projection():
    from src.message_metadata import sanitize_projected_message_metadata

    metadata = {
        "safe": {"label": "keep"},
        CHAT_SESSION_APPROVAL_CONTEXT_MARKER: True,
        "approval_id": "legacy-root",
        "resolved": "approve",
        "session_id": "session-1",
        "tool_events": [
            {
                "ask_user": {
                    "kind": "tool_approval",
                    "approval_id": "legacy-nested",
                    "resolved": "approve",
                    "approved_by_interactive_session": True,
                    "session_id": "session-1",
                    "label": "Allow",
                }
            },
            {
                "kind": "tool_approval",
                "approval_id": "legacy-direct",
                "resolved": "approve",
                "session_id": "session-1",
                "label": "Allow",
            },
            ["not-a-metadata-mapping"],
        ],
    }

    client = sanitize_client_message_metadata(metadata)
    assert client == {"safe": {"label": "keep"}}

    projected = sanitize_projected_message_metadata(metadata)
    assert projected["safe"] == {"label": "keep"}
    assert CHAT_SESSION_APPROVAL_CONTEXT_MARKER not in projected
    assert "approval_id" not in projected
    assert "resolved" not in projected
    assert "session_id" not in projected
    assert "tool_events" in projected
    assert projected["tool_events"][0]["ask_user"] == {
        "kind": "tool_approval",
        "label": "Allow",
    }
    assert projected["tool_events"][1] == {
        "kind": "tool_approval",
        "label": "Allow",
    }


def test_session_metadata_parser_rejects_list_of_pairs_and_non_dict_values():
    from core.session_manager import _parse_message_metadata

    assert _parse_message_metadata(
        '[["tool_events", [{"ask_user": {"resolved": "approve"}}]]]'
    ) == {}
    assert _parse_message_metadata('["approval_id", "forged"]') == {}
    assert _parse_message_metadata('"forged"') == {}
    assert _parse_message_metadata("not-json") == {}
    assert _parse_message_metadata('{"safe": true}') == {"safe": True}


def test_hand_constructed_approval_cannot_mint_durable_chat_provenance():
    from src.tool_approval_provenance import create_chat_session_approval_grant
    from src.tool_approvals import ExactToolApproval, ToolApprovalStore
    from src.tool_capabilities import capabilities_for_action

    store = ToolApprovalStore()
    pending = store.create(
        owner="alice",
        session_id="session-1",
        origin_run_id="run-1",
        tool_name="bash",
        content="printf safe",
        workspace=None,
        external_untrusted_context_seen=False,
        capabilities=capabilities_for_action("bash", "printf safe"),
    )
    forged = ExactToolApproval(pending)
    with pytest.raises(HTTPException) as bearer_exc:
        create_chat_session_approval_grant(
            _request(),
            approval=forged,
            approval_id=pending.approval_id,
            session_id="session-1",
            owner="alice",
        )
    assert bearer_exc.value.status_code == 403

    request = _request(api_token=False, owner="alice", scopes=(), current_user="alice")
    assert create_chat_session_approval_grant(
        request,
        approval=forged,
        approval_id=pending.approval_id,
        session_id="session-1",
        owner="alice",
    ) is False


@pytest.mark.asyncio
async def test_bearer_memory_routes_reject_router_and_direct_entry_points(monkeypatch):
    import routes.memory_routes as memory_routes
    import inspect

    memory_manager = MagicMock()
    session_manager = MagicMock()
    router = memory_routes.setup_memory_routes(memory_manager, session_manager)
    request = _request()
    direct_cases = [
        ("/api/memory/debug", "POST", {"query": "secret"}),
        ("/api/memory/add", "POST", {}),
        ("/api/memory", "GET", {}),
        ("/api/memory/search", "POST", {"query": "secret", "session_id": None, "category": None}),
        ("/api/memory/timeline", "GET", {}),
        ("/api/memory/by-session/{session_id}", "GET", {"session_id": "session-1"}),
        ("/api/memory/extract", "POST", {"session": "session-1"}),
        ("/api/memory/audit", "POST", {"session": None}),
        ("/api/memory/import", "POST", {"session": None, "file": None}),
        ("/api/memory/{memory_id}/pin", "POST", {"memory_id": "memory-1"}),
        ("/api/memory/{memory_id}", "GET", {"memory_id": "memory-1"}),
        ("/api/memory/{memory_id}", "PUT", {"memory_id": "memory-1", "text": "replacement", "category": None}),
        ("/api/memory/{memory_id}", "DELETE", {"memory_id": "memory-1"}),
    ]
    for path, method, kwargs in direct_cases:
        endpoint = next(
            route.endpoint
            for route in router.routes
            if route.path == path and method in route.methods
        )
        with pytest.raises(HTTPException) as exc:
            result = endpoint(request, **kwargs)
            if inspect.isawaitable(result):
                await result
        assert exc.value.status_code == 403, path
    memory_manager.load.assert_not_called()

    app = FastAPI()
    app.include_router(router)
    headers = {
        "x-api-token": "1",
        "x-api-owner": "alice",
        "x-api-scopes": "chat",
    }
    async with _client(_PrincipalState(app)) as client:
        response = await client.get("/api/memory", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bearer_capability_suppresses_deferred_callbacks_and_message_events(monkeypatch):
    from routes import chat_helpers
    from src.auth_helpers import request_capability

    request = _request()
    capability = request_capability(request)
    assert capability.is_bearer is True
    assert capability.allow_deferred_work is False
    assert capability.allow_detached_execution is False
    assert capability.allow_message_events is False
    assert capability.allow_auto_naming is False

    sess = SimpleNamespace(
        history=[object()] * 8,
        endpoint_url="https://selected.example/v1",
        model="selected-model",
        headers={"Authorization": "Bearer selected"},
        name="New chat",
        add_message=MagicMock(),
    )
    webhook_manager = MagicMock()
    monkeypatch.setattr(
        chat_helpers,
        "_spawn_bg",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bearer scheduled work")),
    )
    monkeypatch.setattr(
        chat_helpers,
        "accumulate_token_usage",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bearer usage callback")),
    )
    chat_helpers.run_post_response_tasks(
        sess,
        SimpleNamespace(),
        "session-1",
        "hello",
        "answer",
        {"prompt_tokens": 1},
        {"auto_memory": True, "auto_skills": True},
        MagicMock(),
        MagicMock(),
        webhook_manager,
        agent_rounds=3,
        agent_tool_calls=3,
        skills_manager=MagicMock(),
        owner="alice",
        capability=capability,
    )
    webhook_manager.fire_and_forget.assert_not_called()

    with_marker = SimpleNamespace(
        get_context_messages=lambda: [{
            "role": "user",
            "content": "prior",
            "metadata": {CHAT_SESSION_APPROVAL_CONTEXT_MARKER: True, "safe": "yes"},
        }]
    )
    projected = chat_helpers._history_for_request_capability(with_marker, capability)
    assert projected == [{"role": "user", "content": "prior", "metadata": {"safe": "yes"}}]

    chat_helpers.fire_message_event(
        request,
        webhook_manager,
        "session-1",
        sess,
        "hello",
        capability=capability,
    )
    webhook_manager.fire_and_forget.assert_not_called()

    chat_handler = MagicMock()
    chat_helpers.add_user_message(
        sess,
        chat_handler,
        SimpleNamespace(
            attachment_meta=[],
            user_content="hello",
            text_for_context="hello",
        ),
        capability=capability,
    )
    chat_handler.update_session_name_if_needed.assert_not_called()


def test_bearer_cannot_reach_workspace_or_hwfit_direct_handlers(monkeypatch):
    from routes import hwfit_routes, workspace_routes

    bearer = _request()
    workspace_router = workspace_routes.setup_workspace_routes()
    browse = next(route.endpoint for route in workspace_router.routes if route.path == "/api/workspace/browse")
    vet = next(route.endpoint for route in workspace_router.routes if route.path == "/api/workspace/vet")
    with pytest.raises(HTTPException):
        browse(bearer, path="/")
    with pytest.raises(HTTPException):
        vet(bearer, path="/")

    hwfit_router = hwfit_routes.setup_hwfit_routes()
    for path in ("/api/hwfit/system", "/api/hwfit/models", "/api/hwfit/profiles", "/api/hwfit/image-models"):
        endpoint = next(route.endpoint for route in hwfit_router.routes if route.path == path)
        with pytest.raises(HTTPException):
            endpoint(request=bearer)


@pytest.mark.asyncio
async def test_codex_bearer_rejected_before_direct_and_router_host_control(monkeypatch):
    import routes.codex_routes as codex_routes

    router = codex_routes.setup_codex_routes()
    bearer = _request(scopes=("chat", "cookbook:read", "cookbook:launch"))
    direct_cases = [
        ("/api/codex/capabilities", "GET", (bearer,)),
        ("/api/codex/plugin.zip", "GET", (bearer,)),
        ("/api/codex/cookbook/tasks", "GET", (bearer,)),
        ("/api/codex/cookbook/serve", "POST", (bearer, {})),
        ("/api/codex/cookbook/output/{session_id}", "GET", (bearer, "serve-1")),
    ]
    for path, method, args in direct_cases:
        endpoint = next(
            route.endpoint
            for route in router.routes
            if route.path == path and method in route.methods
        )
        with pytest.raises(HTTPException) as exc:
            result = endpoint(*args)
            if hasattr(result, "__await__"):
                await result
        assert exc.value.status_code == 403

    app = FastAPI()
    app.include_router(router)
    headers = {
        "x-api-token": "1",
        "x-api-owner": "alice",
        "x-api-scopes": "cookbook:read,cookbook:launch",
    }
    async with _client(_PrincipalState(app)) as client:
        for method, path, kwargs in (
            ("GET", "/api/codex/capabilities", {}),
            ("GET", "/api/codex/cookbook/tasks", {}),
            ("POST", "/api/codex/cookbook/serve", {"json": {}}),
        ):
            response = await client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == 403, (path, response.text)
