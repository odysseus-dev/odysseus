"""Pin that every tool dispatched through TOOL_HANDLERS receives `owner`
and `session_id` via the `ctx` dict.

All tools migrated to the class-based pattern share a single dispatch path
(``execute_tool_block`` → ``TOOL_HANDLERS[tool](content, ctx)``). If a
tool is added to TOOL_HANDLERS but omitted from this parametrize list,
there is no automated guard that its ctx was plumbed correctly — the tool
simply receives an empty ctx at runtime.

Each entry is a tool name that MUST be in TOOL_HANDLERS.
"""

import pytest


@pytest.mark.parametrize("tool", [
    "pipeline",
    "ui_control",
])
async def test_handlers_dispatch_passes_owner(monkeypatch, tool):
    from src.agent_tools import TOOL_HANDLERS

    seen_ctx = {}

    async def capture(content, ctx):
        seen_ctx.update(ctx)
        return {"ok": True}

    monkeypatch.setitem(TOOL_HANDLERS, tool, capture)
    await TOOL_HANDLERS[tool]("test", {"session_id": "sid1", "owner": "alice"})

    assert seen_ctx.get("owner") == "alice", (
        f"TOOL_HANDLERS['{tool}'] received owner={seen_ctx.get('owner')!r}, expected 'alice'"
    )
    assert seen_ctx.get("session_id") == "sid1", (
        f"TOOL_HANDLERS['{tool}'] received session_id={seen_ctx.get('session_id')!r}, expected 'sid1'"
    )
