"""Run-now on a paused task: it runs once, and its schedule is left alone.

A paused task is how a user turns recurring execution off without deleting the
task. "Run now" is an explicit one-off, so it has to execute anyway, and the
scheduler tick must keep skipping the same task while its status stays paused.
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


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
        next_run = Column(DateTime)
        last_run = Column(DateTime)
        prompt = Column(Text, default="")
        request_authority_json = Column(Text)
        trigger_type = Column(String, default="schedule")
        schedule = Column(String, default="daily")
        scheduled_time = Column(String, default="08:00")
        scheduled_day = Column(String)
        scheduled_date = Column(DateTime)
        cron_expression = Column(String)
        run_count = Column(Integer, default=0)
        output_target = Column(String, default="session")
        notifications_enabled = Column(Boolean, default=True)
        then_task_id = Column(String)
        model = Column(String)
        endpoint_url = Column(String)
        crew_member_id = Column(String)
        max_steps = Column(Integer)

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


def _make_scheduler():
    from src.task_scheduler import TaskScheduler

    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._executing = set()
    scheduler._executing_lock = asyncio.Lock()
    scheduler._run_semaphore = asyncio.Semaphore(1)
    scheduler._task_handles = {}
    scheduler._concurrency_cap = 1
    scheduler._task_defer_counts = {}
    scheduler._last_run_model = None
    return scheduler


def _seed_task(session_local, ScheduledTask, *, status="paused"):
    next_run = datetime.utcnow() + timedelta(days=2)
    db = session_local()
    try:
        db.add(
            ScheduledTask(
                id="paused-task",
                owner="alice",
                name="Paused Task",
                task_type="action",
                action="noop",
                status=status,
                next_run=next_run,
                prompt="",
                trigger_type="schedule",
                schedule="daily",
                scheduled_time="08:00",
                run_count=0,
            )
        )
        db.commit()
    finally:
        db.close()
    return next_run


def _patch_action(scheduler, monkeypatch, calls):
    async def _fake_action(self, task, run_id=None, **_kwargs):
        calls.append(task.id)
        return "did the thing", True

    async def _fake_deliver(self, *_args, **_kwargs):
        return None

    monkeypatch.setattr(type(scheduler), "_execute_action", _fake_action)
    monkeypatch.setattr(type(scheduler), "_deliver_task_result", _fake_deliver)


def test_run_now_executes_a_paused_task_once_and_keeps_its_schedule(
    tmp_path, monkeypatch
):
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)
    next_run = _seed_task(session_local, ScheduledTask)

    scheduler = _make_scheduler()
    calls = []
    _patch_action(scheduler, monkeypatch, calls)

    async def drive():
        await scheduler._execute_task(
            "paused-task", bypass_model_slot=True, manual=True
        )

    asyncio.run(drive())

    db = session_local()
    try:
        run = db.query(TaskRun).filter(TaskRun.task_id == "paused-task").one()
        task = db.query(ScheduledTask).filter(ScheduledTask.id == "paused-task").one()
        assert calls == ["paused-task"]
        assert run.status == "success"
        assert run.result == "did the thing"
        assert run.finished_at is not None
        # The run happened, but pausing still means pausing: the user's next
        # scheduled time and the paused state are untouched.
        assert task.status == "paused"
        assert task.next_run == next_run
    finally:
        db.close()


def test_run_now_marks_the_task_manual_for_the_executor():
    scheduler = _make_scheduler()
    seen = {}

    async def _fake_execute(task_id, **kwargs):
        seen["task_id"] = task_id
        seen.update(kwargs)

    scheduler._execute_task = _fake_execute

    async def drive():
        assert await scheduler.run_task_now("paused-task", force=True) is True

    asyncio.run(drive())
    assert seen["task_id"] == "paused-task"
    assert seen.get("manual") is True


def test_a_scheduled_dispatch_still_skips_a_paused_task(tmp_path, monkeypatch):
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)
    _seed_task(session_local, ScheduledTask)

    scheduler = _make_scheduler()
    calls = []
    _patch_action(scheduler, monkeypatch, calls)

    async def drive():
        await scheduler._execute_task("paused-task", bypass_model_slot=True)

    asyncio.run(drive())

    db = session_local()
    try:
        run = db.query(TaskRun).filter(TaskRun.task_id == "paused-task").one()
        assert calls == []
        assert run.status == "skipped"
        assert "no longer active" in (run.error or "")
    finally:
        db.close()
