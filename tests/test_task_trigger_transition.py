"""Regression tests for #6588: switching a scheduled task to Event or
Webhook must clear its old next_run timer so _check_due_tasks does not fire it."""

import asyncio
import json
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from routes.task.task_routes import setup_task_routes
from src.task_scheduler import TaskScheduler
from src.tools.system import do_manage_tasks


@pytest.fixture
def task_env(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "tasks.db"),
        connect_args={"check_same_thread": False},
    )
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr("routes.task.task_routes.SessionLocal", factory)
    monkeypatch.setattr(
        "routes.task.task_routes._seal_request_task_authority",
        lambda *_args, **_kwargs: "{}",
    )
    app = FastAPI()
    app.include_router(setup_task_routes(TaskScheduler(None)))
    client = TestClient(app)
    yield client, factory
    engine.dispose()


def _create_scheduled_task(client: TestClient) -> str:
    res = client.post(
        "/api/tasks",
        json={
            "name": "Future once task",
            "prompt": "Check status",
            "trigger_type": "schedule",
            "schedule": "once",
            "scheduled_date": "2030-01-02T10:00:00Z",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["next_run"] == "2030-01-02T10:00:00Z"
    return body["id"]


def test_switching_schedule_to_webhook_clears_next_run_and_prevents_dispatch(
    task_env, monkeypatch
):
    client, factory = task_env
    task_id = _create_scheduled_task(client)

    upd = client.put(f"/api/tasks/{task_id}", json={"trigger_type": "webhook"})
    assert upd.status_code == 200, upd.text
    data = upd.json()
    assert data["trigger_type"] == "webhook"
    assert data["webhook_token"]
    assert data["next_run"] is None

    scheduler = TaskScheduler(None)
    dispatched = []

    async def fake_execute(tid, **_kwargs):
        dispatched.append(tid)

    monkeypatch.setattr(scheduler, "_execute_task", fake_execute)
    monkeypatch.setattr("src.task_scheduler._utcnow", lambda: datetime(2030, 1, 2, 10, 5))
    monkeypatch.setattr("src.interactive_gate.has_foreground_activity", lambda: False)

    asyncio.run(scheduler._check_due_tasks())
    assert dispatched == []


def test_switching_schedule_to_event_clears_next_run_and_prevents_dispatch(
    task_env, monkeypatch
):
    client, factory = task_env
    task_id = _create_scheduled_task(client)

    upd = client.put(
        f"/api/tasks/{task_id}",
        json={
            "trigger_type": "event",
            "trigger_event": "document_created",
            "trigger_count": 5,
        },
    )
    assert upd.status_code == 200, upd.text
    data = upd.json()
    assert data["trigger_type"] == "event"
    assert data["trigger_event"] == "document_created"
    assert data["trigger_count"] == 5
    assert data["next_run"] is None

    scheduler = TaskScheduler(None)
    dispatched = []

    async def fake_execute(tid, **_kwargs):
        dispatched.append(tid)

    monkeypatch.setattr(scheduler, "_execute_task", fake_execute)
    monkeypatch.setattr("src.task_scheduler._utcnow", lambda: datetime(2030, 1, 2, 10, 5))
    monkeypatch.setattr("src.interactive_gate.has_foreground_activity", lambda: False)

    asyncio.run(scheduler._check_due_tasks())
    assert dispatched == []


def test_manage_tasks_edit_switching_to_webhook_clears_next_run(task_env):
    client, factory = task_env
    task_id = _create_scheduled_task(client)

    res = asyncio.run(
        do_manage_tasks(
            json.dumps({"action": "edit", "task_id": task_id, "trigger_type": "webhook"})
        )
    )
    assert res["exit_code"] == 0, res
    with factory() as db:
        row = db.get(database.ScheduledTask, task_id)
        assert row.trigger_type == "webhook"
        assert row.next_run is None
