"""Regression guard for #1659.

`routes/history_routes.py` ordered three ChatMessage queries by
``DbChatMessage.created_at`` — the mark-stopped (`:268`), update-last-meta
(`:323`) and merge-last-assistant (`:404`) handlers. The ``ChatMessage`` model
does **not** inherit ``TimestampMixin`` and exposes only a ``timestamp`` column,
so ``DbChatMessage.created_at`` raised ``AttributeError`` at query-build time ->
HTTP 500 on Stop, last-message metadata updates, and Continue/merge.

This test pins three things:
  1. the model genuinely has ``timestamp`` and no ``created_at`` (justifies the fix);
  2. the corrected ``order_by(DbChatMessage.timestamp)`` query builds and runs;
  3. ``routes/history_routes.py`` never orders a ChatMessage query by the
     non-existent ``created_at`` column again.
"""
import os
from pathlib import Path

# Keep the import-time engine hermetic — no on-disk app.db.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ChatMessage as DbChatMessage, Session as DbSession


HISTORY_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "history" / "history_routes.py"


def test_chatmessage_model_has_timestamp_not_created_at():
    assert hasattr(DbChatMessage, "timestamp"), "ChatMessage should expose a `timestamp` column"
    assert not hasattr(DbChatMessage, "created_at"), (
        "ChatMessage does not inherit TimestampMixin; ordering by `created_at` "
        "raises AttributeError -> HTTP 500 (#1659)"
    )


def test_order_by_timestamp_query_executes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        sid = "sess1234"
        # FK enforcement is on (PRAGMA foreign_keys), so seed the parent session.
        db.add(DbSession(id=sid, name="t", endpoint_url="http://x", model="m"))
        db.add(DbChatMessage(id="m1", session_id=sid, role="assistant", content="first"))
        db.add(DbChatMessage(id="m2", session_id=sid, role="assistant", content="second"))
        db.commit()

        # Mirrors mark_stopped / update_last_meta (descending, .first()).
        last_assistant = (
            db.query(DbChatMessage)
            .filter(DbChatMessage.session_id == sid, DbChatMessage.role == "assistant")
            .order_by(DbChatMessage.timestamp.desc())
            .first()
        )
        assert last_assistant is not None

        # Mirrors merge_last_assistant (ascending, .all()).
        all_rows = (
            db.query(DbChatMessage)
            .filter(DbChatMessage.session_id == sid)
            .order_by(DbChatMessage.timestamp)
            .all()
        )
        assert len(all_rows) == 2
    finally:
        db.close()


def test_history_routes_do_not_order_by_created_at():
    text = HISTORY_ROUTES.read_text(encoding="utf-8")
    assert "DbChatMessage.created_at" not in text, (
        "history_routes must order ChatMessage queries by `.timestamp`, not the "
        "non-existent `.created_at` column (raises AttributeError -> HTTP 500, #1659)"
    )
