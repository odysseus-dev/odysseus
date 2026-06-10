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


class TestTrimForContextProtected:
    """Protected messages (msg["_protected"], e.g. the active editor document)
    must keep their ORIGINAL interleaved position in the conversation: they are
    never hoisted into a block, never dropped, and never truncated.

    Regression for the bug where trim_for_context reassembled every return path
    as ``essential_system + protected_msgs + convo_msgs`` — yanking each
    protected message to a block right after the system messages and losing its
    position relative to the surrounding turns (agent_loop deliberately inserts
    the active document immediately before the user's question about it)."""

    @staticmethod
    def _index_of(msgs, marker):
        for i, m in enumerate(msgs):
            if marker in str(m.get("content", "")):
                return i
        return -1

    def test_protected_keeps_position_relative_to_convo(self):
        # Path A: dropping the extra (memory/RAG) system message is enough. The
        # protected doc sits between Q1 and the current question Q2 in the
        # original order and must NOT be hoisted ahead of Q1.
        messages = [
            {"role": "system", "content": "ESSENTIAL preset prompt."},
            {"role": "system", "content": "EXTRA " + ("x" * 12000)},  # droppable
            {"role": "user", "content": "Q1 first question " * 20},
            {"role": "user", "content": "DOC active document " * 80, "_protected": True},
            {"role": "user", "content": "Q2 current question " * 20},
        ]

        trimmed = trim_for_context(messages, context_length=4096, reserve_tokens=512)

        i_q1 = self._index_of(trimmed, "Q1 first question")
        i_doc = self._index_of(trimmed, "DOC active document")
        i_q2 = self._index_of(trimmed, "Q2 current question")
        assert i_q1 != -1 and i_doc != -1 and i_q2 != -1
        # Original relative order Q1 -> DOC -> Q2 must be preserved.
        assert i_q1 < i_doc < i_q2
        # Protected message preserved byte-for-byte (never truncated).
        assert any(
            m.get("_protected") and m.get("content") == messages[3]["content"]
            for m in trimmed
        )

    def test_protected_not_hoisted_ahead_of_tool_sequence(self):
        # An assistant tool_calls -> tool response pair precedes the protected
        # doc. Hoisting the doc to the front used to move it ahead of that pair;
        # it must stay after the tool response, and every tool message must
        # still immediately follow its assistant tool_calls (adjacency intact).
        messages = [
            {"role": "system", "content": "ESSENTIAL preset prompt."},
            {"role": "system", "content": "EXTRA " + ("x" * 12000)},  # droppable
            {"role": "user", "content": "please search the web"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "TOOLRESULT data"},
            {"role": "user", "content": "DOC active document " * 80, "_protected": True},
            {"role": "user", "content": "CURRENT question"},
        ]

        trimmed = trim_for_context(messages, context_length=4096, reserve_tokens=512)

        i_tool = self._index_of(trimmed, "TOOLRESULT data")
        i_doc = self._index_of(trimmed, "DOC active document")
        assert i_tool != -1 and i_doc != -1
        # Doc must remain AFTER the tool response (not hoisted ahead of it).
        assert i_doc > i_tool
        # Adjacency: every tool message immediately follows an assistant
        # message carrying tool_calls.
        for j, m in enumerate(trimmed):
            if m.get("role") == "tool":
                assert j > 0
                prev = trimmed[j - 1]
                assert prev.get("role") == "assistant" and prev.get("tool_calls")

    def test_protected_last_message_not_truncated(self):
        # Defensive: if a protected message is the last message and very large,
        # it must be returned intact (never truncated) and keep its position.
        huge_doc = "DOC " + ("d" * 30000)
        messages = [
            {"role": "system", "content": "ESSENTIAL preset prompt."},
            {"role": "user", "content": "normal question"},
            {"role": "user", "content": huge_doc, "_protected": True},
        ]

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        # Protected doc is still present, last, and byte-for-byte intact.
        assert trimmed[-1].get("_protected") is True
        assert trimmed[-1].get("content") == huge_doc

    def test_protected_in_old_zone_is_never_dropped(self):
        # >10 prior turns with the protected doc among the OLDEST. The drop pass
        # must step over it (never remove it) while dropping non-protected old
        # turns to fit the budget.
        messages = [{"role": "system", "content": "ESSENTIAL preset prompt."}]
        messages.append(
            {"role": "user", "content": "DOC active document " * 60, "_protected": True}
        )
        messages.extend(
            {"role": "user", "content": f"turn-{i} " + ("y" * 600)} for i in range(12)
        )
        messages.append({"role": "user", "content": "CURRENT final question"})

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        # Protected doc survived despite aggressive old-turn dropping.
        assert any(
            m.get("_protected") and "DOC active document" in str(m.get("content", ""))
            for m in trimmed
        )
        # The oldest non-protected turn was dropped.
        joined = "\n".join(str(m.get("content", "")) for m in trimmed)
        assert "turn-0 " not in joined
        # The current message is kept.
        assert "CURRENT final question" in joined


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

    def test_resolves_utility_endpoint_for_session_owner(self):
        calls = []
        messages = self._four_turn_history_with_tool_call()
        session = type("Session", (), {"owner": "alice"})()

        orig_ctx = cc.get_context_length
        orig_call = cc.llm_call_async
        orig_resolve = cc.resolve_endpoint
        orig_update = cc._update_session_history

        async def _fake_summary(*a, **k):
            return "compact summary text"

        def _fake_resolve(which, **kwargs):
            calls.append((which, kwargs.get("owner")))
            return (None, None, None)

        cc.get_context_length = lambda url, model: 500
        cc.llm_call_async = _fake_summary
        cc.resolve_endpoint = _fake_resolve
        cc._update_session_history = lambda *a, **k: None
        try:
            asyncio.run(
                maybe_compact(
                    session=session,
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

        assert calls == [("utility", "alice")]
