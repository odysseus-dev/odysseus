"""Tests for the user-configurable memory recall count (issue #4948).

`build_context_preface` retrieves a number of extended (non-pinned) memories to
inject into the model context. That count used to be hardcoded; it is now driven
by the `mem_recall_count` parameter (resolved from a user pref) so a setting can
control it.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.chat_processor import ChatProcessor
from routes.chat_helpers import (
    resolve_memory_recall_count,
    MEMORY_RECALL_COUNT_MIN,
    MEMORY_RECALL_COUNT_MAX,
)


def _processor_with_memories(n_extended=12):
    """A ChatProcessor whose memory store returns `n_extended` non-pinned memories."""
    mem_entries = [
        {"id": str(i), "text": f"fact {i}", "category": "fact", "pinned": False}
        for i in range(n_extended)
    ]
    memory_manager = MagicMock()
    memory_manager.load.return_value = mem_entries
    return ChatProcessor(memory_manager=memory_manager, personal_docs_manager=MagicMock())


def _capture_k(monkeypatch, processor):
    captured = {}

    def fake_retrieve(message, mem_entries, k):
        captured["k"] = k
        return []

    monkeypatch.setattr(processor, "_hybrid_retrieve", fake_retrieve)
    return captured


# ── build_context_preface forwards the count to retrieval ───────────────────

def test_mem_recall_count_drives_retrieval_k(monkeypatch):
    """A non-default mem_recall_count is forwarded to the retrieval step as k."""
    processor = _processor_with_memories()
    captured = _capture_k(monkeypatch, processor)
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="hello", session=session,
        use_web=False, use_rag=False, use_memory=True, use_skills=False,
        mem_recall_count=7,
    )

    assert captured.get("k") == 7


def test_mem_recall_count_defaults_to_current_behavior(monkeypatch):
    """Omitting mem_recall_count preserves the previous hardcoded count (3)."""
    processor = _processor_with_memories()
    captured = _capture_k(monkeypatch, processor)
    session = SimpleNamespace(endpoint_url="http://local", model="test", headers={})

    processor.build_context_preface(
        message="hello", session=session,
        use_web=False, use_rag=False, use_memory=True, use_skills=False,
    )

    assert captured.get("k") == 3


# ── resolve_memory_recall_count: pref resolution + clamp + malformed input ──

def test_resolve_uses_valid_pref():
    assert resolve_memory_recall_count({"memory_recall_count": 10}) == 10


def test_resolve_missing_pref_falls_back_to_default():
    # No pref -> global setting -> historical default of 3.
    assert resolve_memory_recall_count({}) == 3


@pytest.mark.parametrize("bad", [None, "abc", float("nan"), [1], {}])
def test_resolve_malformed_pref_falls_back_to_3(bad):
    """A non-int / NaN / null pref must not throw or disable recall — falls back to 3."""
    assert resolve_memory_recall_count({"memory_recall_count": bad}) == 3


def test_resolve_clamps_low_so_recall_is_never_disabled():
    # k<=0 would make `queued >= k`-style logic recall nothing; clamp to the floor.
    assert resolve_memory_recall_count({"memory_recall_count": 0}) == MEMORY_RECALL_COUNT_MIN
    assert resolve_memory_recall_count({"memory_recall_count": -5}) == MEMORY_RECALL_COUNT_MIN


def test_resolve_clamps_high_so_prompt_cannot_balloon():
    assert resolve_memory_recall_count({"memory_recall_count": 9999}) == MEMORY_RECALL_COUNT_MAX


def test_resolve_truncates_float():
    assert resolve_memory_recall_count({"memory_recall_count": 2.9}) == 2
