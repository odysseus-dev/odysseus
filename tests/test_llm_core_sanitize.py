"""Regression tests for message sanitization (PR #968 / DeepSeek thinking mode).

``_append_tool_results`` attaches ``reasoning_content`` to the assistant message
so DeepSeek thinking-mode follow-up requests aren't rejected. The sanitizer used
to strip it because it wasn't in the allow-list.

Preserving it *globally* is unsafe: ``_sanitize_llm_messages`` runs before the
provider-specific payload is built, so a stricter OpenAI-compatible API could
reject the unknown ``reasoning_content`` key. These tests pin the scoped
behaviour the maintainer asked for: the field is kept only for endpoints that
require it (DeepSeek) and stripped for generic providers.
"""
from src.llm_core import (
    _sanitize_llm_messages,
    _endpoint_requires_reasoning_content,
)


def _assistant_with_reasoning():
    return [{
        "role": "assistant",
        "content": "the answer",
        "reasoning_content": "step-by-step thinking",
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "x", "arguments": "{}"}}
        ],
        "odysseus_internal": "should be dropped",
    }]


def test_reasoning_content_preserved_for_deepseek():
    # DeepSeek-style follow-up: keep_reasoning=True keeps the field.
    out = _sanitize_llm_messages(_assistant_with_reasoning(), keep_reasoning=True)
    assert len(out) == 1
    assert out[0]["reasoning_content"] == "step-by-step thinking"
    assert out[0]["tool_calls"][0]["id"] == "c1"
    assert "odysseus_internal" not in out[0]


def test_reasoning_content_stripped_for_generic_provider():
    # Default (generic / strict OpenAI-compatible provider): the extra key must
    # NOT be leaked, while the rest of the message is preserved intact.
    out = _sanitize_llm_messages(_assistant_with_reasoning())
    assert len(out) == 1
    assert "reasoning_content" not in out[0]
    assert out[0]["content"] == "the answer"
    assert out[0]["tool_calls"][0]["id"] == "c1"
    assert "odysseus_internal" not in out[0]


def test_unknown_keys_still_stripped():
    out = _sanitize_llm_messages([{"role": "user", "content": "hi", "foo": "bar"}])
    assert out == [{"role": "user", "content": "hi"}]


def test_endpoint_requires_reasoning_content_routing():
    # DeepSeek requires the echo; generic OpenAI-compatible endpoints do not.
    assert _endpoint_requires_reasoning_content("https://api.deepseek.com/v1") is True
    assert _endpoint_requires_reasoning_content("https://api.openai.com/v1") is False
    assert _endpoint_requires_reasoning_content("http://localhost:11434/v1") is False
    # Look-alike host must not be misclassified as DeepSeek.
    assert _endpoint_requires_reasoning_content("https://api.deepseek.com.evil.test/v1") is False
