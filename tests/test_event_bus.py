import asyncio
import gc
from unittest.mock import patch

from src import event_bus


def test_fire_event_retains_strong_reference_until_completion():
    async def runner():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = asyncio.Event()
        completed = False

        async def fake_handle_event(event_name, owner=None):
            nonlocal completed
            started.set()
            await release.wait()
            completed = True

        with patch.object(event_bus, "_handle_event", side_effect=fake_handle_event):
            event_bus._schedule_event_task(loop, "session_created", "alice")
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert len(event_bus._pending_event_tasks) == 1

            gc.collect()
            assert len(event_bus._pending_event_tasks) == 1

            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert completed is True
        assert len(event_bus._pending_event_tasks) == 0

    asyncio.run(runner())
