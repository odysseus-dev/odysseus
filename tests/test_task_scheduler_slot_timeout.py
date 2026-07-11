"""Regression tests for issue #5431: a hung model-backed task must not hold
the single scheduler slot forever.

- Fix A: `_execute_task` bounds each model-slot run with a wall-clock timeout so
  a wedged task auto-aborts and releases `_run_semaphore` instead of starving
  every other task ("Queued — waiting for a free slot…" forever).
- Fix B: deleting a task cancels its in-flight run (via `stop_task`) so the slot
  is freed immediately, matching the user's expectation that deleting the stuck
  task stops it.
"""

import asyncio
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool


# ---------------------------------------------------------------------------
# Fix A — hung task times out and frees the single model slot
# ---------------------------------------------------------------------------

def _setup_db(tmp_path, monkeypatch):
    import core.database as cd

    base = declarative_base()

    class ScheduledTask(base):
        __tablename__ = "scheduled_tasks"
        id = Column(String, primary_key=True)
        owner = Column(String)
        name = Column(String)
        task_type = Column(String, default="llm")
        action = Column(String)
        status = Column(String, default="active")

    class TaskRun(base):
        __tablename__ = "task_runs"
        id = Column(String, primary_key=True)
        task_id = Column(String)
        started_at = Column(DateTime)
        finished_at = Column(DateTime)
        status = Column(String)
        result = Column(Text)
        error = Column(Text)
        model = Column(String)

    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(cd, "SessionLocal", session_local)
    monkeypatch.setattr(cd, "ScheduledTask", ScheduledTask)
    monkeypatch.setattr(cd, "TaskRun", TaskRun)
    return session_local, ScheduledTask, TaskRun


def test_hung_task_times_out_and_frees_slot(tmp_path, monkeypatch):
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)

    db = session_local()
    db.add(ScheduledTask(id="hang", owner="alice", name="Hang", task_type="llm", status="active"))
    db.commit()
    db.close()

    # Tiny wall-clock cap so the test is fast.
    import src.settings as settings_mod
    monkeypatch.setattr(
        settings_mod, "get_setting",
        lambda key, default=None: 0.5 if key == "task_max_runtime_seconds" else default,
    )

    from src.task_scheduler import TaskScheduler

    async def drive():
        s = TaskScheduler.__new__(TaskScheduler)
        s._executing = set()
        s._executing_lock = asyncio.Lock()
        s._run_semaphore = asyncio.Semaphore(1)
        s._task_handles = {}
        s._concurrency_cap = 1
        s._task_defer_counts = {}
        # Force the model-slot path and isolate the timeout behaviour.
        s._task_needs_model_slot = lambda task_id: True
        s._defer_immediately_due_task = lambda *a, **k: None

        started = asyncio.Event()

        async def hang(task_id, run_id, **kwargs):
            started.set()
            await asyncio.Event().wait()  # never completes on its own

        s._execute_task_locked = hang

        task = asyncio.create_task(s._execute_task("hang"))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert s._run_semaphore.locked(), "slot should be held while the task runs"

        # Without the fix the task hangs forever holding the slot; with it the
        # ~0.5s cap aborts the run and releases the semaphore.
        await asyncio.wait_for(task, timeout=3)
        assert task.done()
        assert not s._run_semaphore.locked(), "slot must be freed after the runtime cap"

    asyncio.run(drive())

    db = session_local()
    try:
        run = db.query(TaskRun).filter(TaskRun.task_id == "hang").first()
        assert run is not None
        assert run.status == "aborted"
        assert run.finished_at is not None
    finally:
        db.close()


def test_suppressing_task_times_out_and_frees_slot(tmp_path, monkeypatch):
    """Production path: the real `_execute_task_locked` catches the timeout
    cancellation, marks its run aborted, and *returns* (suppresses). The slot
    must still be freed even though `wait_for` then returns normally."""
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)

    db = session_local()
    db.add(ScheduledTask(id="hang2", owner="alice", name="Hang2", task_type="llm", status="active"))
    db.commit()
    db.close()

    import src.settings as settings_mod
    monkeypatch.setattr(
        settings_mod, "get_setting",
        lambda key, default=None: 0.5 if key == "task_max_runtime_seconds" else default,
    )

    from datetime import datetime
    from src.task_scheduler import TaskScheduler

    async def drive():
        s = TaskScheduler.__new__(TaskScheduler)
        s._executing = set()
        s._executing_lock = asyncio.Lock()
        s._run_semaphore = asyncio.Semaphore(1)
        s._task_handles = {}
        s._concurrency_cap = 1
        s._task_defer_counts = {}
        s._task_needs_model_slot = lambda task_id: True
        s._defer_immediately_due_task = lambda *a, **k: None

        started = asyncio.Event()

        async def hang_then_suppress(task_id, run_id, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Mirror the real inner handler: mark the run aborted and return
                # instead of re-raising.
                import core.database as cd
                d = cd.SessionLocal()
                try:
                    r = d.query(cd.TaskRun).filter(cd.TaskRun.id == run_id).first()
                    if r:
                        r.status = "aborted"
                        r.error = "Stopped by user"
                        r.finished_at = datetime.utcnow()
                        d.commit()
                finally:
                    d.close()
                return

        s._execute_task_locked = hang_then_suppress

        task = asyncio.create_task(s._execute_task("hang2"))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert s._run_semaphore.locked()

        await asyncio.wait_for(task, timeout=3)
        assert task.done()
        assert not s._run_semaphore.locked(), "slot must be freed even when the inner run suppresses the cancel"

    asyncio.run(drive())

    db = session_local()
    try:
        run = db.query(TaskRun).filter(TaskRun.task_id == "hang2").first()
        assert run is not None and run.status == "aborted"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fix B — deleting a task cancels its in-flight run (frees the slot at once)
# ---------------------------------------------------------------------------

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)


def _route_db():
    from tests.helpers.import_state import clear_fake_database_modules
    clear_fake_database_modules()
    import core.database as cdb
    import routes.task_routes as task_routes
    cdb.Base.metadata.create_all(_ENGINE)
    ts = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
    task_routes.SessionLocal = ts
    return task_routes, cdb, ts


def _delete_endpoint(task_routes, scheduler):
    router = task_routes.setup_task_routes(scheduler)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/tasks/{task_id}" and "DELETE" in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError("DELETE /api/tasks/{task_id} not found")


@pytest.mark.asyncio
async def test_delete_task_cancels_inflight_run():
    task_routes, cdb, ts = _route_db()

    db = ts()
    try:
        db.add(cdb.ScheduledTask(
            id="del-task", owner="alice", name="del-task",
            prompt="do work", task_type="llm",
            status="active", output_target="session",
        ))
        db.commit()
    finally:
        db.close()

    scheduler = MagicMock()
    scheduler.stop_task = AsyncMock(return_value=True)

    delete_task = _delete_endpoint(task_routes, scheduler)
    req = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    resp = await delete_task(req, "del-task")

    assert resp == {"ok": True}
    # The core of Fix B: delete must cancel any in-flight run so a hung task's
    # slot is released instead of staying wedged after the row is gone.
    scheduler.stop_task.assert_awaited_once_with("del-task")

    db = ts()
    try:
        assert db.query(cdb.ScheduledTask).filter(cdb.ScheduledTask.id == "del-task").first() is None
    finally:
        db.close()
