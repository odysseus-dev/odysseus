"""Regression test for #2160: when the compaction summary LLM call fails,
maybe_compact must return the original messages unchanged, not the older half
dropped. Uses mock imports to avoid loading the full app stack."""

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
from src.context_compactor import maybe_compact


class TestCompactionSummaryFailure:
    """When the summary call raises, no conversation history may be lost.

    On success maybe_compact replaces the older half with a summary message.
    On failure it must degrade gracefully and hand back the original messages
    list unchanged, so the next turn (or trim_for_context) can handle length.
    Before the fix the except branch returned `system_msgs + recent`, silently
    discarding the older half while reporting was_compacted=False — the caller
    then treated a materially shorter list as a no-op."""

    def _run(self, messages, *, context_length=100):
        # Force compaction to trigger (pct over COMPACT_THRESHOLD) and make the
        # summary call fail, so the except branch runs. Stub everything so the
        # test is hermetic (no network, no real endpoint resolution).
        orig_ctx = cc.get_context_length
        orig_est = cc.estimate_tokens
        orig_call = cc.llm_call_async
        orig_resolve = cc.resolve_endpoint
        orig_update = cc._update_session_history

        async def _boom(*a, **k):
            raise RuntimeError("summary model down")

        cc.get_context_length = lambda url, model: context_length
        cc.estimate_tokens = lambda msgs: 10000  # well over the threshold
        cc.llm_call_async = _boom
        cc.resolve_endpoint = lambda *a, **k: (None, None, None)
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
            cc.estimate_tokens = orig_est
            cc.llm_call_async = orig_call
            cc.resolve_endpoint = orig_resolve
            cc._update_session_history = orig_update

    def _history(self):
        return [
            {"role": "system", "content": "PRESET"},
            {"role": "user", "content": "OLDER-1"},
            {"role": "assistant", "content": "OLDER-2"},
            {"role": "user", "content": "OLDER-3"},
            {"role": "assistant", "content": "RECENT-1"},
            {"role": "user", "content": "RECENT-2"},
            {"role": "assistant", "content": "RECENT-3"},
        ]

    def test_returns_original_messages_when_summary_fails(self):
        messages = self._history()
        out, _ctx, was_compacted = self._run(messages)

        # Nothing was actually compacted.
        assert was_compacted is False
        # The full original list comes back unchanged — including the older half.
        assert out == messages

    def test_older_messages_not_dropped_on_failure(self):
        messages = self._history()
        out, _ctx, _was = self._run(messages)

        contents = [m["content"] for m in out]
        # The older half must survive the failed summary call.
        for older in ("OLDER-1", "OLDER-2", "OLDER-3"):
            assert older in contents
