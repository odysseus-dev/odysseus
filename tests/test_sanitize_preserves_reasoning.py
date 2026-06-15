"""Regression: _sanitize_llm_messages must preserve reasoning_content.

Providers like Moonshot (Kimi K2.5/K2.6) require reasoning_content on
assistant tool-call messages. Stripping it causes HTTP 400 in multi-turn
tool calling when thinking mode is enabled.

See: https://github.com/pewdiepie-archdaemon/odysseus/issues/3118
"""
import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing.
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'src.agent_tools', 'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.llm_core import _sanitize_llm_messages  # noqa: E402


def test_sanitize_preserves_reasoning_content_on_assistant_tool_call():
    """reasoning_content must survive sanitization.

    Providers like Moonshot (Kimi K2.5/K2.6) require reasoning_content to be
    present on assistant tool-call messages in multi-turn conversations.  Stripping
    it causes HTTP 400: "thinking is enabled but reasoning_content is missing in
    assistant tool call message at index N".
    """
    messages = [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "Let me think about which tool to use...",
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "web_search", "arguments": '{"q":"test"}'}},
            ],
        },
        {
            "role": "tool",
            "content": "search results here",
            "tool_call_id": "call_1",
        },
    ]

    out = _sanitize_llm_messages(messages)
    assistant = next(m for m in out if m["role"] == "assistant")

    assert assistant.get("reasoning_content") == "Let me think about which tool to use...", (
        "reasoning_content was stripped during sanitization; Moonshot/Kimi API will "
        "reject this as HTTP 400 in multi-turn tool calling"
    )
    assert assistant.get("tool_calls"), "tool_calls were lost"
    assert assistant["content"] is None


def test_sanitize_preserves_reasoning_content_on_plain_assistant():
    """reasoning_content also survives on assistant messages without tool_calls."""
    messages = [
        {
            "role": "assistant",
            "content": "Here is my answer.",
            "reasoning_content": "Internal reasoning that should be kept for the next turn.",
        },
    ]

    out = _sanitize_llm_messages(messages)
    assert len(out) == 1
    assert out[0]["reasoning_content"] == "Internal reasoning that should be kept for the next turn."


def test_sanitize_strips_unknown_fields_but_keeps_reasoning_content():
    """Only allowed fields survive; reasoning_content is now in the allow-list."""
    messages = [
        {
            "role": "assistant",
            "content": "reply",
            "reasoning_content": "thinking text",
            "some_custom_field": "should be stripped",
            "another_meta": 123,
        },
    ]

    out = _sanitize_llm_messages(messages)
    assert len(out) == 1
    assert "reasoning_content" in out[0], "reasoning_content was stripped"
    assert "some_custom_field" not in out[0], "custom field was not stripped"
    assert "another_meta" not in out[0], "custom field was not stripped"
