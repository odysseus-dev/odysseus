"""Memory consolidation must delete only memories the model explicitly drops.

The AI tidy path computed deletions as the complement of the model's `keep`
list, so any memory the model simply omitted (a common LLM lapse) was silently
deleted. The fix honors the explicit `drop` set, so an omitted memory survives.
"""
import asyncio
import json

import src.builtin_actions as ba



def test_omitted_memory_survives_only_explicit_drop(monkeypatch, tmp_path):
    import src.memory
    import src.llm_core
    import src.task_endpoint

    from src import constants, ai_interaction
    manager = src.memory.MemoryManager(str(tmp_path))
    manager.save([
            {"id": "a", "owner": "alice", "text": "Likes dark roast coffee", "category": "preference"},
            {"id": "b", "owner": "alice", "text": "Likes dark roast coffee too", "category": "preference"},
            {"id": "c", "owner": "alice", "text": "Lives in Cairo", "category": "fact"},
        ])
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)
    monkeypatch.setattr(
        src.task_endpoint, "resolve_task_candidates",
        lambda owner=None, **kwargs: [("http://x/v1", "model", {})],
    )

    async def fake_llm(_candidates, **kwargs):
        # Model keeps 'a', drops 'b', and OMITS 'c' entirely.
        return json.dumps({
            "keep": [{"id": "a", "text": "Likes dark roast coffee", "category": "preference"}],
            "drop": [{"id": "b", "reason": "duplicate of a"}],
        })

    monkeypatch.setattr(src.llm_core, "llm_call_async_with_fallback", fake_llm)

    msg, ok = asyncio.run(ba.action_consolidate_memory("alice"))

    assert ok, msg
    ids = {m["id"] for m in manager.load_all_for_update()}
    assert "c" in ids, "omitted memory must NOT be deleted"
    assert "a" in ids
    assert "b" not in ids, "explicitly dropped memory should be removed"
