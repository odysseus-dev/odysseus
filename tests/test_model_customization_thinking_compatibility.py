"""Regression tests for per-model thinking defaults and model capability reconciliation.

Verifies:
- Supported vs unsupported thinking models (supports_user_thinking_toggle contract).
- Model with configured thinking default (seeds new chat if supported, ignored if unsupported).
- Model without configured thinking default (defaults to off).
- Session-level override retains precedence where supported and is clamped to off where unsupported.
- Session creation and reload persistence.
- Switching between models clears stale thinking mode when target model does not support it.
- Non-streaming chat guards unsupported models.
- Streaming chat guards unsupported models at initialization and after route reconciliation.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import database
from core import session_manager as sm_module
from core.session_manager import SessionManager
from routes import history_routes, session_routes
from src import model_customization as mc
from src.model_profiles import supports_user_thinking_toggle
from tests.helpers.sqlite_db import make_temp_sqlite

GROQ = "https://api.groq.com/openai/v1"
GROQ_CHAT = GROQ + "/chat/completions"
OPENROUTER_CHAT = "https://openrouter.ai/api/v1/chat/completions"
THINKER_MODEL = "qwen3-32b"       # supports user thinking toggle
UNSUPPORTED_MODEL = "llama-3.3-70b" # does not support user thinking toggle
GROK_MODEL = "x-ai/grok-4.5"      # does not support user thinking toggle


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "STORE_PATH", str(tmp_path / "model_customizations.json"))
    mc._reset_cache()
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(sm_module, "SessionLocal", factory)
    monkeypatch.setattr(session_routes, "SessionLocal", factory)
    monkeypatch.setattr(history_routes, "SessionLocal", factory)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(session_routes, "_verify_session_owner", lambda *a, **k: None)
    mc._reset_endpoint_cache()

    with factory() as s:
        s.add(database.ModelEndpoint(
            id="groq", name="groq", base_url=GROQ,
            cached_models=json.dumps([THINKER_MODEL, UNSUPPORTED_MODEL, GROK_MODEL]),
        ))
        s.commit()

    manager = SessionManager()

    from src.auth_helpers import require_chat_api_token_scope
    app = FastAPI()
    app.dependency_overrides[require_chat_api_token_scope] = lambda: None
    app.include_router(history_routes.setup_history_routes(manager))
    app.include_router(session_routes.setup_session_routes(manager, {"REQUEST_TIMEOUT": 20}))
    client = TestClient(app)

    yield SimpleNamespace(
        factory=factory,
        manager=manager,
        client=client,
        app=app,
    )
    engine.dispose()
    tmpfile.close()


def test_model_profiles_thinking_toggle_support():
    assert supports_user_thinking_toggle(THINKER_MODEL) is True
    assert supports_user_thinking_toggle(UNSUPPORTED_MODEL) is False
    assert supports_user_thinking_toggle(GROK_MODEL) is False
    assert supports_user_thinking_toggle("gpt-4o") is False
    assert supports_user_thinking_toggle("o1-mini") is False


def test_session_creation_seeds_thinking_on_for_supported_model(env):
    mc.save_entry("groq", GROQ, THINKER_MODEL, {"thinking": "on"})
    session = env.manager.create_session("s-thinker", "Chat", GROQ_CHAT, THINKER_MODEL)
    assert session.thinking_mode == "on"

    # Verify database persistence
    with env.factory() as s:
        db_sess = s.get(database.Session, "s-thinker")
        assert db_sess.thinking_mode == "on"

    # Reload from session manager
    reloaded = env.manager.get_session("s-thinker")
    assert reloaded.thinking_mode == "on"


def test_session_creation_does_not_seed_thinking_on_for_unsupported_model(env):
    mc.save_entry("groq", GROQ, UNSUPPORTED_MODEL, {"thinking": "on"})
    session = env.manager.create_session("s-unsupported", "Chat", GROQ_CHAT, UNSUPPORTED_MODEL)
    assert session.thinking_mode == "off"

    # Verify database persistence
    with env.factory() as s:
        db_sess = s.get(database.Session, "s-unsupported")
        assert db_sess.thinking_mode == "off"

    # Reload from session manager
    reloaded = env.manager.get_session("s-unsupported")
    assert reloaded.thinking_mode == "off"


def test_session_creation_without_configured_default_starts_off(env):
    session = env.manager.create_session("s-default", "Chat", GROQ_CHAT, THINKER_MODEL)
    assert session.thinking_mode == "off"

    with env.factory() as s:
        db_sess = s.get(database.Session, "s-default")
        assert db_sess.thinking_mode == "off"


def test_session_level_override_on_supported_model(env):
    # Admin configured thinking=off
    mc.save_entry("groq", GROQ, THINKER_MODEL, {"thinking": "off"})
    session = env.manager.create_session("s-override", "Chat", GROQ_CHAT, THINKER_MODEL)
    assert session.thinking_mode == "off"

    # User overrides thinking to on via generation-settings
    res = env.client.post("/api/session/s-override/generation-settings", json={"thinking_mode": "on"})
    assert res.status_code == 200
    assert session.thinking_mode == "on"

    # Verify context endpoint reports effective thinking mode and support
    ctx = env.client.get("/api/session/s-override/context").json()
    assert ctx["thinking_supported"] is True
    assert ctx["thinking_mode"] == "on"


def test_session_level_override_clamped_on_unsupported_model(env):
    session = env.manager.create_session("s-unsupp-override", "Chat", GROQ_CHAT, UNSUPPORTED_MODEL)
    assert session.thinking_mode == "off"

    # User attempts to set thinking to on
    res = env.client.post("/api/session/s-unsupp-override/generation-settings", json={"thinking_mode": "on"})
    assert res.status_code == 200
    assert session.thinking_mode == "off"

    # Context endpoint reports not supported and off
    ctx = env.client.get("/api/session/s-unsupp-override/context").json()
    assert ctx["thinking_supported"] is False
    assert ctx["thinking_mode"] == "off"


def test_switching_model_clears_stale_thinking_mode(env):
    # Session starts with thinking on with supported model
    mc.save_entry("groq", GROQ, THINKER_MODEL, {"thinking": "on"})
    session = env.manager.create_session("s-switch", "Chat", GROQ_CHAT, THINKER_MODEL)
    assert session.thinking_mode == "on"

    # Switch model to an unsupported model via session_routes
    res = env.client.patch(
        "/api/session/s-switch",
        data={"model": UNSUPPORTED_MODEL, "endpoint_url": GROQ_CHAT, "endpoint_id": "groq"},
    )
    assert res.status_code == 200
    updated_session = env.manager.get_session("s-switch")
    assert updated_session.model == UNSUPPORTED_MODEL
    assert updated_session.thinking_mode == "off"

    # Verify database was updated
    with env.factory() as s:
        db_sess = s.get(database.Session, "s-switch")
        assert db_sess.model == UNSUPPORTED_MODEL
        assert db_sess.thinking_mode == "off"


@pytest.mark.asyncio
async def test_nonstreaming_chat_guards_unsupported_thinking_mode(monkeypatch):
    from routes import chat_routes

    captured = {}

    async def fake_llm_call_async_with_route_fallback(*args, **kwargs):
        captured.update(kwargs)
        return "response", args[0][0], args[0][0][1]

    from src.auth_helpers import require_chat_api_token_scope
    app = FastAPI()
    app.dependency_overrides[require_chat_api_token_scope] = lambda: None
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *a, **k: False)
    monkeypatch.setattr(chat_routes, "require_api_token_scope", lambda *a, **k: None)
    monkeypatch.setattr(chat_routes, "llm_call_async_with_route_fallback", fake_llm_call_async_with_route_fallback)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *a, **k: None)

    session = SimpleNamespace(
        id="s-nonstream",
        name="New Chat",
        model=UNSUPPORTED_MODEL,
        endpoint_url=GROQ_CHAT,
        thinking_mode="on",  # Stale or unsupported value on session
        headers={},
        history=[],
        temperature_override=None,
        max_tokens_override=None,
        prompt_preset=None,
        get_context_messages=lambda: [],
        add_message=lambda msg: None,
    )
    manager = SimpleNamespace(
        get_session=lambda sid: session,
        sessions={"s-nonstream": session},
        save_sessions=lambda: None,
    )

    async def _handle_mem(*a, **k):
        return None

    async def _preprocess(message, *a, **k):
        return (message, message, "", [], {})

    chat_handler = SimpleNamespace(
        handle_memory_command=_handle_mem,
        validate_and_extract_preset=lambda preset_id: (None, None, "", "", "", None),
        preprocess_message=_preprocess,
        update_session_name_if_needed=lambda *a, **k: None,
    )
    chat_processor = SimpleNamespace(build_context_preface=lambda **kw: ([], [], []))
    app.include_router(chat_routes.setup_chat_routes(
        manager,
        chat_handler,
        chat_processor,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    ))

    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "hello", "session": "s-nonstream", "selected_endpoint_id": "groq"},
    )
    assert response.status_code == 200
    assert captured["thinking_mode"] == "off"
