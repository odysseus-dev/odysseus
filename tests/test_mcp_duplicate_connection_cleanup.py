from unittest.mock import AsyncMock

import pytest

from src.mcp_manager import McpManager


@pytest.mark.asyncio
async def test_connecting_existing_server_id_closes_old_resources(monkeypatch):
    manager = McpManager()
    old_stack = AsyncMock()
    old_session = object()

    manager._stacks["same-id"] = old_stack
    manager._sessions["same-id"] = old_session
    manager._tools["same-id"] = [{"name": "old_tool"}]
    manager._connections["same-id"] = {
        "status": "connected",
        "name": "Old server",
        "transport": "stdio",
    }

    async def fake_connect_stdio(
        server_id,
        name,
        command,
        args,
        env,
    ):
        assert server_id == "same-id"
        assert "same-id" not in manager._stacks
        assert "same-id" not in manager._sessions
        assert "same-id" not in manager._tools
        return True

    monkeypatch.setattr(manager, "_connect_stdio", fake_connect_stdio)

    connected = await manager.connect_server(
        server_id="same-id",
        name="Replacement server",
        transport="stdio",
        command="synthetic-command",
        args=[],
        env={},
        url="",
    )

    assert connected is True
    old_stack.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_connection_does_not_call_disconnect(monkeypatch):
    manager = McpManager()
    disconnect = AsyncMock(wraps=manager.disconnect_server)
    monkeypatch.setattr(manager, "disconnect_server", disconnect)

    async def fake_connect_stdio(
        server_id,
        name,
        command,
        args,
        env,
    ):
        return True

    monkeypatch.setattr(manager, "_connect_stdio", fake_connect_stdio)

    connected = await manager.connect_server(
        server_id="new-id",
        name="New server",
        transport="stdio",
        command="synthetic-command",
        args=[],
        env={},
        url="",
    )

    assert connected is True
    disconnect.assert_not_awaited()
