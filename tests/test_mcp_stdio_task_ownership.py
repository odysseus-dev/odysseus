import asyncio
import sys
from types import ModuleType, SimpleNamespace

from src.mcp_manager import McpManager


def test_stdio_transport_is_closed_by_the_task_that_opened_it(monkeypatch):
    tasks = {}

    class Transport:
        async def __aenter__(self):
            tasks["entered"] = asyncio.current_task()
            return object(), object()

        async def __aexit__(self, *exc_info):
            tasks["exited"] = asyncio.current_task()

    class Session:
        def __init__(self, *streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    mcp = ModuleType("mcp")
    mcp.ClientSession = Session
    mcp.StdioServerParameters = lambda **kwargs: kwargs
    stdio = ModuleType("mcp.client.stdio")
    stdio.stdio_client = lambda params: Transport()
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", ModuleType("mcp.client"))
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio)

    async def exercise():
        manager = McpManager()
        assert await manager.connect_server("server", "Server", "stdio", "command")
        tasks["request"] = asyncio.current_task()
        await manager.disconnect_server("server")

    asyncio.run(exercise())

    assert tasks["entered"] is tasks["exited"]
    assert tasks["entered"] is not tasks["request"]
