"""Cleanup must use durable transcript rows for its deletion safeguards."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker

import core.database as database
import src.database as legacy_database
import routes.cleanup.cleanup_routes as routes


@pytest.fixture
def cleanup_api(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cleanup.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(legacy_database, "SessionLocal", factory)
    monkeypatch.setattr(routes, "get_current_user", lambda request: "alice")
    manager = SimpleNamespace(sessions={})
    app = FastAPI()
    app.include_router(routes.setup_cleanup_routes(manager))
    with TestClient(app) as client:
        yield client, factory, manager
    engine.dispose()


def seed(factory, manager, stored, actual, *, archived=True):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with factory() as db:
        for index in range(10):
            db.add(database.Session(
                id=f"recent-{index}", name="Recent", endpoint_url="", model="",
                owner="alice", created_at=now - timedelta(hours=index),
                last_accessed=now, message_count=1,
            ))
        db.add(database.Session(
            id="old-chat", name="Project discussion", endpoint_url="", model="",
            owner="alice", created_at=now - timedelta(days=50),
            last_accessed=now - timedelta(days=30), archived=archived,
            is_important=False, message_count=stored,
        ))
        db.commit()
        db.add_all(database.ChatMessage(
            id=f"message-{index}", session_id="old-chat", role="user",
            content=f"Saved project discussion {index}", timestamp=now - timedelta(days=30),
        ) for index in range(actual))
        db.commit()
    manager.sessions["old-chat"] = SimpleNamespace(message_count=actual)


@pytest.mark.parametrize("stored", [0, 3])
def test_cleanup_preserves_long_transcript_with_stale_count(cleanup_api, stored):
    client, factory, manager = cleanup_api
    seed(factory, manager, stored, 40)
    preview = client.get("/api/cleanup/preview").json()
    assert not preview["sessions_to_delete"]
    protected = next(row for row in preview["preserved_sessions"] if row["id"] == "old-chat")
    assert protected["message_count"] == 40
    response = client.post("/api/cleanup")
    with factory() as db:
        actual = db.query(database.ChatMessage).filter_by(session_id="old-chat").count()
        survives = db.get(database.Session, "old-chat") is not None
    assert response.json()["deleted_count"] == 0
    assert survives and actual == 40, "Cleanup must count actual rows before hard deletion"


def test_cleanup_still_removes_genuinely_short_old_chat(cleanup_api):
    client, factory, manager = cleanup_api
    seed(factory, manager, 3, 3)
    response = client.post("/api/cleanup")
    assert response.json()["deleted_count"] == 1
    with factory() as db:
        assert db.get(database.Session, "old-chat") is None
        assert db.query(database.ChatMessage).filter_by(session_id="old-chat").count() == 0


def test_new_archive_is_not_deleted_in_same_cleanup(cleanup_api):
    client, factory, manager = cleanup_api
    seed(factory, manager, 3, 3, archived=False)
    preview = client.get("/api/cleanup/preview").json()
    assert not preview["sessions_to_delete"]
    response = client.post("/api/cleanup")
    assert response.json()["deleted_count"] == 0
    with factory() as db:
        assert db.get(database.Session, "old-chat").archived


@pytest.mark.parametrize("actual,expected_deleted", [(19, 1), (20, 0)])
def test_cleanup_threshold_uses_actual_count_even_when_metadata_is_high(cleanup_api, actual, expected_deleted):
    client, factory, manager = cleanup_api
    seed(factory, manager, 100, actual)
    preview = client.get("/api/cleanup/preview").json()
    section = "sessions_to_delete" if expected_deleted else "preserved_sessions"
    row = next(row for row in preview[section] if row["id"] == "old-chat")
    assert row["message_count"] == actual
    if expected_deleted:
        assert row["estimated_size_kb"] == 19 * 512 / 1024
    response = client.post("/api/cleanup")
    assert response.json()["deleted_count"] == expected_deleted
    with factory() as db:
        assert (db.get(database.Session, "old-chat") is None) == bool(expected_deleted)


@pytest.mark.parametrize("protection", ["starred", "keyword", "other_owner"])
def test_cleanup_retains_existing_protections(cleanup_api, protection):
    client, factory, manager = cleanup_api
    seed(factory, manager, 0, 3)
    with factory() as db:
        row = db.get(database.Session, "old-chat")
        if protection == "starred":
            row.is_important = True
        elif protection == "keyword":
            row.name = "Important project"
        else:
            row.owner = "bob"
        # Preserve the old activity date despite the column's onupdate default.
        row.last_accessed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        db.commit()
    response = client.post("/api/cleanup")
    assert response.json()["deleted_count"] == 0
    with factory() as db:
        assert db.query(database.ChatMessage).filter_by(session_id="old-chat").count() == 3


def test_cleanup_rechecks_messages_at_the_delete_boundary(cleanup_api):
    """Deliver new rows after selection but before the DELETE executes."""
    client, factory, manager = cleanup_api
    seed(factory, manager, 3, 3)
    engine = factory.kw["bind"]
    injected = False

    def deliver_messages(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected
        if not injected and statement.lstrip().startswith("DELETE FROM sessions"):
            injected = True
            connection.execute(insert(database.ChatMessage), [
                {"id": f"incoming-{i}", "session_id": "old-chat", "role": "assistant",
                 "content": "New durable message"} for i in range(20)
            ])

    event.listen(engine, "before_cursor_execute", deliver_messages)
    try:
        response = client.post("/api/cleanup")
    finally:
        event.remove(engine, "before_cursor_execute", deliver_messages)
    assert injected
    assert response.json()["deleted_count"] == 0
    assert response.json()["space_freed_mb"] == 0
    assert "old-chat" in manager.sessions
    with factory() as db:
        assert db.get(database.Session, "old-chat") is not None
        assert db.query(database.ChatMessage).filter_by(session_id="old-chat").count() == 23
