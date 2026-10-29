"""Trigger changes cancel obsolete timers without losing deferred event runs."""

import asyncio
import threading
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.database as database
from core.database import ScheduledTask
from routes.task import task_routes
from src import interactive_gate, task_scheduler
from tests.helpers.sqlite_db import make_temp_sqlite


NOW = datetime(2030, 1, 1, 12)
OLD_TIMER = datetime(2030, 1, 2, 10)
NEW_TIMER = datetime(2030, 1, 4, 10)


@pytest.fixture
def task_runtime(monkeypatch):
    sessions, engine, temp_file = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "ScheduledTask", ScheduledTask)
    monkeypatch.setattr(task_routes, "SessionLocal", sessions)
    monkeypatch.setattr(task_routes, "ScheduledTask", ScheduledTask)
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(task_scheduler, "_utcnow", lambda: NOW)
    monkeypatch.setattr(interactive_gate, "has_foreground_activity", lambda: False)
    scheduler = task_scheduler.TaskScheduler.__new__(task_scheduler.TaskScheduler)
    scheduler._executing = set()
    scheduler._executing_lock = threading.RLock()
    dispatched = []

    async def record_execution(task_id):
        dispatched.append(task_id)

    scheduler._execute_task = record_execution
    app = FastAPI()
    app.include_router(task_routes.setup_task_routes(scheduler))
    try:
        with TestClient(app) as client:
            yield sessions, client, scheduler, dispatched
    finally:
        engine.dispose()
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)


def _seed(sessions, *, trigger_type="schedule", status="active", next_run=OLD_TIMER):
    with sessions() as db:
        db.add(ScheduledTask(
            id="example", owner="alice", name="Example", task_type="llm",
            trigger_type=trigger_type, trigger_event="document_created",
            trigger_count=5, trigger_counter=2, status=status,
            schedule="once", scheduled_date=NEW_TIMER, next_run=next_run,
        ))
        db.commit()


def _poll_after_old_timer(scheduler, monkeypatch):
    monkeypatch.setattr(task_scheduler, "_utcnow", lambda: datetime(2030, 1, 3, 12))

    async def poll():
        await scheduler._check_due_tasks()
        # The scheduler dispatches through create_task; let its recorder finish.
        await asyncio.sleep(0)

    asyncio.run(poll())


@pytest.mark.parametrize("trigger", ["event", "webhook"])
@pytest.mark.parametrize("status", ["active", "paused"])
def test_switching_away_from_schedule_cancels_old_timer(task_runtime, monkeypatch, trigger, status):
    sessions, client, scheduler, dispatched = task_runtime
    _seed(sessions, status=status)

    response = client.put("/api/tasks/example", json={
        "trigger_type": trigger, "trigger_event": "document_created", "trigger_count": 5,
    })

    assert response.status_code == 200
    task = response.json()
    assert task["next_run"] is None
    assert task["trigger_counter"] == 0
    if trigger == "webhook":
        assert task["webhook_token"]
    assert client.get("/api/tasks/example").json()["next_run"] is None
    _poll_after_old_timer(scheduler, monkeypatch)
    assert dispatched == []


@pytest.mark.parametrize("include_trigger", [False, True])
def test_editing_event_task_preserves_and_dispatches_deferred_run(task_runtime, monkeypatch, include_trigger):
    sessions, client, scheduler, dispatched = task_runtime
    _seed(sessions, trigger_type="event")
    update = {"name": "Edited event"}
    if include_trigger:
        update["trigger_type"] = "event"

    response = client.put("/api/tasks/example", json=update)

    assert response.status_code == 200
    assert response.json()["next_run"] == OLD_TIMER.isoformat() + "Z"
    assert response.json()["trigger_counter"] == 2
    _poll_after_old_timer(scheduler, monkeypatch)
    assert dispatched == ["example"]


def test_switching_event_to_webhook_cancels_deferred_event_timer(task_runtime, monkeypatch):
    sessions, client, scheduler, dispatched = task_runtime
    _seed(sessions, trigger_type="event")

    response = client.put("/api/tasks/example", json={"trigger_type": "webhook"})

    assert response.status_code == 200
    assert response.json()["next_run"] is None
    _poll_after_old_timer(scheduler, monkeypatch)
    assert dispatched == []


@pytest.mark.parametrize("old_trigger", ["event", "webhook"])
def test_switching_to_schedule_recomputes_timer_from_stored_schedule(task_runtime, old_trigger):
    sessions, client, _, _ = task_runtime
    _seed(sessions, trigger_type=old_trigger, next_run=None)

    response = client.put("/api/tasks/example", json={"trigger_type": "schedule"})

    assert response.status_code == 200
    assert response.json()["next_run"] == NEW_TIMER.isoformat() + "Z"
