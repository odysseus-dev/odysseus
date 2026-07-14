"""Regression for #5536: browser heartbeats abort scheduled tasks even when
BACKGROUND_TASK_FOREGROUND_GATE is disabled.

`/api/activity/heartbeat` unconditionally calls
`stop_background_tasks_for_foreground()`, and that method never consults the
gate — so setting BACKGROUND_TASK_FOREGROUND_GATE=false silences
`mark_browser_activity()` / the interactive middleware but heartbeats still
force-cancel every executing task. Additionally, runs cancelled this way never
set the in-task `foreground_cancel["hit"]` flag, so Activity records the
misleading "Stopped by user" instead of "Paused because Odysseus became
active" and the task misses the 15-minute foreground defer.

These tests drive the real `TaskScheduler` (no HTTP layer): the executing
task is a genuine asyncio task registered in `_task_handles`, exactly how the
heartbeat-triggered stop sees it in production.
"""
import asyncio

import pytest


def _make_scheduler():
    from src.task_scheduler import TaskScheduler
    return TaskScheduler(session_manager=None)


async def _register_hanging_task(scheduler, task_id):
    """Register a real, cancellable asyncio task as an executing scheduler job."""
    started = asyncio.Event()

    async def _hang():
        started.set()
        await asyncio.sleep(3600)

    handle = asyncio.create_task(_hang())
    async with scheduler._executing_lock:
        scheduler._executing.add(task_id)
    scheduler._task_handles[task_id] = handle
    await started.wait()
    return handle


def test_stop_is_noop_when_gate_disabled(monkeypatch):
    monkeypatch.setenv("BACKGROUND_TASK_FOREGROUND_GATE", "false")
    scheduler = _make_scheduler()
    aborted = []
    monkeypatch.setattr(
        scheduler, "_mark_run_aborted",
        lambda *a, **k: aborted.append((a, k)) or False,
    )

    async def _scenario():
        handle = await _register_hanging_task(scheduler, "task-gate-off")
        stopped = await scheduler.stop_background_tasks_for_foreground(reason="browser heartbeat")
        # Give a cancelled handle the chance to actually die before asserting.
        await asyncio.sleep(0)
        alive = not handle.done()
        handle.cancel()
        return stopped, alive

    stopped, alive = asyncio.run(_scenario())
    assert stopped == 0, (
        "stop_background_tasks_for_foreground must be a no-op when "
        "BACKGROUND_TASK_FOREGROUND_GATE is disabled"
    )
    assert alive, "executing task must not be cancelled while the gate is disabled"
    assert not aborted, "no run may be marked aborted while the gate is disabled"


def test_stop_cancels_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("BACKGROUND_TASK_FOREGROUND_GATE", "true")
    scheduler = _make_scheduler()
    monkeypatch.setattr(scheduler, "_mark_run_aborted", lambda *a, **k: True)

    async def _scenario():
        handle = await _register_hanging_task(scheduler, "task-gate-on")
        stopped = await scheduler.stop_background_tasks_for_foreground(reason="browser heartbeat")
        await asyncio.sleep(0)
        return stopped, handle.cancelled() or handle.done()

    stopped, cancelled = asyncio.run(_scenario())
    assert stopped > 0
    assert cancelled, "with the gate enabled the executing task must be cancelled"


def test_heartbeat_stop_records_foreground_pause_not_user_stop(monkeypatch):
    """Full path through _execute_task_locked: a heartbeat-triggered stop must
    record the foreground-pause message and the 15-minute defer, not the
    misleading 'Stopped by user' + regular reschedule."""
    monkeypatch.setenv("BACKGROUND_TASK_FOREGROUND_GATE", "true")
    from core.database import SessionLocal, ScheduledTask, TaskRun
    from src.task_scheduler import TaskScheduler, _utcnow

    task_id = "task-5536-fg-pause"
    run_id = "run-5536-fg-pause"
    db = SessionLocal()
    db.add(ScheduledTask(
        id=task_id,
        owner="alice",
        name="hang forever",
        task_type="action",
        action="test_hang_5536",
        status="active",
        trigger_type="manual",
    ))
    db.add(TaskRun(id=run_id, task_id=task_id, status="queued", started_at=_utcnow()))
    db.commit()
    db.close()

    scheduler = TaskScheduler(session_manager=None)
    entered = asyncio.Event()

    async def _hanging_action(task, run_id=None):
        entered.set()
        await asyncio.sleep(3600)
        return "unreachable", True

    monkeypatch.setattr(scheduler, "_execute_action", _hanging_action)

    async def _scenario():
        async with scheduler._executing_lock:
            scheduler._executing.add(task_id)
        handle = asyncio.create_task(
            scheduler._execute_task_locked(
                task_id, run_id, release_executing=False, gate_foreground=True,
            )
        )
        scheduler._task_handles[task_id] = handle
        await asyncio.wait_for(entered.wait(), timeout=10)
        await scheduler.stop_background_tasks_for_foreground(reason="browser heartbeat")
        try:
            await asyncio.wait_for(handle, timeout=10)
        except asyncio.CancelledError:
            pass

    asyncio.run(_scenario())

    db = SessionLocal()
    try:
        run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        assert run.status == "aborted"
        assert run.error == "Paused because Odysseus became active", (
            f"heartbeat-triggered cancel recorded {run.error!r} — this is a "
            "foreground pause, not a user stop"
        )
        assert task.next_run is not None, (
            "a foreground pause must defer the task (15 min), not drop next_run"
        )
        delta = (task.next_run - _utcnow()).total_seconds()
        assert 13 * 60 < delta <= 16 * 60, f"expected ~15min defer, got {delta}s"
    finally:
        db.close()


def test_queued_cancel_does_not_leak_foreground_stop_flag(monkeypatch):
    """A heartbeat stop that lands while the task is still queued behind the
    run semaphore never reaches _execute_task_locked's CancelledError handler.
    The _foreground_stops entry must still be cleaned up, or a later genuine
    user stop of the same task gets misrecorded as a foreground pause and
    silently rescheduled +15 min."""
    monkeypatch.setenv("BACKGROUND_TASK_FOREGROUND_GATE", "true")
    from core.database import SessionLocal, ScheduledTask
    from src.task_scheduler import TaskScheduler

    task_id = "task-5536-queued-leak"
    db = SessionLocal()
    db.add(ScheduledTask(
        id=task_id,
        owner="alice",
        name="queued victim",
        task_type="llm",
        prompt="hang",
        status="active",
        trigger_type="manual",
    ))
    db.commit()
    db.close()

    scheduler = TaskScheduler(session_manager=None)

    async def _scenario():
        # Hold the single run slot so the task parks on the semaphore.
        await scheduler._run_semaphore.acquire()
        try:
            async with scheduler._executing_lock:
                scheduler._executing.add(task_id)
            qtask = asyncio.create_task(
                scheduler._execute_task(task_id, release_executing=False)
            )
            for _ in range(200):
                if scheduler._task_handles.get(task_id) is qtask:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("queued task never registered its handle")
            await asyncio.sleep(0.05)  # let it park on the semaphore

            stopped = await scheduler.stop_background_tasks_for_foreground(
                reason="browser heartbeat"
            )
            assert stopped > 0
            with pytest.raises(asyncio.CancelledError):
                await qtask
        finally:
            scheduler._run_semaphore.release()

    asyncio.run(_scenario())
    assert task_id not in scheduler._foreground_stops, (
        "queued-cancel path leaked the foreground-stop flag; the next user "
        "stop of this task would be misclassified as a foreground pause"
    )
