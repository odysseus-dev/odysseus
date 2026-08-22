"""Regression tests for issue #1789: a user-added MCP server whose session has
died (e.g. the asyncio task that created its stdio session has exited) must be
auto-reconnected on the next tool call, the way builtin servers already are.

call_tool used to gate the reconnect on is_builtin(), so user-added servers
kept failing every call until an app restart even though the integration page
showed them as connected.
"""
import asyncio
from types import SimpleNamespace

from src.mcp_manager import McpManager


class _DeadSession:
    """Session whose transport has died — every call raises."""

    async def call_tool(self, tool_name, arguments):
        raise RuntimeError("session closed (stdio task exited)")


class _LiveSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text="pong")], isError=False)


def _register_user_server(mgr, server_id="usersrv"):
    """Populate internals as connect_server would after a successful connect."""
    mgr._sessions[server_id] = _DeadSession()
    mgr._connections[server_id] = {"status": "connected", "name": server_id}
    mgr._connect_params[server_id] = {
        "name": server_id,
        "transport": "stdio",
        "command": "some-cmd",
        "args": [],
        "env": {},
        "url": None,
    }


def test_user_server_reconnects_and_retries():
    mgr = McpManager()
    _register_user_server(mgr)
    live = _LiveSession()

    async def fake_connect(server_id, **params):
        mgr._sessions[server_id] = live
        mgr._connections[server_id] = {"status": "connected", "name": params.get("name")}
        return True

    mgr.connect_server = fake_connect  # type: ignore[method-assign]

    result = asyncio.run(mgr.call_tool("mcp__usersrv__ping", {"x": 1}))
    assert result.get("exit_code") == 0
    assert result.get("stdout") == "pong"
    assert live.calls == [("ping", {"x": 1})]


def test_reconnect_uses_stored_connect_params():
    mgr = McpManager()
    _register_user_server(mgr)
    seen = {}

    async def fake_connect(server_id, **params):
        seen.update(params, server_id=server_id)
        mgr._sessions[server_id] = _LiveSession()
        return True

    mgr.connect_server = fake_connect  # type: ignore[method-assign]

    asyncio.run(mgr.call_tool("mcp__usersrv__ping", {}))
    assert seen["server_id"] == "usersrv"
    assert seen["transport"] == "stdio"
    assert seen["command"] == "some-cmd"


def test_missing_params_fails_gracefully():
    mgr = McpManager()
    # Session present but no stored connect params (never went through
    # connect_server) — must return an error dict, not crash.
    mgr._sessions["usersrv"] = _DeadSession()
    mgr._connections["usersrv"] = {"status": "connected", "name": "usersrv"}

    result = asyncio.run(mgr.call_tool("mcp__usersrv__ping", {}))
    assert result.get("exit_code") == 1
    assert "reconnect failed" in result.get("error", "")


def test_builtin_path_unchanged():
    mgr = McpManager()
    mgr._sessions["builtin_x"] = _DeadSession()
    called = {}

    async def fake_reconnect_builtin(server_id):
        called["id"] = server_id
        mgr._sessions[server_id] = _LiveSession()
        return True

    mgr._reconnect_builtin = fake_reconnect_builtin  # type: ignore[method-assign]

    result = asyncio.run(mgr.call_tool("mcp__builtin_x__ping", {}))
    assert called["id"] == "builtin_x"
    assert result.get("exit_code") == 0
