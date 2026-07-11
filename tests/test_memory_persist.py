"""Tests for src/agent/memory_persist.py"""
from __future__ import annotations
import os
import tempfile
import pytest
from src.agent.memory_persist import (
    MemoryStore, CheckpointStore, NotesStore, TaskProgressStore,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_memory_store_read_write(tmp_dir):
    store = MemoryStore(tmp_dir)
    store.write("## Rules\n- Use Python 3.9+")
    content = store.read()
    assert "Rules" in content
    assert "Python 3.9+" in content


def test_memory_store_append(tmp_dir):
    store = MemoryStore(tmp_dir)
    store.write("## Rules\n- Rule 1")
    store.append("## Architecture\n- FastAPI backend")
    content = store.read()
    assert "Rule 1" in content
    assert "FastAPI backend" in content


def test_memory_store_initial_content(tmp_dir):
    store = MemoryStore(tmp_dir)
    content = store.read()
    assert "Project memory" in content


def test_checkpoint_store_sections(tmp_dir):
    store = CheckpointStore(tmp_dir)
    store.update_section("active_intent", "User wants to fix the bug")
    store.update_section("next_action", "Run tests")
    content = store.read()
    assert "User wants to fix the bug" in content
    assert "Run tests" in content


def test_checkpoint_store_all_sections(tmp_dir):
    store = CheckpointStore(tmp_dir)
    sections = store.list_sections()
    assert "active_intent" in sections
    assert "next_action" in sections
    assert "directives" in sections
    assert "task_tree" in sections
    assert "current_work" in sections
    assert "files_and_code" in sections
    assert "discovered_knowledge" in sections
    assert "errors_and_fixes" in sections
    assert "live_resources" in sections
    assert "design_decisions" in sections
    assert "open_notes" in sections


def test_checkpoint_store_get_section(tmp_dir):
    store = CheckpointStore(tmp_dir)
    store.update_section("active_intent", "Fix the bug")
    content = store.get_section("active_intent")
    assert "Fix the bug" in content


def test_notes_store_append(tmp_dir):
    store = NotesStore(tmp_dir)
    store.append("Important finding")
    store.append("Another note")
    content = store.read()
    assert "Important finding" in content
    assert "Another note" in content


def test_notes_store_format(tmp_dir):
    store = NotesStore(tmp_dir)
    store.append("Test note")
    content = store.read()
    assert "##" in content


def test_task_progress_store(tmp_dir):
    store = TaskProgressStore(tmp_dir)
    store.write_progress("T1", "Implemented feature X")
    content = store.read_progress("T1")
    assert "Implemented feature X" in content


def test_task_progress_store_multiple(tmp_dir):
    store = TaskProgressStore(tmp_dir)
    store.write_progress("T1", "Task 1 progress")
    store.write_progress("T2", "Task 2 progress")
    assert "Task 1" in store.read_progress("T1")
    assert "Task 2" in store.read_progress("T2")


def test_task_progress_list(tmp_dir):
    store = TaskProgressStore(tmp_dir)
    store.write_progress("T1", "progress 1")
    store.write_progress("T2", "progress 2")
    tasks = store.list_tasks()
    assert "T1" in tasks
    assert "T2" in tasks
