"""Occurrence identifiers must not silently mutate the entire recurring series."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from tests.helpers.database import disposable_database
from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.calendar_routes as calendar_routes


@pytest.fixture
def calendar_db(tmp_path, monkeypatch):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(calendar_routes, "SessionLocal", factory)
        yield factory


def _seed(factory, *, source="local", owner="alice", uid="weekly-class",
          summary="Weekly class"):
    with factory() as db:
        cal = cdb.CalendarCal(id=f"cal-{uid}", owner=owner, name="Classes", source=source)
        db.add(cal)
        db.flush()
        db.add(cdb.CalendarEvent(
            uid=uid, calendar_id=cal.id, summary=summary, location="Room 1",
            dtstart=datetime(2026, 9, 1, 7), dtend=datetime(2026, 9, 1, 8),
            is_utc=False, rrule="FREQ=WEEKLY;BYDAY=TU",
            remote_href=f"/calendars/classes/{uid}.ics" if source == "caldav" else None,
        ))
        db.commit()
    return uid


def _state(factory, uid):
    with factory() as db:
        row = db.get(cdb.CalendarEvent, uid)
        if row is None:
            return None
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@pytest.mark.parametrize("action", ["update_event", "delete_event"])
@pytest.mark.parametrize("prefix", ["", "#event-", "event-"])
@pytest.mark.parametrize("suffix", ["2026-11-17T07:00", "2026-11-17"])
@pytest.mark.parametrize("source", ["local", "caldav"])
async def test_occurrence_uid_is_refused_before_series_side_effects(
    calendar_db, monkeypatch, action, prefix, suffix, source,
):
    from src.tools.calendar import do_manage_calendar

    uid = _seed(calendar_db, source=source)
    before = _state(calendar_db, uid)
    push = AsyncMock()
    reminders = Mock(wraps=calendar_routes._delete_calendar_reminders_for_event)
    monkeypatch.setattr(calendar_routes, "_push_caldav_event_after_commit", push)
    monkeypatch.setattr(calendar_routes, "_delete_calendar_reminders_for_event", reminders)

    result = await do_manage_calendar(json.dumps({
        "action": action,
        "uid": f"{prefix}{uid}::{suffix}",
        "location": "Room 2",
        "reminder_minutes": None,
    }), owner="alice")

    assert _state(calendar_db, uid) == before
    assert result["exit_code"] == 1, result
    assert "occurrence" in result["error"].lower()
    assert "series" in result["error"].lower()
    reminders.assert_not_called()
    push.assert_not_awaited()
    with calendar_db() as db:
        assert db.query(cdb.CalendarDeletedEvent).count() == 0


@pytest.mark.parametrize("action", ["update_event", "delete_event"])
@pytest.mark.parametrize("prefix", ["", "#event-", "event-"])
async def test_series_uid_still_allows_whole_series_changes(calendar_db, action, prefix):
    from src.tools.calendar import do_manage_calendar

    uid = _seed(calendar_db)
    result = await do_manage_calendar(json.dumps({
        "action": action, "uid": f"{prefix}{uid}", "location": "Room 2",
    }), owner="alice")

    assert result["exit_code"] == 0, result
    state = _state(calendar_db, uid)
    if action == "delete_event":
        assert state is None
    else:
        assert state["location"] == "Room 2"


@pytest.mark.parametrize("action", ["update_event", "delete_event"])
async def test_occurrence_uid_does_not_reveal_another_owners_series(calendar_db, action):
    from src.tools.calendar import do_manage_calendar

    uid = _seed(calendar_db, owner="bob")
    before = _state(calendar_db, uid)
    result = await do_manage_calendar(json.dumps({
        "action": action, "uid": f"{uid}::2026-11-17T07:00", "location": "Room 2",
    }), owner="alice")

    assert result["exit_code"] == 1, result
    assert "not found" in result["error"].lower()
    assert _state(calendar_db, uid) == before


@pytest.mark.parametrize("action", ["update_event", "delete_event"])
async def test_title_containing_double_colon_still_resolves(calendar_db, action):
    from src.tools.calendar import do_manage_calendar

    title = "Lab :: section A"
    uid = _seed(calendar_db, summary=title)
    result = await do_manage_calendar(json.dumps({
        "action": action, "summary": title, "location": "Room 2",
    }), owner="alice")

    assert result["exit_code"] == 0, result
    state = _state(calendar_db, uid)
    if action == "delete_event":
        assert state is None
    else:
        assert state["location"] == "Room 2"


@pytest.mark.parametrize("action", ["update_event", "delete_event"])
async def test_literal_event_prefix_takes_precedence_over_anchor_fallback(calendar_db, action):
    from src.tools.calendar import do_manage_calendar

    other_uid = _seed(calendar_db)
    uid = _seed(calendar_db, uid=f"event-{other_uid}")
    other_before = _state(calendar_db, other_uid)
    result = await do_manage_calendar(json.dumps({
        "action": action, "uid": uid, "location": "Room 2",
    }), owner="alice")

    assert result["exit_code"] == 0, result
    assert _state(calendar_db, other_uid) == other_before
    state = _state(calendar_db, uid)
    if action == "delete_event":
        assert state is None
    else:
        assert state["location"] == "Room 2"
