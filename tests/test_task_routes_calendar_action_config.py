import json
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
import types

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.task_routes as task_routes
from core.database import CalendarCal, CalendarEvent, ScheduledTask

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
task_routes.SessionLocal = _TS


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    task_routes.SessionLocal = _TS
    router = task_routes.setup_task_routes(MagicMock())
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _seed_calendar(cal_id: str, owner: str = "alice"):
    db = _TS()
    try:
        db.add(CalendarCal(id=cal_id, owner=owner, name=cal_id))
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_create_task_extract_email_events_requires_calendar_selection():
    create_task = _endpoint("POST", "/api/tasks")

    req = task_routes.TaskCreate(
        task_type="action",
        action="extract_email_events",
        trigger_type="webhook",
    )

    with pytest.raises(HTTPException) as exc:
        await create_task(_req("alice"), req)

    assert exc.value.status_code == 400
    assert "Calendar selection is required" in exc.value.detail


@pytest.mark.asyncio
async def test_create_task_extract_email_events_accepts_valid_calendar_config():
    _seed_calendar("cal://work", "alice")
    create_task = _endpoint("POST", "/api/tasks")

    req = task_routes.TaskCreate(
        task_type="action",
        action="extract_email_events",
        prompt=json.dumps({"calendar_href": "cal://work"}),
        trigger_type="webhook",
    )

    out = await create_task(_req("alice"), req)

    assert out["action"] == "extract_email_events"
    assert json.loads(out["prompt"])["calendar_href"] == "cal://work"


@pytest.mark.asyncio
async def test_list_actions_exposes_calendar_parameters_and_migration_flag():
    list_actions = _endpoint("GET", "/api/tasks/meta/actions")

    out = await list_actions(_req("alice"))

    by_name = {a["name"]: a for a in out["actions"]}
    extract = by_name["extract_email_events"]
    assert extract["parameters"][0]["name"] == "calendar_href"
    assert extract["parameters"][0]["required"] is True
    assert extract["supports_move_existing_events"] is True


def test_move_extract_events_to_calendar_moves_task_tagged_events_only():
    db = _TS()
    try:
        db.query(CalendarEvent).delete()
        db.query(ScheduledTask).delete()
        db.query(CalendarCal).delete()

        db.add(CalendarCal(id="cal-old", owner="alice", name="old"))
        db.add(CalendarCal(id="cal-new", owner="alice", name="new"))

        task = ScheduledTask(
            id="task-move-1",
            owner="alice",
            name="Extract",
            task_type="action",
            action="extract_email_events",
            prompt=json.dumps({"calendar_href": "cal-old"}),
            trigger_type="webhook",
            status="active",
            output_target="session",
        )
        db.add(task)

        db.add(CalendarEvent(
            uid="ev-move",
            calendar_id="cal-old",
            summary="Tagged",
            description="[Auto-added from email]\n[Task:task-move-1]",
            dtstart=datetime(2026, 1, 1, 10, 0),
            dtend=datetime(2026, 1, 1, 11, 0),
            created_at=datetime(2026, 1, 1, 9, 0),
        ))
        db.add(CalendarEvent(
            uid="ev-keep",
            calendar_id="cal-old",
            summary="Other",
            description="[Auto-added from email]",
            dtstart=datetime(2026, 1, 2, 10, 0),
            dtend=datetime(2026, 1, 2, 11, 0),
            created_at=datetime(2026, 1, 2, 9, 0),
        ))
        db.commit()

        moved = task_routes._move_extract_events_to_calendar(
            db,
            task,
            "alice",
            "cal-old",
            "cal-new",
        )
        db.commit()

        assert moved == 1
        ev_move = db.query(CalendarEvent).filter(CalendarEvent.uid == "ev-move").first()
        ev_keep = db.query(CalendarEvent).filter(CalendarEvent.uid == "ev-keep").first()
        assert ev_move.calendar_id == "cal-new"
        assert ev_keep.calendar_id == "cal-old"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_update_task_migration_writes_back_for_caldav(monkeypatch):
    db = _TS()
    try:
        db.query(CalendarEvent).delete()
        db.query(ScheduledTask).delete()
        db.query(CalendarCal).delete()

        db.add(CalendarCal(id="cal-old", owner="alice", name="Old", source="caldav"))
        db.add(CalendarCal(id="cal-new", owner="alice", name="New", source="caldav"))
        task = ScheduledTask(
            id="task-wb-1",
            owner="alice",
            name="Extract",
            task_type="action",
            action="extract_email_events",
            prompt=json.dumps({"calendar_href": "cal-old"}),
            trigger_type="webhook",
            status="active",
            output_target="session",
        )
        db.add(task)
        db.add(CalendarEvent(
            uid="ev-wb",
            calendar_id="cal-old",
            summary="Tagged",
            description="[Auto-added from email]\n[Task:task-wb-1]",
            dtstart=datetime(2026, 1, 1, 10, 0),
            dtend=datetime(2026, 1, 1, 11, 0),
            created_at=datetime(2026, 1, 1, 9, 0),
        ))
        db.commit()
    finally:
        db.close()

    calls = []

    async def _fake_writeback(owner, calendar_source, calendar_id, ev, delete=False):
        calls.append({
            "owner": owner,
            "calendar_source": calendar_source,
            "calendar_id": calendar_id,
            "uid": (ev or {}).get("uid"),
            "delete": bool(delete),
        })
        return {"ok": True}

    import sys
    fake_mod = types.SimpleNamespace(writeback_event=_fake_writeback)
    monkeypatch.setitem(sys.modules, "src.caldav_writeback", fake_mod)

    update_task = _endpoint("PUT", "/api/tasks/{task_id}")
    out = await update_task(
        _req("alice"),
        "task-wb-1",
        task_routes.TaskUpdate(
            prompt=json.dumps({"calendar_href": "cal-new"}),
            move_existing_events=True,
        ),
    )

    assert out["moved_events"] == 1
    assert any(c["calendar_id"] == "cal-old" and c["delete"] and c["uid"] == "ev-wb" for c in calls)
    assert any(c["calendar_id"] == "cal-new" and not c["delete"] and c["uid"] == "ev-wb" for c in calls)
