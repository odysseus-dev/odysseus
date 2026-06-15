"""Fire-and-forget webhook tasks must be referenced until they finish.

asyncio keeps only a weak reference to a bare create_task() result, so a
delivery task could be garbage-collected before it ran and the webhook silently
dropped. WebhookManager now holds a strong reference for the task's lifetime and
releases it on completion.
"""
import asyncio
import sys
import types

from tests.helpers.import_state import clear_module, preserve_import_state

# Import the manager against a private database stub, then restore both modules
# so collection does not mutate shared import state.
with preserve_import_state("src.database", "src.webhook_manager"):
    clear_module("src.database")
    clear_module("src.webhook_manager")
    _db = types.ModuleType("src.database")
    _db.SessionLocal = object()
    _db.Webhook = type("Webhook", (), {})
    sys.modules["src.database"] = _db
    from src.webhook_manager import WebhookManager


def test_spawn_tracked_holds_then_releases_reference():
    async def run():
        wm = WebhookManager.__new__(WebhookManager)
        wm._bg_tasks = set()

        gate = asyncio.Event()

        async def work():
            await gate.wait()

        task = wm._spawn_tracked(work())
        # Referenced while in flight (this is what stops GC from collecting it).
        assert task in wm._bg_tasks
        gate.set()
        await task
        # Reference released once done, so the set does not grow unbounded.
        assert task not in wm._bg_tasks

    asyncio.run(run())


def test_spawn_tracked_runs_the_coroutine():
    async def run():
        wm = WebhookManager.__new__(WebhookManager)
        wm._bg_tasks = set()
        ran = []

        async def work():
            ran.append(True)

        await wm._spawn_tracked(work())
        assert ran == [True]

    asyncio.run(run())
