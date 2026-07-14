"""update_event must anchor datetimes to the user tz, like create_event.

create_event parses a naive/natural-language dtstart in the USER's
timezone (parse_due_for_user -> stored naive-UTC, is_utc=True), but
update_event parsed args["dtstart"] with the raw server-local _parse_dt
and never refreshed is_utc. So updating an event to the same naive value
it was created with silently shifted it by the user's UTC offset (9h for a
Tokyo user) and left is_utc inconsistent. The do_manage_notes update path
was already fixed for the analogous issue.
"""
import json
import uuid

import pytest

import core.database as cdb
from core.database import CalendarEvent
from tests.helpers.sqlite_db import make_temp_sqlite

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    import routes.calendar_routes as cr
    monkeypatch.setattr(cr, "SessionLocal", _TS, raising=False)
    yield


@pytest.fixture
def tokyo_offset():
    from routes.calendar_routes import set_user_tz_offset
    set_user_tz_offset(540)  # Tokyo, UTC+9
    try:
        yield
    finally:
        set_user_tz_offset(None)


async def test_update_event_dtstart_anchored_to_user_tz(tokyo_offset):
    from src.tool_implementations import do_manage_calendar

    owner = "tz-" + uuid.uuid4().hex[:6]
    naive = "2026-06-10T14:00:00"  # 14:00 Tokyo == 05:00 UTC

    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Standup",
        "dtstart": naive,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created
    uid = created["uid"]

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
        created_dtstart, created_is_utc = ev.dtstart, ev.is_utc
    finally:
        db.close()

    # Update the same event to the SAME naive wall-clock value.
    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": uid,
        "dtstart": naive,
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
        # Same input -> same stored moment and same is_utc flag as create.
        assert ev.dtstart == created_dtstart
        assert bool(ev.is_utc) == bool(created_is_utc)
        # And concretely: 14:00 Tokyo is 05:00 UTC, stored naive-UTC.
        assert ev.dtstart.hour == 5
        assert bool(ev.is_utc) is True
    finally:
        db.close()
