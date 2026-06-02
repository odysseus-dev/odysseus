"""Regression tests for action_consolidate_memory multi-owner AI tidy.

Before the fix, allow_ai_tidy = len(memory_owners) <= 1 meant the AI phase
was permanently skipped for housekeeping sweeps (owner="") in any multi-owner
install.  The fix loops over owner groups and calls the AI once per group.
"""
import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub module-level imports that src.builtin_actions needs at import time.
for _name, _attrs in [
    ("src.auth_helpers", {"owner_filter": MagicMock()}),
    ("core.platform_compat", {"IS_WINDOWS": False, "find_bash": MagicMock(return_value=None)}),
]:
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_m, _k, _v)
        sys.modules[_name] = _m

from src.builtin_actions import action_consolidate_memory, TaskNoop  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem(id_, text, owner):
    return {"id": id_, "text": text, "owner": owner, "category": "fact"}


def _keep_response(memories):
    """AI response that keeps all supplied memories unchanged."""
    return json.dumps({
        "keep": [{"id": m["id"], "text": m["text"], "category": m["category"]} for m in memories],
        "drop": [],
    })


def _run(owner, memories, ai_responses):
    """Run action_consolidate_memory with fully stubbed dependencies.

    Returns (result_or_None, llm_call_count, saved_memories).
    result is None when TaskNoop was raised.
    """
    mock_mgr = MagicMock()
    mock_mgr.load_all.return_value = list(memories)
    saved = []
    mock_mgr.save.side_effect = lambda mems: saved.extend(mems)

    mock_llm = AsyncMock(side_effect=list(ai_responses))

    with patch.dict(sys.modules, {
        "src.constants": types.SimpleNamespace(DATA_DIR="/fake"),
        "src.memory": types.SimpleNamespace(MemoryManager=MagicMock(return_value=mock_mgr)),
        "src.endpoint_resolver": types.SimpleNamespace(
            resolve_endpoint=MagicMock(return_value=("http://fake", "test-model", {}))
        ),
        "src.llm_core": types.SimpleNamespace(llm_call_async=mock_llm),
        "src.text_helpers": types.SimpleNamespace(strip_think=lambda s, **kw: s),
    }):
        try:
            result = asyncio.run(action_consolidate_memory(owner=owner))
        except TaskNoop:
            result = None

    return result, mock_llm.await_count, saved


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_housekeeping_calls_ai_once_per_owner_group():
    """owner="" with 2 owners → 2 AI calls, one isolated per group."""
    memories = [
        _mem("a1", "Alice prefers dark mode", "alice"),
        _mem("a2", "Alice likes Python", "alice"),
        _mem("b1", "Bob prefers light mode", "bob"),
        _mem("b2", "Bob likes Go", "bob"),
    ]
    alice_mems = [m for m in memories if m["owner"] == "alice"]
    bob_mems = [m for m in memories if m["owner"] == "bob"]

    _, call_count, _ = _run(
        owner="",
        memories=memories,
        ai_responses=[_keep_response(alice_mems), _keep_response(bob_mems)],
    )

    assert call_count == 2, f"Expected 2 AI calls (one per owner group), got {call_count}"


def test_housekeeping_removes_within_owner_group():
    """AI drops a memory inside alice's group; bob's group is left intact."""
    memories = [
        _mem("a1", "Alice prefers dark mode", "alice"),
        _mem("a2", "Alice prefers dark — duplicate", "alice"),
        _mem("b1", "Bob likes Go", "bob"),
        _mem("b2", "Bob likes Rust", "bob"),
    ]
    alice_response = json.dumps({
        "keep": [{"id": "a1", "text": "Alice prefers dark mode", "category": "fact"}],
        "drop": [{"id": "a2", "reason": "duplicate"}],
    })
    bob_mems = [m for m in memories if m["owner"] == "bob"]

    result, call_count, saved = _run(
        owner="",
        memories=memories,
        ai_responses=[alice_response, _keep_response(bob_mems)],
    )

    assert call_count == 2
    assert result is not None and result[1] is True
    assert "removed 1" in result[0]
    saved_ids = {m["id"] for m in saved}
    assert "a1" in saved_ids
    assert "a2" not in saved_ids, "a2 should have been dropped by AI"
    assert {"b1", "b2"}.issubset(saved_ids)


def test_housekeeping_noop_when_ai_changes_nothing():
    """TaskNoop raised when AI makes no changes across all groups."""
    memories = [
        _mem("a1", "Alice note", "alice"),
        _mem("a2", "Alice other note", "alice"),
    ]

    result, call_count, saved = _run(
        owner="",
        memories=memories,
        ai_responses=[_keep_response(memories)],
    )

    assert result is None  # TaskNoop was raised
    assert call_count == 1
    assert saved == [], "save() must not be called when there are no changes"


def test_named_owner_ai_prompt_excludes_other_owners_memories():
    """owner="alice" → the AI prompt must contain only alice's memory IDs."""
    memories = [
        _mem("a1", "Alice note", "alice"),
        _mem("a2", "Alice other note", "alice"),
        _mem("b1", "Bob note", "bob"),
    ]
    captured_prompts = []

    async def capturing_llm(**kwargs):
        captured_prompts.append(kwargs["messages"][0]["content"])
        alice_mems = [m for m in memories if m["owner"] == "alice"]
        return _keep_response(alice_mems)

    mock_mgr = MagicMock()
    mock_mgr.load_all.return_value = list(memories)
    mock_mgr.save.return_value = None

    with patch.dict(sys.modules, {
        "src.constants": types.SimpleNamespace(DATA_DIR="/fake"),
        "src.memory": types.SimpleNamespace(MemoryManager=MagicMock(return_value=mock_mgr)),
        "src.endpoint_resolver": types.SimpleNamespace(
            resolve_endpoint=MagicMock(return_value=("http://fake", "test-model", {}))
        ),
        "src.llm_core": types.SimpleNamespace(llm_call_async=capturing_llm),
        "src.text_helpers": types.SimpleNamespace(strip_think=lambda s, **kw: s),
    }):
        try:
            asyncio.run(action_consolidate_memory(owner="alice"))
        except TaskNoop:
            pass

    assert len(captured_prompts) == 1, "Expected exactly one AI call for owner='alice'"
    prompt = captured_prompts[0]
    assert "b1" not in prompt, "Bob's memory ID must not appear in Alice's AI prompt"
    assert "a1" in prompt and "a2" in prompt
