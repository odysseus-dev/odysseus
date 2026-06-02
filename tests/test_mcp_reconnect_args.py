"""Verify that MCP reconnect via the agent tool passes full server metadata."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace


def test_reconnect_passes_full_server_config():
    """do_manage_mcp reconnect must pass name/transport/command/args/env/url."""
    from src.tool_implementations import do_manage_mcp

    fake_mcp = MagicMock()
    fake_mcp.disconnect_server = AsyncMock()
    fake_mcp.connect_server = AsyncMock(return_value=True)
    fake_mcp.get_server_status = MagicMock(return_value={"tool_count": 3})

    fake_srv = SimpleNamespace(
        id="srv-123",
        name="test-server",
        transport="stdio",
        command="/usr/bin/test",
        args=json.dumps(["--flag"]),
        env=json.dumps({"KEY": "val"}),
        url=None,
    )

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_srv

    with patch("src.tool_implementations.get_mcp_manager", return_value=fake_mcp), \
         patch("core.database.SessionLocal", return_value=fake_db):
        result = asyncio.run(do_manage_mcp(
            json.dumps({"action": "reconnect", "server_id": "srv-123"})
        ))

    assert result["exit_code"] == 0
    fake_mcp.connect_server.assert_called_once_with(
        server_id="srv-123",
        name="test-server",
        transport="stdio",
        command="/usr/bin/test",
        args=["--flag"],
        env={"KEY": "val"},
        url=None,
    )
