"""manage_tasks mutations must fail closed on owner-less / cross-owner tasks.

The edit/delete/pause/run actions of ``do_manage_tasks`` previously gated with
``if owner and task.owner and task.owner != owner``. The middle term made the
check a no-op whenever the task had no owner — the state a scheduled task is in
when it was created in no-login mode (or via the localhost middleware bypass)
before the periodic legacy-owner sweep reassigns it to the admin user. So any
authenticated user's agent could edit, delete, pause, or *run* another tenant's
owner-less task. The sibling ``list`` action already scopes with an exact
``ScheduledTask.owner == owner`` filter, so the mutators were strictly more
permissive than the reader.
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
# do_manage_tasks does `from core.database import SessionLocal` at call time,
# so patching the module attribute is enough to point it at the temp DB.
cdb.SessionLocal = _TS


def _seed(task_id, owner):
    db = _TS()
    try:
        db.add(ScheduledTask(
            id=task_id, owner=owner, name=task_id, prompt="original",
            task_type="llm", trigger_type="webhook", status="active",
            output_target="session",
        ))
        db.commit()
    finally:
        db.close()


def _get(task_id):
    db = _TS()
    try:
        return db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_edit_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-edit", None)
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "ownerless-edit", "prompt": "pwned"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("ownerless-edit").prompt == "original"


@pytest.mark.asyncio
async def test_delete_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-del", None)
    out = await do_manage_tasks(
        json.dumps({"action": "delete", "task_id": "ownerless-del"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("ownerless-del") is not None


@pytest.mark.asyncio
async def test_pause_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-pause", None)
    out = await do_manage_tasks(
        json.dumps({"action": "pause", "task_id": "ownerless-pause"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("ownerless-pause").status == "active"


@pytest.mark.asyncio
async def test_run_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-run", None)
    out = await do_manage_tasks(
        json.dumps({"action": "run", "task_id": "ownerless-run"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"


@pytest.mark.asyncio
async def test_edit_denied_on_other_owners_task():
    _seed("bob-task", "bob")
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "bob-task", "prompt": "pwned"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("bob-task").prompt == "original"


@pytest.mark.asyncio
async def test_edit_allowed_for_matching_owner():
    _seed("alice-task", "alice")
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "alice-task", "prompt": "updated"}),
        owner="alice",
    )
    assert out["exit_code"] == 0
    assert _get("alice-task").prompt == "updated"


@pytest.mark.asyncio
async def test_edit_allowed_in_no_login_mode():
    # owner is None when auth is disabled — single-user mode keeps full access
    # to shared (owner-less) tasks, exactly as `list` returns them unfiltered.
    _seed("shared-task", None)
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "shared-task", "prompt": "updated"}),
        owner=None,
    )
    assert out["exit_code"] == 0
    assert _get("shared-task").prompt == "updated"
