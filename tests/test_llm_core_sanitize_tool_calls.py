"""Regression test: _sanitize_llm_messages must not drop the no-prose
assistant tool-call message.

Commit cb13d09 changed _append_tool_results so that when the model emits ONLY
tool calls (no prose), the follow-up assistant message carries content=None
(JSON null) instead of "" — Google Gemini's OpenAI-compatible endpoint and
Ollama reject tool_calls alongside an empty-string content with HTTP 400.

But _sanitize_llm_messages drops None values (`v is not None`) and then required
"content" to be present, so it dropped that assistant message entirely — leaving
a dangling role:"tool" result with no parent tool_calls. That re-breaks native
tool-calling on the follow-up round (and regresses providers that accepted ""
before, since the message is now removed instead of sent). cb13d09's tests only
exercised _append_tool_results in isolation, so the sanitizer interaction went
uncaught.

This test drives the real producer (_append_tool_results) into the sanitizer.
"""
import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing (mirrors tests/test_agent_loop.py).
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'src.agent_tools', 'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.agent_loop import _append_tool_results
from src.llm_core import _sanitize_llm_messages


def test_sanitize_keeps_no_prose_assistant_tool_call_message():
    native = [{"id": "call_1", "name": "web_fetch",
               "arguments": '{"url": "https://example.com"}'}]
    messages = []
    # Model emitted only a tool call, no prose -> _append_tool_results sets the
    # assistant message's content to None (cb13d09).
    _append_tool_results(messages, "", native, [{}], ["page text"],
                         used_native=True, round_num=1)
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] is None  # producer contract (cb13d09)

    out = _sanitize_llm_messages(messages)
    roles = [m["role"] for m in out]

    # The assistant tool-call message must survive sanitization, otherwise the
    # following tool result is dangling and the provider call breaks.
    assert "assistant" in roles, (
        "sanitize dropped the no-prose assistant tool-call message; the tool "
        "result is left dangling"
    )
    assistant = next(m for m in out if m["role"] == "assistant")
    assert assistant.get("tool_calls"), "assistant tool_calls were lost"
    # Faithful to cb13d09: keep explicit JSON null rather than an omitted key.
    assert assistant["content"] is None
    # Pairing intact: the tool result references the assistant's tool_call id.
    tool = next(m for m in out if m["role"] == "tool")
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
