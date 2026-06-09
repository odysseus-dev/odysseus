"""Regression coverage for auto-sort session cleanup.

Issue #1851 reported fresh chats being deleted immediately after their first
turn, leaving the browser pointed at a session id that no longer exists.
"""

import asyncio
from datetime import timedelta
import sys
import tempfile
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if type(sqlalchemy).__name__ == "MagicMock":
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ChatMessage as DbMessage, Session as DbSession, utcnow_naive
import src.session_actions as session_actions


def _make_session_factory():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = create_engine(
        f"sqlite:///{tmp.name}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    DbSession.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _install_session_factory(monkeypatch, session_factory):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    core_pkg = sys.modules.get("core")
    if core_pkg is not None:
        monkeypatch.setattr(core_pkg, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", session_factory)


def _add_message(db, sid, role, content, timestamp):
    db.add(
        DbMessage(
            id="m-" + uuid.uuid4().hex,
            session_id=sid,
            role=role,
            content=content,
            timestamp=timestamp,
        )
    )


def test_auto_sort_keeps_fresh_chat_with_completed_first_turn(monkeypatch):
    session_factory = _make_session_factory()
    _install_session_factory(monkeypatch, session_factory)

    sid = "s-" + uuid.uuid4().hex
    db = session_factory()
    try:
        db.add(
            DbSession(
                id=sid,
                owner="alice",
                name="Quick question",
                endpoint_url="",
                model="",
                archived=False,
                message_count=2,
                last_message_at=utcnow_naive(),
            )
        )
        _add_message(db, sid, "user", "hi", utcnow_naive())
        _add_message(db, sid, "assistant", "Hello! How can I help?", utcnow_naive())
        db.commit()
    finally:
        db.close()

    result = asyncio.run(session_actions.run_auto_sort("alice", skip_llm=True))

    db = session_factory()
    try:
        assert db.query(DbSession).filter(DbSession.id == sid).first() is not None
        assert db.query(DbMessage).filter(DbMessage.session_id == sid).count() == 2
        assert "Cleaned 0 sessions" in result
    finally:
        db.close()


def test_auto_sort_keeps_fresh_session_while_first_response_is_pending(monkeypatch):
    session_factory = _make_session_factory()
    _install_session_factory(monkeypatch, session_factory)

    sid = "s-" + uuid.uuid4().hex
    db = session_factory()
    try:
        db.add(
            DbSession(
                id=sid,
                owner="alice",
                name="New chat",
                endpoint_url="",
                model="",
                archived=False,
                message_count=1,
                last_message_at=utcnow_naive(),
            )
        )
        _add_message(db, sid, "user", "Tell me a quick joke", utcnow_naive())
        db.commit()
    finally:
        db.close()

    result = asyncio.run(session_actions.run_auto_sort("alice", skip_llm=True))

    db = session_factory()
    try:
        assert db.query(DbSession).filter(DbSession.id == sid).first() is not None
        assert db.query(DbMessage).filter(DbMessage.session_id == sid).count() == 1
        assert "Cleaned 0 sessions" in result
    finally:
        db.close()


def test_auto_sort_still_deletes_old_throwaway_sessions(monkeypatch):
    session_factory = _make_session_factory()
    _install_session_factory(monkeypatch, session_factory)

    old_time = utcnow_naive() - timedelta(hours=2)
    sid = "s-" + uuid.uuid4().hex
    db = session_factory()
    try:
        db.add(
            DbSession(
                id=sid,
                owner="alice",
                name="New chat",
                endpoint_url="",
                model="",
                archived=False,
                message_count=1,
                created_at=old_time,
                updated_at=old_time,
                last_accessed=old_time,
                last_message_at=old_time,
            )
        )
        _add_message(db, sid, "user", "hi", old_time)
        db.commit()
    finally:
        db.close()

    result = asyncio.run(session_actions.run_auto_sort("alice", skip_llm=True))

    db = session_factory()
    try:
        assert db.query(DbSession).filter(DbSession.id == sid).first() is None
        assert "Cleaned 1 sessions" in result
    finally:
        db.close()
