from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_async_fallback_reports_only_successful_model(monkeypatch):
    from src import llm_core

    attempted = []

    async def fake_call(url, model, messages, **kwargs):
        attempted.append(model)
        if model == "primary":
            raise RuntimeError("primary unavailable")
        return "fallback response"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    used = []

    with llm_core.capture_model_usage(used.append):
        result = await llm_core.llm_call_async_with_fallback(
            [
                ("http://primary/v1", "primary", {}),
                ("http://fallback/v1", "fallback", {}),
            ],
            messages=[{"role": "user", "content": "hello"}],
        )

    assert result == "fallback response"
    assert attempted == ["primary", "fallback"]
    assert used == ["fallback"]


def test_sync_primary_success_reports_model(monkeypatch):
    from src import llm_core

    monkeypatch.setattr(llm_core, "llm_call", lambda *args, **kwargs: "ok")
    used = []

    with llm_core.capture_model_usage(used.append):
        result = llm_core.llm_call_with_fallback(
            [("http://primary/v1", "primary", {})],
            messages=[{"role": "user", "content": "hello"}],
        )

    assert result == "ok"
    assert used == ["primary"]


@pytest.mark.asyncio
async def test_builtin_action_records_actual_fallback_model(monkeypatch):
    from src import builtin_actions, llm_core
    from src.task_scheduler import TaskScheduler

    async def fake_call(url, model, messages, **kwargs):
        if model == "primary":
            raise RuntimeError("primary unavailable")
        return "summary"

    async def model_backed_action(**kwargs):
        result = await llm_core.llm_call_async_with_fallback(
            [
                ("http://primary/v1", "primary", {}),
                ("http://fallback/v1", "fallback", {}),
            ],
            messages=[{"role": "user", "content": "summarize"}],
        )
        return result, True

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    monkeypatch.setitem(
        builtin_actions.BUILTIN_ACTIONS,
        "test_model_usage",
        model_backed_action,
    )

    scheduler = TaskScheduler(session_manager=None)
    scheduler._last_run_model = None
    task = SimpleNamespace(
        action="test_model_usage",
        owner="alice",
        name="Model-backed action",
        prompt=None,
    )

    result, success = await scheduler._execute_action(task)

    assert success is True
    assert result == "summary"
    assert scheduler._last_run_model == "fallback"


@pytest.mark.asyncio
async def test_non_model_action_leaves_usage_empty(monkeypatch):
    from src import builtin_actions
    from src.task_scheduler import TaskScheduler

    async def housekeeping_action(**kwargs):
        return "clean", True

    monkeypatch.setitem(
        builtin_actions.BUILTIN_ACTIONS,
        "test_housekeeping",
        housekeeping_action,
    )

    scheduler = TaskScheduler(session_manager=None)
    scheduler._last_run_model = None
    task = SimpleNamespace(
        action="test_housekeeping",
        owner="alice",
        name="Housekeeping",
        prompt=None,
    )

    result, success = await scheduler._execute_action(task)

    assert success is True
    assert result == "clean"
    assert scheduler._last_run_model is None
