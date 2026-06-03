"""Digest boundary events must land in exactly one window.

_digest_windows buckets are contiguous and share endpoints (by design, so
nothing falls in a gap), but the check-in digest queried each window with
an INCLUSIVE upper bound (dtstart <= end), so an event starting exactly at
now+2d or now+7d satisfied two adjacent windows and was listed twice in
the digest. _checkin_calendar_events now uses a half-open [start, end)
bound.
"""
import tempfile
from datetime import datetime, timedelta

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import CalendarEvent, CalendarCal
from src.task_scheduler import _checkin_calendar_events, _digest_windows

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(f"sqlite:///{_TMPDB.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)

NOW = datetime(2026, 6, 2, 9, 0, 0)


def _bucket_labels(dtstart, status="confirmed"):
    db = _TS()
    try:
        db.query(CalendarEvent).delete(); db.query(CalendarCal).delete()
        db.add(CalendarCal(id="calA", owner="alice", name="A"))
        db.add(CalendarEvent(uid="e1", calendar_id="calA", summary="evt",
                             dtstart=dtstart, dtend=dtstart + timedelta(hours=1),
                             status=status))
        db.commit()
        return [
            label
            for label, start, end in _digest_windows(NOW)
            if _checkin_calendar_events(db, "alice", start, end)
        ]
    finally:
        db.close()


def test_event_on_two_day_boundary_lands_in_exactly_one_window():
    assert _bucket_labels(NOW + timedelta(days=2)) == ["this_week"]


def test_event_on_seven_day_boundary_lands_in_exactly_one_window():
    assert _bucket_labels(NOW + timedelta(days=7)) == ["next_30_days"]


def test_mid_window_event_lands_once():
    assert _bucket_labels(NOW + timedelta(days=1)) == ["today_tomorrow"]
    assert _bucket_labels(NOW + timedelta(days=7, hours=12)) == ["next_30_days"]


def test_cancelled_events_excluded():
    assert _bucket_labels(NOW + timedelta(days=1), status="cancelled") == []


def test_every_event_in_range_is_covered_exactly_once():
    # sweep dtstarts across the full 30-day range including both boundaries
    for hours in range(0, 30 * 24, 12):
        labels = _bucket_labels(NOW + timedelta(hours=hours))
        assert len(labels) == 1, f"event at +{hours}h in {labels}"
