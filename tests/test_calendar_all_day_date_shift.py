"""All-day events must keep the calendar date the caller named.

Both create paths parsed dtstart with _parse_dt_pair, which converts tz-aware
input to naive UTC, then stored the row with is_utc forced to False because
the event is all-day. For a user east of UTC that turns local midnight into
22:00 the previous day; the row reads back one day early and is pushed to
CalDAV as DTSTART;VALUE=DATE for the wrong date.

The agent path makes this deterministic rather than incidental:
_parse_event_dt() routes every value through parse_due_for_user(), which
attaches the user's offset to naive input, so "2026-10-01" reaches
_parse_dt_pair() as "2026-10-01T00:00:00+02:00".
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
def berlin_offset():
    from routes.calendar_routes import set_user_tz_offset
    set_user_tz_offset(120)  # CEST, UTC+2
    try:
        yield
    finally:
        set_user_tz_offset(None)


def _row(uid):
    db = _TS()
    try:
        return db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
    finally:
        db.close()


async def test_all_day_create_from_bare_date(berlin_offset):
    from src.tool_implementations import do_manage_calendar

    owner = "ad-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Reveal",
        "dtstart": "2026-10-01",
        "all_day": True,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    ev = _row(created["uid"])
    assert ev.dtstart.date().isoformat() == "2026-10-01"
    assert ev.dtstart.hour == 0
    assert ev.dtend.date().isoformat() == "2026-10-02"
    assert bool(ev.is_utc) is False


async def test_all_day_create_from_local_midnight_offset(berlin_offset):
    """What the agent path emits once parse_due_for_user has anchored it."""
    from src.tool_implementations import do_manage_calendar

    owner = "ad-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Reveal",
        "dtstart": "2026-10-01T00:00:00+02:00",
        "all_day": True,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    ev = _row(created["uid"])
    assert ev.dtstart.date().isoformat() == "2026-10-01"
    assert bool(ev.is_utc) is False


async def test_all_day_zero_duration_is_clamped(berlin_offset):
    """Models emit dtstart == dtend; the row must still span a day.

    A zero-duration row is silently dropped by the list_events overlap
    filter (dtstart < end AND dtend > start), so the event never appears.
    """
    from src.tool_implementations import do_manage_calendar

    owner = "ad-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Reveal",
        "dtstart": "2026-10-01",
        "dtend": "2026-10-01",
        "all_day": True,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    ev = _row(created["uid"])
    assert ev.dtstart.date().isoformat() == "2026-10-01"
    assert ev.dtend.date().isoformat() == "2026-10-02"


async def test_update_to_all_day_keeps_named_date(berlin_offset):
    """Flipping a timed event to all-day in one call must not shift it."""
    from src.tool_implementations import do_manage_calendar

    owner = "ad-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Reveal",
        "dtstart": "2026-10-01T08:00:00",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "dtstart": "2026-10-01",
        "dtend": "2026-10-02",
        "all_day": True,
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    ev = _row(created["uid"])
    assert ev.dtstart.date().isoformat() == "2026-10-01"
    assert ev.dtend.date().isoformat() == "2026-10-02"
    assert bool(ev.is_utc) is False
