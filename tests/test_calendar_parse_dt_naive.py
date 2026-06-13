"""Regression: _parse_dt's dateutil fallback must return naive datetimes.

_parse_dt documents that it returns local-naive datetimes to match the DB
schema (CalendarEvent.dtstart is naive), and every return path strips tz —
except the last-resort dateutil branch, which returned dateutil's value
verbatim. An offset-bearing non-ISO input (e.g. RFC-2822
"Mon, 05 Jan 2026 14:00:00 +0900", which datetime.fromisoformat rejects but
dateutil parses) therefore leaked a tz-aware datetime into the naive dtstart
column. On read-back, _expand_rrule compares ev.dtstart against naive window
bounds and raises "can't compare offset-naive and offset-aware datetimes".

The fallback now normalizes to UTC and strips tz, exactly like the ISO path.
"""
import pytest

from tests.test_null_owner_gates import _import_calendar_helpers

# Inputs datetime.fromisoformat() rejects (so they hit the dateutil fallback)
# but that carry a numeric UTC offset dateutil resolves to tz-aware.
_OFFSET_NONISO = [
    "Mon, 05 Jan 2026 14:00:00 +0900",
    "January 5, 2026 14:00 +0900",
]


@pytest.mark.parametrize("s", _OFFSET_NONISO)
def test_parse_dt_dateutil_fallback_returns_naive(s):
    cal = _import_calendar_helpers()
    d = cal._parse_dt(s)
    assert d.tzinfo is None, f"{s!r} leaked tz-aware: {d!r}"
    # +0900 14:00 -> 05:00 UTC, naive.
    assert (d.hour, d.minute) == (5, 0)


@pytest.mark.parametrize("s", _OFFSET_NONISO)
def test_parse_dt_pair_fallback_returns_naive(s):
    cal = _import_calendar_helpers()
    dt, _is_utc = cal._parse_dt_pair(s)
    assert dt.tzinfo is None, f"{s!r} leaked tz-aware via _parse_dt_pair: {dt!r}"


def test_parse_dt_naive_input_unchanged():
    cal = _import_calendar_helpers()
    d = cal._parse_dt("January 5, 2026 14:00")  # no offset -> stays as parsed
    assert d.tzinfo is None
    assert (d.hour, d.minute) == (14, 0)
