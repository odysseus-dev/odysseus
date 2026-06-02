"""Regression tests for issue #135 — "Chat context drifting; Breaking other chats".

Two root causes are pinned here:

A. The active-document / active-model pointers were process-global module state,
   so a document write in one chat targeted a document opened/created in another
   chat, and the most-recent-doc fallback crossed chat boundaries. They are now
   request-scoped (ContextVar) and the write fallback is scoped to the current
   chat session.

B. The automatic "Chat Sessions Tidy" sweep deleted recent / in-progress chats.
   It now skips anything touched within a recent-activity guard window.
"""
import contextvars
import unittest.mock as _mock
from datetime import datetime, timedelta

import pytest

from src.tool_implementations import (
    set_active_document, get_active_document, clear_active_document,
    set_active_model, get_active_model,
)
from src.session_actions import _is_recently_active, _RECENT_ACTIVITY_GUARD_MINUTES


def _real_sqlalchemy() -> bool:
    try:
        import sqlalchemy
    except Exception:
        return False
    return not isinstance(sqlalchemy, _mock.MagicMock)


def _require_real_db():
    """Skip at *run time* if the DB layer has been swapped for a MagicMock.

    conftest stubs ``src.database`` and (when sqlalchemy is absent) sqlalchemy
    itself, and some other tests in the full suite replace these modules at run
    time. Several existing DB-backed tests only work when run in isolation for
    the same reason; rather than add to that noise we skip cleanly instead of
    erroring with mock-comparison failures.
    """
    if not _real_sqlalchemy():
        pytest.skip("sqlalchemy stubbed in this run")
    # Probe the exact symbols the tests use — another test in the full suite may
    # have replaced any of them with a MagicMock. Instantiating proves they are
    # real mapped classes (a MagicMock would just return another MagicMock).
    try:
        from core.database import Base, Document, Session as DbSession, ChatMessage
        probe = DbSession(id="__probe__", name="x", endpoint_url="x", model="m")
    except Exception:
        pytest.skip("core.database models unusable in this run")
    if any(isinstance(x, _mock.MagicMock) for x in (Base, Document, DbSession, ChatMessage, probe)):
        pytest.skip("core.database stubbed by another test in this run")


needs_db = pytest.mark.skipif(not _real_sqlalchemy(), reason="needs a real sqlalchemy install")


# --------------------------------------------------------------------------- #
# A. active-document / active-model pointers are request-scoped (no bleed)
# --------------------------------------------------------------------------- #

def test_active_document_pointer_does_not_leak_between_requests():
    set_active_document(None)

    def in_request(doc):
        set_active_document(doc)
        return get_active_document()

    c1 = contextvars.copy_context()
    c2 = contextvars.copy_context()
    assert c1.run(in_request, "doc-req1") == "doc-req1"
    # c2 represents a different chat/request; it never set anything, so the
    # pointer set inside c1 must not be visible here.
    assert c2.run(get_active_document) is None


def test_active_model_pointer_does_not_leak_between_requests():
    set_active_model(None)

    def in_request(model):
        set_active_model(model)
        return get_active_model()

    c1 = contextvars.copy_context()
    c2 = contextvars.copy_context()
    assert c1.run(in_request, "model-req1") == "model-req1"
    assert c2.run(get_active_model) is None


def test_clear_active_document_still_scoped_to_matching_id():
    # Behaviour pinned by issue #1160 must survive the ContextVar migration.
    set_active_document("doc-abc")
    assert clear_active_document("doc-xyz") is False
    assert get_active_document() == "doc-abc"
    assert clear_active_document("doc-abc") is True
    assert get_active_document() is None


# --------------------------------------------------------------------------- #
# A. write-target resolution never crosses chat sessions
# --------------------------------------------------------------------------- #

@needs_db
def test_write_target_never_crosses_sessions():
    _require_real_db()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from core.database import Base, Document, Session as DbSession
    from src.tool_implementations import _resolve_write_target_document

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    db = Sess()
    try:
        now = datetime.utcnow()
        db.add(DbSession(id="sA", name="A", endpoint_url="x", model="m", owner="u1",
                         created_at=now, updated_at=now))
        db.add(DbSession(id="sB", name="B", endpoint_url="x", model="m", owner="u1",
                         created_at=now, updated_at=now))
        db.add(Document(id="docA", session_id="sA", title="A", language="markdown",
                        current_content="A", version_count=1, is_active=True,
                        archived=False, owner="u1"))
        db.commit()

        set_active_document(None)
        # chat B has no documents → must NOT resolve to chat A's document
        assert _resolve_write_target_document(db, Document, None, "u1", "sB") is None
        # chat A resolves to its own document
        assert _resolve_write_target_document(db, Document, None, "u1", "sA").id == "docA"
        # an explicit id always wins
        assert _resolve_write_target_document(db, Document, "docA", "u1", "sB").id == "docA"
        # the request's open document (ContextVar) is honoured even from chat B,
        # because that represents a doc the user actually has open this request
        set_active_document("docA")
        assert _resolve_write_target_document(db, Document, None, "u1", "sB").id == "docA"
        set_active_document(None)
        # a different owner can never resolve to u1's doc
        assert _resolve_write_target_document(db, Document, None, "u2", "sA") is None
    finally:
        db.close()
        set_active_document(None)


# --------------------------------------------------------------------------- #
# B. the auto-tidy recency guard
# --------------------------------------------------------------------------- #

class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_recency_guard_protects_recent_sessions():
    now = datetime.utcnow()
    recent = _Row(created_at=now, updated_at=now, last_message_at=None)
    assert _is_recently_active(recent, now) is True

    in_progress = _Row(created_at=now, updated_at=now,
                       last_message_at=now)  # user message just landed
    assert _is_recently_active(in_progress, now) is True

    old = now - timedelta(minutes=_RECENT_ACTIVITY_GUARD_MINUTES + 5)
    stale = _Row(created_at=old, updated_at=old, last_message_at=old)
    assert _is_recently_active(stale, now) is False

    no_stamps = _Row(created_at=None, updated_at=None, last_message_at=None)
    assert _is_recently_active(no_stamps, now) is False


@needs_db
def test_auto_sort_keeps_recent_chats_deletes_old_junk(monkeypatch):
    _require_real_db()
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import core.database as cdb
    from core.database import Base, Session as DbSession, ChatMessage

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    monkeypatch.setattr(cdb, "SessionLocal", Sess)

    now = datetime.utcnow()
    old = now - timedelta(minutes=90)
    db = Sess()
    try:
        # recent real Q&A
        db.add(DbSession(id="q1", name="ls -la Workspace", endpoint_url="x", model="m",
                         owner="u1", created_at=now, updated_at=now, last_message_at=now))
        db.add(ChatMessage(id="m1", session_id="q1", role="user", content="ls -la Workspace", timestamp=now))
        db.add(ChatMessage(id="m2", session_id="q1", role="assistant", content="empty.", timestamp=now))
        # recent in-progress (no assistant reply yet)
        db.add(DbSession(id="p1", name="New chat", endpoint_url="x", model="m",
                         owner="u1", created_at=now, updated_at=now, last_message_at=now))
        db.add(ChatMessage(id="m3", session_id="p1", role="user", content="Create file Hello.md", timestamp=now))
        # old junk
        db.add(DbSession(id="j1", name="hi", endpoint_url="x", model="m",
                         owner="u1", created_at=old, updated_at=old, last_message_at=old))
        db.add(ChatMessage(id="m4", session_id="j1", role="user", content="hi", timestamp=old))
        db.commit()
    finally:
        db.close()

    from src.session_actions import run_auto_sort
    asyncio.run(run_auto_sort("u1", skip_llm=True))

    db = Sess()
    try:
        alive = {r.id for r in db.query(DbSession).all()}
    finally:
        db.close()
    assert "q1" in alive, "recent Q&A chat must survive"
    assert "p1" in alive, "recent in-progress chat must survive"
    assert "j1" not in alive, "old junk chat should still be cleaned"
