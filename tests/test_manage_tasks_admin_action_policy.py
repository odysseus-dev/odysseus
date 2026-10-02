"""manage_tasks must apply the admin-only task-action policy.

``POST /api/tasks`` (routes/task/task_routes.py) and the scheduler
(src/task_scheduler.py) both refuse ``action`` tasks whose action is in
``ADMIN_ONLY_TASK_ACTIONS`` (run_local, run_script, ssh_command,
cookbook_serve) for owners without admin task privileges — those actions run
``subprocess.run(shell=True)`` or SSH with no sandbox. The model-facing
``do_manage_tasks`` applied neither check, so an agent could store such a task
on create/edit and resume/run it; the scheduler would only pause it at
execution time. Regression for issue #6021: the tool now enforces the same
policy on create, edit, resume and run, and keeps allowing admins and the
non-privileged actions.
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
import src.task_action_policy as policy_mod
import src.tools.system as system_mod
from src.tools.system import do_manage_tasks

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bind_session_factory(monkeypatch):
    # do_manage_tasks does `from core.database import SessionLocal` at call
    # time. Bind it per test (not at import) so this file stays
    # order-independent from sibling files that point it at their own DB.
    monkeypatch.setattr(cdb, "SessionLocal", _TS)


@pytest.fixture
def privileges(monkeypatch):
    """Drive owner_has_admin_task_privileges from a plain dict."""
    admins = {"root"}
    monkeypatch.setattr(
        policy_mod, "owner_has_admin_task_privileges",
        lambda owner: owner in admins,
    )
    return admins


def _seed(task_id, owner, task_type="llm", action=None, status="active"):
    db = _TS()
    try:
        db.add(ScheduledTask(
            id=task_id, owner=owner, name=task_id, prompt="original",
            task_type=task_type, action=action, trigger_type="webhook",
            status=status, output_target="session",
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


def _count(owner):
    db = _TS()
    try:
        return db.query(ScheduledTask).filter(ScheduledTask.owner == owner).count()
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action_name", sorted(policy_mod.ADMIN_ONLY_TASK_ACTIONS))
async def test_create_admin_only_action_denied_for_non_admin(privileges, action_name):
    before = _count("alice")
    out = await do_manage_tasks(json.dumps({
        "action": "create", "task_type": "action", "action_name": action_name,
        "name": "pwn", "schedule": "daily", "scheduled_time": "09:00",
    }), owner="alice")
    assert out["exit_code"] == 1, out
    assert "requires admin privileges" in out["error"]
    assert _count("alice") == before


@pytest.mark.asyncio
async def test_create_admin_only_action_allowed_for_admin(privileges):
    out = await do_manage_tasks(json.dumps({
        "action": "create", "task_type": "action", "action_name": "run_local",
        "name": "ok", "schedule": "daily", "scheduled_time": "09:00",
    }), owner="root")
    assert out["exit_code"] == 0, out
    assert _get(out["task_id"]).action == "run_local"


@pytest.mark.asyncio
async def test_create_regular_action_allowed_for_non_admin(privileges):
    out = await do_manage_tasks(json.dumps({
        "action": "create", "task_type": "action", "action_name": "tidy_sessions",
        "name": "ok", "schedule": "daily", "scheduled_time": "09:00",
    }), owner="alice")
    assert out["exit_code"] == 0, out


@pytest.mark.asyncio
async def test_create_llm_task_allowed_for_non_admin(privileges):
    out = await do_manage_tasks(json.dumps({
        "action": "create", "task_type": "llm", "prompt": "summarize",
        "schedule": "daily", "scheduled_time": "09:00",
    }), owner="alice")
    assert out["exit_code"] == 0, out


@pytest.mark.asyncio
async def test_edit_cannot_turn_llm_task_into_shell_action(privileges):
    _seed("alice-llm", "alice")
    out = await do_manage_tasks(json.dumps({
        "action": "edit", "task_id": "alice-llm",
        "task_type": "action", "action_name": "ssh_command",
    }), owner="alice")
    assert out["exit_code"] == 1, out
    assert "requires admin privileges" in out["error"]
    task = _get("alice-llm")
    assert task.task_type == "llm" and task.action is None


@pytest.mark.asyncio
async def test_edit_cannot_swap_action_name_to_admin_only(privileges):
    _seed("alice-action", "alice", task_type="action", action="tidy_sessions")
    out = await do_manage_tasks(json.dumps({
        "action": "edit", "task_id": "alice-action", "action_name": "run_script",
    }), owner="alice")
    assert out["exit_code"] == 1, out
    assert _get("alice-action").action == "tidy_sessions"


@pytest.mark.asyncio
async def test_edit_of_unrelated_field_on_admin_only_task_denied_for_non_admin(privileges):
    # A task that slipped in before the policy (or via demotion of its owner)
    # cannot be kept alive by a non-admin: the route rejects the same edit.
    _seed("alice-legacy", "alice", task_type="action", action="run_local")
    out = await do_manage_tasks(json.dumps({
        "action": "edit", "task_id": "alice-legacy", "name": "renamed",
    }), owner="alice")
    assert out["exit_code"] == 1, out
    assert _get("alice-legacy").name == "alice-legacy"


@pytest.mark.asyncio
async def test_resume_admin_only_task_denied_for_non_admin(privileges):
    _seed("alice-paused", "alice", task_type="action", action="run_local", status="paused")
    out = await do_manage_tasks(json.dumps({
        "action": "resume", "task_id": "alice-paused",
    }), owner="alice")
    assert out["exit_code"] == 1, out
    assert _get("alice-paused").status == "paused"


@pytest.mark.asyncio
async def test_pause_admin_only_task_still_allowed(privileges):
    # Narrowing is always fine: a non-admin may pause a task they own.
    _seed("alice-active", "alice", task_type="action", action="run_local")
    out = await do_manage_tasks(json.dumps({
        "action": "pause", "task_id": "alice-active",
    }), owner="alice")
    assert out["exit_code"] == 0, out
    assert _get("alice-active").status == "paused"


@pytest.mark.asyncio
async def test_run_admin_only_task_denied_before_scheduler(privileges, monkeypatch):
    _seed("alice-run", "alice", task_type="action", action="ssh_command")
    calls = []

    class _Sched:
        async def run_task_now(self, task_id):
            calls.append(task_id)
            return True

    import src.event_bus as event_bus
    monkeypatch.setattr(event_bus, "get_task_scheduler", lambda: _Sched())
    out = await do_manage_tasks(json.dumps({
        "action": "run", "task_id": "alice-run",
    }), owner="alice")
    assert out["exit_code"] == 1, out
    assert calls == []


@pytest.mark.asyncio
async def test_run_admin_only_task_allowed_for_admin(privileges, monkeypatch):
    _seed("root-run", "root", task_type="action", action="ssh_command")
    calls = []

    class _Sched:
        async def run_task_now(self, task_id):
            calls.append(task_id)
            return True

    import src.event_bus as event_bus
    monkeypatch.setattr(event_bus, "get_task_scheduler", lambda: _Sched())
    out = await do_manage_tasks(json.dumps({
        "action": "run", "task_id": "root-run",
    }), owner="root")
    assert out["exit_code"] == 0, out
    assert calls == ["root-run"]


def test_policy_helper_uses_shared_constants(privileges):
    # The tool must consult the shared policy module, not a private copy of
    # the action names, so a new admin-only action is covered everywhere.
    assert system_mod._admin_task_action_denied("alice", "action", "run_local") is not None
    assert system_mod._admin_task_action_denied("root", "action", "run_local") is None
    assert system_mod._admin_task_action_denied("alice", "action", "tidy_sessions") is None
    assert system_mod._admin_task_action_denied("alice", "llm", "run_local") is None
