import asyncio
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, DateTime, String, Text, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker


@pytest.fixture
def scheduler_db(tmp_path, monkeypatch):
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
    try:
        yield session_local, ScheduledTask, TaskRun
    finally:
        engine.dispose()


@pytest.fixture
def hold_write_lock(scheduler_db, tmp_path, request):
    session_local, _, _ = scheduler_db
    engine = session_local.kw['bind']

    @contextmanager
    def hold(lock_mode):
        loop = asyncio.get_running_loop()
        loop_thread = threading.current_thread()
        collisions = asyncio.Queue()
        released = threading.Event()

        def observe_write(cursor, statement, parameters, context):
            if not context.isupdate or released.is_set():
                return

            # Probe the actual ORM write with a zero busy timeout so SQLite
            # can report the collision before the loop releases the lock.
            with closing(cursor.connection.cursor()) as pragma:
                busy_timeout = pragma.execute('PRAGMA busy_timeout').fetchone()[0]
                pragma.execute('PRAGMA busy_timeout=0')
                try:
                    cursor.execute(statement, parameters)
                except sqlite3.OperationalError as exc:
                    if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                        raise
                else:
                    return True
                finally:
                    pragma.execute(f'PRAGMA busy_timeout={busy_timeout}')

            if threading.current_thread() is loop_thread:
                failure = 'contended SQLite write ran on the event loop'
            else:
                loop.call_soon_threadsafe(collisions.put_nowait, None)
                if released.wait(timeout=10):
                    # Let SQLAlchemy retry with the original busy timeout.
                    return
                failure = 'timed out waiting for the event loop to release the SQLite write lock'

            # pytest.fail bypasses the scheduler's Exception handlers.
            # Close the cursor before SQLAlchemy invalidates its connection.
            cursor.close()
            pytest.fail(failure)

        event.listen(engine, 'do_execute', observe_write)
        request.addfinalizer(lambda: event.remove(engine, 'do_execute', observe_write))
        try:
            with closing(sqlite3.connect(tmp_path / 'tasks.db')) as holder:
                assert holder.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
                holder.execute(f'BEGIN {lock_mode}')
                assert holder.in_transaction
                yield collisions
        finally:
            # Closing the holder rolls back its transaction before writers retry.
            released.set()

    return hold


def test_stop_task_cleans_up_queued_handle_and_run(scheduler_db):
    session_local, ScheduledTask, TaskRun = scheduler_db

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
def test_cancel_keeps_event_loop_responsive_during_database_lock(scheduler_db, hold_write_lock, mode, lock_mode):
    session_local, ScheduledTask, TaskRun = scheduler_db
    queued = mode.startswith('queued')
    with session_local() as db:
        if queued:
            db.add(ScheduledTask(id='locked-task', owner='alice', name='Fixture',
                task_type='llm', status='active',
                next_run=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)))
        else:
            db.add(TaskRun(id='locked-run', task_id='locked-task', status='running'))
        db.commit()
    from src.task_scheduler import TaskScheduler

    async def drive():
        scheduler = TaskScheduler(None)
        scheduler._executing.add('locked-task')
        if queued:
            await scheduler._run_semaphore.acquire()
        try:
            async with asyncio.TaskGroup() as tasks:
                if queued:
                    pending = tasks.create_task(scheduler._execute_task('locked-task'))
                    await asyncio.sleep(0)
                    assert scheduler._task_handles['locked-task'] is pending

                with hold_write_lock(lock_mode) as collisions:
                    if mode == 'foreground':
                        stop = tasks.create_task(scheduler.stop_background_tasks_for_foreground())
                    else:
                        stop = tasks.create_task(scheduler.stop_task('locked-task'))
                    await collisions.get()
                    if queued:
                        # Both stop_task and the cancelled handle persist the abort.
                        await collisions.get()
                    if mode == 'queued_repeat':
                        repeat = tasks.create_task(scheduler.stop_task('locked-task'))
                        await collisions.get()
                        assert pending.cancelling() == 1

                if mode == 'foreground':
                    assert await stop == 1
                else:
                    assert await stop is True
                if mode == 'queued_repeat':
                    assert await repeat is True
                if queued:
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                    assert 'locked-task' not in scheduler._task_handles
                if mode != 'foreground':
                    assert 'locked-task' not in scheduler._executing
        finally:
            if queued:
                scheduler._run_semaphore.release()

    asyncio.run(asyncio.wait_for(drive(), timeout=15))
    with session_local() as db:
        run = db.query(TaskRun).filter_by(task_id='locked-task').one()
        assert run.status == 'aborted'
        assert run.error == 'Stopped by user'
        assert run.finished_at is not None
        if queued:
            task = db.get(ScheduledTask, 'locked-task')
            assert task.status == 'active'
            assert task.next_run > datetime.now(timezone.utc).replace(tzinfo=None)


def test_running_task_cancel_keeps_event_loop_responsive_during_database_lock(
    scheduler_db, hold_write_lock, monkeypatch,
):
    session_local, ScheduledTask, TaskRun = scheduler_db
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
    async def drive():
        scheduler = TaskScheduler(None)
        assert await scheduler.run_task_now('running-task')
        await asyncio.wait_for(started.wait(), timeout=2)
        pending = scheduler._task_handles['running-task']

        async def wait_for_run():
            await pending

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(wait_for_run())
            with hold_write_lock('EXCLUSIVE') as collisions:
                stop = tasks.create_task(scheduler.stop_task('running-task'))
                # Both stop_task and the running-task cleanup persist changes.
                await collisions.get()
                await collisions.get()
            assert await stop is True
        assert 'running-task' not in scheduler._task_handles
        assert 'running-task' not in scheduler._executing

    asyncio.run(asyncio.wait_for(drive(), timeout=15))
    with session_local() as db:
        run = db.query(TaskRun).filter_by(task_id='running-task').one()
        assert run.status == 'aborted'
        assert run.error == 'Stopped by user'
        assert run.finished_at is not None
        task = db.get(ScheduledTask, 'running-task')
        assert task.status == 'active'
        assert task.last_run is not None
        assert task.next_run > datetime.now(timezone.utc).replace(tzinfo=None)
