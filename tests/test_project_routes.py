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
