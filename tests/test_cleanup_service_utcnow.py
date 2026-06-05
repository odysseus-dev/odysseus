"""Regression tests for the datetime.utcnow() removal in src/cleanup_service.py (#1116).

Importing src.cleanup_service is cheap and dependency-free: its only module-level
imports are logging/datetime/typing, and the `from src.database import ...` calls are
lazy (inside the functions), so no DB/sqlalchemy stack is pulled in here.
"""
from datetime import datetime, timedelta, timezone

from src.cleanup_service import _utcnow


def test_utcnow_returns_naive_utc():
    now = _utcnow()
    # Must be naive to match the naive DateTime columns this module compares against.
    assert now.tzinfo is None
    ref = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((ref - now).total_seconds()) < 5


def test_cutoff_math_stays_naive_and_comparable():
    # Guards the archive/delete cutoffs against a naive/aware TypeError regression:
    # an aware _utcnow() would raise when compared with the naive last_accessed column.
    cutoff = _utcnow() - timedelta(days=7)
    assert cutoff.tzinfo is None
    assert cutoff < _utcnow()
