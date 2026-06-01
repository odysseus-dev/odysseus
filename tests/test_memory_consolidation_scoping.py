"""Regression tests for per-owner scoping + truncation guard in action_consolidate_memory.

Pins the fixes for #790: an empty-owner housekeeping consolidation must never compare,
leak, or delete memories across owners, and must not silently truncate long memories.
"""
import asyncio
import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# Import the action chain WITHOUT booting the real SQL database. core.platform_compat
# (pulled in by src.builtin_actions / src.endpoint_resolver) transitively imports
# core.database, which builds a sqlite engine bound to ./data/app.db and connects at
# import — that fails whenever pytest runs outside the repo data dir (e.g. a git
# worktree or CI). action_consolidate_memory only uses the file-based MemoryManager,
# so a stub is safe. Mirrors tests/test_null_owner_gates.py.
if "core.database" not in sys.modules:
    _db_stub = types.ModuleType("core.database")
    for _name in (
        "SessionLocal", "Session", "ChatMessage", "Document", "DocumentVersion",
        "CalendarCal", "CalendarEvent", "GalleryImage", "GalleryAlbum", "Note",
        "ScheduledTask", "TaskRun", "ModelEndpoint", "Memory", "EmailAccount",
    ):
        setattr(_db_stub, _name, MagicMock())
    sys.modules["core.database"] = _db_stub

import src.memory
import src.endpoint_resolver
import src.llm_core
from src.builtin_actions import action_consolidate_memory, TaskNoop


def _mk_fake_mm(initial):
    """A fake MemoryManager whose instances share one in-memory store + record save()."""
    state = {"saved": None, "save_calls": 0, "initial": [dict(m) for m in initial]}

    class FakeMM:
        def __init__(self, data_dir=None):
            pass

        def load_all(self):
            return [dict(m) for m in state["initial"]]

        def save(self, store):
            state["saved"] = store
            state["save_calls"] += 1

    return FakeMM, state


def _patch(monkeypatch, fake_mm, resolve, llm):
    monkeypatch.setattr(src.memory, "MemoryManager", fake_mm)
    monkeypatch.setattr(src.endpoint_resolver, "resolve_endpoint", resolve)
    monkeypatch.setattr(src.llm_core, "llm_call_async", llm)


def test_empty_owner_run_never_deletes_cross_owner(monkeypatch):
    fake_mm, state = _mk_fake_mm([
        {"id": "a1", "owner": "alice", "text": "hello world"},
        {"id": "a2", "owner": "alice", "text": "hello world"},   # dup within alice
        {"id": "b1", "owner": "bob", "text": "hello world"},      # same text, other owner
        {"id": "c1", "owner": "", "text": "orphan note"},
    ])
    # No endpoint -> deterministic exact-duplicate fallback (no network).
    _patch(monkeypatch, fake_mm, lambda kind, owner=None: (None, None, None), None)

    ok, success = asyncio.run(action_consolidate_memory(""))
    ids = {m["id"] for m in state["saved"]}
    assert success
    assert "b1" in ids, "cross-owner duplicate must NOT be deleted"
    assert "c1" in ids
    assert ("a1" in ids) ^ ("a2" in ids), "alice's internal dup collapses to one"
    assert len(ids) == 3


def test_over_window_memory_kept_verbatim(monkeypatch):
    long_text = "A" * 2500  # exceeds the 2000 window
    fake_mm, state = _mk_fake_mm([
        {"id": "1", "owner": "alice", "text": long_text},
        {"id": "2", "owner": "alice", "text": "junk"},   # co-occurring drop so save() runs
    ])

    async def fake_llm(url, model, messages, **k):
        return json.dumps({
            "keep": [{"id": "1", "text": "SHORT REWRITE", "category": "fact"}],
            "drop": [{"id": "2", "reason": "junk"}],
        })

    _patch(monkeypatch, fake_mm, lambda kind, owner=None: ("http://x", "m", {}), fake_llm)

    ok, success = asyncio.run(action_consolidate_memory(""))
    store = {m["id"]: m for m in state["saved"]}
    assert success
    assert "2" not in store, "dropped sibling removed"
    assert store["1"]["text"] == long_text, "over-window memory must keep original text verbatim"


def test_llm_failure_falls_back_per_group(monkeypatch):
    fake_mm, state = _mk_fake_mm([
        {"id": "a1", "owner": "alice", "text": "dup"},
        {"id": "a2", "owner": "alice", "text": "dup"},
        {"id": "b1", "owner": "bob", "text": "unique bob memory"},
    ])

    async def boom(url, model, messages, **k):
        raise RuntimeError("endpoint down")

    _patch(monkeypatch, fake_mm, lambda kind, owner=None: ("http://x", "m", {}), boom)

    ok, success = asyncio.run(action_consolidate_memory(""))   # must NOT raise
    ids = {m["id"] for m in state["saved"]}
    assert success
    assert "b1" in ids, "other owner untouched when one group's LLM fails"
    assert ("a1" in ids) ^ ("a2" in ids), "throwing group fell back to dedup"


def test_explicit_owner_scoping(monkeypatch):
    fake_mm, state = _mk_fake_mm([
        {"id": "a1", "owner": "alice", "text": "dup"},
        {"id": "a2", "owner": "alice", "text": "dup"},
        {"id": "b1", "owner": "bob", "text": "dup"},
    ])
    _patch(monkeypatch, fake_mm, lambda kind, owner=None: (None, None, None), None)

    ok, success = asyncio.run(action_consolidate_memory("alice"))
    ids = {m["id"] for m in state["saved"]}
    assert success
    assert "b1" in ids, "bob untouched by an alice-scoped run"
    assert len(ids) == 2 and ("a1" in ids) ^ ("a2" in ids)


def test_noop_raises_tasknoop_without_saving(monkeypatch):
    fake_mm, state = _mk_fake_mm([
        {"id": "x", "owner": "alice", "text": "only one memory"},
    ])
    _patch(monkeypatch, fake_mm, lambda kind, owner=None: (None, None, None), None)

    with pytest.raises(TaskNoop):
        asyncio.run(action_consolidate_memory(""))
    assert state["save_calls"] == 0, "no-op must not write the store"
