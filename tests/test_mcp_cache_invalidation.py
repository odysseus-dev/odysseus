"""Regression test: McpManager._generation must bust the tool prompt cache
when a server connects/disconnects with the same tool count.

Before the fix, cache_key was (disabled_map, len(_tools)).  A reconnect that
preserved the tool count left the stale description in place.  After the fix
the _generation counter is included so any structural change invalidates it.
"""
import asyncio

from src.mcp_manager import McpManager


def _make_mgr():
    return McpManager()


def _inject_tools(mgr, server_id: str, tools: list):
    """Directly populate internal dicts as _connect_stdio would after success."""
    mgr._tools[server_id] = tools
    mgr._connections[server_id] = {"status": "connected", "name": server_id}


# ---------------------------------------------------------------------------
# _generation increments on disconnect
# ---------------------------------------------------------------------------

def test_generation_increments_on_disconnect():
    mgr = _make_mgr()
    assert mgr._generation == 0
    _inject_tools(mgr, "srv1", [{"name": "tool_a"}])
    mgr._generation += 1  # simulate connect increment

    gen_before = mgr._generation
    asyncio.run(mgr.disconnect_server("srv1"))
    assert mgr._generation == gen_before + 1


# ---------------------------------------------------------------------------
# Core cache-invalidation regression: stale description after reconnect
# ---------------------------------------------------------------------------

def test_prompt_cache_busted_after_disconnect_same_tool_count():
    """The stale-cache bug: two different servers each have 1 tool.
    After the first disconnects and the second connects, the cache must
    reflect the new server's tools, not the old one's description.
    """
    mgr = _make_mgr()

    # Connect server A with one tool
    _inject_tools(mgr, "srv_a", [{"name": "tool_alpha", "description": "Alpha tool",
                                   "inputSchema": {"type": "object", "properties": {}}}])
    mgr._generation += 1  # simulated successful connect

    desc_a = mgr.get_tool_descriptions_for_prompt()
    assert "tool_alpha" in desc_a

    # Disconnect A — same tool count (1) as what follows
    asyncio.run(mgr.disconnect_server("srv_a"))  # bumps _generation

    # Connect server B with a *different* tool but same count (1)
    _inject_tools(mgr, "srv_b", [{"name": "tool_beta", "description": "Beta tool",
                                   "inputSchema": {"type": "object", "properties": {}}}])
    mgr._generation += 1  # simulated successful connect

    desc_b = mgr.get_tool_descriptions_for_prompt()

    # Without the fix both describe tool_alpha (stale cache hit).
    assert "tool_beta" in desc_b, (
        "Cache was not invalidated: got stale description after reconnect"
    )
    assert "tool_alpha" not in desc_b
