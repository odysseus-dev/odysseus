import json
from types import SimpleNamespace

import pytest

from src.task_scheduler import TaskScheduler


@pytest.mark.asyncio
async def test_execute_action_extract_email_events_passes_calendar_href(monkeypatch):
    captured = {}

    async def _fake_action(**kwargs):
        captured.update(kwargs)
        return "ok", True

    import src.builtin_actions as builtin_actions

    monkeypatch.setattr(
        builtin_actions,
        "BUILTIN_ACTIONS",
        {"extract_email_events": _fake_action},
        raising=True,
    )

    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._set_run_progress = lambda run_id, message: None

    task = SimpleNamespace(
        id="task-1",
        owner="alice",
        name="Extract events",
        action="extract_email_events",
        prompt=json.dumps({"calendar_href": "cal://work"}),
    )

    result, ok = await scheduler._execute_action(task)

    assert ok is True
    assert result == "ok"
    assert captured["task_id"] == "task-1"
    assert captured["calendar_href"] == "cal://work"


@pytest.mark.asyncio
async def test_execute_action_classify_events_passes_calendar_name_fallback(monkeypatch):
    captured = {}

    async def _fake_action(**kwargs):
        captured.update(kwargs)
        return "ok", True

    import src.builtin_actions as builtin_actions

    monkeypatch.setattr(
        builtin_actions,
        "BUILTIN_ACTIONS",
        {"classify_events": _fake_action},
        raising=True,
    )

    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._set_run_progress = lambda run_id, message: None

    task = SimpleNamespace(
        id="task-2",
        owner="alice",
        name="Classify events",
        action="classify_events",
        prompt=json.dumps({"calendar": "Work"}),
    )

    result, ok = await scheduler._execute_action(task)

    assert ok is True
    assert result == "ok"
    assert captured["task_id"] == "task-2"
    assert captured["calendar"] == "Work"
