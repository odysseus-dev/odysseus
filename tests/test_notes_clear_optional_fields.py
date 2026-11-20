"""REST nulls clear fields and tool/bridge nulls keep their legacy behavior."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.database import disposable_database


@pytest.fixture
def client(tmp_path, monkeypatch):
    import routes.note.note_routes as routes

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(routes, "SessionLocal", factory)
        app = FastAPI()

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.current_user = request.headers.get("x-test-user", "alice")
            return await call_next(request)

        monkeypatch.setattr(routes, "_scheduler_ref", None)
        app.include_router(routes.setup_note_routes())
        with TestClient(app) as api:
            yield api


@pytest.mark.parametrize("field,value", [
    ("due_date", "2030-10-08T09:00:00"),
    ("label", "home"),
    ("color", "blue"),
    ("gallery_id", "gallery-1"),
    ("agent_session_id", "chat-1"),
])
def test_explicit_null_clears_saved_field(client, field, value):
    created = client.post("/api/notes", json={"title": "Test note"})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"
    populated = client.put(path, json={field: value})
    assert populated.status_code == 200, populated.text
    assert client.get(path).json()[field] == value

    updated = client.put(path, json={field: None})
    assert updated.status_code == 200, updated.text
    saved = client.get(path)
    assert saved.status_code == 200, saved.text
    assert saved.json()[field] is None
    assert updated.json()[field] is None


FOLDED_BODY = {
    "content": "Saved body",
    "items": [{"text": "Saved item", "done": False}],
    "image_url": "https://example.com/note.png",
}


def _assert_folded_body(saved):
    assert saved["content"].startswith("Saved body")
    assert "- [ ] Saved item" in saved["content"]
    assert "![](https://example.com/note.png)" in saved["content"]


@pytest.mark.parametrize("field,content", [
    ("content", "- [ ] Saved item\n\n![](https://example.com/note.png)"),
    ("items", "Saved body\n\n\n![](https://example.com/note.png)"),
    ("image_url", "Saved body\n\n- [ ] Saved item"),
])
def test_null_clears_only_the_requested_part(client, field, content):
    created = client.post("/api/notes", json={"title": "Body", **FOLDED_BODY})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"
    updated = client.put(path, json={field: None})
    assert updated.status_code == 200, updated.text
    assert client.get(path).json()["content"] == content


def test_null_content_keeps_the_owner_check(client):
    created = client.post("/api/notes", json={"title": "Private"})
    other = client.put(f"/api/notes/{created.json()['id']}", json={"content": None}, headers={"x-test-user": "bob"})
    missing = client.put("/api/notes/does-not-exist", json={"content": None})
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_omitted_fields_survive_title_edit(client):
    fields = {
        "due_date": "2030-10-08T09:00:00",
        "label": "home",
        "color": "blue",
        "gallery_id": "gallery-1",
    }
    created = client.post("/api/notes", json={"title": "Before", **fields, **FOLDED_BODY})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"
    updated = client.put(path, json={"title": "After"})
    assert updated.status_code == 200, updated.text
    saved = client.get(path).json()
    assert saved["title"] == "After"
    for field, value in fields.items():
        assert saved[field] == value
    _assert_folded_body(saved)


def test_null_update_cannot_clear_another_users_note(client):
    created = client.post("/api/notes", json={"title": "Private", "label": "home"})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"
    response = client.put(path, json={"label": None}, headers={"x-test-user": "bob"})
    assert response.status_code == 404
    assert client.get(path).json()["label"] == "home"


def test_empty_values_still_clear_the_body(client):
    created = client.post("/api/notes", json={"title": "Body", **FOLDED_BODY})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"

    assert client.put(path, json={"items": []}).status_code == 200
    assert "Saved item" not in client.get(path).json()["content"]
    assert client.put(path, json={"image_url": ""}).status_code == 200
    assert "note.png" not in client.get(path).json()["content"]
    assert client.put(path, json={"content": ""}).status_code == 200
    assert client.get(path).json()["content"] == ""


# --- /api/codex/todos bridge ------------------------------------------------

class _Token:
    """Pure-ASGI shim writing what the bearer-token middleware sets."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {}).update(
                current_user="api", api_token=True,
                api_token_scopes=["todos:write"], api_token_owner="alice")
        await self.app(scope, receive, send)


async def _bridge_post(monkeypatch, payload):
    import routes.codex_routes as cr

    calls = []

    async def _fake_manage_notes(content, owner=None):
        calls.append(json.loads(content))
        return {"ok": True}

    monkeypatch.setattr(cr, "do_manage_notes", _fake_manage_notes)
    app = FastAPI()
    app.include_router(cr.setup_codex_routes())
    transport = httpx.ASGITransport(app=_Token(app), client=("203.0.113.7", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://codex.test") as c:
        return await c.post("/api/codex/todos", json=payload), calls


@pytest.mark.parametrize("field", ["content", "items", "checklist_items", "image_url"])
async def test_bridge_passes_null_on_a_content_field(monkeypatch, field):
    response, calls = await _bridge_post(monkeypatch, {"action": "update", "id": "n1", field: None})
    assert response.status_code == 200
    assert calls and calls[0][field] is None


async def test_bridge_still_passes_null_on_a_real_column(monkeypatch):
    response, calls = await _bridge_post(monkeypatch, {"action": "update", "id": "n1", "label": None})
    assert response.status_code == 200
    assert calls and calls[0]["label"] is None


# --- manage_notes tool --------------------------------------------------------

@pytest.fixture
def tool_note(tmp_path, monkeypatch):
    import core.database as cdb
    from core.database import Note

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        with factory() as db:
            db.add(Note(id="tool-note-1", owner="alice", title="Body", note_type="note",
                        content="Saved body\n\n- [ ] Saved item\n\n![](https://example.com/note.png)"))
            db.commit()

        def row():
            with factory() as db:
                return db.get(Note, "tool-note-1")

        yield row


@pytest.mark.parametrize("field", ["content", "items", "checklist_items", "image_url"])
def test_tool_ignores_null_on_a_content_field(tool_note, field):
    from src import tool_implementations

    before = tool_note()
    result = asyncio.run(tool_implementations.do_manage_notes(
        json.dumps({"action": "update", "id": "tool-note-1", "title": "Renamed", field: None}), owner="alice"))
    assert result["exit_code"] == 0
    after = tool_note()
    assert after.title == "Renamed"
    assert after.content == before.content
