"""Tests for the user-configurable memory recall count (issue #4948).

`build_context_preface` retrieves a number of extended (non-pinned) memories to
inject into the model context. That count used to be hardcoded; it is now driven
by the `mem_recall_count` parameter so a user setting can control it.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.chat_processor import ChatProcessor


def _processor_with_memories(monkeypatch, n_extended=12):
    """Build a ChatProcessor whose memory store returns `n_extended` non-pinned memories."""
    mem_entries = [
        {"id": str(i), "text": f"fact {i}", "category": "fact", "pinned": False}
        for i in range(n_extended)
    ]
    memory_manager = MagicMock()
    memory_manager.load.return_value = mem_entries
    processor = ChatProcessor(memory_manager=memory_manager, personal_docs_manager=MagicMock())
    return processor


def test_mem_recall_count_drives_retrieval_k(monkeypatch):
    """A non-default mem_recall_count is forwarded to the retrieval step as k."""
    processor = _processor_with_memories(monkeypatch)
    captured = {}

    def fake_retrieve(message, mem_entries, k=5):
        captured["k"] = k
        return []

    monkeypatch.setattr(processor, "_hybrid_retrieve", fake_retrieve)
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="hello",
        session=session,
        use_web=False,
        use_rag=False,
        use_memory=True,
        use_skills=False,
        mem_recall_count=7,
    )

    assert captured.get("k") == 7


def test_mem_recall_count_defaults_to_current_behavior(monkeypatch):
    """Omitting mem_recall_count preserves the previous hardcoded count (3)."""
    processor = _processor_with_memories(monkeypatch)
    captured = {}

    def fake_retrieve(message, mem_entries, k=5):
        captured["k"] = k
        return []

    monkeypatch.setattr(processor, "_hybrid_retrieve", fake_retrieve)
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="hello",
        session=session,
        use_web=False,
        use_rag=False,
        use_memory=True,
        use_skills=False,
    )

    assert captured.get("k") == 3
