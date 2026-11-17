from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import core.session_manager as session_manager
from core.database import Session as DbSession
from core.session_manager import SessionManager


def test_create_session_persists_endpoint_headers(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    database.Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(session_manager, "SessionLocal", session_local)

    manager = SessionManager.__new__(SessionManager)
    manager.sessions = {}
    manager.upload_handler = None
    headers = {
        "Authorization": "Bearer test-key",
        "HTTP-Referer": "http://localhost:7011",
    }
    manager.create_session(
        session_id="child",
        name="Child",
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        model="moonshotai/kimi-k3",
        owner="alice",
        headers=headers,
    )

    db = session_local()
    try:
        row = db.query(DbSession).filter(DbSession.id == "child").one()
        restored = manager._db_to_session_meta(row)
    finally:
        db.close()

    assert restored is not None
    assert restored.headers == headers
