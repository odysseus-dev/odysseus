"""Tests for the calendar check-in digest windows (src/task_scheduler.py)."""
import inspect
from datetime import datetime, timedelta

from src.task_scheduler import TaskScheduler, _digest_windows


def test_windows_are_contiguous_with_no_gap():
    now = datetime(2026, 6, 2, 9, 0, 0)
    windows = _digest_windows(now)
    # Each window starts exactly where the previous ended — no gap between
    # buckets (the old code jumped from now+7d to now+8d, dropping events).
    for (prev, cur) in zip(windows, windows[1:]):
        assert cur[1] == prev[2]
    assert windows[0][1] == now
    assert windows[-1][2] == now + timedelta(days=30)


def test_event_seven_and_a_half_days_out_is_covered():
    now = datetime(2026, 6, 2, 9, 0, 0)
    event = now + timedelta(days=7, hours=12)  # fell in the old 7-8 day gap
    buckets = [label for label, start, end in _digest_windows(now) if start <= event < end]
    assert buckets, "event ~7.5 days out should land in a digest window"


def test_boundary_event_belongs_to_later_window_only():
    now = datetime(2026, 6, 2, 9, 0, 0)
    windows = _digest_windows(now)

    first_boundary = windows[1][1]
    second_boundary = windows[2][1]

    first_buckets = [label for label, start, end in windows if start <= first_boundary < end]
    second_buckets = [label for label, start, end in windows if start <= second_boundary < end]

    assert first_buckets == ["this_week"]
    assert second_buckets == ["next_30_days"]


def test_checkin_calendar_query_uses_exclusive_window_end():
    body = inspect.getsource(TaskScheduler._execute_checkin)

    assert "_CE.dtstart < _e" in body
    assert "_CE.dtstart <= _e" not in body
