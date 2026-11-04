"""Seam (h): the session context payload and the thinking gates in the session routes.

The owner check is covered by the session tests; here it is stubbed so the
route bodies run against a ghost session. The customization store and the
endpoint rows are real, on a temp sqlite.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import database
from src import model_customization as mc
from tests.helpers.sqlite_db import make_temp_sqlite

GROQ = "https://api.groq.com/openai/v1"
GROQ_CHAT = GROQ + "/chat/completions"
MODEL = "llama-3.3-70b"  # outside the old supports_user_thinking_toggle list


@pytest.fixture
def session_route(tmp_path, monkeypatch):
    from routes import history_routes

    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    monkeypatch.setattr(mc, "STORE_PATH", str(tmp_path / "customizations.json"))
    mc._reset_cache()
    mc._reset_endpoint_cache()
    monkeypatch.setattr(history_routes, "SessionLocal", factory)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *a, **k: None)

    with factory() as db:
        db.add(database.ModelEndpoint(
            id="groq", name="groq", base_url=GROQ, cached_models=json.dumps([MODEL]),
        ))
        db.add(database.Session(
            id="chat-1", name="Chat", endpoint_url=GROQ_CHAT, model=MODEL,
            thinking_mode="on",
        ))
        db.commit()

    session = SimpleNamespace(
        id="chat-1", model=MODEL, endpoint_url=GROQ_CHAT,
        thinking_mode="on", temperature_override=None,
        history=[{"role": "user", "content": "hi"}],
        get_context_messages=lambda: [{"role": "user", "content": "hi"}],
    )
    manager = SimpleNamespace(
        get_session=lambda sid: session if sid == "chat-1" else (_ for _ in ()).throw(KeyError(sid)),
        sessions={"chat-1": session},
        save_sessions=lambda: None,
    )

    app = FastAPI()
    app.include_router(history_routes.setup_history_routes(manager))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, manager=manager,
                              factory=factory, database=database)
    engine.dispose()
    tmpfile.close()


def test_context_payload_reports_the_model_temperature(session_route):
    mc.save_entry("groq", GROQ, MODEL, {"temperature": 0.6})
    body = session_route.client.get("/api/session/chat-1/context").json()
    assert body["effective_temperature"] == 0.6
    assert body["temperature_source"] == "model"
    assert body["thinking_supported"] is True
    assert body["thinking_mode"] == "on"


def test_context_payload_prefers_the_session_override(session_route):
    mc.save_entry("groq", GROQ, MODEL, {"temperature": 0.6})
    session_route.session.temperature_override = 0.9
    body = session_route.client.get("/api/session/chat-1/context").json()
    assert body["effective_temperature"] == 0.9
    assert body["temperature_source"] == "session"


def test_context_payload_falls_back_to_the_builtin_default(session_route):
    body = session_route.client.get("/api/session/chat-1/context").json()
    assert body["effective_temperature"] == 1.0
    assert body["temperature_source"] == "default"


def test_saving_thinking_is_accepted_for_a_model_outside_the_old_list(session_route):
    response = session_route.client.post(
        "/api/session/chat-1/generation-settings", json={"thinking_mode": "on"})
    assert response.status_code == 200, response.text
    assert session_route.session.thinking_mode == "on"
