"""A run the person asked for is foreground work, not something to gate.

Run now dispatches through _execute_task. If that run is gated on foreground
activity, the request that started it counts as activity and the run it just
started gets held or aborted. Only the callers know whether a person asked,
so they pass manual=True; event triggers, webhooks and the agent's own tool
leave it off and stay gated.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.task_scheduler import TaskScheduler
from tests.test_task_cookbook_admin_gate import (  # noqa: F401  (fixtures)
    _req,
    _seed_action_task,
    configured_auth,
    task_db,
)


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


async def _run_now(scheduler, task_id, **kwargs):
    assert await scheduler.run_task_now(task_id, **kwargs)
    # run_task_now hands the run to a background task; let it finish.
    pending = asyncio.all_tasks() - {asyncio.current_task()}
    await asyncio.gather(*pending)
    return scheduler.calls


def test_a_manual_run_is_not_foreground_gated():
    calls = asyncio.run(_run_now(_Recorder(), "t1", manual=True))
    assert calls == [{"task_id": "t1", "gate_foreground": False}]


def test_run_task_now_stays_gated_unless_a_person_asked():
    calls = asyncio.run(_run_now(_Recorder(), "t2"))
    assert calls == [{"task_id": "t2", "gate_foreground": True}]


def test_a_scheduled_run_is_still_foreground_gated():
    scheduler = _Recorder()
    asyncio.run(scheduler._execute_task("t3"))
    assert scheduler.calls == [{"task_id": "t3", "gate_foreground": True}]


@pytest.mark.asyncio
async def test_the_run_button_asks_for_a_manual_run(task_db, configured_auth):
    _seed_action_task(task_db, "admin-task", "admin")
    scheduler = AsyncMock()
    scheduler.run_task_now.return_value = True
    run = _endpoint_for(scheduler, "POST", "/api/tasks/{task_id}/run")

    await run(_req("admin"), "admin-task")

    scheduler.run_task_now.assert_awaited_once_with("admin-task", force=False, manual=True)


@pytest.mark.asyncio
async def test_a_webhook_run_stays_gated(task_db, configured_auth):
    _seed_action_task(task_db, "admin-task", "admin", webhook_token="secret")
    scheduler = AsyncMock()
    scheduler.run_task_now.return_value = True
    webhook = _endpoint_for(scheduler, "POST", "/api/tasks/{task_id}/webhook/{token}")

    await webhook("admin-task", "secret")

    scheduler.run_task_now.assert_awaited_once_with("admin-task")


def _endpoint_for(scheduler, method, path):
    import routes.task_routes as task_routes

    router = task_routes.setup_task_routes(scheduler)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")
