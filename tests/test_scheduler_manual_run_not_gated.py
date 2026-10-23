"""A run the person asked for is foreground work, not something to gate.

Run now dispatches through _execute_task. If that run is gated on foreground
activity, the request that started it counts as activity and the run it just
started gets held or aborted. These tests pin the flag that decides it.
"""

import asyncio
import inspect

from src.task_scheduler import TaskScheduler


class _Recorder(TaskScheduler):
    """Captures what _execute_task_locked was asked to do."""

    def __init__(self):
        super().__init__(session_manager=None)
        self.calls = []

    def _task_needs_model_slot(self, task_id):
        return False

    async def _execute_task_locked(self, task_id, run_id, *, release_executing=True,
                                   gate_foreground=True):
        self.calls.append({"task_id": task_id, "gate_foreground": gate_foreground})


def test_execute_task_takes_a_manual_flag():
    assert "manual" in inspect.signature(TaskScheduler._execute_task).parameters


def test_a_manual_run_is_not_foreground_gated():
    scheduler = _Recorder()
    asyncio.run(scheduler._execute_task("t1", manual=True))
    assert scheduler.calls and scheduler.calls[0]["gate_foreground"] is False


def test_a_scheduled_run_is_still_foreground_gated():
    scheduler = _Recorder()
    asyncio.run(scheduler._execute_task("t2"))
    assert scheduler.calls and scheduler.calls[0]["gate_foreground"] is True


def test_run_task_now_dispatches_a_manual_run():
    source = inspect.getsource(TaskScheduler.run_task_now)
    assert "manual=True" in source
