"""replace_messages must JSON-serialize multimodal (list) content.

A chat with an image/audio attachment carries list content. When such a
chat is compacted, the manual-compaction path calls replace_messages with
the retained messages. replace_messages wrote message.content straight into
the Text column, so SQLAlchemy bound the list\'s single-quoted repr. On
reload _parse_msg_content only de-serializes a string that contains the
double-quoted "type", so the repr failed the check and the message came
back as a corrupted string blob - the attachment was destroyed. The
sibling _persist_message json.dumps-es list content; replace_messages did
not.
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
        db.add(DbSession(id=sid, owner=owner, name="chat", model="gpt-4o",
                         endpoint_url="http://localhost:11434",
                         archived=False, message_count=1))
        db.commit()
    finally:
        db.close()


def test_multimodal_content_round_trips_through_replace_messages(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)

    multimodal = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    msgs = [ChatMessage(role="user", content=multimodal)]
    assert manager.replace_messages(sid, msgs) is True

    # Drop the in-memory cache so the next read hydrates from the DB.
    manager.sessions.clear()
    reloaded = manager.get_session(sid)
    assert len(reloaded.history) == 1
    # Content must come back as the original list, not a repr string blob.
    assert reloaded.history[0].content == multimodal


def test_plain_string_content_still_round_trips(manager):
    sid = "sess-" + uuid.uuid4().hex[:8]
    _make_session(sid)
    msgs = [ChatMessage(role="user", content="just text")]
    assert manager.replace_messages(sid, msgs) is True
    manager.sessions.clear()
    reloaded = manager.get_session(sid)
    assert reloaded.history[0].content == "just text"
