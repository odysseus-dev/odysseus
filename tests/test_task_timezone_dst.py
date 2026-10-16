"""Regression: schedule-triggered tasks keep their local time across DST.

The Tasks page converted the picked local time to a fixed UTC "HH:MM" before
saving, and compute_next_run treated it as a UTC wall clock. A task saved as
"daily at 9:00 AM" in America/Chicago during daylight time (14:00 UTC) fired
at 8:00 AM local once DST ended. Weekly tasks could also land on the wrong day
when the UTC conversion crossed midnight, because scheduled_day was not shifted.

Tasks now carry an optional IANA ``timezone``. When set, scheduled_time and
scheduled_day are local to it; a linked crew member's timezone still wins.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.task.task_routes import _validate_timezone
from src.task_scheduler import _resolve_task_timezone, compute_next_run

CHICAGO = "America/Chicago"


def test_daily_local_time_is_stable_across_dst_end():
    # 2026-11-01: US daylight time ends (CDT UTC-5 -> CST UTC-6).
    before = compute_next_run("daily", "09:00", after=datetime(2026, 10, 31, 12, 0), tz_name=CHICAGO)
    after = compute_next_run("daily", "09:00", after=datetime(2026, 11, 2, 12, 0), tz_name=CHICAGO)
    assert before == datetime(2026, 10, 31, 14, 0)  # 9:00 CDT
    assert after == datetime(2026, 11, 2, 15, 0)     # 9:00 CST


def test_weekly_evening_local_time_keeps_its_weekday():
    # Monday 20:00 in Chicago is Tuesday 01:00 UTC.
    nxt = compute_next_run("weekly", "20:00", scheduled_day=0,
                           after=datetime(2026, 10, 3, 12, 0), tz_name=CHICAGO)
    assert nxt == datetime(2026, 10, 6, 1, 0)


def test_task_timezone_used_when_no_crew_member():
    task = SimpleNamespace(crew_member_id=None, timezone=CHICAGO)
    assert _resolve_task_timezone(db=None, task=task) == CHICAGO


def test_task_without_timezone_keeps_legacy_utc():
    task = SimpleNamespace(crew_member_id=None, timezone=None)
    assert _resolve_task_timezone(db=None, task=task) is None


def test_validate_timezone():
    assert _validate_timezone(CHICAGO) == CHICAGO
    assert _validate_timezone("") is None
    assert _validate_timezone(None) is None
    with pytest.raises(HTTPException) as exc:
        _validate_timezone("Mars/Olympus_Mons")
    assert exc.value.status_code == 400
