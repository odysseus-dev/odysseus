"""Cycle-4 regressions for the API-token chat capability boundary.

These tests deliberately call route endpoints directly as well as exercising
the same request state that the auth middleware stamps.  Router dependencies
are useful defense in depth, but they must not be the only authorization
check on a callable FastAPI endpoint.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ModelEndpoint, Session as DbSession
from core.models import ChatMessage, Session


class _Request:
    def __init__(
        self,
        *,
        bearer=True,
        owner="alice",
        scopes=("chat",),
        current_user="api",
        body=None,
        auth_manager=None,
        query_params=None,
    ):
        self.state = SimpleNamespace(
            api_token=bearer,
            api_token_owner=owner if bearer else None,
            api_token_scopes=list(scopes),
            current_user=current_user,
        )
        self.app = SimpleNamespace(
            state=SimpleNamespace(auth_manager=auth_manager)
        )
        self.headers = {}
        self.query_params = query_params or {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self._body = body

    async def json(self):
        return self._body


def _endpoint(router, path, method):
    for route in reversed(router.routes):
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _isolated_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cycle4.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.mark.asyncio
async def test_search_alias_rejects_chat_bearer_on_every_standalone_entry_point(monkeypatch):
    from routes import search_routes as alias_routes
    from routes.search import search_routes

    # The flat import is a sys.modules shim; use it as the exercised entry
    # point so a future alias split cannot silently lose the gate.
    router = alias_routes.setup_search_routes(None)
    assert alias_routes is search_routes

    monkeypatch.setattr(search_routes, "comprehensive_web_search", lambda *a, **k: ("hit", []))
    monkeypatch.setattr(search_routes, "_call_provider", lambda *a, **k: [{"title": "hit"}])
    request = _Request()

    for path, kwargs in (
        ("/api/search/config", {}),
        ("/api/search/providers", {}),
        ("/api/search", {}),
        ("/api/search/query", {}),
    ):
        endpoint = _endpoint(router, path, "GET" if path.endswith(("config", "providers")) else "POST")
        with pytest.raises(HTTPException) as exc:
            await endpoint(request=request, **kwargs)
        assert exc.value.status_code == 403, path


def test_auto_sort_direct_handler_rejects_bearer_before_owner_side_effects(monkeypatch):
    from routes import session_routes as sr

    def unexpected(*args, **kwargs):
        raise AssertionError("bearer reached auto-sort side effects")

    manager = SimpleNamespace(
        get_sessions_for_user=unexpected,
        delete_session=unexpected,
    )
    router = sr.setup_session_routes(manager, {})
    auto_sort = _endpoint(router, "/api/sessions/auto-sort", "POST")

    with pytest.raises(HTTPException) as exc:
        auto_sort(request=_Request(), skip_llm=True)
    assert exc.value.status_code == 403


def test_admin_owned_bearer_cannot_use_browser_admin_upload_fallback(tmp_path, monkeypatch):
    from routes import upload_routes as ur

    file_id = "b" * 32 + ".png"
    file_path = tmp_path / file_id
    file_path.write_bytes(b"private upload")

    class _AuthManager:
        is_configured = True

        def is_admin(self, user):
            return user == "admin"

    handler = SimpleNamespace(
        upload_dir=str(tmp_path),
        validate_upload_id=lambda value: value == file_id,
        _load_upload_index=lambda: {
            "bob:file": {
                "id": file_id,
                "name": "bob.png",
                "mime": "image/png",
                "owner": "bob",
            }
        },
    )
    router, _cleanup = ur.setup_upload_routes(handler)
    download = _endpoint(router, "/api/upload/{file_id}", "GET")

    request = _Request(
        owner="admin",
        current_user="api",
        auth_manager=_AuthManager(),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(download(request, file_id))
    assert exc.value.status_code == 404


def test_bearer_session_listing_does_not_purge_other_users_incognito_rows(tmp_path, monkeypatch):
    from routes import session_routes as sr

    ts = _isolated_db(tmp_path)
    monkeypatch.setattr(sr, "SessionLocal", ts)
    db = ts()
    try:
        db.query(DbSession).delete()
        old = cdb.utcnow_naive() - timedelta(hours=2)
        ghost_id = "ghost-" + "a" * 8
        owner_id = "owner-" + "b" * 8
        db.add(DbSession(
            id=ghost_id,
            owner="bob",
            name="Nobody",
            endpoint_url="http://localhost",
            model="model",
            archived=False,
            created_at=old,
            updated_at=old,
        ))
        db.add(DbSession(
            id=owner_id,
            owner="alice",
            name="Alice chat",
            endpoint_url="http://localhost",
            model="model",
            archived=False,
        ))
        db.commit()
    finally:
        db.close()

    visible = SimpleNamespace(
        id=owner_id,
        owner="alice",
        name="Alice chat",
        model="model",
        endpoint_url="http://localhost",
        rag=False,
        archived=False,
    )
    manager = SimpleNamespace(
        get_sessions_for_user=lambda owner: {owner_id: visible},
    )
    router = sr.setup_session_routes(manager, {})
    list_sessions = _endpoint(router, "/api/sessions", "GET")

    result = list_sessions(request=_Request(query_params={"active_incognito_id": ""}))
    assert {item["id"] for item in result} == {owner_id}

    db = ts()
    try:
        assert db.query(DbSession).filter(DbSession.id == ghost_id).first() is not None
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["system", "tool"])
async def test_history_message_ingress_normalizes_privileged_client_roles(monkeypatch, role):
    from routes.history import history_routes as hr

    monkeypatch.setattr(hr, "_verify_session_owner", lambda *args, **kwargs: None)
    stored = []
    manager = SimpleNamespace(add_message=lambda sid, message: stored.append(message))
    router = hr.setup_history_routes(manager)
    add_message = _endpoint(router, "/api/session/{session_id}/message", "POST")

    request = _Request(body={"role": role, "content": "client content"})
    result = await add_message(request=request, session_id="sid")
    assert result == {"status": "ok"}
    assert stored[-1].role == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["system", "tool"])
async def test_bulk_message_ingress_normalizes_privileged_client_roles(monkeypatch, role):
    from routes import session_routes as sr

    monkeypatch.setattr(sr, "_verify_session_owner", lambda *args, **kwargs: None)
    stored = []
    session = SimpleNamespace(add_message=lambda message: stored.append(message))
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        save_sessions=lambda: None,
    )
    router = sr.setup_session_routes(manager, {})
    inject = _endpoint(router, "/api/session/{sid}/inject_messages", "POST")

    request = _Request(body={"messages": [{"role": role, "content": "client content"}]})
    result = await inject(request=request, sid="sid")
    assert result == {"ok": True, "count": 1}
    assert stored[-1].role == "user"


def test_server_owned_system_and_tool_messages_remain_available_to_context():
    session = Session(
        id="sid",
        name="chat",
        endpoint_url="",
        model="",
        history=[
            ChatMessage("system", "server policy"),
            ChatMessage("tool", "server result"),
        ],
    )
    assert [message["role"] for message in session.get_context_messages()] == ["system", "tool"]


def test_session_creation_passes_bearer_no_live_capability_to_model_validation(monkeypatch):
    from routes import session_routes as sr
    from src import llm_core

    monkeypatch.setattr(sr, "_reject_raw_endpoint_url_for_non_admin", lambda *args, **kwargs: None)
    seen = {}

    def list_model_ids(*args, **kwargs):
        seen.update(kwargs)
        return ["chosen"]

    monkeypatch.setattr(llm_core, "list_model_ids", list_model_ids)
    manager = SimpleNamespace(
        create_session=lambda **kwargs: SimpleNamespace(
            id=kwargs["session_id"],
            name=kwargs["name"],
            model=kwargs["model"],
            endpoint_url=kwargs["endpoint_url"],
            rag=kwargs["rag"],
            headers={},
        ),
    )
    router = sr.setup_session_routes(manager, {})
    create_session = _endpoint(router, "/api/session", "POST")

    result = create_session(
        request=_Request(),
        name="chat",
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="",
        rag=None,
        skip_validation=None,
        api_key="",
        endpoint_id="",
    )
    assert result.model == "chosen"
    assert seen["allow_live_probes"] is False


def test_bearer_session_creation_uses_pinned_only_cache_inventory(monkeypatch):
    from routes import session_routes as sr
    from src import database, llm_core

    endpoint = SimpleNamespace(
        id="ep",
        is_enabled=True,
        base_url="https://api.example.test/v1",
        api_key=None,
        endpoint_kind="api",
        cached_models=json.dumps(["stale-cached-model"]),
        pinned_models=json.dumps(["server-pinned-model"]),
        hidden_models=json.dumps(["stale-cached-model"]),
    )
    db = _EndpointDb(endpoint)
    monkeypatch.setattr(sr, "SessionLocal", lambda: db)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(sr, "_reject_raw_endpoint_url_for_non_admin", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        llm_core,
        "httpx_get_kimi_aware",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("bearer setup attempted a live model probe")
        ),
    )
    manager = SimpleNamespace(
        create_session=lambda **kwargs: SimpleNamespace(
            id=kwargs["session_id"],
            name=kwargs["name"],
            model=kwargs["model"],
            endpoint_url=kwargs["endpoint_url"],
            rag=kwargs["rag"],
            headers={},
        ),
    )
    router = sr.setup_session_routes(manager, {})
    create_session = _endpoint(router, "/api/session", "POST")

    result = create_session(
        request=_Request(),
        name="chat",
        endpoint_url="",
        model="",
        rag=None,
        skip_validation=None,
        api_key="",
        endpoint_id="ep",
    )

    assert result.model == "server-pinned-model"


def test_bearer_cache_only_model_normalization_rejects_forbidden_fallback(monkeypatch):
    from routes import chat_helpers
    from src import database, llm_core

    endpoint = SimpleNamespace(
        id="ep",
        is_enabled=True,
        base_url="https://api.example.test/v1",
        endpoint_kind="api",
        cached_models=json.dumps(["stale-cached-model"]),
        pinned_models=json.dumps(["server-pinned-model"]),
        hidden_models=json.dumps(["stale-cached-model"]),
    )
    db = _EndpointDb(endpoint)
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: db)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        llm_core,
        "httpx_get_kimi_aware",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cache-only normalization attempted a live probe")
        ),
    )

    allowed = SimpleNamespace(
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="server-pinned-model",
        owner="alice",
    )
    forbidden = SimpleNamespace(
        endpoint_url=allowed.endpoint_url,
        model="stale-cached-model",
        owner="alice",
    )

    assert chat_helpers._normalize_model_id_from_cache(allowed) == "server-pinned-model"
    assert chat_helpers._normalize_model_id_from_cache(forbidden) is None


def test_explicit_bearer_model_does_not_require_live_setup_probe(monkeypatch):
    from routes import session_routes as sr
    from src import llm_core

    endpoint = SimpleNamespace(
        id="ep",
        is_enabled=True,
        base_url="https://api.example.test/v1",
        api_key=None,
    )
    monkeypatch.setattr(sr, "SessionLocal", lambda: _EndpointDb(endpoint))

    def unexpected(*args, **kwargs):
        raise AssertionError("explicit bearer model triggered setup catalog probe")

    monkeypatch.setattr(llm_core, "list_model_ids", unexpected)
    manager = SimpleNamespace(
        create_session=lambda **kwargs: SimpleNamespace(
            id=kwargs["session_id"],
            name=kwargs["name"],
            model=kwargs["model"],
            endpoint_url=kwargs["endpoint_url"],
            rag=kwargs["rag"],
            headers={},
        ),
    )
    router = sr.setup_session_routes(manager, {})
    create_session = _endpoint(router, "/api/session", "POST")

    result = create_session(
        request=_Request(),
        name="chat",
        endpoint_url="",
        model="explicit-model",
        rag=None,
        skip_validation=None,
        api_key="",
        endpoint_id="ep",
    )
    assert result.model == "explicit-model"


@pytest.mark.asyncio
async def test_context_usage_and_context_info_pass_bearer_no_live_capability(monkeypatch):
    from routes import session_routes as sr
    from routes.history import history_routes as hr
    from src import model_context

    session = SimpleNamespace(
        endpoint_url="http://127.0.0.1:8080/v1/chat/completions",
        model="local-model",
        history=[ChatMessage("user", "hello")],
        get_context_messages=lambda: [{"role": "user", "content": "hello"}],
    )
    monkeypatch.setattr(hr, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "_verify_session_owner", lambda *args, **kwargs: None)
    hr_seen = []
    sr_seen = []

    def history_context(*args, **kwargs):
        hr_seen.append(kwargs)
        return 4096

    def session_context(*args, **kwargs):
        sr_seen.append(kwargs)
        return 4096

    # Both route modules import this helper lazily, so the same patched helper
    # proves each route family forwards the capability independently.
    monkeypatch.setattr(model_context, "get_context_length", history_context)
    history_manager = SimpleNamespace(get_session=lambda sid: session)
    session_manager = SimpleNamespace(get_session=lambda sid: session)
    history_router = hr.setup_history_routes(history_manager)
    session_router = sr.setup_session_routes(session_manager, {})

    # The first call records history's /context path; switch the shared patch
    # after it so the second route's call is separately attributable.
    history_context_endpoint = _endpoint(history_router, "/api/session/{session_id}/context", "GET")
    await history_context_endpoint(request=_Request(), session_id="sid")
    monkeypatch.setattr(model_context, "get_context_length", session_context)
    info_endpoint = _endpoint(session_router, "/api/session/{session_id}/context_info", "GET")
    await info_endpoint(request=_Request(), session_id="sid")

    assert hr_seen == [{"allow_live_probes": False}]
    assert sr_seen == [{"allow_live_probes": False}]


class _NoopDb:
    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def add(self, *args, **kwargs):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


@pytest.mark.asyncio
async def test_bearer_compaction_routes_forward_no_live_capability(monkeypatch):
    from routes import session_routes as sr
    from routes.history import history_routes as hr
    from src import endpoint_resolver, llm_core, model_context

    history = [ChatMessage("user", f"message {i}") for i in range(6)]
    session = SimpleNamespace(
        id="sid",
        owner="alice",
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="model",
        headers={},
        history=list(history),
        get_context_messages=lambda: [{"role": "user", "content": "message"}],
    )
    monkeypatch.setattr(hr, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(hr, "_reject_compact_during_active_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "_reject_compact_during_active_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda *args, **kwargs: (None, None, None))
    monkeypatch.setattr(hr, "SessionLocal", lambda: _NoopDb())
    monkeypatch.setattr(sr, "SessionLocal", lambda: _NoopDb())

    context_seen = []
    llm_seen = []

    def context_length(*args, **kwargs):
        context_seen.append(kwargs)
        return 4096

    async def llm_call_async(*args, **kwargs):
        llm_seen.append(kwargs)
        return "summary"

    monkeypatch.setattr(model_context, "get_context_length", context_length)
    monkeypatch.setattr(llm_core, "llm_call_async", llm_call_async)

    history_manager = SimpleNamespace(save_sessions=lambda: None)
    history_manager.get_session = lambda sid: session
    history_router = hr.setup_history_routes(history_manager)
    history_compact = _endpoint(history_router, "/api/session/{session_id}/compact", "POST")
    await history_compact(request=_Request(), session_id="sid")

    session.history = list(history)
    session_manager = SimpleNamespace(
        get_session=lambda sid: session,
        replace_messages=lambda *args: True,
    )
    session_router = sr.setup_session_routes(session_manager, {})
    session_compact = _endpoint(session_router, "/api/session/{session_id}/compact", "POST")
    await session_compact(request=_Request(), session_id="sid")

    # The history compactor asks for context directly. The session-route
    # compactor delegates context sizing to llm_call_async, so its explicit
    # capability is asserted on the two LLM calls below.
    assert context_seen == [{"allow_live_probes": False}]
    assert len(llm_seen) == 2
    assert all(call["allow_live_probes"] is False for call in llm_seen)


@pytest.mark.asyncio
async def test_rewrite_direct_handler_passes_bearer_no_live_capability(monkeypatch):
    from routes import chat_routes as cr

    monkeypatch.setattr(cr, "_verify_session_owner", lambda *args, **kwargs: None)
    seen = {}

    async def stream_llm(*args, **kwargs):
        seen.update(kwargs)
        yield 'data: {"delta":"rewritten"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(cr, "stream_llm", stream_llm)
    monkeypatch.setattr(cr, "SessionLocal", lambda: _NoopDb())
    session = SimpleNamespace(
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="model",
        headers={},
        history=[ChatMessage("assistant", "old")],
    )
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        save_sessions=lambda: None,
    )
    router = cr.setup_chat_routes(manager, None, None, None, None, None, webhook_manager=None)
    rewrite = _endpoint(router, "/api/rewrite", "POST")
    response = await rewrite(
        request=_Request(body={
            "session_id": "sid",
            "original_text": "old",
            "instruction": "shorter",
        })
    )
    _chunks = [chunk async for chunk in response.body_iterator]
    assert seen["allow_live_probes"] is False


class _EndpointDb:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.endpoint

    def all(self):
        return [self.endpoint]

    def close(self):
        return None


@pytest.mark.asyncio
async def test_sync_chat_fallback_uses_cached_models_without_provider_probe(monkeypatch):
    from routes import webhook_routes as wr
    from src import llm_core

    endpoint = SimpleNamespace(
        owner="alice",
        is_enabled=True,
        created_at=1,
        base_url="http://127.0.0.1:11434/v1",
        api_key="configured-key",
        cached_models=json.dumps(["stale-cached-model"]),
        pinned_models=json.dumps(["server-pinned-model"]),
        hidden_models=json.dumps(["stale-cached-model"]),
        provider_auth_id=None,
    )
    monkeypatch.setattr(wr, "SessionLocal", lambda: _EndpointDb(endpoint))
    monkeypatch.setattr(wr, "validate_public_http_url", lambda url: url)

    class _ForbiddenHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("bearer fallback attempted a model-list probe")

    monkeypatch.setattr(wr.httpx, "AsyncClient", _ForbiddenHttpClient)
    seen = {}

    async def llm_call_async(*args, **kwargs):
        seen.update(kwargs)
        return "reply"

    monkeypatch.setattr(llm_core, "llm_call_async", llm_call_async)
    class _Session:
        def __init__(self, **kwargs):
            self.endpoint_url = kwargs["endpoint_url"]
            self.model = kwargs["model"]
            self.headers = {}
            self.history = []

        def add_message(self, message):
            self.history.append(message)

    manager = SimpleNamespace(
        create_session=lambda **kwargs: _Session(**kwargs),
        save_sessions=lambda: None,
    )
    webhook_manager = SimpleNamespace(fire_and_forget=lambda *args, **kwargs: None)
    router = wr.setup_webhook_routes(webhook_manager, None, session_manager=manager)
    sync_chat = _endpoint(router, "/api/v1/chat", "POST")

    body = SimpleNamespace(
        message="hello",
        model=None,
        session=None,
        api_key=None,
        base_url=None,
        provider=None,
    )
    result = await sync_chat(request=_Request(), body=body)
    assert result["model"] == "server-pinned-model"
    assert seen["allow_live_probes"] is False
