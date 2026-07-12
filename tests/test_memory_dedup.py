"""manage_memory add: exact-duplicate rejection and near-duplicate surfacing."""
import asyncio

import pytest

import mcp_servers.memory_server as memory_server
import src.ai_interaction as ai_interaction
from src.memory import MemoryManager


class FakeVector:
    healthy = True

    def __init__(self):
        self.added = []

    def add(self, memory_id, text):
        self.added.append((memory_id, text))

    def remove(self, memory_id):
        pass


def _manager_with(tmp_path, texts, owner=None):
    manager = MemoryManager(str(tmp_path))
    entries = [manager.add_entry(t, owner=owner) for t in texts]
    manager.save(entries)
    return manager


# ── MemoryManager.dedup_check ─────────────────────────────────────────────
def test_dedup_check_exact_match_case_insensitive(tmp_path):
    manager = _manager_with(tmp_path, ["The user likes green tea"])
    exact, similar = manager.dedup_check("the user likes GREEN tea", manager.load())
    assert exact is not None and exact["text"] == "The user likes green tea"
    # The exact match must not also be reported as merely similar.
    assert similar == []


def test_dedup_check_similar_below_and_above_threshold(tmp_path):
    manager = _manager_with(tmp_path, [
        "The user likes green tea",          # highly similar to the probe
        "Completely unrelated fact about GPUs",
    ])
    exact, similar = manager.dedup_check("the user likes green tea a lot", manager.load())
    assert exact is None
    assert [m["text"] for m in similar] == ["The user likes green tea"]


def test_dedup_check_caps_similar_results(tmp_path):
    manager = _manager_with(tmp_path, [f"user likes green tea variant {i}" for i in range(5)])
    exact, similar = manager.dedup_check("user likes green tea variant", manager.load(),
                                         max_similar=3)
    assert exact is None and len(similar) == 3


# ── do_manage_memory (agent tool path) ────────────────────────────────────
@pytest.mark.asyncio
async def test_manage_memory_add_rejects_exact_duplicate(tmp_path, monkeypatch):
    manager = _manager_with(tmp_path, ["User lives in Stockholm"], owner="alice")
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    res = await ai_interaction.do_manage_memory("add\nuser lives in stockholm", owner="alice")
    assert "Not added" in res["results"] and "action=edit" in res["results"]
    assert res["memory_id"]  # points at the existing entry
    assert len(manager.load_all()) == 1  # nothing was written


@pytest.mark.asyncio
async def test_manage_memory_add_surfaces_similar_but_still_adds(tmp_path, monkeypatch):
    manager = _manager_with(tmp_path, ["User lives in Stockholm with two cats"], owner="alice")
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    res = await ai_interaction.do_manage_memory(
        "add\nUser lives in Stockholm with two cats and a dog", owner="alice")
    assert "Memory added" in res["results"]
    assert "Similar existing memories" in res["results"]
    assert len(manager.load_all()) == 2


@pytest.mark.asyncio
async def test_manage_memory_add_distinct_has_no_similar_note(tmp_path, monkeypatch):
    manager = _manager_with(tmp_path, ["User lives in Stockholm"], owner="alice")
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    res = await ai_interaction.do_manage_memory("add\nPrefers dark roast coffee", owner="alice")
    assert "Memory added" in res["results"]
    assert "Similar existing" not in res["results"]
    assert len(manager.load_all()) == 2


@pytest.mark.asyncio
async def test_manage_memory_add_duplicate_of_other_owner_is_allowed(tmp_path, monkeypatch):
    # Dedup is owner-scoped: bob storing the same text as alice must succeed.
    manager = _manager_with(tmp_path, ["Favorite color is blue"], owner="alice")
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    res = await ai_interaction.do_manage_memory("add\nFavorite color is blue", owner="bob")
    assert "Memory added" in res["results"]
    assert len(manager.load_all()) == 2


# ── MCP memory server path ────────────────────────────────────────────────
def _configure_server(monkeypatch, manager, vector=None):
    monkeypatch.setattr(memory_server, "_memory_manager", manager)
    monkeypatch.setattr(memory_server, "_memory_vector", vector)
    monkeypatch.setattr(memory_server, "_initialized", True)
    for key in memory_server._OWNER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _tool_text(arguments):
    result = asyncio.run(memory_server.call_tool("manage_memory", arguments))
    return result[0].text


def test_mcp_memory_add_rejects_exact_duplicate(monkeypatch, tmp_path):
    manager = _manager_with(tmp_path, ["User lives in Stockholm"], owner="alice")
    _configure_server(monkeypatch, manager, FakeVector())
    monkeypatch.setenv("ODYSSEUS_MCP_MEMORY_OWNER", "alice")

    text = _tool_text({"action": "add", "text": "USER LIVES IN STOCKHOLM"})
    assert "Not added" in text and "action=edit" in text
    assert len(manager.load_all()) == 1


def test_mcp_memory_add_surfaces_similar(monkeypatch, tmp_path):
    manager = _manager_with(tmp_path, ["User lives in Stockholm with two cats"], owner="alice")
    _configure_server(monkeypatch, manager, FakeVector())
    monkeypatch.setenv("ODYSSEUS_MCP_MEMORY_OWNER", "alice")

    text = _tool_text({"action": "add", "text": "User lives in Stockholm with two cats and a dog"})
    assert "Memory added" in text and "Similar existing memories" in text
    assert len(manager.load_all()) == 2
