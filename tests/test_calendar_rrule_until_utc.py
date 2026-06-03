"""Regression test for RRULE expansion with a UTC UNTIL value.

Standard ICS exporters (Google Calendar, Apple Calendar, Outlook,
Fastmail) emit recurrence rules of the form

    RRULE:FREQ=DAILY;UNTIL=20240105T090000Z

When such an event is imported, the calendar route stores the event's
``dtstart`` as a *naive* datetime (the DB column is naive; timed events
are converted to naive-UTC on import). dateutil >= 2.7 raises

    ValueError: RRULE UNTIL values must be specified in UTC
                when DTSTART is timezone-aware

whenever the UNTIL is tz-aware (carries a trailing ``Z``) but the
``dtstart`` is naive. ``_expand_rrule`` catches that ValueError and
*silently downgrades the event to non-recurring*, so every occurrence
after the first vanishes from the calendar.

This test pins the correct behaviour: a daily series bounded by a UTC
UNTIL must expand to all of its occurrences.
"""

from datetime import datetime
from types import SimpleNamespace

from tests.test_null_owner_gates import _import_calendar_helpers


_MOCK_CAL = SimpleNamespace(name="Personal", color="#5b8abf")


def _make_event(**overrides):
    defaults = {
        "uid": "evt-until-utc",
        "summary": "Standup",
        "dtstart": datetime(2024, 1, 1, 9, 0),
        "dtend": datetime(2024, 1, 1, 9, 30),
        "all_day": False,
        "is_utc": True,
        "rrule": "",
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


def test_expand_rrule_with_utc_until_keeps_all_occurrences():
    """FREQ=DAILY;UNTIL=...Z must expand to every occurrence, not collapse
    to a single non-recurring event."""
    cal = _import_calendar_helpers()
    ev = _make_event(rrule="FREQ=DAILY;UNTIL=20240105T090000Z")

    results = cal._expand_rrule(ev, datetime(2024, 1, 1), datetime(2024, 1, 10))

    # Jan 1, 2, 3, 4, 5 — five daily occurrences up to and including UNTIL.
    assert len(results) == 5, (
        f"Expected 5 daily occurrences bounded by UTC UNTIL, got "
        f"{len(results)}: {[r['uid'] for r in results]}"
    )
    assert all(r["is_recurrence"] is True for r in results), (
        "Occurrences must be flagged as recurrences, not silently downgraded "
        f"to non-recurring: {[(r['uid'], r['is_recurrence']) for r in results]}"
    )
    assert results[0]["uid"] == "evt-until-utc::2024-01-01T09:00"
    assert results[-1]["uid"] == "evt-until-utc::2024-01-05T09:00"
