"""Regression: calendar check-in digest silently dropped events 7-8 days out.

The original window list had:
  this_week:    now+2d .. now+7d
  next_30_days: now+8d .. now+30d
Events with dtstart in (now+7d, now+8d) matched neither bucket.

Fix: start next_30_days at now+7d so buckets are contiguous.
"""
from datetime import datetime, timedelta
import pytest

from src.task_scheduler import _digest_windows


def _windows(days_offset=0):
    now = datetime(2025, 6, 1, 9, 0, 0) + timedelta(days=days_offset)
    return _digest_windows(now)


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

def test_three_buckets_returned():
    assert len(_windows()) == 3


def test_bucket_labels():
    labels = [w[0] for w in _windows()]
    assert labels == ["today_tomorrow", "this_week", "next_30_days"]


def test_first_bucket_starts_at_now():
    now = datetime(2025, 6, 1, 12, 0)
    windows = _digest_windows(now)
    assert windows[0][1] == now


def test_last_bucket_ends_at_30_days():
    now = datetime(2025, 6, 1, 12, 0)
    windows = _digest_windows(now)
    assert windows[-1][2] == now + timedelta(days=30)


# ---------------------------------------------------------------------------
# Contiguity: no gap between adjacent buckets
# ---------------------------------------------------------------------------

def test_no_gap_between_today_tomorrow_and_this_week():
    ws = _windows()
    _, _, end_first = ws[0]
    _, start_second, _ = ws[1]
    assert end_first == start_second


def test_no_gap_between_this_week_and_next_30_days():
    """The key regression: next_30_days must start where this_week ends."""
    ws = _windows()
    _, _, end_second = ws[1]
    _, start_third, _ = ws[2]
    assert end_second == start_third, (
        f"Gap detected: this_week ends at {end_second} but "
        f"next_30_days starts at {start_third}"
    )


# ---------------------------------------------------------------------------
# Coverage of the formerly-dropped zone (day 7 to day 8)
# ---------------------------------------------------------------------------

def test_event_at_7d_4h_is_covered():
    """An event 7.5 days out must fall inside a bucket."""
    now = datetime(2025, 6, 1, 0, 0)
    windows = _digest_windows(now)
    event_time = now + timedelta(days=7, hours=4)

    covered = any(start <= event_time <= end for _, start, end in windows)
    assert covered, f"Event at {event_time} is not covered by any window"


def test_event_at_exactly_7d_boundary():
    """The boundary point now+7d must belong to exactly one window."""
    now = datetime(2025, 6, 1, 0, 0)
    windows = _digest_windows(now)
    boundary = now + timedelta(days=7)

    matches = [label for label, start, end in windows if start <= boundary <= end]
    assert len(matches) >= 1, f"Event at day-7 boundary not covered: {matches}"


def test_all_windows_have_positive_duration():
    for label, start, end in _windows():
        assert end > start, f"Window '{label}' has zero or negative duration"
