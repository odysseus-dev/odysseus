"""Tests for project_routes.py (T17: CRUD).

T19+ append more tests in subsequent commits."""

import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.project_routes import setup_project_routes


@pytest.fixture
def app_and_client(monkeypatch, tmp_path):
    """Build a minimal FastAPI app with project routes wired up. Uses
    X-Owner header to simulate auth (the real app uses effective_user
    from request state). Uses a tmp_path SQLite so the migrations can run."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    db_file = tmp_path / "app.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr("core.database.DATABASE_URL", db_url)
    import sqlalchemy
    from core import database as dbmod
    new_engine = sqlalchemy.create_engine(db_url, connect_args={"check_same_thread": False})
    new_session_local = sqlalchemy.orm.sessionmaker(autocommit=False, autoflush=False, bind=new_engine)
    monkeypatch.setattr("core.database.SessionLocal", new_session_local)
    monkeypatch.setattr("services.project.service.SessionLocal", new_session_local)
    from core.database import Base, init_db
    Base.metadata.create_all(bind=new_engine)
    init_db()
    app = FastAPI()
    setup_project_routes(app, project_service=None, memory_service=None)
    return app, TestClient(app)


def test_list_projects_empty(app_and_client):
    _app, client = app_and_client
    res = client.get("/api/projects", headers={"X-Owner": "alice"})
    assert res.status_code == 200
    assert res.json() == []


def test_create_then_get(app_and_client):
    _app, client = app_and_client
    res = client.post(
        "/api/projects",
        json={"name": "My Notes", "icon": "📒", "description": "x", "memory_mode": "isolated"},
        headers={"X-Owner": "alice"},
    )
    assert res.status_code == 200, res.text
    pid = res.json()["id"]

    res = client.get(f"/api/projects/{pid}", headers={"X-Owner": "alice"})
    assert res.status_code == 200
    assert res.json()["name"] == "My Notes"


def test_get_unknown_returns_404(app_and_client):
    _app, client = app_and_client
    res = client.get("/api/projects/prj_missing", headers={"X-Owner": "alice"})
    assert res.status_code == 404


def test_other_owner_gets_404(app_and_client):
    _app, client = app_and_client
    res = client.post(
        "/api/projects", json={"name": "x", "memory_mode": "isolated"},
        headers={"X-Owner": "alice"},
    )
    pid = res.json()["id"]
    res = client.get(f"/api/projects/{pid}", headers={"X-Owner": "bob"})
    assert res.status_code == 404


# ────────────────────────────────────── T19 settings routes ─────────────────────────────────────

def test_settings_get_put_round_trip(app_and_client):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "S", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]

    res = client.put(
        f"/api/projects/{pid}/settings",
        json={"custom_prompt": "Be terse.", "prompt_override_mode": "override"},
        headers={"X-Owner": "alice"},
    )
    assert res.status_code == 200
    assert res.json()["custom_prompt"] == "Be terse."
    assert res.json()["prompt_override_mode"] == "override"

    res = client.get(f"/api/projects/{pid}/settings",
                     headers={"X-Owner": "alice"})
    assert res.status_code == 200
    assert res.json()["custom_prompt"] == "Be terse."


def test_settings_rejects_oversized_prompt(app_and_client):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "S2", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]
    huge = "x" * 4001
    res = client.put(
        f"/api/projects/{pid}/settings",
        json={"custom_prompt": huge},
        headers={"X-Owner": "alice"},
    )
    assert res.status_code == 422
    body = res.json()
    detail = body.get("detail", body)
    assert detail.get("error") == "field_too_long"


# ────────────────────────────────────── T20 sessions routes ───────────────────────────────────

def test_create_session_in_project(app_and_client):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "S3", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]

    res = client.post(
        f"/api/projects/{pid}/sessions",
        json={"name": "first chat", "endpoint_url": "http://x", "model": "m"},
        headers={"X-Owner": "alice"},
    )
    assert res.status_code in (200, 201), res.text
    sid = res.json()["id"]

    # Session IS visible under the project.
    res = client.get(f"/api/projects/{pid}/sessions",
                     headers={"X-Owner": "alice"})
    assert res.status_code == 200
    assert any(s["id"] == sid for s in res.json())


def test_session_with_mismatched_project_404s(app_and_client):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "A", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid_a = res.json()["id"]
    res = client.post(
        f"/api/projects/{pid_a}/sessions",
        json={"name": "s", "endpoint_url": "http://x", "model": "m"},
        headers={"X-Owner": "alice"},
    )
    sid = res.json()["id"]

    # Create a second project and try to access the session under it.
    res = client.post("/api/projects",
                      json={"name": "B", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid_b = res.json()["id"]
    res = client.get(f"/api/projects/{pid_b}/sessions/{sid}",
                     headers={"X-Owner": "alice"})
    assert res.status_code == 404


# ────────────────────────────────────── T21 messages route ─────────────────────────────────────

def test_post_message_attaches_project_ctx(app_and_client):
    """The project-scoped message endpoint must exist and forward the
    project_ctx into the chat pipeline. The test registers a fake
    pipeline on app.state so we can assert it was called with the right ctx."""
    _app, client = app_and_client

    captured = {}
    async def fake_pipeline(session_row, body, ctx=None):
        captured["ctx"] = ctx
        captured["project_id"] = session_row.project_id
        return {"ok": True}

    _app.state.project_chat_pipeline = fake_pipeline

    res = client.post("/api/projects",
                      json={"name": "MemExt", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]
    res = client.post(
        f"/api/projects/{pid}/sessions",
        json={"name": "s", "endpoint_url": "http://x", "model": "m"},
        headers={"X-Owner": "alice"},
    )
    sid = res.json()["id"]
    res = client.post(
        f"/api/projects/{pid}/sessions/{sid}/messages",
        json={"role": "user", "content": "hi"},
        headers={"X-Owner": "alice"},
    )
    assert res.status_code == 200, res.text
    assert captured["project_id"] == pid
    assert captured["ctx"].project_id == pid


# ────────────────────────────────────── T22 resources routes ──────────────────────────────────

def test_upload_list_remove_resource(app_and_client, tmp_path):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "R", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]

    # Build a tiny text file to upload.
    fpath = tmp_path / "hello.txt"
    fpath.write_text("Hello world.\n" * 50)

    with open(fpath, "rb") as f:
        res = client.post(
            f"/api/projects/{pid}/resources",
            files={"file": ("hello.txt", f, "text/plain")},
            headers={"X-Owner": "alice"},
        )
    assert res.status_code == 200, res.text
    rid = res.json()["id"]

    res = client.get(f"/api/projects/{pid}/resources",
                     headers={"X-Owner": "alice"})
    assert res.status_code == 200
    assert any(r["id"] == rid for r in res.json())

    res = client.delete(f"/api/projects/{pid}/resources/{rid}",
                        headers={"X-Owner": "alice"})
    assert res.status_code == 200


# ────────────────────────────────────── T23 memory routes ─────────────────────────────────────

def test_shared_project_memory_returns_409(app_and_client):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "Shared", "memory_mode": "shared"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]
    res = client.get(f"/api/projects/{pid}/memory",
                     headers={"X-Owner": "alice"})
    assert res.status_code == 409
    body = res.json()
    detail = body.get("detail", body)
    assert detail.get("error") == "mode_shared"


def test_isolated_project_memory_list(app_and_client):
    _app, client = app_and_client
    res = client.post("/api/projects",
                      json={"name": "Iso", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]
    res = client.get(f"/api/projects/{pid}/memory",
                     headers={"X-Owner": "alice"})
    assert res.status_code == 200
    assert res.json() == []


# ────────────────────────────────────── T25b memory_extractor wiring ──────────────────────────

def test_post_message_stuffs_project_ctx_in_body(app_and_client):
    """The post_message route must stuff `project_ctx` into the body so
    chat_helpers.py can detect a project chat and swap memory_service + RAG."""
    _app, client = app_and_client

    captured = {}
    async def fake_pipeline(session_row, body, ctx=None):
        captured["body_keys"] = sorted(body.keys())
        captured["ctx"] = ctx
        return {"ok": True}

    _app.state.project_chat_pipeline = fake_pipeline

    res = client.post("/api/projects",
                      json={"name": "Ctx", "memory_mode": "isolated"},
                      headers={"X-Owner": "alice"})
    pid = res.json()["id"]
    res = client.post(
        f"/api/projects/{pid}/sessions",
        json={"name": "s", "endpoint_url": "http://x", "model": "m"},
        headers={"X-Owner": "alice"},
    )
    sid = res.json()["id"]
    res = client.post(
        f"/api/projects/{pid}/sessions/{sid}/messages",
        json={"role": "user", "content": "hi"},
        headers={"X-Owner": "alice"},
    )
    assert res.status_code == 200
    assert "project_ctx" in captured["body_keys"]
    assert captured["ctx"].project_id == pid
