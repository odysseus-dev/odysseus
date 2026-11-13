import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession, sessionmaker
from sqlalchemy.pool import StaticPool

import core.session_manager as sm
from core.database import Base, Session as DbSession, ChatMessage as DbChatMessage
from core.models import Session, ChatMessage


def test_add_message_rolls_back_memory_when_database_commit_fails(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    NormalSessionLocal = sessionmaker(bind=engine)

    class FailingCommitSession(SASession):
        def commit(self):
            raise RuntimeError("simulated commit failure")

    FailingSessionLocal = sessionmaker(
        bind=engine,
        class_=FailingCommitSession,
    )

    session_id = "isolated-transaction-test"

    try:
        # Seed an existing session and one durable message.
        db = NormalSessionLocal()
        try:
            db.add(DbSession(
                id=session_id,
                name="Isolation test",
                endpoint_url="http://localhost",
                model="test-model",
                owner="alice",
                message_count=1,
            ))
            db.add(DbChatMessage(
                id="existing-message",
                session_id=session_id,
                role="user",
                content="Existing durable message",
            ))
            db.commit()
        finally:
            db.close()

        monkeypatch.setattr(sm, "SessionLocal", FailingSessionLocal)
        monkeypatch.setattr(
            sm,
            "reserve_message_upload_references",
            lambda *args, **kwargs: None,
        )

        manager = sm.SessionManager.__new__(sm.SessionManager)
        manager.sessions = {}
        manager.upload_handler = None

        cached_session = Session(
            id=session_id,
            name="Isolation test",
            endpoint_url="http://localhost",
            model="test-model",
            owner="alice",
            history=[
                ChatMessage("user", "Existing durable message")
            ],
        )
        cached_session.message_count = 1

        manager.sessions[session_id] = cached_session

        # Isolate this test from session hydration and unrelated DB reads.
        monkeypatch.setattr(
            manager,
            "get_session",
            lambda requested_id: cached_session,
        )

        new_message = ChatMessage("user", "Message that must not be lost")

        # A failed commit must be visible to the caller.
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            manager.add_message(session_id, new_message)

        # The failed write must not remain in memory.
        assert len(cached_session.history) == 1
        assert cached_session.history[0].content == "Existing durable message"
        assert cached_session.message_count == 1

        # Verify the independent database still contains only the baseline.
        check = NormalSessionLocal()
        try:
            rows = (
                check.query(DbChatMessage)
                .filter(DbChatMessage.session_id == session_id)
                .all()
            )

            assert len(rows) == 1
            assert rows[0].id == "existing-message"
            assert rows[0].content == "Existing durable message"

            db_session = check.query(DbSession).filter(
                DbSession.id == session_id
            ).first()

            assert db_session.message_count == 1
        finally:
            check.close()

    finally:
        engine.dispose()
