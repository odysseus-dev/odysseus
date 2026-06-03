"""Digest window bounds must convert crew-local time to naive UTC.

When a check-in runs in a crew member's timezone, _digest_windows returns
tz-aware LOCAL bounds. The query strips tzinfo for comparison against
CalendarEvent.dtstart, which is stored as naive UTC. Stripping WITHOUT
converting compared local wall-clock against UTC rows, shifting every digest
bucket (today/this-week/next-30-days) by the user's UTC offset.
"""
from datetime import datetime, timezone, timedelta

import pytest

from src.task_scheduler import _naive_utc_bound


def test_tz_aware_bound_is_converted_to_utc():
    zi = pytest.importorskip("zoneinfo")
    ny = zi.ZoneInfo("America/New_York")  # June -> EDT = UTC-4
    local = datetime(2026, 6, 10, 9, 0, tzinfo=ny)
    assert _naive_utc_bound(local) == datetime(2026, 6, 10, 13, 0)


def test_fixed_offset_bound_is_converted():
    local = datetime(2026, 6, 10, 9, 0, tzinfo=timezone(timedelta(hours=9)))  # JST
    assert _naive_utc_bound(local) == datetime(2026, 6, 10, 0, 0)


def test_naive_bound_unchanged():
    naive = datetime(2026, 6, 10, 9, 0)
    assert _naive_utc_bound(naive) == naive
