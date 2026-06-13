"""Regression: parse_due_for_user must handle time-first phrasings.

The tool schema and tool_index both advertise '11pm today' as a valid
due_date example. The parser's natural-language branch only matched
day-first format ('today at 11pm'), so time-first strings like '3pm today'
raised ValueError, fell back to the raw string, and the ISO-only reminder
scanner never fired the note. Fixes #3302.
"""
from datetime import datetime, timezone

import routes.calendar_routes as calendar_routes
from src.user_time import clear_user_time_context, set_user_tz_name, set_user_tz_offset


class _FixedNow(datetime):
    """Freeze server clock at 2026-06-07T10:00:00 UTC for deterministic tests."""
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 6, 7, 10, 0, 0, tzinfo=timezone.utc)
        if tz is not None:
            return value.astimezone(tz)
        return value.replace(tzinfo=None)


def setup_function():
    clear_user_time_context()
    set_user_tz_offset(0)
    set_user_tz_name("UTC")


def teardown_function():
    clear_user_time_context()


def test_time_first_today(monkeypatch):
    monkeypatch.setattr(calendar_routes, "datetime", _FixedNow)
    result = calendar_routes.parse_due_for_user("3pm today")
    assert result.startswith("2026-06-07T15:00:00")


def test_time_first_today_11pm(monkeypatch):
    monkeypatch.setattr(calendar_routes, "datetime", _FixedNow)
    result = calendar_routes.parse_due_for_user("11pm today")
    assert result.startswith("2026-06-07T23:00:00")


def test_time_first_tomorrow(monkeypatch):
    monkeypatch.setattr(calendar_routes, "datetime", _FixedNow)
    result = calendar_routes.parse_due_for_user("9am tomorrow")
    assert result.startswith("2026-06-08T09:00:00")


def test_time_first_with_minutes(monkeypatch):
    monkeypatch.setattr(calendar_routes, "datetime", _FixedNow)
    result = calendar_routes.parse_due_for_user("2:30pm tomorrow")
    assert result.startswith("2026-06-08T14:30:00")


def test_day_first_still_works(monkeypatch):
    """Existing day-first format must not regress."""
    monkeypatch.setattr(calendar_routes, "datetime", _FixedNow)
    result = calendar_routes.parse_due_for_user("today at 3pm")
    assert result.startswith("2026-06-07T15:00:00")
