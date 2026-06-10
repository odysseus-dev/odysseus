"""Tests for topic keyword matching (src/topic_analyzer.py)."""
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

from core.database import Base, Session as DbSession, ChatMessage as DbChatMessage
from core.session_manager import SessionManager
from src.topic_analyzer import analyze_topics
from datetime import datetime


def _sm(*messages):
    history = [{"role": "user", "content": c} for c in messages]
    return SimpleNamespace(sessions={"s1": {"owner": "alice", "name": "S", "history": history}})


def _freq(result):
    return {t["topic"]: t["frequency"] for t in result["topics"]}


def test_substring_does_not_false_match_technology():
    # Regression: "ai" matched inside "email"/"again"/"rain"/"wait", flagging
    # Technology for messages with no technical content at all.
    result = analyze_topics(_sm("Can you send me an email again about the rain? I will wait."), owner="alice")
    assert "Technology" not in _freq(result)


def test_real_keywords_still_match():
    result = analyze_topics(_sm("I wrote some Python code to test the algorithm."), owner="alice")
    assert _freq(result).get("Technology", 0) >= 1


def test_multiword_keyword_matches():
    result = analyze_topics(_sm("Can you explain how to set this up?"), owner="alice")
    assert "Learning" in _freq(result)


def test_topic_analyzer_hydrates_sessions(monkeypatch):
    # 1. Create clean in-memory database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    
    # 2. Create test session factory
    TestSessionLocal = sessionmaker(bind=engine)
    
    # 3. Populate test database with a session and a message about Python
    db = TestSessionLocal()
    session_id = "session-1"
    
    s = DbSession(
        id=session_id,
        name="Python chat",
        endpoint_url="http://localhost:8000",
        model="gpt-4",
        owner="alice",
        message_count=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    m = DbChatMessage(
        id="msg-1",
        session_id=session_id,
        role="user",
        content="I love writing python code.",
        timestamp=datetime.utcnow()
    )
    
    db.add(s)
    db.add(m)
    db.commit()
    db.close()
    
    # 4. Patch SessionLocal to use our in-memory DB
    import core.session_manager
    import core.database
    monkeypatch.setattr(core.session_manager, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(core.database, "SessionLocal", TestSessionLocal)
    
    # 5. Initialize the real SessionManager and load metadata (seeds sessions with empty history)
    sm = SessionManager()
    
    # Verify that the session is in sm.sessions, and its history is currently empty
    assert session_id in sm.sessions
    assert len(sm.sessions[session_id].history) == 0
    
    # 6. Execute the topic analysis
    res = analyze_topics(sm, owner="alice")
    
    # 7. Assertions
    # There should be 1 topic found (Technology, since "python" / "code" are keywords)
    assert res["total_topics"] > 0
    
    # Check that the topic is Technology
    tech_topic = next((t for t in res["topics"] if t["topic"] == "Technology"), None)
    assert tech_topic is not None
    assert tech_topic["frequency"] >= 1
