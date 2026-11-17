"""Explicit null must clear nullable note fields; omission must preserve them."""

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
    ("image_url", "https://example.com/note.png"),
    ("content", "Saved body"),
    ("items", [{"text": "Saved item", "done": False}]),
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


def test_omitted_fields_survive_title_edit(client):
    fields = {
        "due_date": "2030-10-08T09:00:00",
        "label": "home",
        "image_url": "https://example.com/note.png",
        "content": "Saved body",
        "items": [{"text": "Saved item", "done": False}],
        "color": "blue",
        "gallery_id": "gallery-1",
    }
    created = client.post("/api/notes", json={"title": "Before", **fields})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"
    updated = client.put(path, json={"title": "After"})
    assert updated.status_code == 200, updated.text
    saved = client.get(path).json()
    assert saved["title"] == "After"
    for field, value in fields.items():
        assert saved[field] == value


def test_null_update_cannot_clear_another_users_note(client):
    created = client.post("/api/notes", json={"title": "Private", "label": "home"})
    assert created.status_code == 200, created.text
    path = f"/api/notes/{created.json()['id']}"
    response = client.put(path, json={"label": None}, headers={"x-test-user": "bob"})
    assert response.status_code == 404
    assert client.get(path).json()["label"] == "home"
