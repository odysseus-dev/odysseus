"""Regression tests for calendar recurrence expansion.

Tests _expand_rrule and _resolve_base_uid — imported directly from
routes/calendar_routes using the shared stub-friendly test helper.
No live DB or FastAPI test client needed.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.helpers.calendar_routes import import_calendar_routes


# ── _resolve_base_uid ──────────────────────────────────────────────────

def test_resolve_base_uid_plain_passthrough():
    cal = import_calendar_routes()
    assert cal._resolve_base_uid("evt-123") == "evt-123"


def test_resolve_base_uid_compound_strips_suffix_date():
    cal = import_calendar_routes()
    assert cal._resolve_base_uid("evt-123::2026-06-15") == "evt-123"


def test_resolve_base_uid_compound_strips_suffix_datetime():
    cal = import_calendar_routes()
    assert cal._resolve_base_uid("evt-123::2026-06-15T09:00") == "evt-123"


def test_resolve_base_uid_rejects_empty():
    cal = import_calendar_routes()
    with pytest.raises(ValueError, match="empty uid"):
        cal._resolve_base_uid("")


def test_resolve_base_uid_rejects_missing_base():
    cal = import_calendar_routes()
    with pytest.raises(ValueError, match="malformed compound UID"):
        cal._resolve_base_uid("::2026-06-15")


# ── _expand_rrule ──────────────────────────────────────────────────────

_MOCK_CAL = SimpleNamespace(name="Personal", color="#5b8abf")


def _make_event(**overrides):
    """Build a dict-shaped mock CalendarEvent for _expand_rrule."""
    defaults = {
        "uid": "evt-test-001",
        "summary": "Test Event",
        "dtstart": datetime(2026, 6, 1, 9, 0),
        "dtend": datetime(2026, 6, 1, 10, 0),
        "all_day": False,
        "is_utc": False,
        "rrule": "",
        "calendar": _MOCK_CAL.name,
        "calendar_id": "cal-001",
        "color": None,
        "description": "",
        "location": "",
        "event_type": None,
        "importance": "normal",
    }
    defaults.update(overrides)
    ev = SimpleNamespace(**defaults)
    ev.calendar = _MOCK_CAL
    return ev


def test_expand_non_recurring_returns_single():
    """Non-recurring events pass through unchanged with series_uid=uid."""
    cal = import_calendar_routes()
    ev = _make_event(rrule="")
    results = cal._expand_rrule(ev, datetime(2026, 5, 1), datetime(2026, 7, 1))

    assert len(results) == 1
    r = results[0]
    assert r["uid"] == "evt-test-001"
    assert r["series_uid"] == "evt-test-001"
    assert r["is_recurrence"] is False


def test_expand_yearly_old_dtstart_later_year_single_occurrence():
    """Create an old DTSTART + FREQ=YEARLY, query a later year, verify
    exactly one occurrence is returned.

    This is the explicit regression case from PR review feedback.
    """
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-bday-001",
        summary="Annual Review",
        dtstart=datetime(2020, 4, 15, 10, 0),
        dtend=datetime(2020, 4, 15, 11, 0),
        rrule="FREQ=YEARLY",
    )

    # Query year 2028 — should find the 2028-04-15 occurrence only
    results = cal._expand_rrule(ev, datetime(2028, 1, 1), datetime(2029, 1, 1))

    assert len(results) == 1, (
        f"Expected exactly 1 yearly occurrence in 2028, got {len(results)}: "
        f"{[r['uid'] for r in results]}"
    )
    r = results[0]
    assert r["uid"] == "evt-bday-001::2028-04-15T10:00"
    assert r["dtstart"] == "2028-04-15T10:00:00"
    assert r["series_uid"] == "evt-bday-001"
    assert r["is_recurrence"] is True
    assert r["summary"] == "Annual Review"


def test_expand_yearly_narrow_window_after_dtstart_returns_one():
    """DTSTART=2020, query just two months in 2029 — should return
    exactly one occurrence (the one that falls in that window).
    """
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-ann",
        dtstart=datetime(2020, 3, 1),
        dtend=datetime(2020, 3, 2),
        all_day=True,
        rrule="FREQ=YEARLY",
    )
    results = cal._expand_rrule(ev, datetime(2029, 1, 1), datetime(2029, 4, 1))

    assert len(results) == 1
    assert results[0]["uid"] == "evt-ann::2029-03-01"
    assert results[0]["all_day"] is True


def test_expand_yearly_strict_before_window_returns_empty():
    """DTSTART=2020, query a window that ends before the yearly
    occurrence in that year. Should return zero.
    """
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-late",
        dtstart=datetime(2020, 12, 25),
        dtend=datetime(2020, 12, 26),
        all_day=True,
        rrule="FREQ=YEARLY",
    )
    results = cal._expand_rrule(ev, datetime(2026, 1, 1), datetime(2026, 6, 1))

    assert len(results) == 0


def test_expand_yearly_strict_after_window_returns_empty():
    """DTSTART=2020. Query a window that starts after the occurrence in
    that year. Should return zero.
    """
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-early",
        dtstart=datetime(2020, 1, 15),
        dtend=datetime(2020, 1, 16),
        all_day=True,
        rrule="FREQ=YEARLY",
    )
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 12, 31))

    assert len(results) == 0


def test_expand_weekly_unique_no_overwrites():
    """Multiple occurrences from the same series must have unique UIDs
    so _allEvents[uid] = ev doesn't overwrite earlier ones.
    """
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-wk",
        dtstart=datetime(2026, 6, 1, 9, 0),
        dtend=datetime(2026, 6, 1, 10, 0),
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
    )
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 7, 1))

    # June 2026 has 4 Mondays, 5 Wednesdays, 4 Fridays = 13 occurrences
    assert len(results) >= 10  # sanity lower bound

    uids = [r["uid"] for r in results]
    assert len(uids) == len(set(uids)), f"Duplicate UIDs found: {uids}"

    for r in results:
        assert r["series_uid"] == "evt-wk"
        assert r["is_recurrence"] is True


def test_expand_monthly_all_day():
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-rent",
        dtstart=datetime(2026, 1, 1),
        dtend=datetime(2026, 1, 2),
        all_day=True,
        rrule="FREQ=MONTHLY",
    )
    results = cal._expand_rrule(ev, datetime(2026, 1, 1), datetime(2026, 12, 31))
    assert len(results) == 12
    for r in results:
        assert r["uid"].startswith("evt-rent::")
        assert r["all_day"] is True


def test_expand_bad_rrule_graceful():
    """Malformed rrule should fall back to returning the base event,
    but only when the base event overlaps the requested window."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-broken",
        rrule="FREQ=GARBAGE",
    )
    # Base event (2026-06-01) falls inside the window — should appear
    results = cal._expand_rrule(ev, datetime(2026, 1, 1), datetime(2026, 12, 31))
    assert len(results) == 1
    assert results[0]["uid"] == "evt-broken"
    assert results[0]["is_recurrence"] is False


def test_expand_bad_rrule_fallback_rejects_non_overlapping():
    """Malformed rrule with a base event outside the requested window
    must return zero results, not leak the event into an unrelated range."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-old-broken",
        dtstart=datetime(2020, 1, 1, 9, 0),
        dtend=datetime(2020, 1, 1, 10, 0),
        rrule="FREQ=GARBAGE",
    )
    # Query a far-future window that the base event doesn't overlap
    results = cal._expand_rrule(ev, datetime(2030, 1, 1), datetime(2030, 2, 1))
    assert len(results) == 0, (
        f"Malformed rrule base event outside window should return empty, "
        f"got {len(results)}: {[r['uid'] for r in results]}"
    )


def test_expand_exclusive_end_boundary():
    """An occurrence whose start equals the window end must be excluded.
    The contract is [start, end), same as the non-recurring SQL filter."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-daily",
        dtstart=datetime(2026, 6, 1, 9, 0),
        dtend=datetime(2026, 6, 1, 10, 0),
        rrule="FREQ=DAILY",
    )
    # Query [Jun 1, Jun 5) — occurrences on Jun 1-4 only
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 6, 5))
    uids = [r["uid"] for r in results]
    assert len(results) == 4, f"Expected 4 (Jun 1-4), got {len(results)}: {uids}"
    assert "evt-daily::2026-06-05T09:00" not in uids, "Jun 5 is at end boundary, must be excluded"


def test_expand_multi_day_crossing_range_start():
    """A multi-day occurrence that starts before the window but ends inside
    it must be included (matching non-recurring overlap: dtend > start)."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-weekly-multi",
        summary="Weekend Trip",
        dtstart=datetime(2026, 5, 29, 18, 0),   # Friday evening
        dtend=datetime(2026, 6, 1, 12, 0),       # Monday noon
        rrule="FREQ=WEEKLY",
    )
    # Query the Monday window — the occurrence starts Fri but ends Mon,
    # so it overlaps the query.
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 6, 2))
    # The 2026-06-05 occurrence starts Fri Jun 5 and ends Mon Jun 8 —
    # that crosses [Jun 1, Jun 2): occ_start=2026-06-05 >= end=2026-06-02 → excluded.
    # The 2026-05-29 occurrence starts Fri May 29 and ends Mon Jun 1 —
    # occ_end=2026-06-01T12:00 > start=2026-06-01 → included.
    assert len(results) == 1, (
        f"Expected 1 occurrence crossing into the window, got {len(results)}: "
        f"{[r['uid'] for r in results]}"
    )
    assert results[0]["uid"] == "evt-weekly-multi::2026-05-29T18:00"


def test_expand_multi_day_fully_before_window():
    """A multi-day occurrence that ends exactly at the window start
    must be excluded (occ_end <= start)."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-multi",
        dtstart=datetime(2026, 5, 29, 18, 0),
        dtend=datetime(2026, 6, 1, 0, 0),   # ends at midnight Jun 1
        rrule="FREQ=WEEKLY",
    )
    # Query starting Jun 1 midnight — occ_end <= start, excluded
    results = cal._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 6, 8))
    assert len(results) == 1  # only the next week's occurrence (Jun 5-8)
    assert results[0]["uid"] == "evt-multi::2026-06-05T18:00"


def test_expand_metadata_inheritance():
    """Occurrence dicts must carry the base event's metadata
    (summary, importance, event_type, color, location)."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-meta",
        summary="Board Meeting",
        dtstart=datetime(2026, 1, 1, 14, 0),
        dtend=datetime(2026, 1, 1, 16, 0),
        rrule="FREQ=MONTHLY",
        event_type="work",
        importance="critical",
        location="Room 42",
    )
    results = cal._expand_rrule(ev, datetime(2026, 1, 1), datetime(2026, 3, 1))
    assert len(results) == 2  # Jan + Feb
    for r in results:
        assert r["summary"] == "Board Meeting"
        assert r["importance"] == "critical"
        assert r["event_type"] == "work"
        assert r["location"] == "Room 42"


def test_expand_daily_rrule_large_window_is_capped_and_marked_truncated():
    """Wide recurring windows must not materialize unbounded occurrence lists."""
    cal = import_calendar_routes()
    ev = _make_event(
        uid="evt-daily-cap",
        dtstart=datetime(2020, 1, 1, 9, 0),
        dtend=datetime(2020, 1, 1, 10, 0),
        rrule="FREQ=DAILY",
    )

    results = cal._expand_rrule(ev, datetime(2020, 1, 1), datetime(2030, 1, 1))

    assert len(results) == cal._RRULE_EXPANSION_LIMIT
    assert results[-1]["uid"] == "evt-daily-cap::2022-09-26T09:00"
    assert all(r["truncated"] is True for r in results)
