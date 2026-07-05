"""Durable notification event and inbox behavior."""

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb  # noqa: E402
import routes.notification_routes as notification_routes  # noqa: E402
import src.notifications as notifications  # noqa: E402


@pytest.fixture()
def notification_db(monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(cdb.Base.metadata)
    monkeypatch.setattr(notifications, "SessionLocal", SessionLocal)
    try:
        yield SessionLocal
    finally:
        engine.dispose()
        tmpfile.close()
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    router = notification_routes.setup_notification_routes()
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def test_system_events_are_logged_without_creating_inbox_items(notification_db):
    event = notifications.record_notification_event(
        owner="alice",
        title="Folder created",
        body="Inbox/Receipts",
        category="email",
        dedupe_key="email-folder:receipts",
    )

    assert event["event_class"] == notifications.SYSTEM_EVENT
    assert notifications.list_notification_events(owner="alice")[0]["title"] == "Folder created"
    assert notifications.list_inbox_notifications(owner="alice") == []
    assert notifications.count_unread_notifications(owner="alice") == 0


def test_task_notification_body_creates_deduped_inbox_record(notification_db):
    first = notifications.record_task_notification(
        owner="alice",
        task_name="Morning digest",
        status="success",
        task_id="task-1",
        run_id="run-1",
        output_target="notification",
        body="Three urgent messages need review.",
    )
    second = notifications.record_task_notification(
        owner="alice",
        task_name="Morning digest",
        status="success",
        task_id="task-1",
        run_id="run-1",
        output_target="notification",
        body="Three urgent messages need review.",
    )

    inbox = notifications.list_inbox_notifications(owner="alice")
    assert first["id"] == second["id"]
    assert len(inbox) == 1
    assert inbox[0]["notification_kind"] == notifications.INBOX_RECORD
    assert inbox[0]["body"] == "Three urgent messages need review."
    assert notifications.count_unread_notifications(owner="alice") == 1


def test_task_failure_creates_owner_scoped_actionable_notification(notification_db):
    notifications.record_task_notification(
        owner="alice",
        task_name="Urgent email check",
        status="error",
        task_id="alice-task",
        run_id="alice-run",
        body="Provider timeout",
    )
    notifications.record_task_notification(
        owner="bob",
        task_name="Bob task",
        status="error",
        task_id="bob-task",
        run_id="bob-run",
        body="Hidden from Alice",
    )

    inbox = notifications.list_inbox_notifications(owner="alice")
    assert len(inbox) == 1
    assert inbox[0]["notification_kind"] == notifications.ACTIONABLE
    assert inbox[0]["title"] == "Task failed: Urgent email check"
    assert inbox[0]["severity"] == "error"
    assert inbox[0]["metadata"]["task_id"] == "alice-task"
    assert inbox[0]["action_url"].endswith("/alice-task")


@pytest.mark.asyncio
async def test_notification_routes_mark_read_and_reject_cross_owner(notification_db):
    item = notifications.create_inbox_notification(
        owner="alice",
        notification_kind=notifications.ACTIONABLE,
        title="Reply needed",
        body="Urgent email from finance",
        source_type="email",
        source_id="uid-1",
    )
    count_endpoint = _endpoint("GET", "/api/notifications/count")
    read_endpoint = _endpoint("POST", "/api/notifications/{item_id}/read")

    assert await count_endpoint(_req("alice")) == {"unread": 1}

    marked = await read_endpoint(
        _req("alice"),
        item["id"],
        notification_routes.NotificationReadRequest(read=True),
    )
    assert marked["is_read"] is True
    assert await count_endpoint(_req("alice")) == {"unread": 0}

    with pytest.raises(HTTPException) as exc:
        await read_endpoint(
            _req("bob"),
            item["id"],
            notification_routes.NotificationReadRequest(read=True),
        )
    assert exc.value.status_code == 404
