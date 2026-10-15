"""Tests for OXS MCP bridge (in-process tool registration)."""

from __future__ import annotations

import asyncio

import pytest

from core.extensions import mcp_bridge


@pytest.fixture(autouse=True)
def _cleanup_mcp_state():
    """Drop any OXS-namespaced server entries before AND after each test."""
    from src.mcp_manager import get_mcp_manager
    m = get_mcp_manager()
    for sid in list(m._in_process_tools.keys()):
        m.unregister_in_process_tools(sid)
    yield
    m = get_mcp_manager()
    for sid in list(m._in_process_tools.keys()):
        m.unregister_in_process_tools(sid)


def test_install_tools_pushes_to_mcp_manager():
    from src.mcp_manager import get_mcp_manager as McpManager

    def greet(args):
        return {"hi": args.get("name", "world")}

    from core.extensions.host import MCPToolRegistration
    reg = MCPToolRegistration(
        server_name="w2a", tool_name="greet",
        schema={"type": "object", "properties": {"name": {"type": "string"}}},
        handler=greet,
    )
    n = mcp_bridge.install_tools("myext", [reg])
    assert n == 1
    m = McpManager()
    assert "oxs_myext" in m._in_process_tools
    assert "greet" in m._in_process_tools["oxs_myext"]


def test_install_multiple_tools():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    regs = [
        MCPToolRegistration(server_name="", tool_name="a",
                             schema={}, handler=lambda x: "a"),
        MCPToolRegistration(server_name="", tool_name="b",
                             schema={}, handler=lambda x: "b"),
        MCPToolRegistration(server_name="", tool_name="c",
                             schema={}, handler=lambda x: "c"),
    ]
    n = mcp_bridge.install_tools("ext", regs)
    assert n == 3
    m = McpManager()
    assert set(m._in_process_tools["oxs_ext"].keys()) == {"a", "b", "c"}


def test_call_in_process_tool_end_to_end():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    def search(args):
        return {"results": ["a", "b", "c"][:args.get("limit", 3)]}

    mcp_bridge.install_tools("ext", [MCPToolRegistration(
        server_name="", tool_name="search",
        schema={"type": "object"}, handler=search,
    )])

    m = McpManager()
    result = asyncio.run(m.call_tool("mcp__oxs_ext__search", {"limit": 2}))
    assert result == {"output": {"results": ["a", "b"]}, "exit_code": 0}


def test_call_in_process_async_tool():
    import asyncio
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    async def async_tool(args):
        return {"slept": args.get("n", 0)}

    mcp_bridge.install_tools("ext", [MCPToolRegistration(
        server_name="", tool_name="sleepy",
        schema={"type": "object"}, handler=async_tool,
    )])

    m = McpManager()
    result = asyncio.run(m.call_tool("mcp__oxs_ext__sleepy", {"n": 5}))
    assert result == {"output": {"slept": 5}, "exit_code": 0}


def test_call_in_process_string_result():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    def stringy(args):
        return "just a string"

    mcp_bridge.install_tools("ext", [MCPToolRegistration(
        server_name="", tool_name="str",
        schema={}, handler=stringy,
    )])
    m = McpManager()
    result = asyncio.run(m.call_tool("mcp__oxs_ext__str", {}))
    assert result == {"output": "just a string", "exit_code": 0}


def test_call_in_process_handles_exception():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    def boom(args):
        raise RuntimeError("boom")

    mcp_bridge.install_tools("ext", [MCPToolRegistration(
        server_name="", tool_name="boom",
        schema={}, handler=boom,
    )])
    m = McpManager()
    result = asyncio.run(m.call_tool("mcp__oxs_ext__boom", {}))
    assert result["exit_code"] == 1
    assert "boom" in result["error"]


def test_uninstall_tools_removes_server():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    mcp_bridge.install_tools("ext", [MCPToolRegistration(
        server_name="", tool_name="x",
        schema={}, handler=lambda a: None,
    )])
    n = mcp_bridge.uninstall_tools("ext")
    assert n == 1
    m = McpManager()
    assert "oxs_ext" not in m._in_process_tools


def test_uninstall_unknown_returns_zero():
    n = mcp_bridge.uninstall_tools("nope")
    assert n == 0


def test_install_tools_prefers_description_rich_for_rag():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    semantic = (
        "List recent WhatsApp messages exchanged with the operator's "
        "contacts over the configured bridge."
    )

    reg = MCPToolRegistration(
        server_name="w2a",
        tool_name="list_messages",
        schema={"type": "object"},
        handler=lambda a: "ok",
        description_rich=semantic,
    )
    mcp_bridge.install_tools("whats2api_bridge", [reg])
    m = McpManager()
    stored = m._in_process_tools["oxs_whats2api_bridge"]["list_messages"]
    assert stored["description"] == semantic


def test_install_tools_falls_back_to_legacy_id_placeholder():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    reg = MCPToolRegistration(
        server_name="w2a",
        tool_name="legacy",
        schema={"type": "object"},
        handler=lambda a: "ok",
    )
    mcp_bridge.install_tools("bridge", [reg])
    m = McpManager()
    stored = m._in_process_tools["oxs_bridge"]["legacy"]
    assert stored["description"] == "[bridge] legacy"


def test_register_mcp_tool_accepts_description_rich():
    from core.extensions.host import OXSHost
    host = OXSHost(
        extension_id="myext",
        extension_version="1.0",
        declared_capabilities={"mcp_tools": True},
        data_path="/tmp/myext-data",
        plugin_path="/tmp/myext-plugin",
    )
    host.register_mcp_tool(
        server_name="s",
        tool_name="list_messages",
        schema={"type": "object"},
        handler=lambda a: "ok",
        description_rich="List recent WhatsApp messages between operator contacts.",
    )
    reg = host.mcp_tools[0]
    assert reg.description_rich.startswith("List recent WhatsApp")


def test_openai_schema_lists_in_process_tools():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    mcp_bridge.install_tools("ext1", [MCPToolRegistration(
        server_name="", tool_name="hello",
        schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        handler=lambda a: "hi",
    )])
    m = McpManager()
    schemas = m.get_all_openai_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "mcp__oxs_ext1__hello" in names
    hello = next(s for s in schemas if s["function"]["name"] == "mcp__oxs_ext1__hello")
    # The bridge supplies the description, which includes the extension id.
    assert "[ext1]" in hello["function"]["description"]


def test_extensions_have_isolated_servers():
    from src.mcp_manager import get_mcp_manager as McpManager
    from core.extensions.host import MCPToolRegistration

    mcp_bridge.install_tools("alpha", [MCPToolRegistration(
        server_name="", tool_name="x",
        schema={}, handler=lambda a: "alpha",
    )])
    mcp_bridge.install_tools("beta", [MCPToolRegistration(
        server_name="", tool_name="y",
        schema={}, handler=lambda a: "beta",
    )])

    m = McpManager()
    assert "oxs_alpha" in m._in_process_tools
    assert "oxs_beta" in m._in_process_tools
    assert m._in_process_tools["oxs_alpha"] != m._in_process_tools["oxs_beta"]

    mcp_bridge.uninstall_tools("alpha")
    m = McpManager()
    assert "oxs_alpha" not in m._in_process_tools
    assert "oxs_beta" in m._in_process_tools