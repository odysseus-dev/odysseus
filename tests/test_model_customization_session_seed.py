"""Seam (d): a saved thinking setting seeds new chats only.

Spec: openspec/changes/add-model-customization/specs/model-customization/spec.md
(requirement "Thinking seeds new chats"). Sessions are created through
`SessionManager.create_session`, the one constructor every creation path uses,
against a temporary SQLite database.
"""

import json

import pytest

from core import database
from core import session_manager as sm_module
from core.session_manager import SessionManager
from src import model_customization as mc
from tests.helpers.sqlite_db import make_temp_sqlite

GROQ = "https://api.groq.com/openai/v1"
GROQ_CHAT = GROQ + "/chat/completions"
THINKER = "qwen3-32b"            # has the user thinking toggle
PLAIN = "llama-3.3-70b"          # no thinking toggle


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "STORE_PATH", str(tmp_path / "model_customizations.json"))
    mc._reset_cache()
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    monkeypatch.setattr(sm_module, "SessionLocal", factory)
    mc._reset_endpoint_cache()
    with factory() as s:
        s.add(database.ModelEndpoint(
            id="groq", name="groq", base_url=GROQ, cached_models=json.dumps([THINKER, PLAIN]),
        ))
        s.commit()
    yield factory
    engine.dispose()
    tmpfile.close()


def _stored_mode(factory, session_id):
    with factory() as s:
        return s.get(database.Session, session_id).thinking_mode


def _new_chat(session_id, model):
    return SessionManager().create_session(session_id, "Chat", GROQ_CHAT, model)


def test_a_new_chat_starts_with_thinking_on(db):
    mc.save_entry("groq", GROQ, THINKER, {"thinking": "on"})
    session = _new_chat("s1", THINKER)
    assert session.thinking_mode == "on"
    assert _stored_mode(db, "s1") == "on"


def test_models_without_the_thinking_toggle_still_seed_the_value(db):
    mc.save_entry("groq", GROQ, PLAIN, {"thinking": "on"})
    session = _new_chat("s2", PLAIN)
    assert session.thinking_mode == "on"
    assert _stored_mode(db, "s2") == "on"


def test_uncustomized_models_start_with_thinking_off(db):
    session = _new_chat("s3", THINKER)
    assert session.thinking_mode == "off"
    assert _stored_mode(db, "s3") == "off"


def test_the_in_chat_toggle_still_wins(db):
    mc.save_entry("groq", GROQ, THINKER, {"thinking": "on"})
    _new_chat("s4", THINKER)
    # The history settings route persists the toggle on the session row.
    with db() as s:
        s.get(database.Session, "s4").thinking_mode = "off"
        s.commit()
    assert SessionManager().get_session("s4").thinking_mode == "off"


def test_existing_chats_keep_their_thinking_mode(db):
    _new_chat("s5", THINKER)
    mc.save_entry("groq", GROQ, THINKER, {"thinking": "on"})
    assert _stored_mode(db, "s5") == "off"
    assert SessionManager().get_session("s5").thinking_mode == "off"
