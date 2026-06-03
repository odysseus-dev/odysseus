"""Concurrency regression test for the audit_memories keystone refactor.

services.memory.memory_extractor.audit_memories runs an LLM dedup pass UNLOCKED
against a snapshot, then applies the keep/drop/clean decision against the FRESH
on-disk list inside one locked mutate(). The pre-fix code saved a wholesale list
built from the pre-LLM snapshot, silently clobbering any memory written during
the (slow) LLM call.

Unlike action_consolidate_memory (keep/drop object), audit_memories expects the
LLM to return a JSON ARRAY of {id, text, category}; an id omitted from the array
is a drop. These tests assert the FIXED behavior: a concurrent add survives, a
real drop is applied, other owners are untouched, and the vector rebuild guard
is honored.

Seam: monkeypatch src.llm_core.llm_call_async; use a real MemoryManager pointed
at a tmp data dir (no DATA_DIR monkeypatch needed — the manager takes the dir).
"""
import asyncio
import json

import pytest


class _FakeVector:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.rebuild_calls = []

    def rebuild(self, entries):
        self.rebuild_calls.append([dict(e) for e in entries])


def _mgr(tmp_path, memories):
    from src.memory import MemoryManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "memory.json").write_text(json.dumps(memories), encoding="utf-8")
    return MemoryManager(str(data_dir)), str(data_dir)


@pytest.mark.asyncio
async def test_audit_preserves_concurrent_add_and_applies_drop(monkeypatch, tmp_path):
    from src import llm_core
    from src.memory import MemoryManager
    from services.memory.memory_extractor import audit_memories

    mgr, data_dir = _mgr(
        tmp_path,
        [
            {"id": "e1", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "e2", "owner": "u", "text": "User likes tea (dup).", "category": "preference"},
        ],
    )

    async def fake_llm_call_async(*args, **kwargs):
        # Concurrent add DURING the (mocked) LLM call.
        def _concurrent(entries):
            entries.append(
                {"id": "e3", "owner": "u", "text": "Added mid-audit.", "category": "fact"}
            )
            return entries, None

        MemoryManager(data_dir).mutate(_concurrent)

        # Array form: keep e1 (rewritten), omit e2 -> drop.
        return json.dumps(
            [{"id": "e1", "text": "User enjoys tea.", "category": "preference"}]
        )

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    fake_vec = _FakeVector(healthy=False)
    result = await audit_memories(mgr, fake_vec, "http://llm", "model", {}, owner="u")

    saved = {m["id"]: m for m in MemoryManager(data_dir).load_all()}
    # Concurrent add survived (pre-fix: clobbered by the snapshot save).
    assert "e3" in saved
    # The dropped duplicate is gone.
    assert "e2" not in saved
    # The kept entry survived and was rewritten.
    assert saved["e1"]["text"] == "User enjoys tea."
    # before/after reflect the owner slice (e1, e2 -> e1).
    assert result["before"] == 2
    assert result["after"] == 1


@pytest.mark.asyncio
async def test_audit_preserves_concurrent_edit_of_dropped_id(monkeypatch, tmp_path):
    from src import llm_core
    from src.memory import MemoryManager
    from services.memory.memory_extractor import audit_memories

    mgr, data_dir = _mgr(
        tmp_path,
        [
            {"id": "e1", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "e2", "owner": "u", "text": "Stale fact.", "category": "fact"},
        ],
    )

    async def fake_llm_call_async(*args, **kwargs):
        # Concurrently EDIT the entry the LLM is about to drop.
        def _edit(entries):
            for e in entries:
                if e.get("id") == "e2":
                    e["text"] = "Freshly edited fact."
            return entries, None

        MemoryManager(data_dir).mutate(_edit)

        # LLM (pre-edit snapshot) keeps only e1, omitting e2 -> would drop it.
        return json.dumps([{"id": "e1", "text": "User likes tea.", "category": "preference"}])

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    result = await audit_memories(mgr, _FakeVector(healthy=False), "http://llm", "m", {}, owner="u")

    saved = {m["id"]: m for m in MemoryManager(data_dir).load_all()}
    # The concurrently-edited entry survives the stale drop (conflict guard).
    assert "e2" in saved
    assert saved["e2"]["text"] == "Freshly edited fact."


@pytest.mark.asyncio
async def test_audit_leaves_other_owners_untouched_and_rebuilds(monkeypatch, tmp_path):
    from src import llm_core
    from src.memory import MemoryManager
    from services.memory.memory_extractor import audit_memories

    mgr, data_dir = _mgr(
        tmp_path,
        [
            {"id": "u1", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u2", "owner": "u", "text": "User likes tea (dup).", "category": "preference"},
            {"id": "b1", "owner": "bob", "text": "Bob's secret.", "category": "fact"},
            {"id": "legacy", "text": "Ownerless legacy note.", "category": "fact"},
        ],
    )

    async def fake_llm_call_async(*args, **kwargs):
        # Only u's slice is sent; keep u1, drop u2.
        return json.dumps([{"id": "u1", "text": "User likes tea.", "category": "preference"}])

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    fake_vec = _FakeVector(healthy=True)
    result = await audit_memories(mgr, fake_vec, "http://llm", "m", {}, owner="u")

    saved = {m["id"]: m for m in MemoryManager(data_dir).load_all()}
    # Other owner + legacy untouched; u's duplicate dropped.
    assert "b1" in saved and saved["b1"]["owner"] == "bob"
    assert "legacy" in saved and "owner" not in saved["legacy"]
    assert "u1" in saved
    assert "u2" not in saved
    # Vector rebuilt once over the FULL persisted set.
    assert len(fake_vec.rebuild_calls) == 1
    assert {e["id"] for e in fake_vec.rebuild_calls[0]} == set(saved)
