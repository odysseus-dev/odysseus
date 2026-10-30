"""The first message of a chat stores its reasoning effort on the session.

A new chat has no session when the effort is picked, so the picker holds it
client-side and the first message carries it. The chat route must store the
validated value as the session's `thinking_mode` so later messages and
reloads keep it.
"""
import tempfile
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Session as DbSession

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)

CHATGPT_MODEL = "gpt-6-astra"  # has advertised reasoning levels without a network fetch


def _seed(thinking_mode):
    sid = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(DbSession).delete()
        db.add(DbSession(id=sid, owner="alice", name="chat", endpoint_url="http://localhost",
                         model=CHATGPT_MODEL, message_count=2, thinking_mode=thinking_mode))
        db.commit()
    finally:
        db.close()
    return sid


def _stored_mode(sid):
    db = _TS()
    try:
        return db.query(DbSession).filter(DbSession.id == sid).first().thinking_mode
    finally:
        db.close()


@pytest.fixture
def chat_routes(monkeypatch):
    import routes.chat_routes as cr
    monkeypatch.setattr(cr, "SessionLocal", _TS)
    return cr


def test_first_message_effort_is_stored_on_the_session(chat_routes):
    sid = _seed("off")
    sess = SimpleNamespace(id=sid, thinking_mode="off")
    chat_routes._persist_chat_reasoning_effort(sess, "high")
    assert _stored_mode(sid) == "effort:high"
    assert sess.thinking_mode == "effort:high"


def test_changed_effort_overwrites_the_stored_one(chat_routes):
    sid = _seed("effort:low")
    sess = SimpleNamespace(id=sid, thinking_mode="effort:low")
    chat_routes._persist_chat_reasoning_effort(sess, "medium")
    assert _stored_mode(sid) == "effort:medium"


def test_no_effort_leaves_the_stored_mode_alone(chat_routes):
    sid = _seed("effort:high")
    sess = SimpleNamespace(id=sid, thinking_mode="effort:high")
    chat_routes._persist_chat_reasoning_effort(sess, None)
    assert _stored_mode(sid) == "effort:high"


def test_chat_route_persists_only_after_validation():
    """The route must store the validated effort, never the raw request value."""
    import inspect
    import routes.chat_routes as cr
    src = inspect.getsource(cr)
    validate = src.index("reasoning_effort = validate_reasoning_effort(sess.model, reasoning_effort)\n            _persist")
    assert validate > 0
