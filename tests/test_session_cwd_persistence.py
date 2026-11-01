from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as cdb
import core.session_manager as smod
from core.database import Session as DbSession
from core.session_manager import SessionManager


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    cdb.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_session_manager_create_and_reload_preserves_cwd(tmp_path, monkeypatch):
    session_local = _session_factory(tmp_path)
    monkeypatch.setattr(smod, "SessionLocal", session_local)

    manager = SessionManager.__new__(SessionManager)
    manager.sessions = {}
    manager.upload_handler = None

    created = manager.create_session(
        "sid",
        "Workspace",
        "http://localhost/v1/chat/completions",
        "gpt-4",
        owner="alice",
        cwd="/home/pewds/odysseus-tui",
    )

    assert created.cwd == "/home/pewds/odysseus-tui"

    db = session_local()
    try:
        row = db.query(DbSession).filter(DbSession.id == "sid").first()
        assert row.cwd == "/home/pewds/odysseus-tui"
        hydrated = manager._db_to_session_meta(row)
    finally:
        db.close()

    assert hydrated.cwd == "/home/pewds/odysseus-tui"
