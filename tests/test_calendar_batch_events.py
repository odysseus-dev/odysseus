"""Test that do_manage_calendar handles the batch {"events": [...]} format
that models like deepseek-v4-flash emit instead of individual create_event calls.
"""

import json
import sys
import uuid

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarEvent

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


async def test_batch_events_with_datetime_objects():
    """Model emits {"events": [{"summary": ..., "start": {"dateTime": ...}, "end": {"dateTime": ...}}]}."""
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    payload = {
        "events": [
            {
                "summary": "Morning Gym",
                "start": {"dateTime": "2026-06-09T06:00:00+05:30"},
                "end": {"dateTime": "2026-06-09T07:00:00+05:30"},
            },
            {
                "summary": "Morning Gym",
                "start": {"dateTime": "2026-06-10T06:00:00+05:30"},
                "end": {"dateTime": "2026-06-10T07:00:00+05:30"},
            },
        ]
    }
    res = await do_manage_calendar(json.dumps(payload), owner=owner)
    assert res.get("exit_code") == 0, res
    assert "Created 2 event(s)" in res.get("response", "")

    # Verify events exist in DB
    db = _TS()
    events = db.query(CalendarEvent).filter(CalendarEvent.summary == "Morning Gym").all()
    assert len(events) == 2
    db.close()


async def test_batch_events_with_flat_strings():
    """Model emits {"events": [{"summary": ..., "start": "ISO", "end": "ISO"}]}."""
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    payload = {
        "events": [
            {
                "summary": "Standup",
                "start": "2026-06-09T09:00:00",
                "end": "2026-06-09T09:30:00",
            },
        ]
    }
    res = await do_manage_calendar(json.dumps(payload), owner=owner)
    assert res.get("exit_code") == 0, res
    assert "Created 1 event(s)" in res.get("response", "")


async def test_batch_events_partial_failure():
    """Batch with some valid and some invalid events — should surface both counts and first error."""
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    payload = {
        "events": [
            {
                "summary": "Valid Event 1",
                "start": "2026-06-09T10:00:00",
                "end": "2026-06-09T11:00:00",
            },
            {
                "summary": "Invalid Event",
                # Missing required dtstart — will fail
            },
            {
                "summary": "Valid Event 2",
                "start": "2026-06-09T14:00:00",
                "end": "2026-06-09T15:00:00",
            },
        ]
    }
    res = await do_manage_calendar(json.dumps(payload), owner=owner)

    # Partial failure = non-zero exit code
    assert res.get("exit_code") != 0, "Partial failure should return non-zero exit code"

    # Response should mention both created and failed counts
    response = res.get("response", "")
    assert "Created 2 event(s)" in response, f"Should report 2 created: {response}"
    assert "Failed to create 1 event(s)" in response, f"Should report 1 failed: {response}"
    assert "error" in response.lower() or "required" in response.lower(), "Should include error details"

    # Metadata fields
    assert res.get("created_count") == 2
    assert res.get("failed_count") == 1

    # Verify only valid events were created
    db = _TS()
    events = db.query(CalendarEvent).filter(
        CalendarEvent.summary.in_(["Valid Event 1", "Valid Event 2"])
    ).all()
    assert len(events) == 2
    db.close()
