import asyncio

import pytest

from src.mcp_manager import McpManager


class _ExplodingStack:
    async def aclose(self):
        raise RuntimeError("Attempted to exit cancel scope in a different task")


@pytest.mark.asyncio
async def test_disconnect_all_swallows_benign_stack_close_errors():
    mgr = McpManager()
    mgr._stacks["builtin_browser"] = _ExplodingStack()
    mgr._sessions["builtin_browser"] = object()

    await mgr.disconnect_all()

    assert "builtin_browser" not in mgr._stacks
    assert "builtin_browser" not in mgr._sessions
