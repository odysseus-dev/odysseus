"""Tests for manage_notes' move to the agent_tools registry (#3629).

The implementation stays in src/tools/notes.py; ManageNotesTool in
src/agent_tools/productivity_tools.py adapts it to execute(content, ctx).
These assert (1) the handler is registered in TOOL_HANDLERS, (2) it threads
owner from ctx into do_manage_notes, and (3) tool_execution.py dispatches
manage_notes through the registry rather than calling do_manage_notes directly.
"""
import asyncio
from pathlib import Path

from src.agent_tools import TOOL_HANDLERS
from src.agent_tools import productivity_tools as pt


def test_manage_notes_registered():
    assert "manage_notes" in TOOL_HANDLERS


def test_manage_notes_handler_threads_owner_from_ctx(monkeypatch):
    # Spy at the function boundary so the test does not depend on the notes DB.
    seen = {}

    async def spy(content, owner=None):
        seen.update(content=content, owner=owner)
        return {"results": "ok"}

    monkeypatch.setattr(pt, "do_manage_notes", spy)
    res = asyncio.run(pt.ManageNotesTool().execute('{"action": "list"}', {"owner": "alice"}))
    assert res == {"results": "ok"}
    assert seen == {"content": '{"action": "list"}', "owner": "alice"}


def test_manage_notes_handler_passes_none_owner_when_ctx_has_none(monkeypatch):
    # A missing owner must reach do_manage_notes as None (its own default),
    # not raise KeyError in the adapter.
    seen = {}

    async def spy(content, owner=None):
        seen["owner"] = owner
        return {}

    monkeypatch.setattr(pt, "do_manage_notes", spy)
    asyncio.run(pt.ManageNotesTool().execute("{}", {}))
    assert seen == {"owner": None}


def test_tool_execution_routes_manage_notes_through_registry():
    src = (Path(__file__).resolve().parents[1] / "src" / "tool_execution.py").read_text(encoding="utf-8")
    assert "do_manage_notes(" not in src
