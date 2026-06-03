"""audit_memories must rebuild the vector index from ALL owners\' memories.

The JSON store correctly preserves other owners (save(final + other)), but
the vector index was rebuilt from final_entries (only the audited owner),
so every other owner was wiped from the shared semantic-search collection
until they ran their own audit. The rebuild must use the same full set that
was saved.
"""
import asyncio

import pytest

import src.llm_core as llm_core
from services.memory.memory import MemoryManager
from services.memory import memory_extractor


class _FakeVector:
    def __init__(self):
        self.healthy = True
        self.rebuilt_with = None

    def rebuild(self, entries):
        self.rebuilt_with = list(entries)


def test_rebuild_includes_other_owners(monkeypatch, tmp_path):
    mgr = MemoryManager(str(tmp_path))
    mgr.save([
        {"id": "a1", "text": "alice likes tea", "owner": "alice", "category": "fact"},
        {"id": "b1", "text": "bob likes coffee", "owner": "bob", "category": "fact"},
    ])

    async def _fake_llm(url, model, messages, **kwargs):
        # Keep alice's single memory unchanged.
        return '[{"id": "a1", "text": "alice likes tea", "category": "fact"}]'

    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm)

    vec = _FakeVector()
    res = asyncio.run(memory_extractor.audit_memories(
        mgr, vec, "http://x", "model", owner="alice",
    ))
    assert "error" not in res, res

    ids = {e["id"] for e in (vec.rebuilt_with or [])}
    assert "a1" in ids
    assert "b1" in ids, "other owner's memory was wiped from the vector index"


def test_single_user_rebuild_uses_audited_entries(monkeypatch, tmp_path):
    mgr = MemoryManager(str(tmp_path))
    mgr.save([{"id": "x1", "text": "solo note", "category": "fact"}])

    async def _fake_llm(url, model, messages, **kwargs):
        return '[{"id": "x1", "text": "solo note", "category": "fact"}]'

    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm)

    vec = _FakeVector()
    asyncio.run(memory_extractor.audit_memories(mgr, vec, "http://x", "model"))
    ids = {e["id"] for e in (vec.rebuilt_with or [])}
    assert ids == {"x1"}
