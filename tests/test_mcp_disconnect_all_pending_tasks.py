import asyncio

import pytest

from src.mcp_manager import McpManager


@pytest.mark.asyncio
async def test_disconnect_all_cancels_pending_task_without_session():
    manager = McpManager()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_connect():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(pending_connect())
    await started.wait()

    manager._connect_tasks["pending-http"] = task
    manager._connections["pending-http"] = {
        "status": "connecting",
        "name": "Pending HTTP",
        "transport": "http",
    }

    await manager.disconnect_all()
    await asyncio.wait_for(cancelled.wait(), timeout=1)

    assert task.cancelled() or task.done()
    assert "pending-http" not in manager._connect_tasks
    assert "pending-http" not in manager._connections


@pytest.mark.asyncio
async def test_disconnect_server_waits_for_cancelled_connect_task():
    manager = McpManager()
    cleanup_finished = asyncio.Event()

    async def pending_connect():
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleanup_finished.set()

    task = asyncio.create_task(pending_connect())
    await asyncio.sleep(0)

    manager._connect_tasks["pending-http"] = task

    await manager.disconnect_server("pending-http")

    assert cleanup_finished.is_set()
    assert task.done()
