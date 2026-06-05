"""Regression: compute_next_run must fail closed on a malformed scheduled_time.

compute_next_run parsed scheduled_time as "HH:MM" with a bare
`int(parts[0]), int(parts[1])` and no validation, so a value like "9", "9am",
"25:00", "9:" or ":30" raised IndexError/ValueError. The POST /tasks create
route calls it with the user/LLM-supplied scheduled_time *before* its try block
(and only validates cron), so a bad value surfaced as an unhandled 500 instead
of a clean 400 — and the same crash could fire inside the scheduler loop when
recomputing next_run for an already-stored bad row.

Now it fails closed (returns None) like an invalid cron expression does.
"""
from datetime import datetime

from src.task_scheduler import compute_next_run


def test_malformed_scheduled_time_returns_none():
    now = datetime(2026, 6, 2, 12, 0)
    for bad in ("9", "9am", "09", "9:", ":30", "abc", "25:00", "09:99", ""):
        assert compute_next_run("daily", bad, after=now) is None, bad


def test_valid_scheduled_time_still_computes():
    now = datetime(2026, 6, 2, 8, 0)
    assert compute_next_run("daily", "09:00", after=now) == datetime(2026, 6, 2, 9, 0)
