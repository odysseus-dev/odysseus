"""Memory consolidation must delete only memories the model explicitly drops.

The AI tidy path computed deletions as the complement of the model's `keep`
list, so any memory the model simply omitted (a common LLM lapse) was silently
deleted. The fix honors the explicit `drop` set, so an omitted memory survives.
"""
import asyncio
import json
from types import SimpleNamespace

import src.builtin_actions as ba


class _FakeMM:
    saved = None

    def __init__(self, *args, **kwargs):
        pass

    def load_all(self):
        return [
            {"id": "a", "owner": "alice", "text": "Likes dark roast coffee", "category": "preference"},
            {"id": "b", "owner": "alice", "text": "Likes dark roast coffee too", "category": "preference"},
            {"id": "c", "owner": "alice", "text": "Lives in Cairo", "category": "fact"},
        ]

    def save(self, entries):
        _FakeMM.saved = list(entries)


def test_omitted_memory_survives_only_explicit_drop(monkeypatch):
    import src.memory

    _FakeMM.saved = None
    monkeypatch.setattr(src.memory, "MemoryManager", _FakeMM)

    def fake_submit(archetype, payload, owner, **_kwargs):
        return SimpleNamespace(
            output={
                "keep": [{"id": "a", "text": "Likes dark roast coffee", "category": "preference"}],
                "drop": [{"id": "b", "reason": "duplicate of a"}],
            },
            audit={},
        )

    monkeypatch.setattr(ba, "submit_model_job", fake_submit, raising=False)

    msg, ok = asyncio.run(ba.action_consolidate_memory("alice"))

    assert ok, msg
    ids = {m["id"] for m in _FakeMM.saved}
    assert "c" in ids, "omitted memory must NOT be deleted"
    assert "a" in ids
    assert "b" not in ids, "explicitly dropped memory should be removed"
