"""Regression tests for the datetime.utcnow() removal in src/event_bus.py (#1116).

Importing src.event_bus is cheap and dependency-free: its module-level imports are
asyncio/json/logging/os/datetime/typing, and the `from core.database import ...`
calls are lazy (inside `_handle_event`), so no DB/sqlalchemy stack is pulled in.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.event_bus import _utcnow_naive


def test_utcnow_naive_returns_naive_utc():
    now = _utcnow_naive()
    # Must be naive to match the naive ScheduledTask.next_run column it writes.
    assert now.tzinfo is None
    ref = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((ref - now).total_seconds()) < 5


def test_next_run_comparison_stays_naive_and_comparable():
    # _handle_event sets task.next_run = _utcnow_naive() then compares
    # `next_run > _utcnow_naive()`. Both sides must stay naive; an aware value
    # would raise TypeError on that comparison.
    next_run = _utcnow_naive() - timedelta(seconds=1)
    assert next_run.tzinfo is None
    assert next_run < _utcnow_naive()  # the line-108 comparison, must not raise

    aware = datetime.now(timezone.utc)
    with pytest.raises(TypeError):
        _ = aware < _utcnow_naive()
