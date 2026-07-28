"""Regression tests for manage_tasks model-write normalisation (#5757).

Models that were not shown the full tool schema (e.g. when RAG selects a
different tool set or a local model is used without function-call schemas)
sometimes emit action="add" instead of action="create", or wrap task fields in
a nested "task" object rather than providing them at the top level.  Before the
fix, both patterns fell through to the "unknown action" error branch and the
task was silently not created.

This test module verifies that both calling patterns produce a successfully
created task, matching the same-class fix applied to manage_skills (#4013).
"""

import json
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
from core.database import ScheduledTask
from src.tools.system import do_manage_tasks

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
cdb.SessionLocal = _TS


def _count_tasks_named(name):
    db = _TS()
    try:
        return db.query(ScheduledTask).filter(ScheduledTask.name == name).count()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_action_add_is_treated_as_create():
    """action='add' must be normalised to 'create' so the task is persisted."""
    out = await do_manage_tasks(
        json.dumps({
            "action": "add",
            "name": "test task add",
            "prompt": "summarise the news",
            "task_type": "llm",
            "trigger_type": "schedule",
            "schedule": "daily",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0, out
    assert "Created task" in out["response"]
    assert _count_tasks_named("test task add") == 1


@pytest.mark.asyncio
async def test_nested_task_object_is_flattened():
    """{'action':'add','task':{'name':'foo'}} must be treated as create with name=foo."""
    out = await do_manage_tasks(
        json.dumps({
            "action": "add",
            "task": {
                "name": "test task 2",
            },
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0, out
    assert "Created task" in out["response"]
    assert _count_tasks_named("test task 2") == 1


@pytest.mark.asyncio
async def test_nested_task_object_with_prompt():
    """Nested task object with explicit prompt field is handled correctly."""
    out = await do_manage_tasks(
        json.dumps({
            "action": "create",
            "task": {
                "name": "daily summary",
                "prompt": "summarise my emails",
            },
            "trigger_type": "schedule",
            "schedule": "daily",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0, out
    assert "Created task" in out["response"]
    assert _count_tasks_named("daily summary") == 1


@pytest.mark.asyncio
async def test_name_used_as_prompt_fallback_when_prompt_absent():
    """When prompt is absent but name is present, name is used as the prompt."""
    out = await do_manage_tasks(
        json.dumps({
            "action": "create",
            "name": "my simple task",
            "task_type": "llm",
            "trigger_type": "schedule",
            "schedule": "daily",
        }),
        owner="alice",
    )
    assert out["exit_code"] == 0, out
    assert "Created task" in out["response"]
    assert _count_tasks_named("my simple task") == 1
