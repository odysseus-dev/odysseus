import asyncio
import sqlite3
import threading
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, String, Text, create_engine
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
        prompt = Column(Text, default='')
        request_authority_json = Column(Text)
        trigger_type = Column(String, default='schedule')
        schedule = Column(String, default='daily')
        scheduled_time = Column(String, default='08:00')
        scheduled_day = Column(String)
        scheduled_date = Column(DateTime)
        cron_expression = Column(String)

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


def test_stop_task_cleans_up_queued_handle_and_run(tmp_path, monkeypatch):
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)

    db = session_local()
    db.add(ScheduledTask(
        id="queued-task",
        owner="alice",
        name="Queued Task",
        task_type="llm",
        status="active",
    ))
    db.commit()
    db.close()

    from src.task_scheduler import TaskScheduler

    async def drive():
        scheduler = TaskScheduler.__new__(TaskScheduler)
        scheduler._executing = {"queued-task"}
        scheduler._executing_lock = asyncio.Lock()
        scheduler._run_semaphore = asyncio.Semaphore(1)
        scheduler._task_handles = {}
        scheduler._concurrency_cap = 1
        scheduler._task_defer_counts = {}
        await scheduler._run_semaphore.acquire()

        task = asyncio.create_task(scheduler._execute_task("queued-task"))
        try:
            for _ in range(50):
                if "queued-task" in scheduler._task_handles:
                    db2 = session_local()
                    try:
                        run = db2.query(TaskRun).filter(TaskRun.task_id == "queued-task").first()
                        if run:
                            break
                    finally:
                        db2.close()
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("queued run was not created")

            assert await scheduler.stop_task("queued-task") is True
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            scheduler._run_semaphore.release()

        assert "queued-task" not in scheduler._task_handles
        assert "queued-task" not in scheduler._executing

    asyncio.run(drive())

    db = session_local()
    try:
        run = db.query(TaskRun).filter(TaskRun.task_id == "queued-task").first()
        assert run.status == "aborted"
        assert run.error == "Stopped by user"
        assert run.finished_at is not None
        assert run.finished_at >= run.started_at
    finally:
        db.close()


@pytest.mark.parametrize('mode', ['foreground', 'single', 'queued', 'queued_repeat'])
@pytest.mark.parametrize('lock_mode', ['EXCLUSIVE', 'IMMEDIATE'])
def test_cancel_keeps_event_loop_responsive_during_database_lock(tmp_path, monkeypatch, mode, lock_mode):
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)
    with session_local() as db:
        if mode.startswith('queued'):
            db.add(ScheduledTask(id='locked-task', owner='alice', name='Fixture',
                task_type='llm', status='active',
                next_run=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)))
        else:
            db.add(TaskRun(id='locked-run', task_id='locked-task', status='running'))
        db.commit()
    from src.task_scheduler import TaskScheduler

    holder = sqlite3.connect(tmp_path / 'tasks.db', check_same_thread=False)
    assert holder.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    heartbeat = threading.Event()
    progress_before_release = []
    def release_lock():
        progress_before_release.append(heartbeat.is_set())
        holder.commit()
    release = threading.Timer(0.25, release_lock)

    async def drive():
        scheduler = TaskScheduler(None)
        scheduler._executing.add('locked-task')
        pending = None
        if mode.startswith('queued'):
            await scheduler._run_semaphore.acquire()
            pending = asyncio.create_task(scheduler._execute_task('locked-task'))
            await asyncio.sleep(0)
            assert 'locked-task' in scheduler._task_handles
        holder.execute(f'BEGIN {lock_mode}')
        asyncio.get_running_loop().call_later(0.02, heartbeat.set)
        repeated_stops = []
        if mode == 'queued_repeat':
            asyncio.get_running_loop().call_later(0.04, lambda: repeated_stops.append(
                asyncio.create_task(scheduler.stop_task('locked-task'))))
        release.start()
        if mode == 'foreground':
            assert await scheduler.stop_background_tasks_for_foreground() == 1
        else:
            assert await scheduler.stop_task('locked-task') is True
        if pending:
            with pytest.raises(asyncio.CancelledError):
                await pending
            scheduler._run_semaphore.release()
        if repeated_stops:
            await asyncio.gather(*repeated_stops)

    try:
        asyncio.run(drive())
    finally:
        release.join(timeout=2)
        holder.close()
    assert progress_before_release == [True], 'foreground event loop froze behind SQLite'
    with session_local() as db:
        assert db.query(TaskRun).filter_by(task_id='locked-task').one().status == 'aborted'
        if mode.startswith('queued'):
            task = db.get(ScheduledTask, 'locked-task')
            assert task.status == 'active'
            assert task.next_run > datetime.now(timezone.utc).replace(tzinfo=None)


def test_running_task_cancel_keeps_event_loop_responsive_during_database_lock(tmp_path, monkeypatch):
    session_local, ScheduledTask, TaskRun = _setup_db(tmp_path, monkeypatch)
    from src.task_scheduler import TaskScheduler
    from src.builtin_actions import BUILTIN_ACTIONS
    monkeypatch.setenv('BACKGROUND_TASK_FOREGROUND_GATE', 'false')
    from src.agent_runtime.authority import seal_task_authority
    with session_local() as db:
        db.add(ScheduledTask(id='running-task', owner='alice', name='Fixture action',
            task_type='action', action='fixture_wait', status='active',
            request_authority_json=seal_task_authority('', 'action', 'fixture_wait', owner='alice')))
        db.commit()
    started = asyncio.Event()
    async def external_action(**kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setitem(BUILTIN_ACTIONS, 'fixture_wait', external_action)
    holder = sqlite3.connect(tmp_path / 'tasks.db', check_same_thread=False)
    heartbeat = threading.Event()
    progress_before_release = []
    def release_lock():
        progress_before_release.append(heartbeat.is_set())
        holder.commit()
    release = threading.Timer(0.25, release_lock)

    async def drive():
        scheduler = TaskScheduler(None)
        assert await scheduler.run_task_now('running-task')
        await asyncio.wait_for(started.wait(), timeout=2)
        pending = scheduler._task_handles['running-task']
        holder.execute('BEGIN EXCLUSIVE')
        asyncio.get_running_loop().call_later(0.02, heartbeat.set)
        release.start()
        assert await scheduler.stop_task('running-task')
        await pending
    try:
        asyncio.run(drive())
    finally:
        if release.ident is not None:
            release.join(timeout=2)
        holder.close()
    assert progress_before_release == [True], 'running-task cleanup blocked foreground'
    with session_local() as db:
        assert db.query(TaskRun).filter_by(task_id='running-task').one().status == 'aborted'
        task = db.get(ScheduledTask, 'running-task')
        assert task.status == 'active'
        assert task.next_run > datetime.now(timezone.utc).replace(tzinfo=None)
