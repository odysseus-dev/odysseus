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


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('zone,start,end,expected_start,expected_end', [
    ('Asia/Tokyo', '2026-10-06T18:00:00', '2026-10-06T18:30:00',
     '2026-10-06T09:00:00Z', '2026-10-06T09:30:00Z'),
    ('+05:30', '2026-10-06T00:15:00', '2026-10-06T00:45:00',
     '2026-10-05T18:45:00Z', '2026-10-05T19:15:00Z'),
])
async def test_mutation_results_report_saved_times(zone, start, end, expected_start, expected_end, structured):
    from src.tools.calendar import do_manage_calendar

    owner = 'saved-times-' + uuid.uuid4().hex
    args = dict(action='create_event', summary='Call', timezone=zone,
                dtstart=start, dtend=end)
    if structured:
        for source, target in [('dtstart', 'local_start'), ('dtend', 'local_end')]:
            day, clock = args.pop(source).split('T')
            args[target] = {'date': day, 'time': clock}
    created = await do_manage_calendar(json.dumps(args), owner=owner)
    assert created.get('exit_code') == 0, created
    duplicate = await do_manage_calendar(json.dumps(args), owner=owner)
    assert duplicate.get('duplicate') is True, duplicate
    updated = await do_manage_calendar(json.dumps({
        **args, 'action': 'update_event', 'uid': created['uid'],
    }), owner=owner)
    for result in (created, duplicate, updated):
        assert result['dtstart'] == expected_start
        assert result['dtend'] == expected_end
        assert result['is_utc'] is True
    with _TS() as db:
        events = db.query(CalendarEvent).filter(CalendarEvent.uid == created['uid']).all()
        assert len(events) == 1
        assert events[0].dtstart.isoformat() + 'Z' == expected_start
        assert events[0].dtend.isoformat() + 'Z' == expected_end


@pytest.mark.parametrize('start,zone', [
    ('2027-03-14T02:30:00', 'America/New_York'),
    ('2027-11-07T01:30:00', 'America/New_York'),
    ('2027-07-06T10:00:00', 'Not/AZone'),
])
async def test_invalid_explicit_zone_time_does_not_mutate_event(start, zone):
    from src.tools.calendar import do_manage_calendar

    owner = 'invalid-zone-' + uuid.uuid4().hex
    created = await do_manage_calendar(json.dumps({
        'action': 'create_event', 'summary': 'Original',
        'dtstart': '2027-07-06T10:00:00', 'timezone': 'UTC',
    }), owner=owner)
    assert created['exit_code'] == 0
    for action in ('create_event', 'update_event'):
        result = await do_manage_calendar(json.dumps({
            'action': action, 'uid': created['uid'] if action == 'update_event' else '',
            'summary': 'Changed', 'dtstart': start, 'timezone': zone,
        }), owner=owner)
        assert result.get('exit_code') == 1, result
    with _TS() as db:
        events = db.query(CalendarEvent).join(cdb.CalendarCal).filter(cdb.CalendarCal.owner == owner).all()
        assert len(events) == 1
        assert events[0].summary == 'Original'
        assert events[0].dtstart.isoformat() == '2027-07-06T10:00:00'


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
    assert created["all_day"] is False
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
    assert updated["all_day"] is False

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

async def test_update_event_new_start_preserves_duration_when_end_is_omitted(tokyo_offset):
    from src.tool_implementations import do_manage_calendar

    owner = "move-duration-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Dinner",
        "dtstart": "2026-10-11T09:30:00",
        "dtend": "2026-10-11T11:00:00",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "dtstart": "2026-10-12T21:30:00",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        event = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert (event.dtend - event.dtstart).total_seconds() == 90 * 60
        assert event.dtend > event.dtstart
    finally:
        db.close()

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start": "2026-10-12T18:00:00",
        "end": "2026-10-13T00:00:00",
    }), owner=owner)
    assert listed.get("exit_code", 0) == 0, listed
    assert [event["summary"] for event in listed["events"]] == ["Dinner"]


async def test_update_event_with_time_converts_all_day_event_to_timed(tokyo_offset):
    from src.tool_implementations import do_manage_calendar

    owner = "all-day-to-timed-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Summer festival",
        "dtstart": "2026-08-28",
        "all_day": True,
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created
    assert created["all_day"] is True

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "dtstart": "2026-08-28T15:30:00",
        "dtend": "2026-08-28T16:30:00",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated
    assert updated["all_day"] is False
    assert "has_reminder" in updated
    assert updated["dtstart"] == "2026-08-28T06:30:00Z"

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert bool(ev.all_day) is False
        assert bool(ev.is_utc) is True
        assert ev.dtstart.hour == 6
        assert ev.dtstart.minute == 30
    finally:
        db.close()


async def test_create_all_day_date_preserves_literal_calendar_day(tokyo_offset):
    from src.tool_implementations import do_manage_calendar

    owner = "all-day-date-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "My Birthday",
        "dtstart": "2026-10-24",
        "all_day": True,
        "rrule": "FREQ=YEARLY",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert bool(ev.all_day) is True
        assert bool(ev.is_utc) is False
        assert ev.dtstart.isoformat() == "2026-10-24T00:00:00"
        assert ev.dtend.isoformat() == "2026-10-25T00:00:00"
    finally:
        db.close()


async def test_update_event_resolves_exact_title_when_model_sends_id(tokyo_offset):
    from src.tool_implementations import do_manage_calendar

    owner = "title-update-" + uuid.uuid4().hex[:6]
    title = "Temporary calendar fixture"
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": title,
        "dtstart": "2030-01-02T10:00",
        "dtend": "2030-01-02T11:00",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "id": title,
        "location": "Updated fixture location",
    }), owner=owner)
    assert updated.get("exit_code", 0) == 0, updated

    db = _TS()
    try:
        event = db.query(CalendarEvent).filter(CalendarEvent.uid == created["uid"]).first()
        assert event.location == "Updated fixture location"
    finally:
        db.close()

async def test_list_events_accepts_start_time_end_time_aliases():
    from src.tool_implementations import do_manage_calendar

    owner = "list-" + uuid.uuid4().hex[:6]
    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "July planning",
        "dtstart": "2026-07-15T12:00:00Z",
        "dtend": "2026-07-15T13:00:00Z",
    }), owner=owner)
    assert created.get("exit_code", 0) == 0, created

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "start_time": "2026-07-01T00:00:00Z",
        "end_time": "2026-08-01T00:00:00Z",
    }), owner=owner)
    assert listed.get("exit_code", 0) == 0, listed
    assert "between 2026-07-01 and 2026-08-01" in listed["response"]
    assert [event["summary"] for event in listed["events"]] == ["July planning"]


async def test_list_events_query_without_range_does_not_default_to_two_weeks():
    from src.tool_implementations import do_manage_calendar

    listed = await do_manage_calendar(json.dumps({
        "action": "list_events",
        "query": "July",
    }), owner="list-" + uuid.uuid4().hex[:6])
    assert listed.get("exit_code") == 1, listed
    assert "explicit start/end" in listed["error"]
