"""Regression test: reasoning_content must survive message sanitization.

_append_tool_results attaches reasoning_content to the assistant message so
DeepSeek thinking-mode follow-up requests aren't rejected; the sanitizer used to
strip it because it wasn't in the allow-list.
"""
from src.llm_core import _sanitize_llm_messages


def test_reasoning_content_preserved():
    msgs = [{
        "role": "assistant",
        "content": "the answer",
        "reasoning_content": "step-by-step thinking",
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "x", "arguments": "{}"}}
        ],
        "odysseus_internal": "should be dropped",
    }]
    out = _sanitize_llm_messages(msgs)
    assert len(out) == 1
    assert out[0]["reasoning_content"] == "step-by-step thinking"
    assert out[0]["tool_calls"][0]["id"] == "c1"
    assert "odysseus_internal" not in out[0]


def test_unknown_keys_still_stripped():
    out = _sanitize_llm_messages([{"role": "user", "content": "hi", "foo": "bar"}])
    assert out == [{"role": "user", "content": "hi"}]
