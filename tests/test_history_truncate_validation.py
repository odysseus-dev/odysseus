"""Invalid truncation requests must leave saved chat history intact."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.database import isolated_session_database


@pytest.fixture
def history_client(tmp_path, monkeypatch):
    from core.models import ChatMessage
    from routes.history import history_routes
    from routes import session_routes

    with isolated_session_database(tmp_path) as (manager, database):
        monkeypatch.setattr(history_routes, "SessionLocal", database.SessionLocal)
        monkeypatch.setattr(session_routes, "SessionLocal", database.SessionLocal)
        monkeypatch.setenv("AUTH_ENABLED", "true")
        manager.create_session("truncate-test", "Test", "http://example.test", "test", owner="alice")
        for text in ("first", "second", "third"):
            manager.add_message("truncate-test", ChatMessage("user", text))
        app = FastAPI()
        app.state.auth_manager = SimpleNamespace(is_configured=True)

        @app.middleware("http")
        async def owner(request, call_next):
            request.state.current_user = request.headers.get("X-Test-Owner", "alice")
            return await call_next(request)

        app.include_router(history_routes.setup_history_routes(manager))
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, manager, database


def saved_messages(database):
    with database.SessionLocal() as db:
        return [row.content for row in db.query(database.ChatMessage).filter(
            database.ChatMessage.session_id == "truncate-test"
        ).order_by(database.ChatMessage.timestamp).all()]


@pytest.mark.parametrize("payload", [
    {}, {"unrelated": 1}, {"before_msg_id": ""},
    {"keep_count": None}, {"keep_count": False}, {"keep_count": True},
    {"keep_count": 1.9}, {"keep_count": -1}, {"keep_count": "bad"},
    {"keep_count": []}, {"keep_count": {}},
    {"before_msg_id": True}, {"before_msg_id": ["message"]},
    [], None, "two", 2, False,
])
def test_invalid_target_preserves_history(history_client, payload):
    client, manager, database = history_client
    import json
    response = client.post("/api/session/truncate-test/truncate", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code == 400, response.text
    assert saved_messages(database) == ["first", "second", "third"]
    assert [m.content for m in manager.get_session("truncate-test").history] == ["first", "second", "third"]


def test_malformed_json_preserves_history(history_client):
    client, _, database = history_client
    response = client.post("/api/session/truncate-test/truncate", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 400, response.text
    assert saved_messages(database) == ["first", "second", "third"]


@pytest.mark.parametrize("count,expected", [(0, []), (2, ["first", "second"]), (9, ["first", "second", "third"]), ("2", ["first", "second"])])
def test_explicit_count_remains_supported(history_client, count, expected):
    client, _, database = history_client
    response = client.post("/api/session/truncate-test/truncate", json={"keep_count": count})
    assert response.status_code == 200, response.text
    assert saved_messages(database) == expected


@pytest.mark.parametrize("field", ["before_msg_id", "message_id"])
def test_message_id_boundary_remains_supported(history_client, field):
    client, _, database = history_client
    with database.SessionLocal() as db:
        message_id = db.query(database.ChatMessage).filter(database.ChatMessage.content == "second").one().id
    response = client.post("/api/session/truncate-test/truncate", json={field: message_id})
    assert response.status_code == 200, response.text
    assert saved_messages(database) == ["first"]


def test_missing_message_id_does_not_clear_history(history_client):
    client, _, database = history_client
    response = client.post("/api/session/truncate-test/truncate", json={"before_msg_id": "missing"})
    assert response.status_code == 404
    assert saved_messages(database) == ["first", "second", "third"]


def test_owner_gate_still_precedes_validation(history_client):
    client, _, database = history_client
    response = client.post("/api/session/truncate-test/truncate", json={}, headers={"X-Test-Owner": "bob"})
    assert response.status_code == 404
    assert saved_messages(database) == ["first", "second", "third"]
