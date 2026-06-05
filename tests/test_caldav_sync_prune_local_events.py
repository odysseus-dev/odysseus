"""CalDAV sync must not prune locally-created events (#2704).

The prune step in `_sync_blocking` deletes events in the synced calendar+window
whose UID the server didn't just return, to propagate upstream deletions. But
`CalendarEvent` had no way to distinguish a server-pulled row from a locally
created one (agent / email triage / a UI event whose write-back failed), so it
also deleted events that were never on the server — silent data loss.

The fix adds an `origin` column and gates the prune on `origin == "caldav"`.
This test replicates the exact prune query against an in-memory DB (the prune is
pure DB logic; `_sync_blocking` itself needs a live CalDAV client) and asserts a
local-origin event survives while a server-origin one with a vanished UID does
not.
"""
import tempfile
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import CalendarEvent, CalendarCal

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)

_NOW = datetime(2026, 6, 4, 12, 0)
_START = _NOW - timedelta(days=90)
_END = _NOW + timedelta(days=365)


def _prune(db, calendar_id, seen_uids):
    """The exact prune filter from src/caldav_sync.py (post-fix)."""
    stale = db.query(CalendarEvent).filter(
        CalendarEvent.calendar_id == calendar_id,
        CalendarEvent.origin == "caldav",
        CalendarEvent.dtstart >= _START,
        CalendarEvent.dtstart <= _END,
        ~CalendarEvent.uid.in_(seen_uids) if seen_uids else CalendarEvent.uid.isnot(None),
    ).all()
    for ev in stale:
        db.delete(ev)
    db.commit()
    return len(stale)


def _seed():
    db = _TS()
    try:
        db.query(CalendarEvent).delete()
        db.query(CalendarCal).delete()
        db.add(CalendarCal(id="cal1", owner="alice", name="Work", source="caldav"))
        # A server-synced event whose UID is NO LONGER returned (deleted upstream).
        db.add(CalendarEvent(
            uid="server-gone@svc", calendar_id="cal1", summary="Old server event",
            dtstart=_NOW + timedelta(days=1), dtend=_NOW + timedelta(days=1, hours=1),
            origin="caldav",
        ))
        # A locally-created event (agent / triage / failed write-back) — origin NULL.
        db.add(CalendarEvent(
            uid="local-uuid", calendar_id="cal1", summary="Dentist",
            dtstart=_NOW + timedelta(days=2), dtend=_NOW + timedelta(days=2, hours=1),
            origin=None,
        ))
        db.commit()
    finally:
        db.close()


def test_local_event_survives_prune():
    _seed()
    db = _TS()
    try:
        # Server returned nothing (both UIDs absent from seen_uids).
        deleted = _prune(db, "cal1", seen_uids={"some-other-uid"})
        # Only the server-origin, now-vanished event is pruned.
        assert deleted == 1
        assert db.query(CalendarEvent).filter_by(uid="local-uuid").first() is not None
        assert db.query(CalendarEvent).filter_by(uid="server-gone@svc").first() is None
    finally:
        db.close()


def test_synced_event_still_returned_is_kept():
    _seed()
    db = _TS()
    try:
        # The server still returns the synced event → it must be kept.
        deleted = _prune(db, "cal1", seen_uids={"server-gone@svc"})
        assert deleted == 0
        assert db.query(CalendarEvent).filter_by(uid="server-gone@svc").first() is not None
        assert db.query(CalendarEvent).filter_by(uid="local-uuid").first() is not None
    finally:
        db.close()
