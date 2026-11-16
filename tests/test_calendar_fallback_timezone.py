"""Non-ISO offset inputs must retain UTC provenance when stored as naive UTC."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.calendar_routes as calendar


OFFSET_DATES = [
    ('Sat, 05 Jan 2030 14:00:00 +0900', '2030-01-05T05:00:00'),
    ('January 5, 2030 14:00 +0530', '2030-01-05T08:30:00'),
    ('January 5, 2030 14:00 +0545', '2030-01-05T08:15:00'),
    ('January 5, 2030 14:00 -0330', '2030-01-05T17:30:00'),
    ('January 5, 2030 14:00 UTC', '2030-01-05T14:00:00'),
    ('Sat, 05 Jan 2030 14:00:00 GMT', '2030-01-05T14:00:00'),
]


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'calendar.db'}",
                           connect_args={"check_same_thread": False})
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(calendar, 'SessionLocal', factory)
    app = FastAPI()

    @app.middleware('http')
    async def identify(request, call_next):
        request.state.current_user = 'fallback-timezone-test'
        return await call_next(request)

    app.include_router(calendar.setup_calendar_routes())
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        engine.dispose()


@pytest.mark.parametrize('source,expected', OFFSET_DATES)
def test_pair_keeps_offset_provenance_and_legacy_parser_stays_naive(source, expected):
    legacy = calendar._parse_dt(source)
    parsed, is_utc = calendar._parse_dt_pair(source)

    assert legacy == parsed == datetime.fromisoformat(expected)
    assert legacy.tzinfo is parsed.tzinfo is None
    assert is_utc is True


@pytest.mark.parametrize('source,expected', OFFSET_DATES)
@pytest.mark.parametrize('operation', ['create', 'update'])
def test_offset_input_is_saved_and_serialized_as_utc(api, source, expected, operation):
    client, factory = api
    if operation == 'create':
        response = client.post('/api/calendar/events', json={
            'summary': 'Offset input', 'dtstart': source,
            'dtend': source.replace('14:00', '15:00'),
        })
        assert response.status_code == 200, response.text
        uid = response.json()['uid']
    else:
        seed = client.post('/api/calendar/events', json={
            'summary': 'Naive original', 'dtstart': '2030-01-01T09:00:00',
        })
        assert seed.status_code == 200
        uid = seed.json()['uid']
        response = client.put(f'/api/calendar/events/{uid}', json={
            'dtstart': source, 'dtend': source.replace('14:00', '15:00'),
        })
        assert response.status_code == 200, response.text
    saved = client.get(f'/api/calendar/events/{uid}').json()['event']
    assert saved['dtstart'] == expected + 'Z'
    assert saved['dtend'] == (datetime.fromisoformat(expected) + timedelta(hours=1)).isoformat() + 'Z'
    assert response.json()['event']['dtstart'] == expected + 'Z'
    assert saved['is_utc'] is True
    with factory() as db:
        row = db.get(cdb.CalendarEvent, uid)
        assert row.dtstart == datetime.fromisoformat(expected)
        assert row.dtstart.tzinfo is None
        assert row.is_utc is True


@pytest.mark.parametrize('source,expected,is_utc,all_day', [
    ('2030-01-05T14:00:00+09:00', '2030-01-05T05:00:00Z', True, False),
    ('2030-01-05T14:00:00Z', '2030-01-05T14:00:00Z', True, False),
    ('2030-01-05T14:00:00', '2030-01-05T14:00:00', False, False),
    ('January 5, 2030 14:00', '2030-01-05T14:00:00', False, False),
    ('2030-01-05', '2030-01-05', False, True),
])
def test_supported_iso_naive_and_date_only_inputs_keep_their_contract(api, source, expected, is_utc, all_day):
    client, _ = api
    response = client.post('/api/calendar/events', json={
        'summary': 'Positive control', 'dtstart': source, 'all_day': all_day,
    })
    assert response.status_code == 200, response.text
    saved = response.json()['event']
    assert saved['dtstart'] == expected
    assert saved['is_utc'] is is_utc


def test_recurring_offset_input_reads_back_in_utc(api):
    client, _ = api
    response = client.post('/api/calendar/events', json={
        'summary': 'Offset recurrence', 'dtstart': OFFSET_DATES[0][0],
        'rrule': 'FREQ=DAILY;COUNT=2',
    })
    assert response.status_code == 200, response.text
    listed = client.get('/api/calendar/events', params={
        'start': '2030-01-05T00:00:00Z', 'end': '2030-01-07T00:00:00Z',
    })
    assert listed.status_code == 200, listed.text
    assert [e['dtstart'] for e in listed.json()['events']] == [
        '2030-01-05T05:00:00Z', '2030-01-06T05:00:00Z',
    ]
