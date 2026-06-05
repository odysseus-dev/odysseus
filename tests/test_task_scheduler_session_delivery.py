"""Regression tests for task-result delivery into chat sessions (issue #326)."""
import asyncio
import sys
import types as _types

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, _types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
from core.database import Base, Session as DbSession
from src.task_scheduler import TaskScheduler

# This test needs the real core.database (real SQLAlchemy Base/ChatMessage).
# test_null_owner_gates.py no longer leaks its stubs (per-test fixture cleanup
# since PR #1513), but several other files still install core.database stubs
# at module level without teardown (test_model_routes, test_companion_readonly,
# test_endpoint_probing, test_vault_password_not_in_argv).  When any of those
# are collected before us, core.database is a stub and Base is a MagicMock.
# Skip in that case — the test passes correctly in isolation or when collected
# before the stubbing files.
if type(Base).__name__ == "MagicMock":
    pytest.skip("core.database is stubbed — run this file in isolation", allow_module_level=True)


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


def test_session_delivery_survives_empty_database(monkeypatch):
    """On a fresh/wiped database there is no session to inherit endpoint/model
    from, so _resolve_defaults returns None. The delivery must still persist a
    session instead of crashing on the NOT NULL constraint (issue #326)."""
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)

    db = _make_db()
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._session_manager = None

    asyncio.run(scheduler._deliver_task_result(_make_task(), "done", db))

    sessions = db.query(DbSession).all()
    assert len(sessions) == 1
    assert sessions[0].endpoint_url == ""
    assert sessions[0].model == ""
