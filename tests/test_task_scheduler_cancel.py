import asyncio

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
