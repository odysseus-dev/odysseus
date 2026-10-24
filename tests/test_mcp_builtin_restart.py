"""A restarted built-in MCP server must outlive the task that restarted it.

Built-in servers are reconnected from short-lived tasks (an HTTP request, the
crash-recovery branch of a tool call). The stdio transport belongs to the task
that entered it, so the new connection has to be made and held by a long-lived
owner task, as startup registration does; otherwise the next tool call reaches
a session whose streams already closed.
"""
import asyncio
import textwrap

import pytest

from src import builtin_mcp
from src.mcp_manager import McpManager

ECHO_SERVER = textwrap.dedent(
    """
    import asyncio

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    server = Server("echo")


    @server.list_tools()
    async def list_tools():
        return [Tool(name="ping", description="Answer pong.", inputSchema={"type": "object", "properties": {}})]


    @server.call_tool()
    async def call_tool(name, arguments):
        return [TextContent(type="text", text="pong")]


    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


    asyncio.run(main())
    """
)


@pytest.fixture
def echo_builtin(tmp_path, monkeypatch):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER)
    # An absolute script path survives os.path.join with the app root.
    monkeypatch.setitem(builtin_mcp._BUILTIN_SERVERS, "echo", (str(script), "Built-in: Echo"))
    return "echo"


async def test_restarted_builtin_survives_the_task_that_restarted_it(echo_builtin):
    manager = McpManager()
    try:
        # Restart from a short-lived task, as a request handler would.
        restarted = await asyncio.create_task(manager._reconnect_builtin(echo_builtin))
        assert restarted
        await asyncio.sleep(0.5)  # the restarting task has finished and unwound

        result = await asyncio.wait_for(manager.call_tool(f"mcp__{echo_builtin}__ping", {}), timeout=20)
        assert result.get("exit_code") == 0, result
        assert "pong" in result.get("stdout", ""), result
    finally:
        await asyncio.wait_for(manager.disconnect_server(echo_builtin), timeout=20)


async def test_restart_replaces_a_held_connection_cleanly(echo_builtin):
    manager = McpManager()
    try:
        assert await manager._reconnect_builtin(echo_builtin)
        # A second restart must close the first owner's transport, not leak it.
        assert await asyncio.create_task(manager._reconnect_builtin(echo_builtin))
        await asyncio.sleep(0.5)
        result = await asyncio.wait_for(manager.call_tool(f"mcp__{echo_builtin}__ping", {}), timeout=20)
        assert result.get("exit_code") == 0 and "pong" in result.get("stdout", ""), result
    finally:
        await asyncio.wait_for(manager.disconnect_server(echo_builtin), timeout=20)
