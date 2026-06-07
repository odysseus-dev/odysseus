"""Task chaining must not cross owner boundaries."""

import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.task_routes as task_routes
from core.database import ScheduledTask

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
task_routes.SessionLocal = _TS


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    task_routes.SessionLocal = _TS
    router = task_routes.setup_task_routes(MagicMock())
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _seed_task(task_id, owner, *, then_task_id=None):
    db = _TS()
    try:
        task = ScheduledTask(
            id=task_id,
            owner=owner,
            name=task_id,
            prompt="do work",
            task_type="llm",
            trigger_type="webhook",
            status="active",
            output_target="session",
            then_task_id=then_task_id,
        )
        db.add(task)
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_create_task_rejects_cross_owner_chain_target():
    _seed_task("bob-target-create", "bob")
    create_task = _endpoint("POST", "/api/tasks")

    req = task_routes.TaskCreate(
        prompt="alice source",
        trigger_type="webhook",
        then_task_id="bob-target-create",
    )
    with pytest.raises(HTTPException) as exc:
        await create_task(_req("alice"), req)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_task_rejects_cross_owner_chain_target():
    _seed_task("alice-source-update", "alice")
    _seed_task("bob-target-update", "bob")
    update_task = _endpoint("PUT", "/api/tasks/{task_id}")

    with pytest.raises(HTTPException) as exc:
        await update_task(
            _req("alice"),
            "alice-source-update",
            task_routes.TaskUpdate(then_task_id="bob-target-update"),
        )

    assert exc.value.status_code == 404
    db = _TS()
    try:
        source = db.query(ScheduledTask).filter(ScheduledTask.id == "alice-source-update").first()
        assert source.then_task_id is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_update_task_allows_same_owner_chain_target():
    _seed_task("alice-source-allow", "alice")
    _seed_task("alice-target-allow", "alice")
    update_task = _endpoint("PUT", "/api/tasks/{task_id}")

    out = await update_task(
        _req("alice"),
        "alice-source-allow",
        task_routes.TaskUpdate(then_task_id="alice-target-allow"),
    )

    assert out["then_task_id"] == "alice-target-allow"


def test_scheduler_cycle_guard_treats_cross_owner_chain_as_unsafe():
    _seed_task("bob-target-cycle", "bob")
    from src.task_scheduler import TaskScheduler

    scheduler = TaskScheduler.__new__(TaskScheduler)
    db = _TS()
    try:
        assert scheduler._has_chain_cycle(db, "bob-target-cycle", owner="alice") is True
    finally:
        db.close()
