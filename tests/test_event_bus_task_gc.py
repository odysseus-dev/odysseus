"""Regression test for event_bus fire-and-forget task GC bug.

Verifies that fire_event() holds a strong reference to the created task
so that Python's GC cannot collect it before _handle_event completes.

Mirrors the pattern tested in test_token_cache_atomic_swap.py — exercises
the real fire_event() and _BG_TASKS, mocking only the DB-dependent internals.
"""
import asyncio
import gc
import sys
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def event_bus_module(monkeypatch):
    """Import event_bus with mocked _handle_event to avoid real DB access."""
    monkeypatch.delitem(sys.modules, "src.event_bus", raising=False)

    import src.event_bus as mod  # noqa: E402

    # Replace _handle_event with an async mock so fire_event runs without DB
    mock_handler = AsyncMock()
    mod._handle_event = mock_handler
    return mod, mock_handler


@pytest.mark.asyncio
async def test_task_held_by_bg_tasks_during_execution(event_bus_module):
    """_BG_TASKS must contain the task while _handle_event is running."""
    mod, mock_handler = event_bus_module

    # Use an Event to pause the handler so we can inspect _BG_TASKS mid-flight
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_handler(*args, **kwargs):
        started.set()
        await proceed.wait()

    mod._handle_event = slow_handler

    mod.fire_event("test_event", owner="alice")

    # Give the event loop a chance to start the task
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # The task should be in _BG_TASKS while it's still running
    assert len(mod._BG_TASKS) == 1, (
        f"Expected 1 task in _BG_TASKS during execution, got {len(mod._BG_TASKS)}"
    )

    # Let the handler finish
    proceed.set()
    # Yield to let the task complete and the done callback fire
    await asyncio.sleep(0.05)

    # After completion, the task should be removed from _BG_TASKS
    assert len(mod._BG_TASKS) == 0, (
        f"Expected 0 tasks in _BG_TASKS after completion, got {len(mod._BG_TASKS)}"
    )


@pytest.mark.asyncio
async def test_handler_actually_called(event_bus_module):
    """fire_event must invoke _handle_event with correct arguments."""
    mod, mock_handler = event_bus_module

    mod.fire_event("message_sent", owner="bob")
    await asyncio.sleep(0.05)

    mock_handler.assert_awaited_once_with("message_sent", "bob")


@pytest.mark.asyncio
async def test_multiple_events_all_tasks_tracked(event_bus_module):
    """Multiple concurrent fire_event calls must all be tracked."""
    mod, _ = event_bus_module

    barrier = asyncio.Event()

    async def blocking_handler(*args, **kwargs):
        await barrier.wait()

    mod._handle_event = blocking_handler

    # Fire 5 events
    for i in range(5):
        mod.fire_event(f"event_{i}", owner=f"user_{i}")

    # Give tasks time to start
    await asyncio.sleep(0.05)

    assert len(mod._BG_TASKS) == 5, (
        f"Expected 5 tasks in _BG_TASKS, got {len(mod._BG_TASKS)}"
    )

    # Release all tasks
    barrier.set()
    await asyncio.sleep(0.05)

    assert len(mod._BG_TASKS) == 0, (
        f"Expected 0 tasks after completion, got {len(mod._BG_TASKS)}"
    )


@pytest.mark.asyncio
async def test_gc_cannot_collect_running_task(event_bus_module):
    """Forced GC while handler is running must not lose the task."""
    mod, _ = event_bus_module

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_handler(*args, **kwargs):
        started.set()
        await proceed.wait()

    mod._handle_event = slow_handler

    mod.fire_event("gc_test", owner="eve")
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # Force garbage collection — the task must survive because _BG_TASKS holds it
    collected_before = gc.collect()
    gc.collect()
    gc.collect()

    # Task should still be tracked
    assert len(mod._BG_TASKS) == 1

    proceed.set()
    await asyncio.sleep(0.05)

    assert len(mod._BG_TASKS) == 0
