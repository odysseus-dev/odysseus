"""Tests for src/agent/checkpoint_writer.py"""
from __future__ import annotations
import os
import tempfile
import pytest
from src.agent.checkpoint_writer import CheckpointWriter


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_checkpoint_writer_creation(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    assert writer.base_dir == tmp_dir


def test_checkpoint_writer_write_checkpoint(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_checkpoint(
        active_intent="User wants to fix the bug",
        next_action="Run tests",
        current_work="Investigating the root cause",
    )
    content = writer.checkpoint_store.read()
    assert "User wants to fix the bug" in content
    assert "Run tests" in content


def test_checkpoint_writer_write_memory(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_memory(
        project_context="Odysseus is a self-hosted AI chat app",
        rules=["Use Python 3.9+", "Follow existing patterns"],
    )
    content = writer.memory_store.read()
    assert "Odysseus" in content
    assert "Python 3.9+" in content


def test_checkpoint_writer_write_notes(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_note("Important finding about the architecture")
    content = writer.notes_store.read()
    assert "Important finding" in content


def test_checkpoint_writer_write_task_progress(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_task_progress("T1", "Implemented feature X, tests passing")
    content = writer.task_store.read_progress("T1")
    assert "Implemented feature X" in content


def test_checkpoint_writer_rebuild_context(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_checkpoint(
        active_intent="Fix the bug",
        next_action="Run tests",
        current_work="Debugging",
    )
    writer.write_memory(
        project_context="Test project",
        rules=["Rule 1"],
    )
    context = writer.rebuild_context()
    assert "Fix the bug" in context
    assert "Test project" in context


def test_checkpoint_writer_render_for_prompt(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_checkpoint(
        active_intent="Fix the bug",
        next_action="Run tests",
    )
    prompt_text = writer.render_for_prompt()
    assert "Fix the bug" in prompt_text
