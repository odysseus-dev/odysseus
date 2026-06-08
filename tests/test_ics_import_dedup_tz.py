"""ICS re-import must dedup tz-aware timed events.

import_ics stores a tz-aware DTSTART as naive UTC (e.g. 09:00 America/
New_York becomes 13:00), but the dedup key stripped tzinfo WITHOUT the UTC
conversion (kept 09:00 wall clock). So the dedup query never matched the
stored row and every re-import of a TZID event inserted a duplicate. The
shared _ics_naive_dtstart helper now drives both.
"""
from datetime import date, datetime, timezone, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from routes.calendar_routes import _ics_naive_dtstart


def test_tz_aware_dedup_key_matches_utc_storage_form():
    zi = pytest.importorskip("zoneinfo")
    ny = zi.ZoneInfo("America/New_York")
    dt = datetime(2026, 6, 15, 9, 0, tzinfo=ny)  # EDT = UTC-4 -> 13:00 UTC
    assert _ics_naive_dtstart(dt) == datetime(2026, 6, 15, 13, 0)


def test_fixed_offset_dedup_key_is_utc():
    dt = datetime(2026, 6, 15, 9, 0, tzinfo=timezone(timedelta(hours=2)))
    assert _ics_naive_dtstart(dt) == datetime(2026, 6, 15, 7, 0)


def test_naive_datetime_unchanged():
    dt = datetime(2026, 6, 15, 9, 0)
    assert _ics_naive_dtstart(dt) == dt


def test_all_day_date_becomes_midnight_datetime():
    assert _ics_naive_dtstart(date(2026, 6, 15)) == datetime(2026, 6, 15, 0, 0)


def test_dedup_key_equals_storage_conversion():
    zi = pytest.importorskip("zoneinfo")
    dt_val = datetime(2026, 11, 1, 9, 30, tzinfo=zi.ZoneInfo("America/New_York"))
    stored = dt_val.astimezone(timezone.utc).replace(tzinfo=None)
    assert _ics_naive_dtstart(dt_val) == stored
