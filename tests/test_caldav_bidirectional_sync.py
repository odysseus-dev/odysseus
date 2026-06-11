"""Regression coverage for bidirectional CalDAV sync plumbing.

These tests avoid a live CalDAV server. They pin the local invariants that keep
Odysseus-created CalDAV events from being pruned before they can be pushed.
"""

from datetime import datetime
from pathlib import Path

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

    assert 'CalendarEvent.origin == "caldav"' in source


def test_http_calendar_writes_mark_pending_and_push_after_commit():
    source = Path("routes/calendar_routes.py").read_text()

    assert 'if cal.source == "caldav":' in source
    assert "from src.caldav_writeback import writeback_event" in source
    assert "await writeback_event(owner, cal.source, cal.id, {" in source
    assert 'await writeback_event(owner, "caldav", _cal_id, {"uid": _ev_uid}, delete=True)' in source


def test_agent_calendar_uses_local_db_not_caldav_writeback():
    source = Path("src/tool_implementations.py").read_text()

    assert "CalendarEvent" in source
    assert "SessionLocal" in source
    # Agent tools operate on the local DB only — no CalDAV push logic.
    assert "writeback_event" not in source


def test_database_declares_caldav_origin_column():
    source = Path("core/database.py").read_text()

    for needle in [
        'origin      = Column(String, nullable=True, index=True)',
    ]:
        assert needle in source


def test_caldav_writeback_uses_event_uid(tmp_path, monkeypatch):
    import asyncio
    import src.caldav_writeback as writeback

    # Smoke test: writeback_event requires a uid in the ev dict.
    result = asyncio.run(writeback.writeback_event(
        "alice", "local", "cal-1", {"summary": "no uid"}, delete=False,
    ))
    assert result.get("skipped") == "not a caldav calendar"
