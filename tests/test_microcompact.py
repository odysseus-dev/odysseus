"""Old tool results are stubbed before an LLM compaction summary."""
import pytest

from src import context_compactor as cc
from src.context_compactor import microcompact_tool_results

STUB = "[tool result cleared for context"


def _tool(text):
    return {"role": "tool", "tool_call_id": "call", "content": text}


def _conversation(count, chars=400):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "start"},
    ]
    for i in range(count):
        messages.append(_tool("x" * chars + str(i)))
    return messages


def test_keeps_the_last_five_tool_results():
    messages = [{"role": "user", "content": "start"}]
    for i in range(8):
        messages.append(_tool("x" * 400 + str(i)))
    out = microcompact_tool_results(messages, keep_recent_tools=5)
    tools = [m for m in out if m["role"] == "tool"]
    assert len(tools) == 8
    assert all(str(m["content"]).startswith(STUB) for m in tools[:3])
    assert all(not str(m["content"]).startswith(STUB) for m in tools[3:])
    assert out[0]["content"] == "start"


def test_no_tool_messages_returns_the_same_list():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert microcompact_tool_results(messages) is messages


def test_fewer_tools_than_keep_returns_the_same_list():
    messages = _conversation(3)
    assert microcompact_tool_results(messages, keep_recent_tools=5) is messages


def test_zero_keep_clears_every_tool():
    messages = _conversation(4)
    out = microcompact_tool_results(messages, keep_recent_tools=0)
    tools = [m for m in out if m["role"] == "tool"]
    assert all(str(m["content"]).startswith(STUB) for m in tools)


def test_short_results_are_left_alone():
    messages = [{"role": "user", "content": "start"}] + [_tool("short") for _ in range(8)]
    assert microcompact_tool_results(messages, keep_recent_tools=2) is messages


def test_second_pass_is_idempotent():
    messages = _conversation(8)
    first = microcompact_tool_results(messages, keep_recent_tools=5)
    assert first is not messages
    second = microcompact_tool_results(first, keep_recent_tools=5)
    assert second is first
    # One marker per stubbed result, never a stub of a stub.
    joined = "\n".join(
        str(m["content"]) for m in second if m["role"] == "tool"
    )
    assert joined.count(STUB) == 3


def test_multimodal_content_is_flattened_into_the_stub():
    messages = [{"role": "user", "content": "start"}] + [
        _tool([{"type": "text", "text": "z" * 500}]) for _ in range(6)
    ]
    out = microcompact_tool_results(messages, keep_recent_tools=2)
    stubbed = [m for m in out if m["role"] == "tool" and m.get("_microcompacted")]
    assert len(stubbed) == 4
    assert all(isinstance(m["content"], str) for m in stubbed)


@pytest.mark.asyncio
async def test_maybe_compact_uses_microcompact_before_summary(monkeypatch):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(12):
        messages.append({"role": "user", "content": f"ask-{i}"})
        messages.append(_tool("OUT-" + ("y" * 4000)))

    monkeypatch.setattr(cc, "auto_compact_threshold_percent", lambda: 85)

    async def _fail_summary(*args, **kwargs):
        raise AssertionError("the LLM summary must not run when microcompact suffices")

    monkeypatch.setattr(cc, "llm_call_async", _fail_summary)

    out, context_length, changed = await cc.maybe_compact(
        session=None,
        endpoint_url="https://example.invalid/v1",
        model="test-model",
        messages=messages,
        persist=False,
        context_length=8000,
    )
    assert changed is True
    assert context_length == 8000
    assert any(m.get("_microcompacted") for m in out if m.get("role") == "tool")
