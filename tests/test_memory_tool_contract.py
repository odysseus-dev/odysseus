"""Contract tests for the single-step memory search/list tool."""

import pytest

import mcp_servers.memory_server as memory_server


class _MemoryManager:
    def __init__(self, entries):
        self.entries = entries

    def load_all(self):
        return list(self.entries)

    def get_relevant_memories(self, query, memories, threshold=0.05, max_items=20):
        return [m for m in memories if query.lower() in m["text"].lower()][:max_items]


@pytest.fixture
def memory_state(monkeypatch):
    entries = [
        {"id": "coffee_roaster-01", "owner": "maya", "category": "fact", "text": "Harbor Guji arrives Friday."},
        {"id": "other-owner-01", "owner": "other", "category": "fact", "text": "Private other-owner memory."},
    ]
    monkeypatch.setattr(memory_server, "_memory_manager", _MemoryManager(entries))
    monkeypatch.setattr(memory_server, "_memory_vector", None)
    monkeypatch.setattr(memory_server, "_initialized", True)
    monkeypatch.setenv("ODYSSEUS_MCP_MEMORY_OWNER", "maya")


@pytest.mark.asyncio
async def test_memory_search_returns_matching_text_and_id(memory_state):
    result = await memory_server.call_tool("manage_memory", {"action": "search", "text": "Harbor Guji"})
    output = result[0].text
    assert "`coffee_r`" in output
    assert "Harbor Guji arrives Friday" in output
    assert "Private other-owner memory" not in output


@pytest.mark.asyncio
async def test_memory_list_is_owner_scoped(memory_state):
    result = await memory_server.call_tool("manage_memory", {"action": "list"})
    output = result[0].text
    assert "Harbor Guji arrives Friday" in output
    assert "Private other-owner memory" not in output
