"""ICS transfers must not restore occurrences excluded by their owner."""
import io
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import UploadFile
from icalendar import Calendar, Event
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as cdb
import routes.calendar_routes as cr


@pytest.fixture
def routes(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/app.db')
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cr, 'SessionLocal', factory)
    monkeypatch.setattr(cr, 'require_user', lambda request: request.state.current_user)
    endpoints = {}
    for route in cr.setup_calendar_routes().routes:
        for method in route.methods:
            endpoints[(method, route.path)] = route.endpoint
    yield factory, endpoints, SimpleNamespace(state=SimpleNamespace(current_user='alice'))
    engine.dispose()


def case(kind):
    if kind == 'all-day':
        start = date(2026, 12, 1)
    else:
        start = datetime(2026, 12, 1, 10, 0, 30 if kind == 'seconds' else 0,
                         tzinfo=timezone.utc if kind in {'utc', 'seconds'} else None)
    return start, start + (timedelta(days=1) if kind == 'all-day' else timedelta(hours=1))


def content(start, end, exclusions):
    cal = Calendar(); cal.add('version', '2.0'); cal.add('prodid', '-//Owned test//EN')
    event = Event(); event.add('uid', 'owned-input'); event.add('summary', 'Owned recurring')
    event.add('dtstart', start); event.add('dtend', end)
    event.add('rrule', {'FREQ': 'DAILY', 'COUNT': 3})
    for group in exclusions:
        event.add('exdate', group)
    cal.add_component(event)
    return cal.to_ical()


def values(component):
    props = component.get('EXDATE', [])
    if not isinstance(props, list):
        props = [props]
    return [entry.dt for prop in props for entry in prop.dts]


def stored(start):
    if not isinstance(start, datetime):
        return datetime.combine(start, datetime.min.time())
    return start.astimezone(timezone.utc).replace(tzinfo=None) if start.tzinfo else start


def seed(factory, kind, exclusions):
    start, end = case(kind)
    with factory() as db:
        db.add(cdb.CalendarCal(id='owned', owner='alice', name='Owned transfer', source='local'))
        db.add(cdb.CalendarEvent(uid='owned-series', calendar_id='owned', summary='Owned recurring',
               dtstart=stored(start), dtend=stored(end), all_day=kind == 'all-day',
               is_utc=kind in {'utc', 'seconds'}, rrule='FREQ=DAILY;COUNT=3',
               recurrence_exdates=json.dumps(exclusions)))
        db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['all-day', 'utc', 'floating', 'seconds'])
async def test_export_preserves_excluded_value_type_and_seconds(routes, kind):
    factory, endpoints, request = routes
    start, end = case(kind); excluded = start + timedelta(days=1)
    key = excluded.strftime('%Y-%m-%d' if kind == 'all-day' else '%Y-%m-%dT%H:%M')
    seed(factory, kind, [key])

    response = await endpoints[('GET', '/api/calendar/export/{cal_id}')](request, 'owned')
    component = Calendar.from_ical(response.body).walk('VEVENT')[0]

    assert values(component) == [excluded]
    assert component['DTSTART'].dt == start
    assert component['DTEND'].dt == end
    assert str(component['RRULE']['FREQ'][0]) == 'DAILY'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['all-day', 'utc', 'floating', 'seconds'])
async def test_import_keeps_excluded_occurrence_out_of_actual_listing(routes, kind):
    factory, endpoints, request = routes
    start, end = case(kind); excluded = start + timedelta(days=1)
    upload = UploadFile(filename='owned.ics', file=io.BytesIO(content(start, end, [[excluded]])))
    imported = await endpoints[('POST', '/api/calendar/import')](request, upload, 'Owned import')
    listing = await endpoints[('GET', '/api/calendar/events')](request, '2026-12-01', '2026-12-05', imported['calendar_id'])

    assert len(listing['events']) == 2
    with factory() as db:
        event = db.get(cdb.CalendarEvent, imported['event_uids'][0])
        expected = stored(excluded).strftime('%Y-%m-%d' if kind == 'all-day' else '%Y-%m-%dT%H:%M')
        assert json.loads(event.recurrence_exdates) == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['all-day', 'utc'])
async def test_import_flattens_repeated_and_comma_separated_exdates(routes, kind):
    factory, endpoints, request = routes
    start, end = case(kind)
    excluded = [start + timedelta(days=1), start + timedelta(days=2)]
    upload = UploadFile(filename='owned.ics', file=io.BytesIO(content(start, end, [excluded, [excluded[0]]])))
    imported = await endpoints[('POST', '/api/calendar/import')](request, upload, 'Owned import')
    listing = await endpoints[('GET', '/api/calendar/events')](request, '2026-12-01', '2026-12-05', imported['calendar_id'])

    assert len(listing['events']) == 1
    with factory() as db:
        event = db.get(cdb.CalendarEvent, imported['event_uids'][0])
        assert len(json.loads(event.recurrence_exdates)) == 2


@pytest.mark.asyncio
async def test_import_normalizes_aware_exdate_to_the_stored_utc_key(routes):
    factory, endpoints, request = routes
    start = datetime(2026, 12, 1, 10, tzinfo=ZoneInfo('Asia/Tokyo'))
    upload = UploadFile(filename='owned.ics', file=io.BytesIO(content(start, start + timedelta(hours=1), [[start + timedelta(days=1)]])))
    imported = await endpoints[('POST', '/api/calendar/import')](request, upload, 'Owned timezone')
    with factory() as db:
        event = db.get(cdb.CalendarEvent, imported['event_uids'][0])
        assert event.is_utc is True
        assert json.loads(event.recurrence_exdates) == ['2026-12-02T01:00']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['all-day', 'utc', 'floating', 'seconds'])
async def test_transfer_without_exclusions_keeps_all_occurrences(routes, kind):
    _, endpoints, request = routes
    start, end = case(kind)
    upload = UploadFile(filename='owned.ics', file=io.BytesIO(content(start, end, [])))
    imported = await endpoints[('POST', '/api/calendar/import')](request, upload, 'Owned control')
    listing = await endpoints[('GET', '/api/calendar/events')](request, '2026-12-01', '2026-12-05', imported['calendar_id'])
    response = await endpoints[('GET', '/api/calendar/export/{cal_id}')](request, imported['calendar_id'])
    assert len(listing['events']) == 3
    assert values(Calendar.from_ical(response.body).walk('VEVENT')[0]) == []


@pytest.mark.asyncio
async def test_export_skips_invalid_keys_and_deduplicates_valid_exclusions(routes):
    factory, endpoints, request = routes
    seed(factory, 'utc', ['bad', '2026-99-99T10:00', '2026-12-02T10:00', '2026-12-02T10:00'])
    response = await endpoints[('GET', '/api/calendar/export/{cal_id}')](request, 'owned')
    assert values(Calendar.from_ical(response.body).walk('VEVENT')[0]) == [datetime(2026, 12, 2, 10, tzinfo=timezone.utc)]


@pytest.mark.asyncio
async def test_duplicate_import_keeps_existing_local_exclusions(routes):
    factory, endpoints, request = routes
    seed(factory, 'utc', ['2026-12-03T10:00'])
    start, end = case('utc')
    upload = UploadFile(filename='owned.ics', file=io.BytesIO(content(start, end, [[start + timedelta(days=1)]])))
    result = await endpoints[('POST', '/api/calendar/import')](request, upload, 'Owned transfer')
    assert result['skipped'] == 1 and result['imported'] == 0
    with factory() as db:
        assert json.loads(db.get(cdb.CalendarEvent, 'owned-series').recurrence_exdates) == ['2026-12-03T10:00']
