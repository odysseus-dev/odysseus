import json
import sys

import pytest


def _import_consolidate_action():
    mod = sys.modules.get("src.builtin_actions")
    if mod is not None and not hasattr(mod, "action_consolidate_memory"):
        sys.modules.pop("src.builtin_actions", None)
        if "src" in sys.modules and hasattr(sys.modules["src"], "builtin_actions"):
            delattr(sys.modules["src"], "builtin_actions")
    from src.builtin_actions import action_consolidate_memory

    return action_consolidate_memory


def _write_memories(tmp_path, memories):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "memory.json").write_text(json.dumps(memories), encoding="utf-8")
    return data_dir


def _read_memories(data_dir):
    return json.loads((data_dir / "memory.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_consolidate_memory_empty_owner_treats_each_owner_separately(monkeypatch, tmp_path):
    from src import constants
    from src import llm_core
    from src import task_endpoint
    action_consolidate_memory = _import_consolidate_action()

    long_alice_text = "Alice private project context. " + ("A" * 2200)
    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "alice-long", "owner": "alice", "text": long_alice_text, "category": "project"},
            {"id": "alice-short", "owner": "alice", "text": "Alice likes quiet summaries.", "category": "preference"},
            {"id": "bob-keep", "owner": "bob", "text": "Bob secret deployment note.", "category": "project"},
            {"id": "bob-drop", "owner": "bob", "text": "Bob secret deployment note duplicate.", "category": "project"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        task_endpoint,
        "resolve_task_candidates",
        lambda *args, **kwargs: [("http://llm", "model", {})],
    )

    prompts = []

    async def fake_llm_call_async(_candidates, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        if "alice-long" in prompt:
            assert "bob-keep" not in prompt
            return json.dumps(
                {
                    "keep": [
                        {"id": "alice-long", "text": "TRUNCATED REWRITE", "category": "project"},
                        {"id": "alice-short", "text": "Alice likes concise summaries.", "category": "preference"},
                    ],
                    "drop": [],
                }
            )
        assert "bob-keep" in prompt
        assert "alice-long" not in prompt
        return json.dumps(
            {
                "keep": [{"id": "bob-keep", "text": "Bob secret deployment note.", "category": "project"}],
                "drop": [{"id": "bob-drop", "reason": "duplicate"}],
            }
        )

    monkeypatch.setattr(llm_core, "llm_call_async_with_fallback", fake_llm_call_async)

    message, ok = await action_consolidate_memory("")

    assert ok is True
    assert "removed 1" in message
    assert len(prompts) == 2
    saved = {m["id"]: m for m in _read_memories(data_dir)}
    assert set(saved) == {"alice-long", "alice-short", "bob-keep"}
    assert saved["alice-long"]["text"] == long_alice_text
    assert saved["alice-short"]["text"] == "Alice likes concise summaries."


@pytest.mark.asyncio
async def test_consolidate_memory_specific_owner_does_not_absorb_ownerless_rows(monkeypatch, tmp_path):
    from src import constants
    from src import endpoint_resolver
    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "alice-1", "owner": "alice", "text": "Alice likes local models.", "category": "preference"},
            {"id": "alice-2", "owner": "alice", "text": "Alice likes local models.", "category": "preference"},
            {"id": "legacy", "text": "Alice likes local models.", "category": "preference"},
            {"id": "bob-1", "owner": "bob", "text": "Bob likes hosted models.", "category": "preference"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda *args, **kwargs: ("", "", {}))

    message, ok = await action_consolidate_memory("alice")

    assert ok is True
    assert "Removed 1 duplicate" in message
    saved = {m["id"]: m for m in _read_memories(data_dir)}
    assert set(saved) == {"alice-1", "legacy", "bob-1"}
    assert "owner" not in saved["legacy"]
    assert saved["bob-1"]["owner"] == "bob"
