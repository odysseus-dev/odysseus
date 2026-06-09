"""Regression: _parse_dt must understand "tonight" like parse_due_for_user does.

parse_due_for_user's natural-language regex accepts
`(today|tonight|tomorrow|tmrw|yesterday)`, but _parse_dt (the parser
_parse_dt_pair falls back to for calendar event start/end) only had
`(today|tomorrow|tmrw|yesterday)`. So an event start like "tonight at 9pm"
missed the today-branch and fell through to dateutil, which does not know the
word "tonight" and raises, breaking event creation for a phrasing that works
fine for reminders. "tonight" is now handled, mapped to today like the sibling.
"""
from routes.calendar_routes import _parse_dt


def test_tonight_with_time_parses_to_today_evening():
    got = _parse_dt("tonight at 9pm")
    ref = _parse_dt("today at 9pm")
    assert got.hour == 21 and got.minute == 0
    assert got.date() == ref.date()


def test_bare_tonight_is_today():
    assert _parse_dt("tonight").date() == _parse_dt("today").date()


def test_tonight_matches_today_time_exactly():
    assert _parse_dt("tonight at 7:30pm") == _parse_dt("today at 7:30pm")
