"""daily_brief must render calendar event times in LOCAL wall time.

CalDAV-imported CalendarEvent rows store dtstart/dtend as naive UTC instants
with is_utc=True (caldav_sync._to_utc_naive). action_daily_brief formatted
e.dtstart with strftime directly, so an event stored with TZID=Europe/Berlin
showed up 2 hours early (its UTC time). The same mismatch applied to the
day-window query, which compared local-midnight bounds against UTC instants.

The conversion now lives in _event_local_start / _local_to_utc_naive; both
take an injectable tz so the tests do not depend on the machine timezone.
"""
from datetime import datetime, timedelta, timezone

from src.builtin_actions import _event_local_start, _local_to_utc_naive

BERLIN_SUMMER = timezone(timedelta(hours=2))


def test_utc_event_renders_local_wall_time():
    # 09:00 Europe/Berlin stored as naive 07:00 UTC with is_utc=True
    start = _event_local_start(datetime(2026, 6, 11, 7, 0), True, tz=BERLIN_SUMMER)
    assert start.strftime("%H:%M") == "09:00"


def test_legacy_local_event_passes_through_unchanged():
    dt = datetime(2026, 6, 11, 9, 0)
    assert _event_local_start(dt, False, tz=BERLIN_SUMMER) == dt


def test_day_window_bounds_convert_to_utc():
    # Local midnight in Berlin summer time is 22:00 UTC the previous day, so
    # a 00:30 local event (22:30 UTC, is_utc row) stays in today's brief.
    local_midnight = datetime(2026, 6, 11, 0, 0)
    assert _local_to_utc_naive(local_midnight, tz=BERLIN_SUMMER) == datetime(2026, 6, 10, 22, 0)


def test_system_tz_roundtrip():
    # Without tz injection the helpers use the system zone; converting a
    # local wall time to UTC and rendering it back must be lossless.
    local = datetime(2026, 6, 11, 9, 0)
    assert _event_local_start(_local_to_utc_naive(local), True) == local
