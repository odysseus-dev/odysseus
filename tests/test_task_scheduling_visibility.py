from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.task.task_routes as task_routes
from routes.task.task_routes import (
    compute_task_health,
    normalize_scheduled_time,
    setup_task_routes,
)


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_normalize_scheduled_time():
    assert normalize_scheduled_time("9:05") == "09:05"
    assert normalize_scheduled_time("09:00") == "09:00"
    assert normalize_scheduled_time("23:59") == "23:59"
    assert normalize_scheduled_time(None) is None


def test_compute_task_health_flags_failures_and_overdue_tasks():
    now = _utcnow_naive()
    healthy = SimpleNamespace(
        id="t-1",
        name="Daily Digest",
        action=None,
        status="active",
        trigger_type="schedule",
        next_run=now + timedelta(hours=2),
        runs=[
            SimpleNamespace(
                status="completed",
                started_at=now - timedelta(minutes=10, seconds=2),
                finished_at=now - timedelta(minutes=10),
                error=None,
                result="ok",
            )
        ],
    )
    failing_and_overdue = SimpleNamespace(
        id="t-2",
        name="Sync Invoices",
        action=None,
        status="active",
        trigger_type="schedule",
        next_run=now - timedelta(minutes=45),
        runs=[
            SimpleNamespace(
                status="error",
                started_at=now - timedelta(minutes=46, seconds=1),
                finished_at=now - timedelta(minutes=46),
                error="Connection timed out",
                result=None,
            ),
            SimpleNamespace(
                status="error",
                started_at=now - timedelta(hours=2, seconds=1),
                finished_at=now - timedelta(hours=2),
                error="Connection timed out",
                result=None,
            ),
        ],
    )
    paused = SimpleNamespace(
        id="t-3",
        name="Paused Job",
        action=None,
        status="paused",
        trigger_type="schedule",
        next_run=now - timedelta(days=3),
        runs=[],
    )

    health = compute_task_health([healthy, failing_and_overdue, paused], now=now, scheduler_running=True)
    assert health["scheduler_running"] is True
    assert health["counts"] == {
        "total": 3,
        "active": 2,
        "paused": 1,
        "completed": 0,
        "overdue": 1,
        "failing": 1,
    }
    assert health["runs"]["sampled"] == 3
    assert health["runs"]["succeeded"] == 1
    assert health["runs"]["failed"] == 2
    assert health["runs"]["avg_duration_ms"] is not None
    assert len(health["needs_attention"]) == 1
    alert = health["needs_attention"][0]
    assert alert["id"] == "t-2"
    assert alert["consecutive_failures"] == 2
    assert alert["is_overdue"] is True
    assert alert["overdue_minutes"] == 45
    assert "Connection timed out" in alert["last_error"]


def test_tasks_health_endpoint(monkeypatch):
    monkeypatch.setattr(task_routes, "get_current_user", lambda req: "admin")
    now = _utcnow_naive()
    sample_task = SimpleNamespace(
        id="t-10",
        owner="admin",
        name="Audit Skills",
        action="audit_skills",
        status="active",
        trigger_type="schedule",
        next_run=now + timedelta(hours=1),
        runs=[],
    )

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = [sample_task]
    monkeypatch.setattr(task_routes, "SessionLocal", lambda: fake_db)

    sched = SimpleNamespace(_running=True, ensure_defaults=AsyncMock())
    app = FastAPI()
    app.include_router(setup_task_routes(sched))
    client = TestClient(app)

    resp = client.get("/api/tasks/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scheduler_running"] is True
    assert body["counts"]["total"] == 1
    assert body["counts"]["active"] == 1
    assert body["needs_attention"] == []
