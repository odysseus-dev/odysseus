import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.database import Base, Session, ChatMessage
from datetime import datetime

def test_sqlite_foreign_keys_cascade():
    # 1. Create a real SQLite in-memory engine the same way the app does
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    # 2. Create session factory and session
    TestSessionLocal = sessionmaker(bind=engine)
    db = TestSessionLocal()
    
    # 3. Insert a Session + ChatMessage
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
    
    # Verify both exist
    assert db.query(Session).count() == 1
    assert db.query(ChatMessage).count() == 1
    
    # 4. Delete the Session via bulk query().delete() (bypassing ORM-level cascades)
    db.query(Session).filter(Session.id == session_id).delete()
    db.commit()
    
    # 5. Assert the ChatMessage is automatically deleted by DB-level cascade
    assert db.query(ChatMessage).count() == 0
    
    db.close()
