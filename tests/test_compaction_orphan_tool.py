"""maybe_compact must not emit an orphan tool message.

When the compaction split lands mid-tool-batch, the kept `recent` half begins
with a role:"tool" message whose assistant tool_calls parent is in the
summarized `older` half. The compacted list (summary + recent) then leads
with an orphan tool message, which OpenAI-compatible providers reject. The
post-compaction history is usually under budget, so trim_for_context's
pre-send sanitize never runs — compaction must sanitize its own output.
"""
import asyncio

import pytest

import src.context_compactor as cc


def _run(messages, monkeypatch):
    async def _fake_llm(*a, **k):
        return "SUMMARY TEXT"
    monkeypatch.setattr(cc, "estimate_tokens", lambda msgs: 100000)
    monkeypatch.setattr(cc, "get_context_length", lambda url, model: 1000)
    monkeypatch.setattr(cc, "resolve_endpoint", lambda *a, **k: ("", "", {}))
    monkeypatch.setattr(cc, "llm_call_async", _fake_llm)
    monkeypatch.setattr(cc, "_update_session_history", lambda *a, **k: None)
    return asyncio.run(cc.maybe_compact(object(), "http://x", "m", messages))


def test_orphan_tool_dropped_after_compaction(monkeypatch):
    # Tool batch straddles the midpoint: msg index 3 (assistant tool_calls) is
    # in `older`, index 4 (its tool response) starts `recent`.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "A", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "A", "content": "TOOLRESULT"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    out, _ctx, compacted = _run(messages, monkeypatch)
    assert compacted is True
    # the orphan tool response must not survive in the compacted output
    assert all(m.get("role") != "tool" for m in out),         [m.get("role") for m in out]
    assert all(m.get("content") != "TOOLRESULT" for m in out)


def test_valid_tool_batch_kept(monkeypatch):
    # A self-contained tool batch fully inside `recent` must be preserved.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a-mid"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "B", "type": "function", "function": {"name": "y", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "B", "content": "KEEP"},
        {"role": "assistant", "content": "a3"},
    ]
    out, _ctx, compacted = _run(messages, monkeypatch)
    assert compacted is True
    assert any(m.get("content") == "KEEP" for m in out)
