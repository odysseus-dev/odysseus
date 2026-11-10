"""Run the actual scheduler completion path with real task/run/notification rows."""
import asyncio
from datetime import datetime
from itertools import product

import pytest

import core.database as database
from src.task_scheduler import TaskScheduler
from tests.helpers.database import disposable_database


RESULT = 'Owned task result "quoted"\n' + "x" * 550
CASES = [(kind, target, enabled, "success", "sent")
         for kind, target, enabled in product(
             ["llm", "research", "action"], ["notification", "session", "none"], [True, False])]
CASES += [
    ("action", "notification", True, "error", "sent"),
    ("action", "notification", False, "error", "sent"),
    ("llm", "notification", True, "raise", "sent"),
    ("action", "notification", True, "skipped", "sent"),
    ("llm", "notification", True, "success", "failed"),
    ("action", "notification", True, "success", "raise"),
]


@pytest.mark.parametrize("kind,target,enabled,outcome,delivery", CASES)
def test_completion_routes_only_enabled_successful_notification_output(
    tmp_path, monkeypatch, kind, target, enabled, outcome, delivery,
):
    from routes import note_routes
    from src.builtin_actions import TaskNoop

    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        if delivery == "raise":
            raise RuntimeError("Owned notification transport failure")
        return {"channel": "webhook", "webhook_sent": delivery == "sent",
                "webhook_error": "Owned receiver rejection" if delivery == "failed" else ""}

    async def model_result(*args, **kwargs):
        if outcome == "raise":
            raise RuntimeError("Owned executor failure")
        return RESULT

    async def action_result(*args, **kwargs):
        if outcome == "skipped":
            raise TaskNoop("No work")
        return RESULT, outcome != "error"

    async def other_output(*args, **kwargs):
        pass

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(note_routes, "dispatch_reminder", dispatch)
        scheduler = TaskScheduler(None)
        monkeypatch.setattr(scheduler, "_execute_llm_task", model_result)
        monkeypatch.setattr(scheduler, "_execute_research_task", model_result)
        monkeypatch.setattr(scheduler, "_execute_action", action_result)
        monkeypatch.setattr(scheduler, "_deliver_task_result", other_output)
        monkeypatch.setattr(scheduler, "_log_to_assistant", lambda *args: None)
        with factory() as db:
            db.add(database.ScheduledTask(
                id="task", name="Task result", owner="alice", task_type=kind,
                action="daily_brief" if kind == "action" else None,
                prompt="Owned prompt", output_target=target, notifications_enabled=enabled,
                schedule="once", scheduled_date=datetime(2030, 1, 1), status="active",
            ))
            db.flush()
            db.add(database.TaskRun(id="run", task_id="task", status="queued"))
            db.commit()

        asyncio.run(scheduler._execute_task_locked("task", "run", gate_foreground=False))

        success = outcome == "success"
        should_send = success and target == "notification" and enabled
        assert len(calls) == int(should_send)
        if should_send:
            assert calls[0] == {
                "title": "Task result", "note_body": RESULT, "note_id": "", "owner": "alice",
                "queue_browser": False, "settings_override": {"reminder_llm_synthesis": False},
            }
        if outcome == "skipped":
            expected_queue = 0
        elif outcome == "error":
            expected_queue = 1  # Existing action-error alerts remain visible even when muted.
        elif outcome == "raise":
            expected_queue = int(enabled and kind in {"llm", "research"})
        else:
            expected_queue = int(enabled and (kind in {"llm", "research"} or target == "notification"))
        assert scheduler.pop_notifications(owner="bob") == []
        notifications = scheduler.pop_notifications(owner="alice")
        assert len(notifications) == expected_queue
        if notifications:
            assert notifications[0]["task_id"] == "task"
            if success:
                assert notifications[0]["body"] == (RESULT[:500] + "…" if target == "notification" else None)
        with factory() as db:
            run = db.get(database.TaskRun, "run")
            expected_status = "success" if success else "skipped" if outcome == "skipped" else "error"
            assert run.status == expected_status
            assert run.finished_at is not None
            assert db.query(database.NotificationLog).filter_by(owner="alice").count() == expected_queue
