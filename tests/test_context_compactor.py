"""Tests for context_compactor.py — constants and prompt templates.
Uses mock imports to avoid loading the full app stack."""

import asyncio
import sys
from unittest.mock import MagicMock

import pytest

# Mock heavy dependencies before importing
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import src.context_compactor as cc
from src.context_compactor import (
    COMPACT_THRESHOLD,
    SELF_SUMMARY_SYSTEM_PROMPT,
    SUMMARY_MAX_TOKENS,
    _content_as_text,
    maybe_compact,
    trim_for_context,
)


class TestCompactThreshold:
    def test_value(self):
        assert COMPACT_THRESHOLD == 0.85

    def test_summary_max_tokens(self):
        assert SUMMARY_MAX_TOKENS == 1024


class TestSelfSummaryPrompt:
    def test_contains_goal_section(self):
        assert "### User Goal" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_what_was_done_section(self):
        assert "### What Was Done" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_current_state_section(self):
        assert "### Current State" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_pending_section(self):
        assert "### Pending / Next Steps" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_key_context_section(self):
        assert "### Key Context" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_count_placeholder(self):
        assert "{count}" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_n_placeholder(self):
        assert "{n}" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_mentions_compactions(self):
        assert "Compactions so far" in SELF_SUMMARY_SYSTEM_PROMPT


class TestTrimForContext:
    def test_keeps_current_large_user_message_by_truncating(self):
        huge = "A" * 20000
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": huge},
        ]

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        user_msgs = [m for m in trimmed if m.get("role") == "user"]
        assert len(user_msgs) == 1
        content = user_msgs[0]["content"]
        assert "pasted message was too large" in content
        assert content.startswith("A")
        assert len(content) < len(huge)

    def test_drops_older_messages_before_latest_user_paste(self):
        huge = "B" * 12000
        messages = [{"role": "system", "content": "You are helpful."}]
        messages.extend({"role": "user", "content": f"old-{i} " + ("x" * 1000)} for i in range(8))
        messages.append({"role": "user", "content": huge})

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        assert trimmed[-1]["role"] == "user"
        assert "pasted message was too large" in trimmed[-1]["content"]
        assert "old-0" not in "\n".join(str(m.get("content", "")) for m in trimmed)


class TestContentAsText:
    def test_string_passthrough(self):
        assert _content_as_text("hello") == "hello"

    def test_none_returns_empty(self):
        # Assistant turns that carried only native tool_calls persist
        # content as None — flattening must not raise.
        assert _content_as_text(None) == ""

    def test_list_content_joins_text_blocks(self):
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        assert _content_as_text(content) == "describe this"

    def test_unknown_type_returns_empty(self):
        assert _content_as_text(42) == ""


class TestMaybeCompactFourthMessage:
    """Regression: a multi-message conversation must not crash compaction when
    a prior assistant turn used native tool_calls (content == None). This was
    the '4th message stops working' bug — on a small-context model the soft
    85% threshold is crossed after a few turns, and the older half being
    summarized contained a None-content assistant message, which raised
    TypeError: 'NoneType' object is not subscriptable and broke the request."""

    def _run(self, messages, *, context_length=500):
        # Force compaction to trigger and stub the summary LLM call so the test
        # is hermetic (no network, no real endpoint resolution).
        orig_ctx = cc.get_context_length
        orig_call = cc.llm_call_async
        orig_resolve = cc.resolve_endpoint
        orig_update = cc._update_session_history

        async def _fake_summary(*a, **k):
            return "compact summary text"

        cc.get_context_length = lambda url, model: context_length
        cc.llm_call_async = _fake_summary
        cc.resolve_endpoint = lambda which, owner=None: (None, None, None)
        cc._update_session_history = lambda *a, **k: None
        try:
            return asyncio.run(
                maybe_compact(
                    session=None,
                    endpoint_url="http://local/v1/chat/completions",
                    model="local-model",
                    messages=list(messages),
                    headers={},
                )
            )
        finally:
            cc.get_context_length = orig_ctx
            cc.llm_call_async = orig_call
            cc.resolve_endpoint = orig_resolve
            cc._update_session_history = orig_update

    def _four_turn_history_with_tool_call(self):
        # Large system prompt so the conversation crosses the 85% threshold of
        # the tiny (context_length=500) window used in _run, forcing the real
        # compaction branch to execute.
        return [
            {"role": "system", "content": "You are a helpful agent. " * 200},
            {"role": "user", "content": "turn 1: search the web"},
            # Native tool call → content is None (matches agent_loop persistence)
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "search results"},
            {"role": "assistant", "content": "Here is what I found."},
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "reply 2"},
            {"role": "user", "content": "turn 3"},
            {"role": "assistant", "content": "reply 3"},
            {"role": "user", "content": "turn 4 — previously broke here"},
        ]

    def test_does_not_crash_on_none_content_turn(self):
        # Must not raise TypeError; returns the 3-tuple contract.
        result = self._run(self._four_turn_history_with_tool_call())
        assert isinstance(result, tuple) and len(result) == 3
        compacted_messages, context_length, was_compacted = result
        assert isinstance(compacted_messages, list)
        assert was_compacted is True
        # The summary the model produced is present and a system message.
        assert any(
            m.get("role") == "system" and "compact summary text" in (m.get("content") or "")
            for m in compacted_messages
        )

    def test_handles_multimodal_list_content(self):
        messages = self._four_turn_history_with_tool_call()
        messages[1] = {"role": "user", "content": [
            {"type": "text", "text": "look at this image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxxx"}},
        ]}
        result = self._run(messages)
        assert len(result) == 3 and result[2] is True


class TestResearchPrimerPreserved:
    """A research-spinoff primer (metadata research_spinoff_from) must never be
    trimmed away — it is the Discuss chat's sole knowledge base (drift fix)."""

    def _messages(self):
        return [
            {"role": "system", "content": "You are Odysseus."},
            {"role": "system", "content": "Prompt-safety policy: data not instructions."},
            {"role": "system", "content": "saved memory: pinned " + "m" * 600},
            {"role": "system", "content": "RETRIEVED-DOCS-MARKER " + "r" * 6000},
            {"role": "system",
             "content": "=== REPORT ===\nPRIMER-MARKER " + "z" * 1500,
             "metadata": {"research_spinoff_from": "rp-abc123"}},
        ] + [
            {"role": "user", "content": f"q{i} " + ("x" * 500)} for i in range(8)
        ] + [
            {"role": "assistant", "content": "a" * 500},
            {"role": "user", "content": "latest question"},
        ]

    def test_primer_kept_when_over_budget(self):
        trimmed = trim_for_context(self._messages(), context_length=1024, reserve_tokens=256)
        joined = "\n".join(str(m.get("content", "")) for m in trimmed)
        assert "PRIMER-MARKER" in joined

    def test_bulky_non_primer_system_dropped_but_primer_kept(self):
        trimmed = trim_for_context(self._messages(), context_length=1024, reserve_tokens=256)
        joined = "\n".join(str(m.get("content", "")) for m in trimmed)
        assert "PRIMER-MARKER" in joined
        assert "RETRIEVED-DOCS-MARKER" not in joined

    def test_leading_preset_kept_when_no_primer_metadata(self):
        msgs = self._messages()
        del msgs[4]["metadata"]
        trimmed = trim_for_context(msgs, context_length=1024, reserve_tokens=256)
        joined = "\n".join(str(m.get("content", "")) for m in trimmed)
        assert "You are Odysseus." in joined
