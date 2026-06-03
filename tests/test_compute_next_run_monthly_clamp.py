"""compute_next_run monthly must clamp to short months, not skip them.

Old behavior: now.replace(day=31) raises ValueError in February, the
except set candidate = now, candidate <= now then jumped straight to the
NEXT month (which does clamp). A task scheduled for day 31 therefore never
fired in February, April, June, September or November.
"""

from datetime import datetime

import pytest

from src.task_scheduler import compute_next_run


@pytest.mark.parametrize(
    "day,after,expected",
    [
        (31, datetime(2026, 2, 15, 12, 0), datetime(2026, 2, 28, 9, 0)),
        (30, datetime(2026, 2, 1, 12, 0), datetime(2026, 2, 28, 9, 0)),
        (29, datetime(2026, 2, 1, 12, 0), datetime(2026, 2, 28, 9, 0)),
        (29, datetime(2028, 2, 1, 12, 0), datetime(2028, 2, 29, 9, 0)),
        (31, datetime(2026, 4, 1, 12, 0), datetime(2026, 4, 30, 9, 0)),
    ],
)
def test_monthly_clamps_to_last_day_of_current_short_month(day, after, expected):
    out = compute_next_run("monthly", "09:00", scheduled_day=day, after=after)
    assert out == expected


def test_monthly_clamped_slot_already_passed_rolls_to_next_month():
    out = compute_next_run(
        "monthly", "09:00", scheduled_day=31, after=datetime(2026, 2, 28, 10, 0)
    )
    assert out == datetime(2026, 3, 31, 9, 0)


def test_monthly_regular_day_still_fires_this_month():
    out = compute_next_run(
        "monthly", "09:00", scheduled_day=15, after=datetime(2026, 6, 10, 12, 0)
    )
    assert out == datetime(2026, 6, 15, 9, 0)


def test_monthly_regular_day_passed_rolls_to_next_month():
    out = compute_next_run(
        "monthly", "09:00", scheduled_day=15, after=datetime(2026, 6, 20, 12, 0)
    )
    assert out == datetime(2026, 7, 15, 9, 0)


def test_monthly_december_year_rollover():
    out = compute_next_run(
        "monthly", "09:00", scheduled_day=31, after=datetime(2026, 12, 31, 10, 0)
    )
    assert out == datetime(2027, 1, 31, 9, 0)
