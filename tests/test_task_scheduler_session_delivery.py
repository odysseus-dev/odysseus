"""Regression tests for task-result delivery into chat sessions (issue #326)."""
import asyncio
import sys
import types as _types

import pytest

_SQLALCHEMY_MODULES = (
    "sqlalchemy",
    "sqlalchemy.orm",
    "sqlalchemy.types",
    "sqlalchemy.ext",
    "sqlalchemy.ext.declarative",
    "sqlalchemy.sql",
    "sqlalchemy.sql.expression",
    "sqlalchemy.sql.sqltypes",
)


def _drop_module(name: str):
    module = sys.modules.pop(name, None)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    sentinel = object()
    parent_attr = getattr(parent, attr, sentinel) if isinstance(parent, _types.ModuleType) else sentinel
    if parent_attr is not sentinel and (module is None or parent_attr is module):
        delattr(parent, attr)


def _drop_stale_mock(name: str):
    module = sys.modules.get(name)
    if module is not None and not isinstance(module, _types.ModuleType):
        _drop_module(name)


for _mod in _SQLALCHEMY_MODULES:
    _drop_stale_mock(_mod)

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, _types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if not isinstance(sys.modules.get("sqlalchemy.orm"), _types.ModuleType):
    pytest.skip("sqlalchemy.orm is stubbed in this environment", allow_module_level=True)

def _load_real_scheduler():
    for _mod in _SQLALCHEMY_MODULES:
        _drop_stale_mock(_mod)
    for _mod in ("src.task_scheduler", "src.endpoint_resolver", "src.database", "core.database"):
        _drop_module(_mod)

    from core.database import Base, Session as DbSession
    from src.task_scheduler import TaskScheduler

    if type(Base).__name__ == "MagicMock" or type(TaskScheduler).__name__ == "MagicMock":
        pytest.skip("core.database/task_scheduler is stubbed — run this file in isolation")
    return Base, DbSession, TaskScheduler


def _make_db(Base):
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
    Base, DbSession, TaskScheduler = _load_real_scheduler()
    db = _make_db(Base)
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._session_manager = None
    scheduler._resolve_defaults = lambda _db, _owner: (None, None)

    asyncio.run(scheduler._deliver_task_result(_make_task(), "done", db))

    sessions = db.query(DbSession).all()
    assert len(sessions) == 1
    assert sessions[0].endpoint_url == ""
    assert sessions[0].model == ""
