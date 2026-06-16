"""HTTP-level tests for the Atlas API: CRUD, graph, search, owner isolation.

A tiny middleware maps an ``X-Test-User`` header onto
``request.state.current_user`` so ``require_user`` resolves a per-request owner,
letting one TestClient act as different users to prove isolation.
"""

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


def _as(user):
    return {"X-Test-User": user}


def test_put_get_and_links(client):
    client.put("/api/atlas/note", json={"path": "A", "content": "# A\nlinks to [[B]]"}, headers=_as("alice"))
    client.put("/api/atlas/note", json={"path": "B", "content": "# B\nback to [[A]] #topic"}, headers=_as("alice"))

    r = client.get("/api/atlas/note", params={"path": "B"}, headers=_as("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "B.md"
    assert body["tags"] == ["topic"]
    assert {"target": "A", "resolved": "A.md"} in body["outlinks"]
    assert body["backlinks"] == ["A.md"]


def test_list_and_search(client):
    client.put("/api/atlas/note", json={"path": "Alpha", "content": "# Alpha\nfindme #proj"}, headers=_as("alice"))
    client.put("/api/atlas/note", json={"path": "Beta", "content": "# Beta"}, headers=_as("alice"))

    names = {n["path"] for n in client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"]}
    assert names == {"Alpha.md", "Beta.md"}

    hits = client.get("/api/atlas/search", params={"q": "findme"}, headers=_as("alice")).json()["results"]
    assert [h["path"] for h in hits] == ["Alpha.md"]

    tag_hits = client.get("/api/atlas/search", params={"q": "#proj"}, headers=_as("alice")).json()["results"]
    assert [h["path"] for h in tag_hits] == ["Alpha.md"]


def test_graph_endpoint(client):
    client.put("/api/atlas/note", json={"path": "A", "content": "[[B]]"}, headers=_as("alice"))
    client.put("/api/atlas/note", json={"path": "B", "content": "[[Ghost]]"}, headers=_as("alice"))
    g = client.get("/api/atlas/graph", headers=_as("alice")).json()
    assert {"source": "A.md", "target": "B.md"} in g["links"]
    assert any(n["missing"] for n in g["nodes"])


def test_rename_and_delete(client):
    client.put("/api/atlas/note", json={"path": "Old", "content": "x"}, headers=_as("alice"))
    r = client.post("/api/atlas/rename", json={"path": "Old.md", "new_path": "New"}, headers=_as("alice"))
    assert r.json()["path"] == "New.md"
    assert client.get("/api/atlas/note", params={"path": "Old.md"}, headers=_as("alice")).status_code == 404

    assert client.post("/api/atlas/delete", json={"path": "New.md"}, headers=_as("alice")).json()["ok"]
    assert client.get("/api/atlas/notes", headers=_as("alice")).json()["notes"] == []


def test_owner_isolation(client):
    client.put("/api/atlas/note", json={"path": "secret", "content": "alice only"}, headers=_as("alice"))
    # Bob sees nothing and cannot read alice's note.
    assert client.get("/api/atlas/notes", headers=_as("bob")).json()["notes"] == []
    assert client.get("/api/atlas/note", params={"path": "secret.md"}, headers=_as("bob")).status_code == 404


def test_traversal_rejected_over_http(client):
    r = client.get("/api/atlas/note", params={"path": "../../etc/passwd"}, headers=_as("alice"))
    assert r.status_code == 400
