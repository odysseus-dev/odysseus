"""Regression: _parse_dt must understand "time-first" phrasings like parse_due_for_user does.

parse_due_for_user accepts both day-first ("tomorrow at 9am") and time-first
("9am tomorrow") forms, but _parse_dt (the parser _parse_dt_pair falls back to
for calendar event start/end) only handled the day-first form. A time-first
start like "3pm tomorrow" missed every branch and fell through to dateutil,
which raises ParserError on "3pm tomorrow", so creating an event with that
phrasing failed. Time-first is now handled identically to its day-first
equivalent, mirroring the sibling reminder parser.
"""
from routes.calendar_routes import _parse_dt


def test_time_first_today_equals_day_first():
    assert _parse_dt("3pm today") == _parse_dt("today at 3pm")


def test_time_first_tomorrow_equals_day_first():
    assert _parse_dt("9am tomorrow") == _parse_dt("tomorrow at 9am")


def test_time_first_with_minutes_equals_day_first():
    assert _parse_dt("2:30pm tomorrow") == _parse_dt("tomorrow at 2:30pm")


def test_time_first_tonight_maps_to_today():
    assert _parse_dt("11pm tonight") == _parse_dt("today at 11pm")


def test_time_first_yesterday_equals_day_first():
    assert _parse_dt("8am yesterday") == _parse_dt("yesterday at 8am")
