"""Digest boundary events must land in exactly one window.

_digest_windows buckets are contiguous and share endpoints (by design, so
nothing falls in a gap), but the check-in digest queried each window with
an INCLUSIVE upper bound (dtstart <= end), so an event starting exactly at
now+2d or now+7d satisfied two adjacent windows and was listed twice in
the digest. The query now lives in _digest_window_events with a
half-open [start, end) bound.
"""
from datetime import datetime, timedelta

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from src.task_scheduler import _digest_window_events, _digest_windows

Base = declarative_base()


class _Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    dtstart = Column(DateTime)
    status = Column(String, default="confirmed")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


NOW = datetime(2026, 6, 2, 9, 0, 0)


def _bucket_labels(db, dtstart, status="confirmed"):
    db.add(_Event(dtstart=dtstart, status=status))
    db.commit()
    labels = [
        label
        for label, start, end in _digest_windows(NOW)
        if _digest_window_events(db, _Event, start, end)
    ]
    db.query(_Event).delete()
    db.commit()
    return labels


def test_event_on_two_day_boundary_lands_in_exactly_one_window(db):
    labels = _bucket_labels(db, NOW + timedelta(days=2))
    assert labels == ["this_week"]


def test_event_on_seven_day_boundary_lands_in_exactly_one_window(db):
    labels = _bucket_labels(db, NOW + timedelta(days=7))
    assert labels == ["next_30_days"]


def test_mid_window_event_lands_once(db):
    labels = _bucket_labels(db, NOW + timedelta(days=1))
    assert labels == ["today_tomorrow"]
    labels = _bucket_labels(db, NOW + timedelta(days=7, hours=12))
    assert labels == ["next_30_days"]


def test_cancelled_events_excluded(db):
    labels = _bucket_labels(db, NOW + timedelta(days=1), status="cancelled")
    assert labels == []


def test_every_event_in_range_is_covered_exactly_once(db):
    # sweep dtstarts across the full 30-day range including both boundaries
    for hours in range(0, 30 * 24, 12):
        labels = _bucket_labels(db, NOW + timedelta(hours=hours))
        assert len(labels) == 1, f"event at +{hours}h in {labels}"
