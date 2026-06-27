"""Regression coverage for bidirectional CalDAV sync plumbing.

These tests avoid a live CalDAV server. They pin the local invariants that keep
Odysseus-created CalDAV events from being pruned before they can be pushed.
"""

from datetime import datetime
import importlib.util
from pathlib import Path
import sys

from src.caldav_writeback import build_event_ical


def test_event_to_ical_serializes_core_fields_and_rrule():
    ical = build_event_ical({
        "uid": "evt-123",
        "summary": "Planning",
        "description": "Bring notes",
        "location": "HQ",
        "dtstart": datetime(2026, 6, 5, 9, 0),
        "dtend": datetime(2026, 6, 5, 10, 0),
        "all_day": False,
        "is_utc": False,
        "rrule": "FREQ=WEEKLY;COUNT=2",
    })

    assert "UID:evt-123" in ical
    assert "SUMMARY:Planning" in ical
    assert "DESCRIPTION:Bring notes" in ical
    assert "LOCATION:HQ" in ical
    assert "RRULE:FREQ=WEEKLY;COUNT=2" in ical


def test_caldav_pull_prune_skips_unsynced_or_pending_local_rows():
    source = Path("src/caldav_sync.py").read_text()

    assert 'existing.caldav_sync_pending in {"create", "update"}' in source
    assert "CalendarEvent.remote_href.isnot(None)" in source
    assert "CalendarEvent.caldav_sync_pending.is_(None)" in source


def test_http_calendar_writes_mark_pending_and_push_after_commit():
    source = Path("routes/calendar_routes.py").read_text()

    assert 'caldav_sync_pending="create" if cal.source == "caldav" else None' in source
    assert 'ev.caldav_sync_pending = "update"' in source
    assert 'await _push_caldav_event_after_commit(owner, uid, "create")' in source
    assert 'await _push_caldav_event_after_commit(owner, base_uid, "update")' in source
    assert 'await _push_caldav_event_after_commit(owner, base_uid, "delete")' in source
    assert "_record_caldav_delete_tombstone(db, ev, owner)" in source
    assert 'not result.get("ok")' in source


def test_agent_calendar_writes_share_caldav_push_path():
    source = Path("src/tools/calendar.py").read_text()

    assert "_push_caldav_event_after_commit" in source
    assert 'caldav_sync_pending="create" if cal.source == "caldav" else None' in source
    assert 'ev.caldav_sync_pending = "update"' in source
    assert 'await _push_caldav_event_after_commit(owner, uid, "create")' in source
    assert 'await _push_caldav_event_after_commit(owner, base_uid, "update")' in source
    assert 'await _push_caldav_event_after_commit(owner, base_uid, "delete")' in source
    assert "_record_caldav_delete_tombstone(db, ev, owner)" in source


def test_database_declares_and_migrates_caldav_remote_metadata():
    source = Path("core/database.py").read_text()

    for needle in [
        "class CalendarDeletedEvent",
        "remote_href = Column(String, nullable=True)",
        "remote_etag = Column(String, nullable=True)",
        "caldav_sync_pending = Column(String, nullable=True)",
        "caldav_base_url = Column(String, nullable=True)",
        "ALTER TABLE calendar_events ADD COLUMN remote_href TEXT",
        "ALTER TABLE calendar_events ADD COLUMN remote_etag TEXT",
        "ALTER TABLE calendar_events ADD COLUMN caldav_sync_pending TEXT",
        "ALTER TABLE calendars ADD COLUMN caldav_base_url TEXT",
        "_migrate_add_caldav_sync_columns()",
    ]:
        assert needle in source


def test_failed_remote_delete_leaves_tombstone_and_later_retry_cleans_up(tmp_path, monkeypatch):
    import src.caldav_writeback as writeback

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'calendar.db'}")
    spec = importlib.util.spec_from_file_location("core.database", Path("core/database.py"))
    dbmod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "core.database", dbmod)
    spec.loader.exec_module(dbmod)

    CalendarCal = dbmod.CalendarCal
    CalendarDeletedEvent = dbmod.CalendarDeletedEvent
    CalendarEvent = dbmod.CalendarEvent
    TestingSessionLocal = dbmod.SessionLocal

    session = TestingSessionLocal()
    try:
        cal = CalendarCal(
            id="caldav-test",
            owner="alice",
            name="Remote",
            source="caldav",
            caldav_base_url="https://caldav.example/calendars/alice/main/",
        )
        ev = CalendarEvent(
            uid="evt-delete",
            calendar_id=cal.id,
            summary="Delete me",
            dtstart=datetime(2026, 6, 5, 9, 0),
            dtend=datetime(2026, 6, 5, 10, 0),
            remote_href="https://caldav.example/calendars/alice/main/evt-delete.ics",
        )
        session.add(cal)
        session.add(ev)
        session.commit()

        tombstone = CalendarDeletedEvent(
            uid=ev.uid,
            owner="alice",
            calendar_id=ev.calendar_id,
            remote_href=ev.remote_href,
            remote_etag=ev.remote_etag,
            caldav_base_url=cal.caldav_base_url,
            summary=ev.summary,
        )
        session.add(tombstone)
        session.delete(ev)
        session.commit()

        assert session.query(CalendarEvent).filter_by(uid="evt-delete").first() is None
        tombstone = session.query(CalendarDeletedEvent).filter_by(uid="evt-delete").first()
        assert tombstone is not None
        assert tombstone.remote_href.endswith("evt-delete.ics")
    finally:
        session.close()

    writeback._persist_writeback_result(
        "alice",
        "caldav-test",
        "evt-delete",
        {"ok": False, "error": "temporary remote delete failure"},
        delete=True,
    )

    session = TestingSessionLocal()
    try:
        tombstone = session.query(CalendarDeletedEvent).filter_by(uid="evt-delete").first()
        assert tombstone is not None
        assert "temporary remote delete failure" in tombstone.last_error
    finally:
        session.close()

    writeback._persist_writeback_result(
        "alice",
        "caldav-test",
        "evt-delete",
        {"ok": True},
        delete=True,
    )

    session = TestingSessionLocal()
    try:
        assert session.query(CalendarDeletedEvent).filter_by(uid="evt-delete").first() is None
        assert session.query(CalendarEvent).filter_by(uid="evt-delete").first() is None
    finally:
        session.close()
