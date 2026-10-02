"""A manually triggered task must survive the foreground activity gate.

Clicking "Run now" means the user is, by definition, in the browser. Treating
that run as background work made every manual run either wait forever on
`wait_for_interactive_quiet` or get cancelled by the next heartbeat, so the
Activity list filled up with `aborted / "Stopped by user"` rows.
"""

import asyncio

import pytest


def _bare_scheduler():
    from src.task_scheduler import TaskScheduler

    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._executing = set()
    scheduler._executing_lock = asyncio.Lock()
    scheduler._run_semaphore = asyncio.Semaphore(1)
    scheduler._task_handles = {}
    scheduler._concurrency_cap = 1
    scheduler._task_defer_counts = {}
    return scheduler


def test_manual_run_dispatches_as_user_initiated():
    async def drive():
        scheduler = _bare_scheduler()
        calls = []

        async def fake_execute(task_id, **kwargs):
            calls.append((task_id, kwargs))

        scheduler._execute_task = fake_execute
        assert await scheduler.run_task_now("manual-task") is True
        await asyncio.sleep(0)
        return calls

    calls = asyncio.run(drive())
    assert len(calls) == 1
    task_id, kwargs = calls[0]
    assert task_id == "manual-task"
    assert kwargs.get("user_initiated") is True
    # Still serialized behind the model semaphore — only the foreground gate
    # is lifted, so a manual run cannot fight an active chat for the GPU.
    assert kwargs.get("bypass_model_slot") is not True


def test_force_run_dispatches_as_user_initiated():
    async def drive():
        scheduler = _bare_scheduler()
        calls = []

        async def fake_execute(task_id, **kwargs):
            calls.append((task_id, kwargs))

        scheduler._execute_task = fake_execute
        assert await scheduler.run_task_now("forced-task", force=True) is True
        await asyncio.sleep(0)
        return calls

    calls = asyncio.run(drive())
    assert len(calls) == 1
    assert calls[0][1].get("user_initiated") is True
    assert calls[0][1].get("bypass_model_slot") is True


def test_foreground_stop_spares_user_initiated_runs():
    async def drive():
        scheduler = _bare_scheduler()
        scheduler._mark_run_aborted = lambda *a, **k: False

        async def _sleep():
            await asyncio.sleep(60)

        manual = asyncio.create_task(_sleep())
        scheduled = asyncio.create_task(_sleep())
        scheduler._executing = {"manual-task", "scheduled-task"}
        scheduler._task_handles = {"manual-task": manual, "scheduled-task": scheduled}
        scheduler._mark_user_initiated("manual-task")

        stopped = await scheduler.stop_background_tasks_for_foreground(reason="browser heartbeat")
        await asyncio.sleep(0)

        result = (stopped, manual.cancelled(), scheduled.cancelled(), set(scheduler._executing))
        manual.cancel()
        for handle in (manual, scheduled):
            with pytest.raises(asyncio.CancelledError):
                await handle
        return result

    stopped, manual_cancelled, scheduled_cancelled, still_executing = asyncio.run(drive())
    assert manual_cancelled is False
    assert scheduled_cancelled is True
    assert stopped == 1
    # The manual run keeps its slot so the scheduler still sees it in flight.
    assert "manual-task" in still_executing


def test_manual_run_marker_is_cleared_after_completion():
    async def drive():
        scheduler = _bare_scheduler()
        scheduler._mark_user_initiated("manual-task")
        assert scheduler._is_user_initiated("manual-task") is True
        scheduler._clear_user_initiated("manual-task")
        return scheduler._is_user_initiated("manual-task")

    assert asyncio.run(drive()) is False


def test_overlapping_manual_runs_keep_the_marker_until_the_last_finishes():
    scheduler = _bare_scheduler()
    scheduler._mark_user_initiated("manual-task")
    scheduler._mark_user_initiated("manual-task")
    scheduler._clear_user_initiated("manual-task")
    assert scheduler._is_user_initiated("manual-task") is True
    scheduler._clear_user_initiated("manual-task")
    assert scheduler._is_user_initiated("manual-task") is False
