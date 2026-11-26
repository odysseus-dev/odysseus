"""Regression tests for the admin-only direct MCP call route.

The route exists so an operator can call the local PowerShell remote-console
tools when the model tool loop is unavailable. It must stay narrow: one server,
an explicit tool allowlist, and normal admin plus disabled-tool checks.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from routes.mcp import mcp_routes


class _FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class _FakeServer:
    id = "powershell01"
    is_enabled = True


class _FakeQuery:
    def __init__(self, server):
        self.server = server

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.server


class _FakeSession:
    def __init__(self, server):
        self.server = server

    def query(self, *args, **kwargs):
        return _FakeQuery(self.server)

    def close(self):
        pass


def _call_endpoint(monkeypatch, *, server=None):
    """Register the route with a fake manager and return its raw endpoint."""
    monkeypatch.setattr(mcp_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: _FakeSession(server or _FakeServer()))
    monkeypatch.setattr(mcp_routes, "_load_disabled_map", lambda: {})
    manager = MagicMock()
    manager.get_server_status = MagicMock(return_value={"status": "connected"})
    manager.get_all_tools = MagicMock(return_value=[
        {"server_id": "powershell01", "name": "accept_remote_console"},
    ])
    manager.call_tool = AsyncMock(return_value={
        "stdout": "pairing output",
        "stderr": "",
        "exit_code": 0,
    })
    router = mcp_routes.setup_mcp_routes(manager)
    route = [r for r in router.routes if getattr(r, "name", None) == "call_server_tool"][-1]
    return route.endpoint, manager


def test_call_route_allows_powershell_remote_console_tool(monkeypatch):
    endpoint, manager = _call_endpoint(monkeypatch)
    result = asyncio.run(endpoint(
        server_id="powershell01",
        request=_FakeRequest({
            "tool": "accept_remote_console",
            "arguments": {"port": 43345, "install": "none"},
        }),
    ))

    assert result == {"stdout": "pairing output", "stderr": "", "exit_code": 0}
    manager.call_tool.assert_awaited_once_with(
        "mcp__powershell01__accept_remote_console",
        {"port": 43345, "install": "none"},
    )


def test_call_route_rejects_other_server(monkeypatch):
    endpoint, manager = _call_endpoint(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(
            server_id="ghmcp01",
            request=_FakeRequest({"tool": "accept_remote_console", "arguments": {}}),
        ))
    assert exc.value.status_code == 404
    manager.call_tool.assert_not_awaited()


def test_call_route_rejects_tool_outside_allowlist(monkeypatch):
    endpoint, manager = _call_endpoint(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(
            server_id="powershell01",
            request=_FakeRequest({"tool": "execute_command", "arguments": {}}),
        ))
    assert exc.value.status_code == 400
    manager.call_tool.assert_not_awaited()


def test_call_route_rejects_disabled_tool(monkeypatch):
    endpoint, manager = _call_endpoint(monkeypatch)
    monkeypatch.setattr(
        mcp_routes, "_load_disabled_map",
        lambda: {"powershell01": {"accept_remote_console"}},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(
            server_id="powershell01",
            request=_FakeRequest({"tool": "accept_remote_console", "arguments": {}}),
        ))
    assert exc.value.status_code == 403
    manager.call_tool.assert_not_awaited()
