"""Regression tests for task-result delivery into chat sessions (issue #326)."""
import asyncio
import types as _types

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, _types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Session as DbSession
from src.task_scheduler import TaskScheduler


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_task():
    return _types.SimpleNamespace(
        id="task-1",
        name="Chat Sessions Tidy",
        prompt="tidy",
        output_target="session",
        endpoint_url=None,
        model=None,
        session_id=None,
        owner=None,
        crew_member_id=None,
    )


def test_session_delivery_survives_empty_database():
    """On a fresh/wiped database there is no session to inherit endpoint/model
    from, so _resolve_defaults returns None. The delivery must still persist a
    session instead of crashing on the NOT NULL constraint (issue #326)."""
    db = _make_db()
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._session_manager = None

    asyncio.run(scheduler._deliver_task_result(_make_task(), "done", db))

    sessions = db.query(DbSession).all()
    assert len(sessions) == 1
    assert sessions[0].endpoint_url == ""
    assert sessions[0].model == ""
