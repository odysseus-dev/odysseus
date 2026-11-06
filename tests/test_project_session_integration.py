"""Projects coexist with current session persistence and sidebar semantics."""
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, ChatMessage as DbMessage, Project, Session as DbSession
from core.session_manager import SessionManager
import core.session_manager as smod
from routes import session_routes as sr


@pytest.fixture
def project_sessions(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(smod, "SessionLocal", factory)
    monkeypatch.setattr(sr, "SessionLocal", factory)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")
    monkeypatch.setattr(sr, "_current_user_is_admin", lambda *args: True)
    monkeypatch.setattr(sr, "router", APIRouter(prefix="/api"))
    import src.event_bus as event_bus
    monkeypatch.setattr(event_bus, "fire_event", lambda *args, **kwargs: None)
    with factory() as db:
        db.add_all([
            Project(id="project", owner="alice", name="Release", instructions="Keep compatibility"),
            Project(id="private", owner="bob", name="Private project"),
        ])
        db.commit()
    manager = SessionManager.__new__(SessionManager)
    manager.sessions = {}
    manager.upload_handler = None
    app = FastAPI()
    app.include_router(sr.setup_session_routes(manager, {}))
    try:
        with TestClient(app) as client:
            yield client, manager, factory
    finally:
        engine.dispose()


def test_project_create_and_hydration_preserve_workspace_and_endpoint(project_sessions):
    client, manager, factory = project_sessions
    response = client.post("/api/session", data={
        "name": "Project chat", "model": "test-model", "endpoint_url": "http://example.test/v1",
        "skip_validation": "true", "project_id": "project", "cwd": "/workspace/release",
    })
    assert response.status_code == 200, response.text
    sid = response.json()["id"]
    assert response.json()["project_id"] == "project"
    assert response.json()["cwd"] == "/workspace/release"
    with factory() as db:
        row = db.get(DbSession, sid)
        row.endpoint_id = "saved-account"
        row.memory_injection_enabled = False
        db.add(DbMessage(id="message", session_id=sid, role="user", content="Release context"))
        db.commit()
        for hydrated in (manager._db_to_session_meta(row), manager._db_to_session(row, db)):
            assert hydrated.project_id == "project"
            assert hydrated.cwd == "/workspace/release"
            assert hydrated.endpoint_id == "saved-account"
            assert hydrated.memory_injection_enabled is False


def test_project_sidebar_uses_persisted_chats_and_owner_scoped_names(project_sessions):
    client, manager, factory = project_sessions
    with factory() as db:
        db.add_all([
            DbSession(id="older", owner="alice", name="Older chat", endpoint_url="", model="m", project_id="project", message_count=2),
            DbSession(id="mismatched", owner="alice", name="Legacy chat", endpoint_url="", model="m", project_id="private", message_count=2),
            DbSession(id="hidden", owner="alice", name="SFT trace batch private", endpoint_url="", model="m", project_id="project", message_count=2),
            DbSession(id="other-owner", owner="bob", name="Private chat", endpoint_url="", model="m", project_id="private", message_count=2),
        ])
        db.commit()
    assert not manager.sessions
    response = client.get("/api/sessions")
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert set(rows) == {"older", "mismatched"}
    assert rows["older"]["project_id"] == "project"
    assert rows["older"]["project_name"] == "Release"
    assert rows["mismatched"]["project_name"] is None


def test_session_update_can_change_project_and_cwd_together(project_sessions):
    client, manager, factory = project_sessions
    manager.create_session("session", "Chat", "", "m", owner="alice")
    response = client.patch("/api/session/session", data={"project_id": "project", "cwd": "/workspace/new"})
    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == "project"
    assert response.json()["cwd"] == "/workspace/new"
    with factory() as db:
        row = db.get(DbSession, "session")
        assert row.project_id == "project"
        assert row.cwd == "/workspace/new"
    assert manager.sessions["session"].project_id == "project"
    assert manager.sessions["session"].cwd == "/workspace/new"


def test_session_create_rejects_other_owners_project(project_sessions):
    client, manager, factory = project_sessions
    response = client.post("/api/session", data={"skip_validation": "true", "project_id": "private"})
    assert response.status_code == 404
    assert not manager.sessions
