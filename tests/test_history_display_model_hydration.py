"""Display pagination must stay separate from full model-context hydration."""

import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
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


def _seed_session(db_factory, *, session_id="session-1", message_count=6):
    db = db_factory()
    try:
        db.add(
            DbSession(
                id=session_id,
                name="Long chat",
                endpoint_url="http://model.test/v1",
                model="test-model",
                owner="alice",
                message_count=message_count,
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

    chat_selects = _chat_message_selects(statements)
    assert len(chat_selects) == 2, chat_selects
    assert sum("count(" in statement for statement in chat_selects) == 1
    page_select = next(statement for statement in chat_selects if "count(" not in statement)
    assert " limit " in page_select
    assert " offset " in page_select


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

    monkeypatch.setattr("core.session_manager.SessionLocal", db_factory)
    manager = object.__new__(SessionManager)
    manager.upload_handler = None
    manager.sessions = {
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
    }

    statements = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        hydrated = manager.get_session("session-1")
        first_full_loads = len(_chat_message_selects(statements))
        warm = manager.get_session("session-1")
        second_full_loads = len(_chat_message_selects(statements))
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
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


class _ContextBuildReached(Exception):
    pass


class _ToolPolicy:
    block_all_tool_calls = False

    def blocks(self, _tool_name):
        return False


class _ChatHandler:
    async def handle_memory_command(self, _session, _message):
        return None


class _HydratingSendManager:
    def __init__(self):
        self.calls = 0
        self.hydrations = 0
        self.session = Session(
            id="session-1",
            name="Long chat",
            endpoint_url="http://model.test/v1",
            model="test-model",
            owner="alice",
            history=[],
            message_count=1,
        )

    def get_session(self, _session_id):
        self.calls += 1
        if not self.session.history:
            self.hydrations += 1
            self.session.history = [
                ChatMessage("system", "complete model context", {"hidden": True})
            ]
        return self.session


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
    manager = _HydratingSendManager()

    async def assert_complete_context(session, *_args, **_kwargs):
        assert session is manager.session
        assert [message.content for message in session.history] == [
            "complete model context"
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

    with pytest.raises(_ContextBuildReached):
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

    assert manager.hydrations == 1
    assert manager.calls >= 1
