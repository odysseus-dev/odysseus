"""Task CRUD must not let non-admins schedule Cookbook serve actions."""

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.auth as core_auth
import core.database as cdb
import routes.task_routes as task_routes
from core.database import ScheduledTask
from core.database import TaskRun
from src.task_scheduler import TaskScheduler

_REAL_DATABASE_ATTRS = {
    "Base": cdb.Base,
    "SessionLocal": cdb.SessionLocal,
    "ScheduledTask": ScheduledTask,
    "TaskRun": TaskRun,
}
if hasattr(cdb, "engine"):
    _REAL_DATABASE_ATTRS["engine"] = cdb.engine


def _restore_module_binding(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, name, module)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        monkeypatch.setattr(parent, attr, module, raising=False)


@pytest.fixture()
def task_db(monkeypatch, tmp_path):
    _restore_module_binding(monkeypatch, "core.database", cdb)
    for attr, value in _REAL_DATABASE_ATTRS.items():
        monkeypatch.setattr(cdb, attr, value, raising=False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(task_routes, "SessionLocal", testing_session)
    monkeypatch.setattr(cdb, "SessionLocal", testing_session)
    return testing_session


@pytest.fixture()
def configured_auth(monkeypatch):
    _restore_module_binding(monkeypatch, "core.auth", core_auth)
    monkeypatch.setenv("AUTH_ENABLED", "true")

    class FakeAuthManager:
        is_configured = True

        def is_admin(self, user):
            return user == "admin"

    monkeypatch.setattr(core_auth, "AuthManager", FakeAuthManager)


@pytest.fixture()
def builtin_action_info(monkeypatch):
    mod = sys.modules.get("src.builtin_actions")
    if mod is None:
        import src.builtin_actions as mod
    monkeypatch.setattr(
        mod,
        "BUILTIN_ACTION_INFO",
        {
            "summarize_emails": "Summarize emails",
            "cookbook_serve": "Serve Cookbook model",
        },
        raising=False,
    )


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    router = task_routes.setup_task_routes(MagicMock())
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _cookbook_create_req():
    return task_routes.TaskCreate(
        name="Serve test model",
        prompt="{}",
        task_type="action",
        action="cookbook_serve",
        trigger_type="webhook",
    )


def _seed_action_task(
    session_factory,
    task_id,
    owner,
    action="summarize_emails",
    *,
    task_type="action",
    webhook_token=None,
    next_run=None,
):
    db = session_factory()
    try:
        task = ScheduledTask(
            id=task_id,
            owner=owner,
            name=task_id,
            prompt="{}",
            task_type=task_type,
            action=action,
            trigger_type="webhook",
            status="active",
            output_target="session",
            webhook_token=webhook_token,
            next_run=next_run,
        )
        db.add(task)
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_create_cookbook_serve_task(task_db, configured_auth):
    create_task = _endpoint("POST", "/api/tasks")

    with pytest.raises(HTTPException) as exc:
        await create_task(_req("alice"), _cookbook_create_req())

    assert exc.value.status_code == 403
    db = task_db()
    try:
        assert db.query(ScheduledTask).count() == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_update_task_to_cookbook_serve(task_db, configured_auth):
    _seed_action_task(task_db, "alice-task", "alice")
    update_task = _endpoint("PUT", "/api/tasks/{task_id}")

    with pytest.raises(HTTPException) as exc:
        await update_task(
            _req("alice"),
            "alice-task",
            task_routes.TaskUpdate(action="cookbook_serve"),
        )

    assert exc.value.status_code == 403
    db = task_db()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == "alice-task").first()
        assert task.action == "summarize_emails"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_update_task_type_to_activate_existing_cookbook_serve(
    task_db, configured_auth
):
    _seed_action_task(
        task_db,
        "alice-task",
        "alice",
        action="cookbook_serve",
        task_type="llm",
    )
    update_task = _endpoint("PUT", "/api/tasks/{task_id}")

    with pytest.raises(HTTPException) as exc:
        await update_task(
            _req("alice"),
            "alice-task",
            task_routes.TaskUpdate(task_type="action"),
        )

    assert exc.value.status_code == 403
    db = task_db()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == "alice-task").first()
        assert task.task_type == "llm"
        assert task.action == "cookbook_serve"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_manually_run_existing_cookbook_serve_task(
    task_db, configured_auth
):
    _seed_action_task(task_db, "alice-task", "alice", action="cookbook_serve")
    scheduler = SimpleNamespace(run_task_now=MagicMock())
    router = task_routes.setup_task_routes(scheduler)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/tasks/{task_id}/run":
            run_task = route.endpoint
            break
    else:
        raise RuntimeError("POST /api/tasks/{task_id}/run not found")

    with pytest.raises(HTTPException) as exc:
        await run_task(_req("alice"), "alice-task")

    assert exc.value.status_code == 403
    scheduler.run_task_now.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_rejects_stale_non_admin_cookbook_serve_task(
    task_db, configured_auth
):
    _seed_action_task(
        task_db,
        "alice-task",
        "alice",
        action="cookbook_serve",
        webhook_token="secret",
    )
    webhook_trigger = _endpoint("POST", "/api/tasks/{task_id}/webhook/{token}")

    with pytest.raises(HTTPException) as exc:
        await webhook_trigger("alice-task", "secret")

    assert exc.value.status_code == 403
    db = task_db()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == "alice-task").first()
        assert task.status == "paused"
        assert task.next_run is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_scheduler_pauses_stale_non_admin_cookbook_serve_task(
    task_db, configured_auth
):
    due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    _seed_action_task(
        task_db,
        "alice-task",
        "alice",
        action="cookbook_serve",
        next_run=due,
    )
    db = task_db()
    try:
        db.add(TaskRun(id="run-1", task_id="alice-task", status="queued"))
        db.commit()
    finally:
        db.close()

    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._task_handles = {}
    await scheduler._execute_task_locked(
        "alice-task",
        "run-1",
        gate_foreground=False,
        release_executing=False,
    )

    db = task_db()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == "alice-task").first()
        run = db.query(TaskRun).filter(TaskRun.id == "run-1").first()
        assert task.status == "paused"
        assert task.next_run is None
        assert run.status == "error"
        assert run.error == "Action 'cookbook_serve' requires admin privileges"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_non_admin_action_metadata_hides_cookbook_serve(
    configured_auth, builtin_action_info
):
    list_actions = _endpoint("GET", "/api/tasks/meta/actions")

    out = await list_actions(_req("alice"))

    action_names = {action["name"] for action in out["actions"]}
    assert "cookbook_serve" not in action_names


@pytest.mark.asyncio
async def test_admin_can_create_cookbook_serve_task(task_db, configured_auth):
    create_task = _endpoint("POST", "/api/tasks")

    out = await create_task(_req("admin"), _cookbook_create_req())

    assert out["action"] == "cookbook_serve"
    db = task_db()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == out["id"]).first()
        assert task.owner == "admin"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_admin_action_metadata_includes_cookbook_serve(
    configured_auth, builtin_action_info
):
    list_actions = _endpoint("GET", "/api/tasks/meta/actions")

    out = await list_actions(_req("admin"))

    action_names = {action["name"] for action in out["actions"]}
    assert "cookbook_serve" in action_names


@pytest.mark.asyncio
async def test_auth_disabled_single_user_can_create_cookbook_serve_task(
    monkeypatch, task_db
):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    create_task = _endpoint("POST", "/api/tasks")

    out = await create_task(_req(None), _cookbook_create_req())

    assert out["action"] == "cookbook_serve"
    db = task_db()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == out["id"]).first()
        assert task.owner is None
    finally:
        db.close()
