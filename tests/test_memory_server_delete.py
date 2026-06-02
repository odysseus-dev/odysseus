"""Regression: MCP memory delete must remove only the resolved id, not every prefix match."""
import asyncio

import pytest

pytest.importorskip("mcp")  # memory_server imports the MCP SDK at module load

import mcp_servers.memory_server as ms


class _FakeMgr:
    def __init__(self, mems):
        self.mems = mems
        self.saved = None

    def load_all(self):
        return list(self.mems)

    def save(self, mems):
        self.saved = mems


def test_delete_removes_only_the_resolved_id(monkeypatch):
    mems = [
        {"id": "ab11", "text": "first", "category": "fact"},
        {"id": "ab22", "text": "second", "category": "fact"},
    ]
    monkeypatch.setattr(ms, "_memory_manager", _FakeMgr(mems))
    monkeypatch.setattr(ms, "_memory_vector", None)
    monkeypatch.setattr(ms, "_initialized", True)

    # "ab" is a prefix of both ids; only the first resolved one must be deleted.
    asyncio.run(ms.call_tool("manage_memory", {"action": "delete", "memory_id": "ab"}))

    saved_ids = [m["id"] for m in ms._memory_manager.saved]
    assert saved_ids == ["ab22"]  # ab11 (first match) removed, sibling kept
