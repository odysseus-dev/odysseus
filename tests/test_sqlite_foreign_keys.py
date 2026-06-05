import pytest
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _drop_fake_core_database():
    parent = sys.modules.get("core")
    attr = getattr(parent, "database", None) if parent is not None else None
    mod = sys.modules.get("core.database") or attr
    if mod is None or isinstance(getattr(mod, "__file__", None), str):
        return
    sys.modules.pop("core.database", None)
    sys.modules.pop("src.database", None)
    if parent is not None and attr is mod:
        delattr(parent, "database")


_drop_fake_core_database()

from core.database import Base, Session, ChatMessage
from datetime import datetime

def test_sqlite_foreign_keys_cascade():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    TestSessionLocal = sessionmaker(bind=engine)
    db = TestSessionLocal()
    
    session_id = "test-session-123"
    s = Session(
        id=session_id,
        name="Test Session",
        endpoint_url="http://localhost:8000",
        model="gpt-4",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    m = ChatMessage(id="test-msg-123", session_id=session_id, role="user", content="test message")
    
    db.add(s)
    db.add(m)
    db.commit()
    
    assert db.query(Session).count() == 1
    assert db.query(ChatMessage).count() == 1
    
    db.query(Session).filter(Session.id == session_id).delete()
    db.commit()
    
    assert db.query(ChatMessage).count() == 0
    
    db.close()
