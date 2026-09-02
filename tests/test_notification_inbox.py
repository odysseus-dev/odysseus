"""Durable notification event and inbox behavior."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

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


def test_notification_retention_defaults_follow_event_class(notification_db):
    system = notifications.record_notification_event(
        owner="alice",
        title="System audit event",
        dedupe_key="retention:system",
    )
    inbox = notifications.create_inbox_notification(
        owner="alice",
        notification_kind=notifications.INBOX_RECORD,
        title="Inbox record",
        dedupe_key="retention:inbox",
    )
    actionable = notifications.create_inbox_notification(
        owner="alice",
        notification_kind=notifications.ACTIONABLE,
        title="Action required",
        dedupe_key="retention:actionable",
    )

    def _parse(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    assert _parse(system["retention_expires_at"]) - _parse(system["created_at"]) == timedelta(days=30)
    assert _parse(inbox["event"]["retention_expires_at"]) - _parse(inbox["event"]["created_at"]) == timedelta(days=90)
    assert _parse(actionable["event"]["retention_expires_at"]) - _parse(
        actionable["event"]["created_at"]
    ) == timedelta(days=180)


def test_expired_notifications_are_hidden_and_purged_in_bounded_batches(notification_db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active = notifications.create_inbox_notification(
        owner="alice",
        title="Still active",
        dedupe_key="retention:active",
        retention_expires_at=now + timedelta(days=1),
    )
    expired = [
        notifications.create_inbox_notification(
            owner="alice",
            title=f"Expired {idx}",
            dedupe_key=f"retention:expired:{idx}",
            retention_expires_at=now - timedelta(minutes=idx + 1),
        )
        for idx in range(5)
    ]

    assert [item["id"] for item in notifications.list_inbox_notifications(owner="alice")] == [active["id"]]
    assert notifications.count_unread_notifications(owner="alice") == 1
    assert [event["id"] for event in notifications.list_notification_events(owner="alice")] == [
        active["event_id"]
    ]
    assert notifications.mark_notification_read(
        item_id=expired[0]["id"], owner="alice"
    ) is None

    first = notifications.purge_expired_notifications(limit=2, now=now)
    second = notifications.purge_expired_notifications(limit=2, now=now)
    third = notifications.purge_expired_notifications(limit=2, now=now)
    assert [first["events_deleted"], second["events_deleted"], third["events_deleted"]] == [2, 2, 1]
    assert [first["inbox_items_deleted"], second["inbox_items_deleted"], third["inbox_items_deleted"]] == [2, 2, 1]

    db = notification_db()
    try:
        assert db.query(cdb.NotificationEvent).count() == 1
        assert db.query(cdb.NotificationInboxItem).count() == 1
    finally:
        db.close()


def test_concurrent_sessions_create_one_deduped_inbox_item(notification_db, monkeypatch):
    original_upsert = notifications._upsert_event
    barrier = threading.Barrier(2)
    call_lock = threading.Lock()
    first_calls = 0

    def racing_upsert(*args, **kwargs):
        nonlocal first_calls
        event = original_upsert(*args, **kwargs)
        with call_lock:
            first_calls += 1
            wait_for_peer = first_calls <= 2
        if wait_for_peer:
            barrier.wait(timeout=10)
        return event

    monkeypatch.setattr(notifications, "_upsert_event", racing_upsert)

    def create_one():
        return notifications.create_inbox_notification(
            owner="alice",
            notification_kind=notifications.ACTIONABLE,
            title="Concurrent alert",
            dedupe_key="concurrent:one",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: create_one(), range(2)))

    assert results[0]["id"] == results[1]["id"]
    assert notifications.count_unread_notifications(owner="alice") == 1
    db = notification_db()
    try:
        assert db.query(cdb.NotificationEvent).count() == 1
        assert db.query(cdb.NotificationInboxItem).count() == 1
    finally:
        db.close()


def test_notification_migration_backfills_retention_and_collapses_duplicates(tmp_path, monkeypatch):
    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy-notifications.db'}")
    with legacy_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE notification_events (
                id TEXT PRIMARY KEY,
                owner TEXT,
                event_class TEXT NOT NULL,
                dedupe_key TEXT,
                retention_expires_at DATETIME,
                created_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE notification_inbox_items (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("""
            INSERT INTO notification_events
                (id, owner, event_class, dedupe_key, created_at)
            VALUES
                ('event-old', 'alice', 'actionable', 'same-key', '2026-01-01 00:00:00'),
                ('event-new', 'alice', 'actionable', 'same-key', '2026-01-02 00:00:00'),
                ('event-system', NULL, 'system_event', NULL, '2026-01-03 00:00:00')
        """))
        conn.execute(text("""
            INSERT INTO notification_inbox_items (id, event_id, created_at)
            VALUES
                ('item-old', 'event-old', '2026-01-01 00:00:00'),
                ('item-new', 'event-new', '2026-01-02 00:00:00'),
                ('item-system-old', 'event-system', '2026-01-03 00:00:00'),
                ('item-system-new', 'event-system', '2026-01-04 00:00:00')
        """))

    monkeypatch.setattr(cdb, "engine", legacy_engine)
    cdb._migrate_notification_retention_and_uniqueness()
    cdb._migrate_notification_retention_and_uniqueness()

    with legacy_engine.connect() as conn:
        events = conn.execute(text("""
            SELECT id, retention_expires_at
              FROM notification_events
             ORDER BY id
        """)).fetchall()
        items = conn.execute(text("""
            SELECT id, event_id
              FROM notification_inbox_items
             ORDER BY id
        """)).fetchall()
        event_indexes = {
            row[1]: row[2]
            for row in conn.execute(text("PRAGMA index_list(notification_events)"))
        }
        inbox_indexes = {
            row[1]: row[2]
            for row in conn.execute(text("PRAGMA index_list(notification_inbox_items)"))
        }

    assert events == [
        ("event-new", "2026-07-01 00:00:00"),
        ("event-system", "2026-02-02 00:00:00"),
    ]
    assert items == [
        ("item-new", "event-new"),
        ("item-system-new", "event-system"),
    ]
    assert event_indexes["uq_notification_events_owner_dedupe"] == 1
    assert inbox_indexes["uq_notification_inbox_event"] == 1
    legacy_engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_runs_bounded_notification_purge_on_hourly_cadence(monkeypatch):
    import src.task_scheduler as task_scheduler

    scheduler = task_scheduler.TaskScheduler.__new__(task_scheduler.TaskScheduler)
    scheduler._last_notification_purge = None
    ticks = iter((100.0, 200.0, 3701.0))
    calls = []

    monkeypatch.setattr(
        task_scheduler,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    monkeypatch.setattr(notifications, "NOTIFICATION_PURGE_BATCH_SIZE", 500)
    monkeypatch.setattr(notifications, "NOTIFICATION_PURGE_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(
        notifications,
        "purge_expired_notifications",
        lambda *, limit: calls.append(limit) or {
            "events_deleted": 0,
            "inbox_items_deleted": 0,
        },
    )

    await scheduler._purge_expired_notifications_if_due()
    await scheduler._purge_expired_notifications_if_due()
    await scheduler._purge_expired_notifications_if_due()

    assert calls == [500, 500]


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
