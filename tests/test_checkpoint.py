"""Tests for src/agent/checkpoint.py"""
from __future__ import annotations
from src.agent.checkpoint import ContextManager, CompactionResult


def test_context_manager_creation():
    cm = ContextManager(max_tokens=8192)
    assert cm.max_tokens == 8192
    assert cm.current_tokens == 0


def test_context_manager_tracks_tokens():
    cm = ContextManager(max_tokens=8192)
    cm.add_tokens(1000)
    assert cm.current_tokens == 1000
    cm.add_tokens(500)
    assert cm.current_tokens == 1500


def test_needs_compaction():
    cm = ContextManager(max_tokens=8192, compaction_threshold=0.8)
    cm.add_tokens(6000)
    assert cm.needs_compaction() is False
    cm.add_tokens(1000)
    assert cm.needs_compaction() is True


def test_needs_checkpoint_rebuild():
    cm = ContextManager(max_tokens=8192, rebuild_threshold=0.95)
    cm.add_tokens(7000)
    assert cm.needs_checkpoint_rebuild() is False
    cm.add_tokens(1000)
    assert cm.needs_checkpoint_rebuild() is True


def test_compact_messages_reduces_tokens():
    cm = ContextManager(max_tokens=8192)
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there! How can I help?"},
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "Let me check... [tool output: sunny 25C] The weather is sunny."},
        {"role": "user", "content": "Thanks, now do something else."},
    ]
    cm.add_tokens(7000)
    result = cm.compact_messages(messages)
    assert isinstance(result, CompactionResult)
    assert result.removed_count >= 0
    assert len(result.messages) <= len(messages)


def test_preserve_recent_messages():
    cm = ContextManager(max_tokens=8192)
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Old message 1"},
        {"role": "assistant", "content": "Old reply 1"},
        {"role": "user", "content": "Recent message"},
        {"role": "assistant", "content": "Recent reply"},
    ]
    result = cm.compact_messages(messages, keep_recent=2)
    roles = [m["role"] for m in result.messages]
    assert roles[-1] == "assistant"
    assert roles[-2] == "user"


def test_compaction_result():
    result = CompactionResult(messages=[], removed_count=3, tokens_saved=2000, summary="Conversation about weather and tasks.")
    assert result.removed_count == 3
    assert result.tokens_saved == 2000