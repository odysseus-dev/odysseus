"""Regression tests for #3702: tasks with output_target='notification' must
also deliver through the configured reminder channel (email/ntfy/webhook),
not only the in-memory queue polled by an open browser tab."""
import asyncio
import types as _types

import routes.note_routes as note_routes
from src.task_scheduler import TaskScheduler


def _scheduler():
    return TaskScheduler.__new__(TaskScheduler)


def _task(output_target="notification", owner="alice", name="Summarize emails"):
    return _types.SimpleNamespace(
        id="task-1",
        name=name,
        output_target=output_target,
        owner=owner,
    )


def _run(status="success", result="3 new emails: ..."):
    return _types.SimpleNamespace(status=status, result=result)


def _capture_dispatch(monkeypatch, result=None):
    calls = []

    async def fake_dispatch_reminder(**kwargs):
        calls.append(kwargs)
        return result or {
            "channel": "webhook",
            "synthesis": None,
            "email_sent": False, "email_error": "",
            "ntfy_sent": False, "ntfy_error": "",
            "webhook_sent": True, "webhook_error": "",
            "browser_sent": False,
        }

    monkeypatch.setattr(note_routes, "dispatch_reminder", fake_dispatch_reminder)
    return calls


def test_notification_output_dispatches_via_reminder_channel(monkeypatch):
    calls = _capture_dispatch(monkeypatch)

    asyncio.run(_scheduler()._notify_via_reminder_channel(_task(), _run()))

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["title"] == "Summarize emails"
    assert kwargs["note_body"] == "3 new emails: ..."
    assert kwargs["owner"] == "alice"
    # The in-app queue is fed by add_notification; double-queueing here would
    # show two browser notifications for one task run.
    assert kwargs["queue_browser"] is False
    # Each run is a distinct result — must not be swallowed by the 25-min
    # reping dedupe cache keyed on note_id.
    assert kwargs["note_id"] == ""
    # The full task result is the deliverable, not a one-sentence summary.
    assert kwargs["settings_override"] == {"reminder_llm_synthesis": False}


def test_session_output_does_not_dispatch(monkeypatch):
    calls = _capture_dispatch(monkeypatch)

    asyncio.run(_scheduler()._notify_via_reminder_channel(
        _task(output_target="session"), _run()))

    assert calls == []


def test_error_run_does_not_dispatch(monkeypatch):
    calls = _capture_dispatch(monkeypatch)

    asyncio.run(_scheduler()._notify_via_reminder_channel(
        _task(), _run(status="error", result="boom")))

    assert calls == []


def test_empty_result_does_not_dispatch(monkeypatch):
    calls = _capture_dispatch(monkeypatch)

    asyncio.run(_scheduler()._notify_via_reminder_channel(
        _task(), _run(result="   ")))

    assert calls == []


def test_dispatch_failure_is_swallowed(monkeypatch):
    async def exploding_dispatch(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(note_routes, "dispatch_reminder", exploding_dispatch)

    # Must not raise — a broken external channel cannot fail the task run.
    result = asyncio.run(_scheduler()._notify_via_reminder_channel(_task(), _run()))
    assert result is None


def test_failed_external_send_logs_warning(monkeypatch, caplog):
    _capture_dispatch(monkeypatch, result={
        "channel": "webhook",
        "synthesis": None,
        "email_sent": False, "email_error": "",
        "ntfy_sent": False, "ntfy_error": "",
        "webhook_sent": False, "webhook_error": "Webhook returned HTTP 404",
        "browser_sent": False,
    })

    with caplog.at_level("WARNING"):
        asyncio.run(_scheduler()._notify_via_reminder_channel(_task(), _run()))

    assert any("Webhook returned HTTP 404" in r.message for r in caplog.records)
