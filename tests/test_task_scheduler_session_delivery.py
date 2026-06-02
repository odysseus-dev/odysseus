"""Regression tests for task-result delivery into chat sessions (issue #326)."""
import asyncio
import importlib
import sys
import types as _types

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, _types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

def _real_modules():
    for name, mod in list(sys.modules.items()):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            if not getattr(mod, "__file__", None):
                sys.modules.pop(name, None)
    sys.modules.pop("src.task_scheduler", None)
    sys.modules.pop("core.database", None)
    if "core" in sys.modules and not getattr(sys.modules["core"], "__file__", None):
        sys.modules.pop("core", None)
    if "core" in sys.modules and hasattr(sys.modules["core"], "database"):
        delattr(sys.modules["core"], "database")

    core_db = importlib.import_module("core.database")
    task_scheduler = importlib.import_module("src.task_scheduler")
    return core_db, task_scheduler.TaskScheduler


def _make_db():
    core_db, _TaskScheduler = _real_modules()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    core_db.Base.metadata.create_all(engine)
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
    core_db, TaskScheduler = _real_modules()
    db = _make_db()
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._session_manager = None

    asyncio.run(scheduler._deliver_task_result(_make_task(), "done", db))

    sessions = db.query(core_db.Session).all()
    assert len(sessions) == 1
    assert sessions[0].endpoint_url == ""
    assert sessions[0].model == ""
