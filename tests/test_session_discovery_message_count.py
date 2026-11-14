"""Real-SQLite regressions for session discovery with stale derived counts."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.session_manager as session_manager


def _make_manager(db_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    session_manager.DbSession.metadata.create_all(bind=engine)
    session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    monkeypatch.setattr(session_manager, "SessionLocal", session_local)
    return session_manager.SessionManager(), session_manager


def test_discovery_uses_persisted_rows_and_repairs_cached_count(tmp_path, monkeypatch):
    from core.models import ChatMessage

    manager, session_manager = _make_manager(
        tmp_path / "session-discovery.db", monkeypatch
    )
    for session_id in ("stale-low", "empty", "stale-high"):
        manager.create_session(
            session_id=session_id,
            name=session_id,
            endpoint_url="http://example.invalid",
            model="test-model",
            rag=False,
            owner="tester",
        )
    manager.add_message("stale-low", ChatMessage("user", "persisted message"))

    db = session_manager.SessionLocal()
    try:
        db.query(session_manager.DbSession).filter(
            session_manager.DbSession.id == "stale-low"
        ).update({"message_count": 0})
        db.query(session_manager.DbSession).filter(
            session_manager.DbSession.id == "stale-high"
        ).update({"message_count": 7})
        db.commit()
    finally:
        db.close()

    restarted = session_manager.SessionManager()

    assert set(restarted.sessions) == {"stale-low"}
    assert restarted.sessions["stale-low"].history == []
    assert restarted.sessions["stale-low"].message_count == 1

    hydrated = restarted.get_session("stale-low")
    assert [message.content for message in hydrated.history] == ["persisted message"]


def test_empty_session_remains_discoverable_by_owner_after_restart(tmp_path, monkeypatch):
    """Empty saved sessions must remain visible to their owner after restart."""
    manager, sm = _make_manager(
        tmp_path / "empty-session-restart.db", monkeypatch
    )

    manager.create_session(
        session_id="empty-restart",
        name="Saved Empty Chat",
        endpoint_url="",
        model="",
        rag=False,
        owner="alice",
    )

    restarted = sm.SessionManager()

    assert "empty-restart" in restarted.get_sessions_for_user("alice")
    assert "empty-restart" not in restarted.get_sessions_for_user("bob")


def test_deleted_database_session_disappears_from_listing(tmp_path, monkeypatch):
    """An externally deleted session must not remain visible in the cache."""
    manager, session_manager = _make_manager(tmp_path / "ghost-listing.db", monkeypatch)

    manager.create_session(
        session_id="ghost-listing",
        name="Ghost listing",
        endpoint_url="",
        model="",
        owner="alice",
    )

    assert "ghost-listing" in manager.get_sessions_for_user("alice")

    db = session_manager.SessionLocal()
    try:
        row = db.query(session_manager.DbSession).filter(
            session_manager.DbSession.id == "ghost-listing"
        ).one()
        db.delete(row)
        db.commit()
    finally:
        db.close()

    assert "ghost-listing" not in manager.get_sessions_for_user("alice")
