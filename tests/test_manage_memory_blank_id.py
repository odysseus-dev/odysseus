import asyncio
import json
import pytest

from src import ai_interaction
from src.memory import MemoryManager


def test_manage_memory_delete_blank_id_rejected(tmp_path, monkeypatch):
    """Calling delete with a blank memory_id should return an error and not delete the first memory."""
    memory_file = tmp_path / "memory.json"
    initial_memories = [
        {"id": "first-mem-1111", "text": "First memory that must not be deleted", "category": "fact", "timestamp": 1000},
        {"id": "second-mem-2222", "text": "Second memory", "category": "fact", "timestamp": 2000},
    ]
    memory_file.write_text(json.dumps(initial_memories), encoding="utf-8")

    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    # Calling delete with a blank line before the ID
    result = asyncio.run(ai_interaction.do_manage_memory("delete\n\nsecond-mem-2222"))

    # Must reject the call
    assert "error" in result
    assert "memory_id" in result["error"].lower()

    # Verify that the first memory was NOT deleted
    remaining = manager.load_all()
    assert len(remaining) == 2
    assert remaining[0]["id"] == "first-mem-1111"


def test_manage_memory_edit_blank_id_rejected(tmp_path, monkeypatch):
    """Calling edit with a blank memory_id should return an error and not overwrite the first memory."""
    memory_file = tmp_path / "memory.json"
    initial_memories = [
        {"id": "first-mem-1111", "text": "First memory original content", "category": "fact", "timestamp": 1000},
        {"id": "second-mem-2222", "text": "Second memory", "category": "fact", "timestamp": 2000},
    ]
    memory_file.write_text(json.dumps(initial_memories), encoding="utf-8")

    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    # Calling edit with a blank line before the ID
    result = asyncio.run(ai_interaction.do_manage_memory("edit\n\nnew content for second"))

    # Must reject the call
    assert "error" in result
    assert "memory_id" in result["error"].lower()

    # Verify that the first memory was NOT overwritten
    remaining = manager.load_all()
    assert len(remaining) == 2
    assert remaining[0]["text"] == "First memory original content"
