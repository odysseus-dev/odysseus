import asyncio

import pytest

from src.mcp_manager import McpManager


@pytest.mark.asyncio
async def test_http_disconnect_cancels_and_awaits_pending_connection():
    manager = McpManager()
    started = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def pending_http_connect():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleanup_finished.set()

    task = asyncio.create_task(pending_http_connect())
    await started.wait()

    manager._connect_tasks["http-id"] = task
    manager._connections["http-id"] = {
        "status": "connecting",
        "name": "HTTP",
        "transport": "http",
    }

    await manager.disconnect_server("http-id")

    assert cleanup_finished.is_set()
    assert task.done()
    assert "http-id" not in manager._connect_tasks
    assert "http-id" not in manager._connections


@pytest.mark.asyncio
async def test_completed_http_task_is_removed_from_tracking(monkeypatch):
    manager = McpManager()

    async def completed_connect(server_id, name, url):
        return True

    monkeypatch.setattr(manager, "_connect_http", completed_connect)

    result = await manager._start_http_connect(
        "http-id",
        "HTTP server",
        "https://example.invalid/mcp",
    )

    assert result is True
    assert "http-id" not in manager._connect_tasks


@pytest.mark.asyncio
async def test_failed_http_task_is_removed_from_tracking(monkeypatch):
    manager = McpManager()

    async def failed_connect(server_id, name, url):
        raise RuntimeError("synthetic HTTP connection failure")

    monkeypatch.setattr(manager, "_connect_http", failed_connect)

    result = await manager._start_http_connect(
        "http-id",
        "HTTP server",
        "https://example.invalid/mcp",
    )

    # Immediate failures are converted into status=False by the public helper.
    assert result is False
    assert manager._connections["http-id"]["status"] == "error"
    assert "http-id" not in manager._connect_tasks


@pytest.mark.asyncio
async def test_background_http_task_remains_tracked_until_completion(
    monkeypatch,
):
    manager = McpManager()
    release = asyncio.Event()

    async def delayed_connect(server_id, name, url):
        await release.wait()
        return True

    monkeypatch.setattr(manager, "_connect_http", delayed_connect)

    result = await manager._start_http_connect(
        "http-id",
        "HTTP server",
        "https://example.invalid/mcp",
        wait=0,
    )

    assert result is False
    task = manager._connect_tasks["http-id"]
    assert not task.done()

    release.set()
    await task
    await asyncio.sleep(0)

    assert "http-id" not in manager._connect_tasks
