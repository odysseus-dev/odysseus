import tempfile
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ChatMessage as DbChatMessage, Project, Session as DbSession
from routes.project_routes import setup_project_routes


_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _client(monkeypatch, user="alice", session_manager=None):
    import routes.project_routes as pr

    monkeypatch.setattr(pr, "SessionLocal", _TS)
    monkeypatch.setattr(pr, "effective_user", lambda request: user)

    app = FastAPI()
    app.include_router(setup_project_routes(session_manager))
    return TestClient(app)


def _reset_db():
    db = _TS()
    try:
        db.query(DbChatMessage).delete()
        db.query(DbSession).delete()
        db.query(Project).delete()
        db.commit()
    finally:
        db.close()


def _session(owner: str | None, name: str, *, project_id: str | None = None) -> str:
    sid = str(uuid.uuid4())
    db = _TS()
    try:
        db.add(DbSession(
            id=sid,
            owner=owner,
            name=name,
            endpoint_url="http://example.test/v1/chat/completions",
            model="test-model",
            archived=False,
            headers={},
            project_id=project_id,
        ))
        db.commit()
    finally:
        db.close()
    return sid


def test_project_routes_create_and_assign_owner_scoped_session(monkeypatch):
    _reset_db()

    alice_sid = _session("alice", "Alice chat")
    bob_sid = _session("bob", "Bob chat")
    sm = SimpleNamespace(sessions={alice_sid: SimpleNamespace(project_id=None)})
    client = _client(monkeypatch, session_manager=sm)

    created = client.post("/api/projects", data={"name": "Odysseus release"})
    assert created.status_code == 200
    project_id = created.json()["id"]

    assigned = client.post(f"/api/projects/{project_id}/sessions/{alice_sid}")
    assert assigned.status_code == 200
    assert assigned.json()["project_id"] == project_id
    assert sm.sessions[alice_sid].project_id == project_id

    rejected = client.post(f"/api/projects/{project_id}/sessions/{bob_sid}")
    assert rejected.status_code == 404

    db = _TS()
    try:
        assert db.query(DbSession).filter(DbSession.id == alice_sid).first().project_id == project_id
        assert db.query(DbSession).filter(DbSession.id == bob_sid).first().project_id is None
    finally:
        db.close()


def test_project_list_excludes_other_users_projects(monkeypatch):
    _reset_db()
    db = _TS()
    try:
        db.add_all([
            Project(id=str(uuid.uuid4()), owner="alice", name="Alice project", archived=False),
            Project(id=str(uuid.uuid4()), owner="bob", name="Bob project", archived=False),
        ])
        db.commit()
    finally:
        db.close()

    client = _client(monkeypatch, user="alice")

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert [p["name"] for p in listed.json()] == ["Alice project"]


def test_shared_project_detail_and_brief_exclude_other_owners_chats(monkeypatch):
    _reset_db()
    project_id = str(uuid.uuid4())
    db = _TS()
    try:
        db.add(Project(id=project_id, owner=None, name="Shared legacy project", archived=False))
        db.commit()
    finally:
        db.close()

    alice_sid = _session("alice", "Alice private chat", project_id=project_id)
    bob_sid = _session("bob", "Bob private chat", project_id=project_id)
    shared_sid = _session(None, "Shared chat", project_id=project_id)

    db = _TS()
    try:
        db.add_all([
            DbChatMessage(
                id=str(uuid.uuid4()),
                session_id=alice_sid,
                role="user",
                content="ALICE_PRIVATE_TRANSCRIPT",
            ),
            DbChatMessage(
                id=str(uuid.uuid4()),
                session_id=bob_sid,
                role="user",
                content="BOB_PRIVATE_TRANSCRIPT",
            ),
            DbChatMessage(
                id=str(uuid.uuid4()),
                session_id=shared_sid,
                role="assistant",
                content="SHARED_TRANSCRIPT",
            ),
        ])
        db.commit()
    finally:
        db.close()

    import src.endpoint_resolver as endpoint_resolver
    import src.llm_core as llm_core

    captured = {}

    def _llm_call(url, model, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return "Scoped brief"

    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda purpose, owner=None: ("http://example.test/v1/chat/completions", "test-model", {}),
    )
    monkeypatch.setattr(llm_core, "llm_call", _llm_call)

    client = _client(monkeypatch, user="bob")
    detail = client.get(f"/api/projects/{project_id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["session_count"] == 2
    assert {session["name"] for session in payload["sessions"]} == {
        "Bob private chat",
        "Shared chat",
    }

    refreshed = client.post(f"/api/projects/{project_id}/brief/refresh")

    assert refreshed.status_code == 200
    assert refreshed.json()["brief"] == "Scoped brief"
    assert "BOB_PRIVATE_TRANSCRIPT" in captured["prompt"]
    assert "SHARED_TRANSCRIPT" in captured["prompt"]
    assert "ALICE_PRIVATE_TRANSCRIPT" not in captured["prompt"]
