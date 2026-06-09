"""Validator + regression test for FINDING 6.2 — restart double-fires overdue
scheduled tasks.

Demonstrates the bug: TaskScheduler.start() aborts stale TaskRun rows but never
advances ScheduledTask.next_run, so the in-memory _executing guard resets
across a restart and _check_due_tasks will re-dispatch any task whose
next_run is still in the past.

After the fix (start() advances overdue next_run to now + 60s), the regression
test asserts the opposite: the task fires at most once across two consecutive
polls.
"""
import sys, types, asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base


def _test_utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stub_heavy():
    for name in [
        "src.builtin_actions", "src.ai_interaction", "src.endpoint_resolver",
        "src.agent_loop", "src.session_manager",
    ]:
        sys.modules.setdefault(name, types.ModuleType(name))


def _setup_isolated_db():
    import core.database as cd
    B = declarative_base()

    class ScheduledTask(B):
        __tablename__ = "scheduled_tasks"
        id = Column(String, primary_key=True)
        owner = Column(String)
        name = Column(String, default="t")
        prompt = Column(Text)
        task_type = Column(String, default="llm")
        next_run = Column(DateTime, index=True)
        last_run = Column(DateTime)
        status = Column(String, default="active")
        run_count = Column(Integer, default=0)

    class TaskRun(B):
        __tablename__ = "task_runs"
        id = Column(String, primary_key=True)
        task_id = Column(String)
        started_at = Column(DateTime)
        finished_at = Column(DateTime)
        status = Column(String, default="queued")
        error = Column(Text)

    eng = create_engine("sqlite:///:memory:")
    B.metadata.create_all(eng)
    cd.engine = eng
    cd.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    cd.ScheduledTask = ScheduledTask
    cd.TaskRun = TaskRun
    return cd, ScheduledTask, TaskRun


def test_scheduler_utcnow_preserves_naive_utc_contract():
    from src.task_scheduler import _utcnow

    now = _utcnow()

    assert now.tzinfo is None
    assert abs((now - _test_utcnow()).total_seconds()) < 2


def _drive_scheduler(monkeypatch, pre_start_setup=None):
    """Build a TaskScheduler bypassing __init__ and run start() + two polls."""
    _stub_heavy()
    cd, ScheduledTask, TaskRun = _setup_isolated_db()

    from src.task_scheduler import TaskScheduler
    sch = TaskScheduler.__new__(TaskScheduler)
    sch._executing = set()
    sch._executing_lock = asyncio.Lock()
    sch._concurrency_cap = 1
    sch._run_semaphore = asyncio.Semaphore(1)
    sch._running = True
    sch._task = None
    sch._note_pings_task = None
    sch._known_task_owners = lambda: []
    sch._task_defer_counts = {}

    if pre_start_setup:
        pre_start_setup(cd, ScheduledTask, TaskRun)

    async def _never():
        await asyncio.sleep(3600)
    monkeypatch.setattr(sch, "_loop", _never)
    monkeypatch.setattr(sch, "_note_pings_loop", _never)

    dispatched = []
    def _fake_create_task(coro):
        dispatched.append(coro)
        class _T:
            def cancel(self): pass
        return _T()
    monkeypatch.setattr("src.task_scheduler.asyncio.create_task", _fake_create_task)

    async def _drive():
        await sch.start()
        await sch._check_due_tasks()
        await sch._check_due_tasks()
        return dispatched

    all_dispatched = asyncio.run(_drive())
    # start() also fires the long-lived _loop and _note_pings_loop as tasks
    # (stubbed to _never here); filter those out so the test only counts
    # real per-poll task dispatches.
    real_dispatches = [c for c in all_dispatched if c.__name__ != "_never"]
    return cd, ScheduledTask, TaskRun, real_dispatches


def test_restart_does_not_re_dispatch_overdue_task(monkeypatch):
    """After restart, an overdue active task should fire at most once across
    two consecutive polls (the first poll re-fires it, but next_run is then
    advanced so the second poll does not)."""
    def _setup(cd, ScheduledTask, TaskRun):
        db = cd.SessionLocal()
        db.add(ScheduledTask(
            id="t_due_1", owner="alice", name="overdue",
            task_type="llm",
            next_run=_test_utcnow() - timedelta(hours=1),
            status="active",
        ))
        db.commit()
        db.close()

    cd, ScheduledTask, TaskRun, dispatched = _drive_scheduler(monkeypatch, _setup)

    db = cd.SessionLocal()
    t = db.query(ScheduledTask).filter(ScheduledTask.id == "t_due_1").first()
    db.close()
    assert t.next_run >= _test_utcnow() - timedelta(seconds=1), (
        f"After start(), next_run should have been pushed into the future; "
        f"got {t.next_run}"
    )
    assert len(dispatched) <= 1, (
        f"Expected at most 1 dispatch across two polls; got {len(dispatched)}. "
        "The startup next_run advance is not preventing the second poll from "
        "re-firing the same overdue task."
    )


def test_startup_does_not_advance_fresh_tasks(monkeypatch):
    """Tasks whose next_run is in the future must be untouched by the startup
    sweep — only overdue ones get pushed forward."""
    future = _test_utcnow() + timedelta(hours=2)
    def _setup(cd, ScheduledTask, TaskRun):
        db = cd.SessionLocal()
        db.add(ScheduledTask(
            id="t_fresh", owner="alice", name="fresh",
            task_type="llm", next_run=future, status="active",
        ))
        db.commit()
        db.close()

    cd, ScheduledTask, TaskRun, dispatched = _drive_scheduler(monkeypatch, _setup)

    db = cd.SessionLocal()
    t = db.query(ScheduledTask).filter(ScheduledTask.id == "t_fresh").first()
    db.close()
    assert t.next_run == future, (
        f"Fresh task's next_run was modified: expected {future}, got {t.next_run}"
    )
    assert len(dispatched) == 0


def test_startup_does_not_advance_paused_tasks(monkeypatch):
    """A paused task with an old next_run is not overdue for execution —
    it should not be advanced by the startup sweep."""
    def _setup(cd, ScheduledTask, TaskRun):
        db = cd.SessionLocal()
        db.add(ScheduledTask(
            id="t_paused", owner="alice", name="paused",
            task_type="llm",
            next_run=_test_utcnow() - timedelta(hours=1),
            status="paused",
        ))
        db.commit()
        db.close()

    cd, ScheduledTask, TaskRun, dispatched = _drive_scheduler(monkeypatch, _setup)

    db = cd.SessionLocal()
    t = db.query(ScheduledTask).filter(ScheduledTask.id == "t_paused").first()
    db.close()
    # The stored next_run should still be ~1h in the past (the startup sweep
    # only advances active overdue tasks; a paused task with an old next_run
    # is left alone). Allow a small delta to absorb the time the sweep took.
    one_hour_ago = _test_utcnow() - timedelta(hours=1)
    assert abs((t.next_run - one_hour_ago).total_seconds()) < 5, (
        f"Paused task's next_run was modified: "
        f"expected ~{one_hour_ago}, got {t.next_run}"
    )
