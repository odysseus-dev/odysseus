from datetime import datetime, timedelta
import asyncio
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.database import ChatMessage as DbChatMessage
from core.database import Session as DbSession
from src.session_search import SessionSearchResult, search_session_messages


def _db(with_fts=True):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    if with_fts:
        db.connection().exec_driver_sql(
            """
            CREATE VIRTUAL TABLE chat_messages_fts USING fts5(
                content,
                message_id UNINDEXED,
                session_id UNINDEXED,
                role UNINDEXED
            )
            """
        )
    return db


def _add_session(db, sid, owner="alice", archived=False, name=None):
    db.add(
        DbSession(
            id=sid,
            name=name or sid,
            endpoint_url="http://example.test",
            model="test-model",
            owner=owner,
            archived=archived,
            message_count=0,
        )
    )


def _add_message(db, sid, mid, role, content, when):
    db.add(DbChatMessage(id=mid, session_id=sid, role=role, content=content, timestamp=when))
    if _has_fts(db):
        db.connection().exec_driver_sql(
            "INSERT INTO chat_messages_fts(content, message_id, session_id, role) VALUES (?, ?, ?, ?)",
            (content, mid, sid, role),
        )


def _has_fts(db):
    return (
        db.connection()
        .exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages_fts'")
        .first()
        is not None
    )


def test_session_search_uses_fts_and_returns_context():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "s1", owner="alice", name="Jazz planning")
        _add_message(db, "s1", "m1", "user", "Before context about music", base)
        _add_message(db, "s1", "m2", "assistant", "We talked about modal jazz theory", base + timedelta(minutes=1))
        _add_message(db, "s1", "m3", "user", "After context about tasks", base + timedelta(minutes=2))
        db.commit()

        results = search_session_messages("modal jazz", owner="alice", db=db)

        assert [r.message_id for r in results] == ["m2"]
        assert results[0].session_name == "Jazz planning"
        assert results[0].context_before[0]["message_id"] == "m1"
        assert results[0].context_after[0]["message_id"] == "m3"
        assert "modal" in results[0].content_snippet.lower()
    finally:
        db.close()


def test_session_search_escapes_like_wildcards_in_fallback():
    db = _db(with_fts=False)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "s1", owner="alice")
        _add_message(db, "s1", "literal", "user", "The literal token is foo_bar.", base)
        _add_message(db, "s1", "wild", "user", "The wildcard-looking token is fooXbar.", base + timedelta(minutes=1))
        db.commit()

        results = search_session_messages("foo_bar", owner="alice", db=db)

        assert [r.message_id for r in results] == ["literal"]
    finally:
        db.close()


def test_session_search_owner_scope_includes_legacy_and_excludes_other_users():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "alice", owner="alice")
        _add_session(db, "legacy", owner=None)
        _add_session(db, "bob", owner="bob")
        _add_message(db, "alice", "m-alice", "user", "shared recall target", base)
        _add_message(db, "legacy", "m-legacy", "user", "shared recall target", base + timedelta(minutes=1))
        _add_message(db, "bob", "m-bob", "user", "shared recall target", base + timedelta(minutes=2))
        db.commit()

        results = search_session_messages("shared recall target", owner="alice", db=db)

        assert {r.message_id for r in results} == {"m-alice", "m-legacy"}
    finally:
        db.close()


def test_session_search_can_exclude_legacy_rows_for_authenticated_ui_scope():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "alice", owner="alice")
        _add_session(db, "legacy", owner=None)
        _add_message(db, "alice", "m-alice", "user", "exact owner target", base)
        _add_message(db, "legacy", "m-legacy", "user", "exact owner target", base + timedelta(minutes=1))
        db.commit()

        results = search_session_messages(
            "exact owner target",
            owner="alice",
            include_legacy_owner=False,
            db=db,
        )

        assert [r.message_id for r in results] == ["m-alice"]
    finally:
        db.close()


def test_session_search_ownerless_call_only_sees_legacy_rows():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "alice", owner="alice")
        _add_session(db, "legacy", owner=None)
        _add_message(db, "alice", "m-alice", "user", "ownerless search target", base)
        _add_message(db, "legacy", "m-legacy", "user", "ownerless search target", base + timedelta(minutes=1))
        db.commit()

        results = search_session_messages("ownerless search target", owner=None, db=db)

        assert [r.message_id for r in results] == ["m-legacy"]
    finally:
        db.close()


def test_session_search_falls_back_to_like_when_fts_has_no_substring_hits():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "s1", owner="alice")
        _add_message(db, "s1", "m1", "user", "We discussed customidentifier routing.", base)
        db.commit()

        results = search_session_messages("identifier", owner="alice", db=db)

        assert [r.message_id for r in results] == ["m1"]
        assert "identifier" in results[0].content_snippet
    finally:
        db.close()


def test_session_search_merges_like_substring_hits_with_fts_hits():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "s1", owner="alice")
        _add_message(db, "s1", "m-token", "user", "The identifier token is standalone.", base)
        _add_message(db, "s1", "m-substring", "assistant", "We also discussed customidentifier routing.", base + timedelta(minutes=1))
        db.commit()

        results = search_session_messages("identifier", owner="alice", db=db)

        assert {r.message_id for r in results} == {"m-token", "m-substring"}
    finally:
        db.close()


def test_session_search_can_preserve_unrestricted_no_auth_route_scope():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "owned", owner="admin")
        _add_session(db, "legacy", owner=None)
        _add_message(db, "owned", "m-owned", "user", "no auth search target", base)
        _add_message(db, "legacy", "m-legacy", "user", "no auth search target", base + timedelta(minutes=1))
        db.commit()

        results = search_session_messages(
            "no auth search target",
            owner=None,
            restrict_owner=False,
            db=db,
        )

        assert {r.message_id for r in results} == {"m-owned", "m-legacy"}
    finally:
        db.close()


def test_session_search_excludes_archived_by_default():
    db = _db(with_fts=True)
    try:
        base = datetime(2026, 1, 1, 12, 0, 0)
        _add_session(db, "active", owner="alice")
        _add_session(db, "archived", owner="alice", archived=True)
        _add_message(db, "active", "m-active", "user", "archive filter target", base)
        _add_message(db, "archived", "m-archived", "user", "archive filter target", base + timedelta(minutes=1))
        db.commit()

        results = search_session_messages("archive filter target", owner="alice", db=db)

        assert [r.message_id for r in results] == ["m-active"]
    finally:
        db.close()


def test_chat_messages_fts_migration_backfills_and_tracks_inserts(tmp_path, monkeypatch):
    from core import database as cdb

    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL
        );
        INSERT INTO chat_messages(id, session_id, role, content)
        VALUES ('m1', 's1', 'user', 'backfilled transcript search');
        """
    )
    conn.close()

    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_path}")

    cdb._migrate_chat_messages_fts()

    conn = sqlite3.connect(db_path)
    try:
        backfilled = conn.execute(
            "SELECT message_id FROM chat_messages_fts WHERE chat_messages_fts MATCH 'backfilled'"
        ).fetchall()
        assert backfilled == [("m1",)]

        conn.execute(
            "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
            ("m2", "s1", "assistant", "triggered transcript search"),
        )
        triggered = conn.execute(
            "SELECT message_id FROM chat_messages_fts WHERE chat_messages_fts MATCH 'triggered'"
        ).fetchall()
        assert triggered == [("m2",)]
    finally:
        conn.close()


def test_search_chats_formats_shared_results(monkeypatch):
    from src import session_search
    from src.tool_implementations import do_search_chats

    def fake_search(query, limit=20, owner=None, include_archived=False, context_messages=1, db=None):
        return [
            SessionSearchResult(
                message_id="m2",
                session_id="s1",
                session_name="Design notes",
                role="assistant",
                content="We discussed session search.",
                content_snippet="We discussed session search.",
                timestamp="2026-01-01T12:00:00",
                context_before=[{"message_id": "m1", "role": "user", "content": "Can you find old chats?", "timestamp": None}],
                context_after=[{"message_id": "m3", "role": "user", "content": "That helps.", "timestamp": None}],
            )
        ]

    monkeypatch.setattr(session_search, "search_session_messages", fake_search)

    out = asyncio.run(do_search_chats("session search", owner="alice"))

    assert "Design notes" in out["results"]
    assert "Match (assistant): We discussed session search." in out["results"]
    assert "Before (user): Can you find old chats?" in out["results"]
    assert "After (user): That helps." in out["results"]
