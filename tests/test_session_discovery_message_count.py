"""Real-SQLite regressions for session discovery with stale derived counts."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as database
import core.session_manager as session_manager


def _make_manager(db_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    database.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    monkeypatch.setattr(session_manager, "SessionLocal", session_local)
    return session_manager.SessionManager(), database, session_manager


def test_discovery_uses_persisted_rows_and_repairs_cached_count(tmp_path, monkeypatch):
    from core.models import ChatMessage

    manager, database, session_manager = _make_manager(
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

    db = database.SessionLocal()
    try:
        db.query(database.Session).filter(
            database.Session.id == "stale-low"
        ).update({"message_count": 0})
        db.query(database.Session).filter(
            database.Session.id == "stale-high"
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
