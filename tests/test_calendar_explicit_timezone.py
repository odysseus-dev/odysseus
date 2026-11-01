from datetime import datetime

import pytest

from src.tools.calendar import _explicit_calendar_time, _normalize_local_event_times


@pytest.mark.parametrize('value,zone,expected', [
    ('2026-10-06T18:00', '+09:00', '2026-10-06T09:00'),
    ('2026-10-06T18:00', 'Asia/Tokyo', '2026-10-06T09:00'),
    ('2026-10-06T18:00', 'UTC', '2026-10-06T18:00'),
    ('2026-10-06T00:15', 'UTC+05:30', '2026-10-05T18:45'),
    ('2026-10-06T22:00', '-04:00', '2026-10-07T02:00'),
    ('2026-07-01T10:00', 'America/New_York', '2026-07-01T14:00'),
    ('2026-01-01T10:00', 'America/New_York', '2026-01-01T15:00'),
    ('2026-11-01T01:30-04:00', 'America/New_York', '2026-11-01T05:30'),
])
def test_explicit_zone_converts_once(value, zone, expected):
    assert _explicit_calendar_time(value, zone) == (datetime.fromisoformat(expected), True)


@pytest.mark.parametrize('value,zone', [
    ('2026-10-06T18:00', 'Imaginary/City'),
    ('2026-10-06T18:00', '+09:70'),
    ('2026-10-06T18:00Z', '+09:00'),
    ('2026-03-08T02:30', 'America/New_York'),
    ('2026-11-01T01:30', 'America/New_York'),
])
def test_invalid_or_ambiguous_zone_is_not_guessed(value, zone):
    with pytest.raises(ValueError):
        _explicit_calendar_time(value, zone)


@pytest.mark.parametrize('args', [
    {'local_start': '2026-10-06T18:00'},
    {'local_start': {'date': '2026-10-06'}},
    {'local_start': {'date': '2026-02-30', 'time': '18:00'}},
    {'local_start': {'date': '2026-10-06', 'time': '25:00'}},
    {'local_start': {'date': '2026-10-06', 'time': '18:00+09:00'}},
    {'local_start': {'date': '2026-10-06', 'time': '18:00'}, 'dtstart': '2026-10-06T09:00'},
    {'local_start': {'date': '2026-10-06', 'time': '18:00'}, 'all_day': True},
])
def test_invalid_local_time_shape_is_rejected(args):
    with pytest.raises(ValueError):
        _normalize_local_event_times(args)


def test_all_day_local_date_is_not_converted():
    args = {'local_start': {'date': '2026-10-06'}, 'all_day': True, 'timezone': 'Asia/Tokyo'}
    normalized = _normalize_local_event_times(args)
    assert normalized['dtstart'] == '2026-10-06'
    assert 'dtstart' not in args
