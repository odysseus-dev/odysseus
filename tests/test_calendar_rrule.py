"""Issue #1320 — the agent's manage_calendar tool can create a recurring event.

The create_event handler already persists `rrule`, but it wasn't documented in the
tool schema, so the agent took "a roundabout way". This pins the end-to-end path:
calling do_manage_calendar with an rrule stores a single event carrying that RRULE.
"""

import json
import sys
import uuid

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarEvent

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    # do_manage_calendar does `from core.database import SessionLocal` at call
    # time, so patch the module attribute to our temp DB — via monkeypatch so it
    # is RESTORED after each test and can't leak into later tests in the process.
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


async def test_create_event_with_rrule_persists_recurrence():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    rrule = "FREQ=WEEKLY;BYDAY=MO"
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Standup",
        "dtstart": "2026-06-08T09:00:00Z",
        "rrule": rrule,
    }), owner=owner)
    assert res.get("exit_code", 0) == 0, res
    uid = res.get("uid")
    assert uid, res

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
        assert ev is not None
        assert ev.rrule == rrule  # ONE event carrying the recurrence rule
        assert ev.summary == "Standup"
    finally:
        db.close()


async def test_create_event_without_rrule_is_single():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "One-off",
        "dtstart": "2026-06-09T10:00:00Z",
    }), owner=owner)
    assert res.get("exit_code", 0) == 0, res
    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == res["uid"]).first()
        assert ev is not None and (ev.rrule or "") == ""
    finally:
        db.close()


async def test_update_event_can_clear_rrule():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Repeating standup",
        "dtstart": "2026-07-01T14:00:00Z",
        "rrule": "FREQ=WEEKLY;BYDAY=WE",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "rrule": "",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert ev is not None
        assert (ev.rrule or "") == ""
    finally:
        db.close()


async def test_update_event_can_clear_rrule_with_repeat_none_alias():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Repeating review",
        "dtstart": "2026-07-01T15:00:00Z",
        "rrule": "FREQ=WEEKLY;BYDAY=WE",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "repeat": "none",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert ev is not None
        assert (ev.rrule or "") == ""
    finally:
        db.close()


async def test_list_events_exposes_rrule_for_repeating_events():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    rrule = "FREQ=WEEKLY;BYDAY=WE"
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Weekly sync",
        "dtstart": "2026-07-01T14:00:00Z",
        "rrule": rrule,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-07-01T00:00:00Z",
        "end": "2026-07-02T00:00:00Z",
    }), owner=owner)
    assert listed.get("exit_code", 0) == 0, listed
    matches = [ev for ev in listed["events"] if ev["uid"] == created["uid"]]
    assert matches
    assert matches[0]["rrule"] == rrule
    assert f"repeats({rrule})" in listed["response"]
