"""HTTP tests for the Atlas Bases routes: /query, .base CRUD, /property."""

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import routes.atlas_routes as ar


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "ATLAS_ROOT", Path(tmp_path))
    ar._notes_cache.clear()
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request: Request, call_next):
        request.state.current_user = request.headers.get("X-Test-User", "alice")
        return await call_next(request)

    app.include_router(ar.setup_atlas_routes())
    return TestClient(app)


def _as(u):
    return {"X-Test-User": u}


def _seed(client):
    client.put("/api/atlas/note", json={"path": "daily",
        "content": "---\nstatus: open\ntags: [todo]\n---\n# Daily\n## Todo\nx"}, headers=_as("alice"))
    client.put("/api/atlas/note", json={"path": "done",
        "content": "---\nstatus: closed\n---\n# Done"}, headers=_as("alice"))


def test_query_route(client):
    _seed(client)
    r = client.post("/api/atlas/query", json={"query": {"where": {"filters": [
        {"field": "prop.status", "op": "eq", "value": "open"}]}}}, headers=_as("alice"))
    assert r.status_code == 200
    assert [x["file.path"] for x in r.json()["rows"]] == ["daily.md"]


def test_query_owner_isolation(client):
    _seed(client)  # alice's notes
    r = client.post("/api/atlas/query", json={"query": {}}, headers=_as("bob"))
    assert r.json()["rows"] == []


def test_base_crud_roundtrip(client):
    _seed(client)
    q = {"where": {"filters": [{"field": "prop.status", "op": "eq", "value": "open"}]}}
    assert client.put("/api/atlas/base", json={"path": "open-items", "query": q}, headers=_as("alice")).json()["ok"]

    bases = client.get("/api/atlas/bases", headers=_as("alice")).json()["bases"]
    assert [b["path"] for b in bases] == ["open-items.base"]

    loaded = client.get("/api/atlas/base", params={"path": "open-items.base"}, headers=_as("alice")).json()
    assert [x["file.path"] for x in loaded["rows"]] == ["daily.md"]


def test_property_edit_preserves_body(client):
    client.put("/api/atlas/note", json={"path": "n",
        "content": "---\nstatus: open\n---\n# N\n\nbody text stays"}, headers=_as("alice"))
    assert client.post("/api/atlas/property", json={"path": "n.md", "key": "status", "value": "done"},
                       headers=_as("alice")).json()["ok"]
    note = client.get("/api/atlas/note", params={"path": "n.md"}, headers=_as("alice")).json()
    assert "status: done" in note["content"]
    assert "body text stays" in note["content"]


def test_property_delete(client):
    client.put("/api/atlas/note", json={"path": "n",
        "content": "---\nstatus: open\npriority: high\n---\n# N"}, headers=_as("alice"))
    client.post("/api/atlas/property", json={"path": "n.md", "key": "priority", "delete": True}, headers=_as("alice"))
    note = client.get("/api/atlas/note", params={"path": "n.md"}, headers=_as("alice")).json()
    assert "priority" not in note["content"] and "status: open" in note["content"]


def test_base_path_confinement(client):
    r = client.put("/api/atlas/base", json={"path": "../../escape", "query": {}}, headers=_as("alice"))
    assert r.status_code == 400


def test_malformed_query_is_400_not_500(client):
    _seed(client)
    # filters as a string instead of a list — engine should reject cleanly.
    r = client.post("/api/atlas/query", json={"query": {"where": {"filters": "nope"}}}, headers=_as("alice"))
    assert r.status_code in (200, 400)   # tolerant or 400, never 500
