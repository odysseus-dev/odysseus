"""Tests for src/agent/context_rebuild.py"""
from __future__ import annotations
import os
import tempfile
import pytest
from src.agent.context_rebuild import ContextRebuilder


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_context_rebuilder_creation(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    assert rebuilder.base_dir == tmp_dir


def test_context_rebuilder_needs_rebuild_false(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    assert rebuilder.needs_rebuild() is False


def test_context_rebuilder_needs_rebuild_true(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    from src.agent.memory_persist import CheckpointStore
    store = CheckpointStore(tmp_dir)
    store.update_section("active_intent", "Fix the bug")
    assert rebuilder.needs_rebuild() is True


def test_context_rebuilder_build_rebuild_message(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    from src.agent.memory_persist import CheckpointStore, MemoryStore
    cs = CheckpointStore(tmp_dir)
    cs.update_section("active_intent", "Fix the bug")
    cs.update_section("next_action", "Run tests")
    ms = MemoryStore(tmp_dir)
    ms.write("## Rules\n- Use Python 3.9+")
    message = rebuilder.build_rebuild_message()
    assert "Fix the bug" in message
    assert "Run tests" in message
    assert "Python 3.9+" in message


def test_context_rebuilder_build_system_message(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    from src.agent.memory_persist import CheckpointStore
    cs = CheckpointStore(tmp_dir)
    cs.update_section("active_intent", "Fix the bug")
    msg = rebuilder.build_system_message()
    assert msg["role"] == "system"
    assert "Fix the bug" in msg["content"]


def test_context_rebuilder_compact_messages(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm fine, thanks!"},
    ]
    compacted = rebuilder.compact_messages(messages, keep_recent=2)
    assert len(compacted) <= len(messages)
    assert compacted[-1]["role"] == "assistant"
