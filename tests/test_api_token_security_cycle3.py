"""Cycle-3 regressions for the bearer capability boundary.

The tests intentionally call route endpoints directly as well as exercising
the shared helpers. FastAPI dependency execution is not a substitute for the
handler's own authorization checks when an endpoint can be called by another
in-process route or test harness.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _request(*, bearer=True, owner="alice", scopes=("chat",), current_user="api", auth_manager=None):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=bearer,
            api_token_owner=owner if bearer else None,
            api_token_scopes=list(scopes),
            current_user=current_user if bearer else current_user,
        ),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager)),
        headers={},
    )


class _JsonRequest:
    def __init__(self, body=None, *, bearer=True, owner="alice", scopes=("chat",)):
        self.state = SimpleNamespace(
            api_token=bearer,
            api_token_owner=owner if bearer else None,
            api_token_scopes=list(scopes),
            current_user="api" if bearer else owner,
        )
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self.headers = {}
        self.body = body or {}

    async def json(self):
        return self.body


def _latest_endpoint(router, path, method):
    for route in reversed(router.routes):
        if route.path == path and method in (route.methods or set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_admin_owned_bearer_does_not_inherit_raw_endpoint_or_model_privileges():
    from routes.chat_helpers import _allowed_models_for_request, _enforce_chat_privileges
    from routes.session_routes import _reject_raw_endpoint_url_for_non_admin

    auth_manager = MagicMock()
    auth_manager.is_admin.return_value = True
    auth_manager.get_privileges.side_effect = AssertionError("bearer used owner privilege lookup")
    request = _request(owner="admin", auth_manager=auth_manager)

    with pytest.raises(HTTPException) as exc:
        _reject_raw_endpoint_url_for_non_admin(
            request,
            "admin",
            endpoint_id="",
            endpoint_url="http://127.0.0.1:9/private",
        )
    assert exc.value.status_code == 403
    assert _allowed_models_for_request(request) is None
    _enforce_chat_privileges(request, SimpleNamespace(model="admin-only"))
    auth_manager.get_privileges.assert_not_called()

    cookie_request = _request(
        bearer=False,
        owner=None,
        current_user="admin",
        auth_manager=auth_manager,
    )
    auth_manager.get_privileges.side_effect = None
    auth_manager.get_privileges.return_value = {
        "allowed_models": ["admin-only"],
        "allowed_models_restricted": True,
    }
    _reject_raw_endpoint_url_for_non_admin(
        cookie_request,
        "admin",
        endpoint_id="",
        endpoint_url="http://127.0.0.1:9/private",
    )
    assert _allowed_models_for_request(cookie_request) == frozenset({"admin-only"})


class _SessionManager:
    def __init__(self):
        self.sessions = {}
        self.saved = 0

    def create_session(self, **kwargs):
        session = SimpleNamespace(
            id=kwargs["session_id"],
            name=kwargs.get("name", ""),
            endpoint_url=kwargs.get("endpoint_url", ""),
            model=kwargs.get("model", ""),
            rag=kwargs.get("rag", False),
            owner=kwargs.get("owner"),
            headers={},
            history=[],
        )
        self.sessions[session.id] = session
        return session

    def save_sessions(self):
        self.saved += 1


def test_bearer_session_lifecycle_routes_do_not_emit_webhook_or_event(monkeypatch):
    import src.event_bus as event_bus
    from routes import session_routes

    events = []
    monkeypatch.setattr(event_bus, "fire_event", lambda *args, **kwargs: events.append((args, kwargs)))
    manager = _SessionManager()
    webhook_manager = MagicMock()
    router = session_routes.setup_session_routes(
        manager,
        {
            "REQUEST_TIMEOUT": 1,
            "OPENAI_API_KEY": "server-key",
            "SESSIONS_FILE": "sessions.json",
        },
        webhook_manager=webhook_manager,
    )
    request = _request()

    create = _latest_endpoint(router, "/api/session", "POST")
    with pytest.raises(HTTPException) as exc:
        create(
            request,
            name="Private endpoint",
            endpoint_url="http://127.0.0.1:9/private",
            model="stored-model",
            rag="false",
            skip_validation="true",
            api_key="",
            endpoint_id="",
        )
    assert exc.value.status_code == 403
    assert manager.sessions == {}

    result = create(
        request,
        name="API chat",
        endpoint_url="",
        model="stored-model",
        rag="false",
        skip_validation="true",
        api_key="",
        endpoint_id="",
    )
    assert result.model == "stored-model"

    create_openai = _latest_endpoint(router, "/api/session/openai", "POST")
    with pytest.raises(HTTPException) as exc:
        create_openai(request, name="OpenAI", model="gpt-4o", rag="false")
    assert exc.value.status_code == 403
    assert len(manager.sessions) == 1
    webhook_manager.fire_and_forget.assert_not_called()
    assert events == []

    cookie_request = _request(bearer=False, owner=None, current_user="alice", scopes=())
    cookie_result = create(
        cookie_request,
        name="Browser chat",
        endpoint_url="",
        model="stored-model",
        rag="false",
        skip_validation="true",
        api_key="",
        endpoint_id="",
    )
    assert cookie_result.model == "stored-model"
    assert events and events[-1][0] == ("session_created", "alice")


def test_generic_email_dependency_rejects_bearer_before_legacy_fallback(monkeypatch):
    from routes import email_helpers

    with pytest.raises(HTTPException) as exc:
        email_helpers._require_auth(_request(owner="alice", scopes=("chat",)))
    assert exc.value.status_code == 403

    assert email_helpers._require_auth(
        _request(bearer=False, owner=None, current_user="alice", scopes=())
    ) == "alice"
    monkeypatch.setattr(email_helpers, "_auth_disabled", lambda: True)
    assert email_helpers._require_auth(
        _request(bearer=False, owner=None, current_user=None, scopes=())
    ) == ""


@pytest.mark.asyncio
async def test_bearer_context_builder_uses_no_live_model_or_context_probes(monkeypatch):
    from routes import chat_helpers
    from src.auth_helpers import request_capability

    calls = {"normalize": 0, "compact": []}

    class _ChatHandler:
        def validate_and_extract_preset(self, _preset_id):
            return 0.2, 64, None, None

        async def preprocess_message(self, message, att_ids, sess, **kwargs):
            assert kwargs["allow_tool_preprocessing"] is False
            return message, message, message, [], []

    class _ChatProcessor:
        def build_context_preface(self, **kwargs):
            return [], [], []

    def fail_normalize(*args, **kwargs):
        calls["normalize"] += 1
        raise AssertionError("bearer context performed a live model probe")

    async def fake_compact(*args, **kwargs):
        calls["compact"].append(kwargs)
        return args[3], 128000, False

    monkeypatch.setattr(chat_helpers, "_normalize_model_id_from_cache", lambda _sess: None)
    monkeypatch.setattr(chat_helpers, "normalize_model_id", fail_normalize)
    monkeypatch.setattr(chat_helpers, "maybe_compact", fake_compact)
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda _owner: {})

    session = SimpleNamespace(
        endpoint_url="http://127.0.0.1:9999/v1/chat/completions",
        model="uncached-model",
        headers={},
        owner="alice",
        history=[],
        get_context_messages=lambda: [],
        add_message=lambda _message: None,
    )
    request = _request()
    context = await chat_helpers.build_chat_context(
        session,
        request,
        _ChatHandler(),
        _ChatProcessor(),
        message="hello",
        session_id="session-1",
        incognito=True,
        allow_tool_preprocessing=False,
        persist_user_message=False,
        capability=request_capability(request),
    )

    assert context.context_length == 128000
    assert calls["normalize"] == 0
    assert calls["compact"] == [{"owner": "alice", "allow_live_probes": False}]


def test_model_and_context_no_live_probe_options_do_not_touch_endpoints(monkeypatch):
    from src import llm_core, model_context

    monkeypatch.setattr(llm_core, "_configured_cached_model_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        llm_core,
        "httpx_get_kimi_aware",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model endpoint touched")),
    )
    assert llm_core.list_model_ids(
        "http://127.0.0.1:9999/v1",
        allow_live_probes=False,
    ) == []
    assert llm_core.normalize_model_id(
        "http://127.0.0.1:9999/v1",
        "uncached-model",
        allow_live_probes=False,
    ) is None

    monkeypatch.setattr(model_context, "_context_cache", {})
    monkeypatch.setattr(
        model_context,
        "httpx",
        SimpleNamespace(
            get=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("context endpoint touched")
            )
        ),
    )
    assert model_context.get_context_length(
        "http://127.0.0.1:9999/v1",
        "uncached-model",
        allow_live_probes=False,
    ) == model_context.DEFAULT_CONTEXT
    assert model_context.get_context_length(
        "http://127.0.0.1:9999/v1",
        "gpt-4o",
        allow_live_probes=False,
    ) == 128000
    assert model_context._context_cache == {}


def _forged_metadata():
    return {
        "safe": {"label": "retain"},
        "_tool_approval_chat_session_granted": True,
        "approval_id": "approval-1",
        "approved_by_interactive_session": True,
        "resolved": "approve",
        "session_id": "session-1",
        "tool_events": [
            {
                "kind": "tool_approval",
                "approval_id": "nested-1",
                "resolved": "approve",
                "ask_user": {
                    "approval_id": "nested-2",
                    "approved_by_interactive_session": True,
                    "label": "display-only",
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_bearer_history_and_fork_scrub_legacy_approval_metadata(monkeypatch):
    import src.event_bus as event_bus
    from core.models import ChatMessage
    from routes.history import history_routes

    display_source = SimpleNamespace(
        id="source",
        name="Source",
        owner="alice",
        endpoint_url="https://example.test/v1",
        model="model",
        history=[
            ChatMessage("user", "hello", _forged_metadata()),
            {"role": "assistant", "content": "answer", "metadata": _forged_metadata()},
        ],
    )
    fork_source = SimpleNamespace(
        id="source",
        name="Source",
        owner="alice",
        endpoint_url="https://example.test/v1",
        model="model",
        history=[ChatMessage("user", "hello", _forged_metadata())],
    )

    class _Forked:
        def __init__(self):
            self.history = []

        def add_message(self, message):
            self.history.append(message)

    class _Manager:
        def __init__(self, source):
            self.source = source
            self.created = None

        def get_session(self, _session_id):
            return self.source

        def create_session(self, **kwargs):
            self.created = _Forked()
            return self.created

    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    events = []
    monkeypatch.setattr(event_bus, "fire_event", lambda *args, **kwargs: events.append(args))
    display_manager = _Manager(display_source)
    display_router = history_routes.setup_history_routes(display_manager)
    history_endpoint = _latest_endpoint(display_router, "/api/history/{session_id}", "GET")
    request = _JsonRequest({"keep_count": 1})

    displayed = await history_endpoint(request, "source")
    assert len(displayed["history"]) == 2
    for entry in displayed["history"]:
        metadata = entry.get("metadata", {})
        assert metadata.get("safe") == {"label": "retain"}
        assert all(
            field not in metadata
            for field in (
                "_tool_approval_chat_session_granted",
                "approval_id",
                "approved_by_interactive_session",
                "resolved",
                "session_id",
            )
        )
        assert metadata["tool_events"][0]["ask_user"] == {"label": "display-only"}

    fork_manager = _Manager(fork_source)
    fork_router = history_routes.setup_history_routes(fork_manager)
    fork_endpoint = _latest_endpoint(fork_router, "/api/session/{session_id}/fork", "POST")
    forked = await fork_endpoint(request, "source")
    assert forked["status"] == "ok"
    assert events == []
    assert fork_manager.created.history[0].metadata["safe"] == {"label": "retain"}
    assert "approval_id" not in fork_manager.created.history[0].metadata


class _ColumnQuery:
    def __init__(self, result, *, count=0, rows=None):
        self.result = result
        self.count_value = count
        self.rows = rows or []

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def offset(self, value):
        return self

    def limit(self, value):
        return self

    def first(self):
        return self.result

    def count(self):
        return self.count_value

    def all(self):
        return self.rows


class _HistoryDb:
    def __init__(self, history_routes, session_row, message_rows):
        self.history_routes = history_routes
        self.session_row = session_row
        self.message_rows = message_rows

    def query(self, model):
        if model is self.history_routes.DbSession:
            return _ColumnQuery(self.session_row)
        return _ColumnQuery(None, count=len(self.message_rows), rows=self.message_rows)

    def close(self):
        return None


@pytest.mark.asyncio
async def test_bearer_paginated_history_scrubs_db_projection(monkeypatch):
    from routes.history import history_routes
    db_session = SimpleNamespace(model="model", endpoint_url="https://example.test/v1", name="Chat")
    db_message = SimpleNamespace(
        role="assistant",
        content="answer",
        meta_data=json.dumps(_forged_metadata()),
        timestamp=datetime(2026, 1, 1, 0, 0, 0),
    )
    db = _HistoryDb(history_routes, db_session, [db_message])
    monkeypatch.setattr(history_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    router = history_routes.setup_history_routes(SimpleNamespace())
    endpoint = _latest_endpoint(router, "/api/history/{session_id}", "GET")

    payload = await endpoint(_JsonRequest(), "source", limit=10, offset=0)
    metadata = payload["history"][0]["metadata"]
    assert metadata["safe"] == {"label": "retain"}
    assert "approval_id" not in metadata
    assert "approved_by_interactive_session" not in metadata
    assert metadata["tool_events"][0]["ask_user"] == {"label": "display-only"}


@pytest.mark.asyncio
async def test_sync_bearer_chat_keeps_response_but_suppresses_completion_webhook(monkeypatch):
    from routes.webhook import webhook_routes
    from src import llm_core

    class _Session:
        def __init__(self, kwargs):
            self.endpoint_url = kwargs["endpoint_url"]
            self.model = kwargs["model"]
            self.owner = kwargs["owner"]
            self.headers = {}
            self.history = []

        def add_message(self, message):
            self.history.append(message)

    class _Manager:
        def __init__(self):
            self.created = []

        def create_session(self, **kwargs):
            session = _Session(kwargs)
            self.created.append(session)
            return session

        def save_sessions(self):
            return None

    class _Webhooks:
        def __init__(self):
            self.events = []

        def fire_and_forget(self, event, payload):
            self.events.append((event, payload))

    async def fake_llm(*args, **kwargs):
        return "answer"

    monkeypatch.setattr(webhook_routes, "validate_public_http_url", lambda value: value)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    webhook_manager = _Webhooks()
    router = webhook_routes.setup_webhook_routes(
        webhook_manager,
        auth_manager=None,
        session_manager=_Manager(),
    )
    endpoint = _latest_endpoint(router, "/api/v1/chat", "POST")
    body = SimpleNamespace(
        message="hello",
        model="gpt-4o",
        session=None,
        api_key="test-key",
        base_url="https://api.example.com/v1",
        provider=None,
    )

    result = await endpoint(_request(), body)
    assert result["response"] == "answer"
    assert webhook_manager.events == []


@pytest.mark.asyncio
async def test_upload_vision_handlers_reject_bearer_before_ai_or_cache_work():
    from routes import upload_routes

    upload_handler = MagicMock()
    router, _cleanup = upload_routes.setup_upload_routes(upload_handler)
    get_vision = _latest_endpoint(router, "/api/upload/{file_id}/vision", "GET")
    put_vision = _latest_endpoint(router, "/api/upload/{file_id}/vision", "PUT")
    request = _request()

    for endpoint, kwargs in ((get_vision, {"force": 0}), (put_vision, {})):
        with pytest.raises(HTTPException) as exc:
            result = endpoint(request, "upload-1", **kwargs)
            if hasattr(result, "__await__"):
                await result
        assert exc.value.status_code == 403
    upload_handler.validate_upload_id.assert_not_called()

    cookie_request = _request(bearer=False, owner=None, current_user="alice", scopes=())
    with pytest.raises(HTTPException) as exc:
        result = get_vision(cookie_request, "upload-1", force=0)
        if hasattr(result, "__await__"):
            await result
    assert exc.value.status_code == 404
    upload_handler.validate_upload_id.assert_called_once_with("upload-1")
