"""Rewriting the last assistant message must persist to the DB.

The /api/rewrite persistence path ordered by DBChatMessage.created_at, but
the ChatMessage model has no created_at column (only timestamp). Building
that query raised AttributeError, which the surrounding except swallowed,
and since session_manager.save_sessions() is a no-op this DB UPDATE was the
only persistence path. The rewrite was shown live but silently lost on
reload.
"""
import tempfile
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ChatMessage as DBChatMessage, Session as DbSession


def test_chatmessage_has_timestamp_not_created_at():
    # The old code referenced .created_at, which does not exist -> AttributeError.
    assert hasattr(DBChatMessage, "timestamp")
    assert not hasattr(DBChatMessage, "created_at")


def test_rewrite_query_selects_and_updates_latest_assistant_message():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
    cdb.Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    sid = "s-" + uuid.uuid4().hex[:8]
    base = datetime(2026, 6, 3, 12, 0, 0)
    db = TS()
    try:
        db.add(DbSession(id=sid, owner="alice", name="c", model="m", archived=False))
        db.add(DBChatMessage(id="m1", session_id=sid, role="assistant", content="old first", timestamp=base))
        db.add(DBChatMessage(id="m2", session_id=sid, role="assistant", content="old latest", timestamp=base + timedelta(minutes=1)))
        db.commit()
    finally:
        db.close()

    # Exactly the query the rewrite path runs (with the fixed column).
    db = TS()
    try:
        db_msg = (
            db.query(DBChatMessage)
            .filter(DBChatMessage.session_id == sid, DBChatMessage.role == "assistant")
            .order_by(DBChatMessage.timestamp.desc())
            .first()
        )
        assert db_msg is not None and db_msg.id == "m2"
        db_msg.content = "rewritten"
        db.commit()
    finally:
        db.close()

    db = TS()
    try:
        latest = db.query(DBChatMessage).filter(DBChatMessage.id == "m2").first()
        assert latest.content == "rewritten"
    finally:
        db.close()
