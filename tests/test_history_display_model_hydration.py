"""Display pagination must stay separate from full model-context hydration."""

import json
from datetime import datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, ChatMessage as DbChatMessage, Session as DbSession
from core.models import ChatMessage, Session
from core.session_manager import SessionManager
from routes import chat_routes
from routes.history import history_routes
from routes import session_routes
from src.request_models import ChatRequest


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[DbSession.__table__, DbChatMessage.__table__],
    )
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_session(db_factory, *, session_id="session-1", message_count=6, stored_count=None):
    """Seed `message_count` real rows; `stored_count` overrides the denormalized
    sessions.message_count column so drift can be reproduced."""
    db = db_factory()
    try:
        db.add(
            DbSession(
                id=session_id,
                name="Long chat",
                endpoint_url="http://model.test/v1",
                model="test-model",
                owner="alice",
                message_count=message_count if stored_count is None else stored_count,
            )
        )
        start = datetime(2026, 1, 1, 12, 0, 0)
        for index in range(message_count):
            db.add(
                DbChatMessage(
                    id=f"message-{index}",
                    session_id=session_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"content-{index}",
                    timestamp=start + timedelta(seconds=index),
                )
            )
        db.commit()
    finally:
        db.close()


def _chat_message_selects(statements):
    return [
        " ".join(statement.lower().split())
        for statement in statements
        if statement.lstrip().lower().startswith("select")
        and "chat_messages" in statement.lower()
    ]


def _manager(db_factory, monkeypatch, sessions=None):
    """A real SessionManager bound to the temp DB, with load counting."""
    monkeypatch.setattr("core.session_manager.SessionLocal", db_factory)
    manager = object.__new__(SessionManager)
    manager.upload_handler = None
    manager.sessions = sessions if sessions is not None else {}
    manager.full_loads = 0

    original_load = manager._load_session_from_db

    def counting_load(session_id):
        manager.full_loads += 1
        return original_load(session_id)

    manager._load_session_from_db = counting_load
    return manager


def test_paginated_history_reads_only_count_and_requested_page(monkeypatch):
    engine, db_factory = _database()
    _seed_session(db_factory)

    class DisplayOnlyManager:
        def get_session(self, _session_id):
            raise AssertionError("paginated display history must not hydrate model context")

    monkeypatch.setattr(history_routes, "SessionLocal", db_factory)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *_args: None)

    app = FastAPI()
    app.include_router(history_routes.setup_history_routes(DisplayOnlyManager()))

    statements = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        response = TestClient(app).get("/api/history/session-1?limit=2")
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert [message["content"] for message in payload["history"]] == [
        "content-4",
        "content-5",
    ]
    assert payload["total"] == 6
    assert payload["offset"] == 4
    assert payload["has_more_before"] is True
    assert payload["has_more_after"] is False

    # One COUNT for the total plus one page read — never a full-transcript
    # select. The page bounds are asserted through the response above rather
    # than by matching SQL text.
    chat_selects = _chat_message_selects(statements)
    assert len(chat_selects) == 2, chat_selects
    assert sum("count(" in statement for statement in chat_selects) == 1


def test_production_router_order_reaches_bounded_canonical_history(monkeypatch):
    """The assembled app must not shadow canonical history with session routes."""
    engine, db_factory = _database()
    _seed_session(db_factory, message_count=1200)

    class DisplayOnlyManager:
        def get_session(self, _session_id):
            raise AssertionError("bounded initial history must not hydrate all messages")

    manager = DisplayOnlyManager()
    monkeypatch.setattr(
        session_routes,
        "router",
        APIRouter(prefix="/api", tags=["sessions"]),
    )
    monkeypatch.setattr(history_routes, "SessionLocal", db_factory)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *_args: None)

    app = FastAPI()
    app.include_router(session_routes.setup_session_routes(manager, {}))
    app.include_router(history_routes.setup_history_routes(manager))

    try:
        response = TestClient(app).get("/api/history/session-1?limit=24")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.request.url.params["limit"] == "24"
    payload = response.json()
    displayed = len(payload["history"])
    assert 0 < displayed <= payload["limit"] <= 100
    assert payload["total"] >= 1200
    assert payload["has_more_before"] is True
    assert displayed < payload["total"]


def test_incomplete_cached_history_hydrates_once_for_model_context(monkeypatch):
    engine, db_factory = _database()
    raw_multimodal = json.dumps(
        [
            {"type": "text", "text": "look at the source image"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
            },
        ]
    )
    db = db_factory()
    try:
        db.add(
            DbSession(
                id="session-1",
                name="Long chat",
                endpoint_url="http://model.test/v1",
                model="test-model",
                owner="alice",
                message_count=3,
            )
        )
        start = datetime(2026, 1, 1, 12, 0, 0)
        db.add_all(
            [
                DbChatMessage(
                    id="message-0",
                    session_id="session-1",
                    role="user",
                    content=raw_multimodal,
                    meta_data=json.dumps(
                        {
                            "attachments": [
                                {
                                    "id": "upload-1",
                                    "filename": "source.png",
                                    "content_type": "image/png",
                                }
                            ]
                        }
                    ),
                    timestamp=start,
                ),
                DbChatMessage(
                    id="message-1",
                    session_id="session-1",
                    role="assistant",
                    content="answer",
                    timestamp=start + timedelta(seconds=1),
                ),
                DbChatMessage(
                    id="message-2",
                    session_id="session-1",
                    role="system",
                    content="compaction summary",
                    meta_data=json.dumps({"hidden": True}),
                    timestamp=start + timedelta(seconds=2),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    manager = _manager(
        db_factory,
        monkeypatch,
        sessions={
            "session-1": Session(
                id="session-1",
                name="Long chat",
                endpoint_url="http://model.test/v1",
                model="test-model",
                owner="alice",
                history=[ChatMessage("user", "stale partial cache")],
                # Deliberately stale too: get_session must refresh metadata before
                # checking whether the cached transcript is complete.
                message_count=1,
            )
        },
    )

    try:
        hydrated = manager.get_session("session-1")
        first_full_loads = manager.full_loads
        warm = manager.get_session("session-1")
        second_full_loads = manager.full_loads
    finally:
        engine.dispose()

    assert hydrated is warm
    assert len(hydrated.history) == 3
    assert first_full_loads == 1
    assert second_full_loads == first_full_loads

    context = hydrated.get_context_messages()
    assert len(context) == 3
    assert context[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    assert context[0]["metadata"]["attachments"] == [
        {
            "id": "upload-1",
            "filename": "source.png",
            "content_type": "image/png",
        }
    ]
    hidden_summary = next(message for message in context if message["role"] == "system")
    assert hidden_summary["content"] == "compaction summary"
    assert hidden_summary["metadata"]["hidden"] is True


def test_inflated_message_count_column_does_not_reload_warm_sessions(monkeypatch):
    """A drifted-high sessions.message_count must not reload on every read.

    `_persist_message` swallows a failed insert while `add_message` has already
    appended in memory, so the next successful persist writes rows+1. Keyed on
    that column, the hydration gate would stay true forever and re-select the
    whole transcript on every send, edit, delete and truncate.
    """
    engine, db_factory = _database()
    _seed_session(db_factory, message_count=6, stored_count=8)

    manager = _manager(db_factory, monkeypatch)
    try:
        session = manager.get_session("session-1")
        cold_loads = manager.full_loads
        for _ in range(3):
            manager.get_session("session-1")
    finally:
        engine.dispose()

    assert len(session.history) == 6
    assert cold_loads == 1
    assert manager.full_loads == 1


def test_stale_low_message_count_column_still_hydrates_for_the_model(monkeypatch):
    """The other drift direction must not hand the model a truncated transcript.

    `_persist_message` writes message_count = 0 when the session is not cached.
    A partly-filled cache plus that stale-low column previously left the send
    path with whatever RAM happened to hold.
    """
    engine, db_factory = _database()
    _seed_session(db_factory, message_count=6, stored_count=0)

    manager = _manager(
        db_factory,
        monkeypatch,
        sessions={
            "session-1": Session(
                id="session-1",
                name="Long chat",
                endpoint_url="http://model.test/v1",
                model="test-model",
                owner="alice",
                history=[ChatMessage("user", "content-0")],
                message_count=0,
            )
        },
    )
    try:
        session = manager.get_session("session-1")
        manager.get_session("session-1")
    finally:
        engine.dispose()

    assert [message.content for message in session.history] == [
        f"content-{index}" for index in range(6)
    ]
    assert manager.full_loads == 1


def test_fork_after_restart_copies_the_real_transcript(monkeypatch):
    """Forking reads source.history, so it must hydrate through get_session.

    Display pagination no longer fills the cache, so a fork taken after a
    restart used to return HTTP 200 with an empty conversation.
    """
    engine, db_factory = _database()
    _seed_session(db_factory, message_count=6)

    # Restart state: metadata-only cache entry, exactly what load_sessions seeds.
    manager = _manager(db_factory, monkeypatch)
    manager.load_sessions()

    monkeypatch.setattr(history_routes, "SessionLocal", db_factory)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *_args: None)
    monkeypatch.setattr("core.models._SESSION_MANAGER_INSTANCE", manager)

    app = FastAPI()
    app.include_router(history_routes.setup_history_routes(manager))
    client = TestClient(app)

    try:
        page = client.get("/api/history/session-1?limit=2")
        assert page.status_code == 200
        assert len(manager.sessions["session-1"].history) == 0

        response = client.post("/api/session/session-1/fork", json={"keep_count": 4})
        assert response.status_code == 200
        payload = response.json()
        assert payload["kept"] == 4

        forked = manager.get_session(payload["id"])
        assert [message.content for message in forked.history] == [
            f"content-{index}" for index in range(4)
        ]
    finally:
        engine.dispose()


class _ContextBuildReached(Exception):
    pass


class _ToolPolicy:
    block_all_tool_calls = False

    def blocks(self, _tool_name):
        return False


class _ChatHandler:
    async def handle_memory_command(self, _session, _message):
        return None


def _json_request(path, payload):
    raw = json.dumps(payload).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


def _route_endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/chat", "/api/chat_stream"])
async def test_model_send_routes_hydrate_before_context_build(monkeypatch, path):
    # A real SessionManager over a real (temp) DB — a stub here would only
    # assert that the stub hydrates, not that SessionManager does.
    engine, db_factory = _database()
    _seed_session(db_factory, message_count=6, stored_count=8)
    manager = _manager(db_factory, monkeypatch)
    manager.load_sessions()  # restart state: metadata only, no messages cached
    contexts_built = []

    async def assert_complete_context(session, *_args, **_kwargs):
        contexts_built.append(session)
        assert [message.content for message in session.history] == [
            f"content-{index}" for index in range(6)
        ]
        raise _ContextBuildReached

    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda *_args: "alice")
    monkeypatch.setattr(
        chat_routes,
        "_clear_orphaned_session_endpoint",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        chat_routes,
        "_recover_empty_session_model",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *_args: None)
    monkeypatch.setattr(
        chat_routes,
        "build_effective_tool_policy",
        lambda **_kwargs: _ToolPolicy(),
    )
    monkeypatch.setattr(chat_routes, "build_chat_context", assert_complete_context)
    monkeypatch.setattr(
        chat_routes,
        "_resolve_request_workspace",
        lambda *_args: (None, False),
    )
    monkeypatch.setattr(chat_routes, "_classify_tool_intent", lambda *_args: None)
    monkeypatch.setattr(
        chat_routes,
        "_is_contextual_web_followup",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        chat_routes,
        "_is_contextual_browser_followup",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        chat_routes,
        "_resolve_workspace_from_message_path",
        lambda *_args: (None, None),
    )
    monkeypatch.setattr(
        chat_routes,
        "_reconcile_selected_route_from_request",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda *_args: "chat")
    monkeypatch.setattr(
        chat_routes,
        "_is_image_generation_session",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(chat_routes, "web_search_enabled_for_turn", lambda *_args: False)

    router = chat_routes.setup_chat_routes(
        manager,
        _ChatHandler(),
        object(),
        object(),
        object(),
        object(),
    )
    endpoint = _route_endpoint(router, path)

    async def send():
        if path == "/api/chat":
            await endpoint(
                _json_request(path, {}),
                ChatRequest(message="hello", session="session-1"),
            )
        else:
            await endpoint(
                _json_request(
                    path,
                    {"message": "hello", "session": "session-1"},
                )
            )

    try:
        with pytest.raises(_ContextBuildReached):
            await send()
        first_loads = manager.full_loads

        # Second send on the now-warm session: the transcript is complete, so
        # it must be served from RAM even though sessions.message_count is
        # still drifted high in the DB.
        with pytest.raises(_ContextBuildReached):
            await send()
    finally:
        engine.dispose()

    assert len(contexts_built) == 2
    assert contexts_built[0] is contexts_built[1]
    assert first_loads == 1
    assert manager.full_loads == 1
