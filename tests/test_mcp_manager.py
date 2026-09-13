import asyncio
from unittest.mock import patch

from src.mcp_manager import (
    _describe_exception,
    _format_mcp_connection_error,
    _is_dead_transport_error,
    McpManager,
)


class _ClosedResourceError(Exception):
    """Stand-in for anyio's ClosedResourceError: no message, matched by name."""


_ClosedResourceError.__name__ = "ClosedResourceError"


def test_describe_exception_falls_back_to_type_name_when_message_is_empty():
    # Regression: several MCP-adjacent exceptions (anyio's
    # ClosedResourceError/BrokenResourceError when a stdio subprocess's pipe
    # closes underneath it, among others) carry no message -- str(e) is "".
    # `str(error) if error else "Unknown error"` only catches error being
    # None/falsy; an Exception instance is always truthy even with an empty
    # str(), so that produced e.g. "MCP tool call failed: mcp__x__y: " with
    # nothing after the colon -- undiagnosable from logs alone.
    class _NoMessageError(Exception):
        pass

    desc = _describe_exception(_NoMessageError())
    assert desc != ""
    assert "_NoMessageError" in desc


def test_describe_exception_preserves_real_message():
    assert _describe_exception(RuntimeError("boom")) == "boom"


def test_describe_exception_handles_none():
    assert _describe_exception(None) == "Unknown error"


def test_connection_error_with_empty_exception_message_is_not_blank():
    msg = _format_mcp_connection_error(
        "Custom MCP", "python", ["server.py"], ConnectionError(),
    )

    assert msg.strip() != ""
    assert "ConnectionError" in msg


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Odysseus" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_todoist_connection_error_explains_cli_wrapper():
    msg = _format_mcp_connection_error(
        "Todoist",
        "td",
        ["today", "--json"],
        RuntimeError("Connection closed"),
    )

    assert "Connection closed" in msg
    assert "td" in msg
    assert "not an MCP server" in msg
    assert "mcp_servers/todoist_server.py" in msg


def test_dead_transport_errors_are_distinguished_from_tool_errors():
    assert _is_dead_transport_error(_ClosedResourceError())
    assert _is_dead_transport_error(BrokenPipeError())
    assert not _is_dead_transport_error(RuntimeError("tool said no"))


def _connected(server_id: str, name: str, tool_names) -> McpManager:
    mgr = McpManager()
    mgr._tools = {
        server_id: [
            {"name": n, "description": f"{n} tool.", "input_schema": {}}
            for n in tool_names
        ]
    }
    mgr._connections = {server_id: {"status": "connected", "name": name, "identity": ""}}
    return mgr


def test_gated_tool_names_excludes_user_added_external_servers():
    # Regression for the Penpot MCP incident: a user-added server (any id not
    # in the hardcoded embedded-catalog set) must not be gated behind RAG/
    # intent tool selection -- it's a handful of tools the user explicitly
    # connected, not a large ambient catalog like the browser/GitHub ones.
    mgr = _connected("penpot", "Penpot", ["execute_code", "get_page", "list_boards"])
    assert mgr.gated_tool_names() == set()


def test_gated_tool_names_includes_large_embedded_catalogs():
    # The browser (Playwright, ~30 tools) and similar embedded catalogs stay
    # gated so a single semantic match doesn't flood a small model's schema
    # list with every tool in that catalog.
    mgr = _connected("builtin_browser", "Browser", ["click", "navigate", "screenshot"])
    assert mgr.gated_tool_names() == {
        "mcp__builtin_browser__click",
        "mcp__builtin_browser__navigate",
        "mcp__builtin_browser__screenshot",
    }
    assert not _is_dead_transport_error(None)


def _connected_stdio_manager(server_id="ntfy1"):
    mgr = McpManager()
    mgr._sessions[server_id] = object()
    mgr._connections[server_id] = {"status": "connected", "name": "ntfy"}
    mgr._configs[server_id] = {
        "server_id": server_id,
        "name": "ntfy",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "ntfy-me-mcp"],
        "env": {},
        "url": None,
    }
    return mgr


def test_user_added_stdio_server_reconnects_after_its_subprocess_dies():
    # Regression: reconnect used to be gated on is_builtin(), so a user-added
    # stdio server whose subprocess had exited stayed dead until Odysseus
    # restarted -- every call failing in ~1ms with a messageless anyio error.
    mgr = _connected_stdio_manager()
    calls = []

    async def fake_do_call(session, tool_name, arguments):
        calls.append(session)
        if len(calls) == 1:
            raise _ClosedResourceError()
        return {"stdout": "sent", "stderr": "", "exit_code": 0}

    async def fake_connect(**config):
        mgr._sessions[config["server_id"]] = object()
        return True

    with patch.object(McpManager, "_do_call", side_effect=fake_do_call), \
         patch.object(McpManager, "disconnect_server", side_effect=_noop), \
         patch.object(McpManager, "connect_server", side_effect=fake_connect):
        result = asyncio.run(mgr.call_tool("mcp__ntfy1__notify_me", {"message": "hi"}))

    assert result["exit_code"] == 0
    assert result["stdout"] == "sent"
    assert len(calls) == 2


async def _noop(*args, **kwargs):
    return None


def test_ordinary_tool_exception_is_not_retried_on_a_user_added_server():
    # A tool that actually ran and raised must not be replayed: the first attempt
    # may already have had a side effect (e.g. a notification was delivered).
    mgr = _connected_stdio_manager()
    calls = []

    async def fake_do_call(session, tool_name, arguments):
        calls.append(session)
        raise RuntimeError("topic not found")

    with patch.object(McpManager, "_do_call", side_effect=fake_do_call):
        result = asyncio.run(mgr.call_tool("mcp__ntfy1__notify_me", {"message": "hi"}))

    assert result["exit_code"] == 1
    assert "topic not found" in result["error"]
    assert len(calls) == 1


def test_dead_transport_error_reported_to_the_agent_is_actionable():
    # The whole symptom was exit_code=1 with an empty error message. Whatever
    # else changes, the agent must get the server name and its command back.
    mgr = _connected_stdio_manager()

    async def fake_do_call(session, tool_name, arguments):
        raise _ClosedResourceError()

    async def failed_reconnect(server_id):
        return False

    with patch.object(McpManager, "_do_call", side_effect=fake_do_call), \
         patch.object(McpManager, "_reconnect_server", side_effect=failed_reconnect):
        result = asyncio.run(mgr.call_tool("mcp__ntfy1__notify_me", {"message": "hi"}))

    assert result["exit_code"] == 1
    error = result["error"]
    assert error.strip() != ""
    assert "ntfy" in error
    assert "npx -y ntfy-me-mcp" in error
    assert "not running" in error


def test_stderr_capture_tail_is_quoted_in_the_failure_message():
    mgr = _connected_stdio_manager()
    handle = mgr._open_stderr_log("ntfy1")
    assert handle is not None
    handle.write("Error: NTFY_TOPIC is required\n")
    handle.flush()

    message = mgr._describe_call_failure("ntfy1", _ClosedResourceError())
    assert "NTFY_TOPIC is required" in message

    # Cleanup removes the capture file.
    path = mgr._stderr_logs["ntfy1"]["path"]
    mgr._close_stderr_log("ntfy1")
    import os

    assert not os.path.exists(path)


def test_http_server_is_not_auto_reconnected_mid_call():
    # Restarting an HTTP/OAuth server inside a tool call could re-enter the
    # browser authorization flow, so it must be left alone.
    mgr = _connected_stdio_manager()
    mgr._configs["ntfy1"]["transport"] = "http"

    with patch.object(McpManager, "connect_server", side_effect=_noop) as connect:
        reconnected = asyncio.run(mgr._reconnect_server("ntfy1"))

    assert reconnected is False
    connect.assert_not_called()


class _FakeStack:
    """Stand-in for the AsyncExitStack that owns a server's transport."""

    def __init__(self, closed, delay=0.0):
        self._closed = closed
        self._delay = delay
        self.is_closed = False

    async def aclose(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.is_closed = True
        self._closed.append(self)


def _stub_stdio_connect(mgr, created, closed, connect_delay=0.01, close_delay=0.0):
    """A _connect_stdio replacement that publishes state like the real one."""

    async def fake_connect_stdio(server_id, name, command, args, env):
        await asyncio.sleep(connect_delay)  # subprocess spawn + MCP handshake
        stack = _FakeStack(closed, close_delay)
        created.append(stack)
        mgr._sessions[server_id] = object()
        mgr._stacks[server_id] = stack
        mgr._tools[server_id] = [
            {"name": "notify_me", "description": "send a push", "input_schema": {}}
        ]
        mgr._connections[server_id] = {
            "status": "connected",
            "name": name,
            "transport": "stdio",
            "tool_count": 1,
        }
        return True

    return fake_connect_stdio


def _restart_kwargs(server_id="ntfy1"):
    return {
        "server_id": server_id,
        "name": "ntfy",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "ntfy-me-mcp"],
        "env": {},
        "url": None,
    }


async def test_concurrent_reconnects_do_not_corrupt_server_state():
    # Regression: the Reconnect button fanned out into several overlapping
    # POST /api/mcp/servers/{id}/reconnect calls, and each ran its own
    # unsynchronized disconnect+connect. A disconnect that resumed after a
    # newer connect had published state popped _sessions/_tools/_connections
    # for a live connection, so the UI showed the server as disconnected with
    # 0 tools while the agent could still call its tools.
    mgr = _connected_stdio_manager()
    created, closed = [], []
    original = _FakeStack(closed, delay=0.01)
    mgr._stacks["ntfy1"] = original

    with patch.object(
        McpManager, "_connect_stdio", side_effect=_stub_stdio_connect(mgr, created, closed)
    ):
        results = await asyncio.gather(
            *[mgr.restart_server(**_restart_kwargs()) for _ in range(4)]
        )

    assert results == [True, True, True, True]
    conn = mgr._connections.get("ntfy1", {})
    assert conn.get("status") == "connected"
    assert conn.get("tool_count") == 1
    assert "ntfy1" in mgr._sessions
    assert mgr._tools.get("ntfy1")
    assert mgr._configs.get("ntfy1")

    # Concurrent requests join the in-flight restart instead of each spawning
    # its own subprocess and orphaning the previous one's exit stack (the
    # orphans were what produced the detached "Attempted to exit cancel scope
    # in a different task" errors).
    assert len(created) == 1
    assert original.is_closed
    assert mgr._stacks["ntfy1"] is created[0]
    assert not created[0].is_closed


async def test_slow_disconnect_cannot_wipe_a_newer_connects_state():
    # disconnect_server() awaits stack.aclose() *between* popping _stacks and
    # popping _sessions/_tools/_connections. That await is a suspension point,
    # so without the per-server lock a connect completing during it had its
    # state wiped by the disconnect on the way out.
    mgr = _connected_stdio_manager()
    created, closed = [], []
    release = asyncio.Event()

    class _BlockingStack:
        async def aclose(self):
            await release.wait()

    mgr._stacks["ntfy1"] = _BlockingStack()

    with patch.object(
        McpManager,
        "_connect_stdio",
        side_effect=_stub_stdio_connect(mgr, created, closed, connect_delay=0),
    ):
        disconnecting = asyncio.create_task(mgr.disconnect_server("ntfy1"))
        await asyncio.sleep(0)  # park it inside stack.aclose()
        connecting = asyncio.create_task(
            mgr.connect_server(
                server_id="ntfy1",
                name="ntfy",
                transport="stdio",
                command="npx",
                args=["-y", "ntfy-me-mcp"],
                env={},
            )
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(disconnecting, connecting)

    assert mgr._connections.get("ntfy1", {}).get("status") == "connected"
    assert "ntfy1" in mgr._sessions
    assert mgr._tools.get("ntfy1")


async def test_connect_closes_a_previous_transport_instead_of_orphaning_it():
    # Overwriting _stacks[server_id] left the old stdio subprocess running with
    # nothing referencing its exit stack, so the GC closed it later from an
    # unrelated task -> anyio "different task" RuntimeError.
    mgr = _connected_stdio_manager()
    created, closed = [], []
    original = _FakeStack(closed)
    mgr._stacks["ntfy1"] = original

    with patch.object(
        McpManager,
        "_connect_stdio",
        side_effect=_stub_stdio_connect(mgr, created, closed, connect_delay=0),
    ):
        assert await mgr.connect_server(**_restart_kwargs()) is True

    assert original.is_closed
    assert mgr._stacks["ntfy1"] is created[-1]


async def test_internal_reconnect_does_not_deadlock_on_the_server_lock():
    # _reconnect_server() holds the server's lock and then calls the public
    # disconnect_server()/connect_server(), which take it again. The lock has
    # to be re-entrant for the owning task or crash recovery would hang.
    mgr = _connected_stdio_manager()
    created, closed = [], []

    with patch.object(
        McpManager,
        "_connect_stdio",
        side_effect=_stub_stdio_connect(mgr, created, closed, connect_delay=0),
    ):
        ok = await asyncio.wait_for(mgr._reconnect_server("ntfy1"), timeout=5)

    assert ok is True
    assert mgr._connections["ntfy1"]["status"] == "connected"


async def test_a_cancelled_reconnect_request_does_not_abort_the_restart():
    # A browser navigating away cancels its request task; the restart other
    # callers are waiting on must survive it.
    mgr = _connected_stdio_manager()
    created, closed = [], []

    with patch.object(
        McpManager, "_connect_stdio", side_effect=_stub_stdio_connect(mgr, created, closed)
    ):
        first = asyncio.create_task(mgr.restart_server(**_restart_kwargs()))
        await asyncio.sleep(0)
        second = asyncio.create_task(mgr.restart_server(**_restart_kwargs()))
        await asyncio.sleep(0)
        first.cancel()
        assert await second is True

    assert mgr._connections["ntfy1"]["status"] == "connected"
    assert len(created) == 1


def test_http_transport_routes_to_start_http_connect():
    mgr = McpManager()

    async def fake_start(server_id, name, url):
        return "ROUTED"

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as m:
        result = asyncio.run(mgr.connect_server("id1", "n", "http", url="https://x/mcp"))
    assert result == "ROUTED"
    m.assert_called_once()
