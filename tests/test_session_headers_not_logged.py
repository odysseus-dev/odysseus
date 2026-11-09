"""Endpoint API keys held in a chat session's auth headers must never reach a
log line or an error string.

SQLAlchemy renders a failed statement's bound parameters into the exception
text (``[parameters: (...)]``). Session rows carry the endpoint auth headers
(``Authorization: Bearer <key>``), so any logged or returned DB error on an
INSERT/UPDATE of a session row would print the key unless the engine hides
parameters.
"""
import logging
import uuid

import pytest
from sqlalchemy import text

from core import database
from core.database import Session as DbSession, SessionLocal

KEY = "sk-live-session-header-key-5c1e9a"
NEW_KEY = "sk-live-rotated-header-key-0b7d42"


@pytest.fixture
def db_ready():
    database.Base.metadata.create_all(bind=database.engine)
    yield


def _insert_row(sid):
    db = SessionLocal()
    try:
        db.add(DbSession(id=sid, name="n", endpoint_url="http://x/v1/chat/completions",
                         model="m", headers={"Authorization": f"Bearer {KEY}"}))
        db.commit()
    finally:
        db.close()


def test_persist_session_headers_failure_log_omits_key(db_ready, caplog):
    from routes import session_routes

    sid = f"hdr-{uuid.uuid4().hex[:8]}"
    _insert_row(sid)
    trigger = f"block_update_{uuid.uuid4().hex[:8]}"
    with database.engine.begin() as conn:
        conn.execute(text(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON sessions "
            "BEGIN SELECT RAISE(ABORT, 'blocked by test'); END"
        ))
    try:
        with caplog.at_level(logging.WARNING):
            # A different value, so the UPDATE actually carries the headers.
            ok = session_routes._persist_session_headers(
                sid, {"Authorization": f"Bearer {NEW_KEY}"}
            )
    finally:
        with database.engine.begin() as conn:
            conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger}"))
            conn.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": sid})

    assert ok is False
    assert "Failed to persist headers" in caplog.text
    assert "blocked by test" in caplog.text
    assert NEW_KEY not in caplog.text
    assert KEY not in caplog.text


def test_create_session_failure_log_and_error_omit_key(db_ready, caplog):
    from core.session_manager import SessionManager

    sid = f"hdr-{uuid.uuid4().hex[:8]}"
    _insert_row(sid)
    manager = SessionManager.__new__(SessionManager)
    manager.sessions = {}
    try:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(Exception) as excinfo:
                manager.create_session(
                    session_id=sid, name="dup", endpoint_url="http://x/v1/chat/completions",
                    model="m", headers={"Authorization": f"Bearer {KEY}"},
                )
    finally:
        with database.engine.begin() as conn:
            conn.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": sid})

    # src/agent_tools/session_tools.create_session returns this text to the model.
    assert "UNIQUE constraint failed" in str(excinfo.value)
    assert KEY not in str(excinfo.value)
    assert "Error creating session" in caplog.text
    assert KEY not in caplog.text
