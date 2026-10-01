"""App-level foreground stops (heartbeat / UI requests) must defer a running
scheduled task, not record it as a user stop that skips to the next slot."""
import asyncio

from src.task_scheduler import TaskScheduler


class _Handle:
    cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


def test_foreground_stop_tags_task_for_deferral(monkeypatch):
    sched = TaskScheduler(session_manager=None)
    handle = _Handle()
    sched._executing = {"t1"}
    sched._task_handles = {"t1": handle}
    messages = []
    monkeypatch.setattr(sched, "_mark_run_aborted", lambda task_id, message="": messages.append(message) or True)

    stopped = asyncio.run(sched.stop_background_tasks_for_foreground(reason="heartbeat"))

    assert stopped == 2 and handle.cancelled
    assert sched._foreground_stopped() == {"t1"}
    assert messages == ["Paused because Odysseus became active"]
