"""A plain text message that merely *looks* like a JSON array of objects must
NOT be silently re-parsed into a list on reload.

_parse_msg_content de-serializes multimodal (image/audio) content back into a
list of content blocks. The old heuristic accepted ANY string that started
with "[{" and contained the substring '"type"'. A user who pasted an API
schema / sample such as `[{"type": "object", "name": "foo"}]` therefore had
their text message permanently corrupted into a Python list on the next
session hydration. The fix restricts the round-trip to lists whose elements
are all recognized content-block types (text/image_url/audio/...).
"""
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Session as DbSession
from core.models import ChatMessage

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture
def manager(monkeypatch):
    import core.session_manager as sm
    monkeypatch.setattr(sm, "SessionLocal", _TS)
    mgr = sm.SessionManager.__new__(sm.SessionManager)
    mgr.sessions = {}
    return mgr


def _make_session(sid, owner="alice"):
    db = _TS()
    try:
        db.add(DbSession(id=sid, owner=owner, name="chat",
                         endpoint_url="http://x", model="gpt-4o",
                         archived=False, message_count=1))
        db.commit()
    finally:
        db.close()


def test_jsonlike_user_string_not_corrupted(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)
    text = '[{"type": "object", "name": "foo"}]'
    msgs = [ChatMessage(role="user", content=text)]
    assert manager.replace_messages(sid, msgs) is True

    manager.sessions.clear()
    reloaded = manager.get_session(sid)
    # Must come back as the ORIGINAL STRING, not silently parsed into a list.
    assert isinstance(reloaded.history[0].content, str)
    assert reloaded.history[0].content == text


def test_real_multimodal_content_still_round_trips(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)
    multimodal = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    msgs = [ChatMessage(role="user", content=multimodal)]
    assert manager.replace_messages(sid, msgs) is True

    manager.sessions.clear()
    reloaded = manager.get_session(sid)
    assert reloaded.history[0].content == multimodal
