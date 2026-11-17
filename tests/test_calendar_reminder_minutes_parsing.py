"""do_manage_calendar must honour abbreviated reminder phrasings like "mins"/"hrs".

`_reminder_minutes` parsed the reminder offset with regexes anchored on
`(?:m|min|minute|minutes)\b` / `(?:h|hr|hour|hours)\b`. The trailing `\b`
made the very common plural abbreviations "mins" and "hrs" fail to match
(after "min" the next char "s" is a word char, so no boundary), so a request
like ``reminder_minutes: "5 mins"`` silently produced no reminder at all —
even though the sibling duration parser (no `\b`) already accepted them.
"""

import json
import sys
import uuid

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import Note

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


async def _create_with_reminder(reminder, owner):
    from src.tool_implementations import do_manage_calendar

    payload = {
        "action": "create_event",
        "summary": "Dentist",
        # Far-future so the reminder is never "already passed".
        "dtstart": "2030-01-01T10:00:00",
        "reminder_minutes": reminder,
    }
    return await do_manage_calendar(json.dumps(payload), owner=owner)


@pytest.mark.parametrize("reminder,expected", [
    ("5 mins", 5),
    ("10 mins", 10),
    ("2 hrs", 120),
    ("1 hr", 60),
    ("15 minutes", 15),   # regression: long form still works
    ("30m", 30),          # regression: bare unit still works
])
async def test_reminder_minutes_accepts_abbreviations(reminder, expected):
    owner = "tester-" + uuid.uuid4().hex[:6]
    res = await _create_with_reminder(reminder, owner)
    assert res.get("exit_code") == 0, res
    assert f"reminder {expected} min before" in res.get("response", ""), res
    assert res.get("reminder_note_id"), res
    assert res.get("reminder_minutes") == expected, res
    assert res.get("dtstart") == "2030-01-01T10:00:00", res

    db = _TS()
    try:
        note = (
            db.query(Note)
            .filter(Note.owner == owner, Note.title == "Calendar reminder: Dentist")
            .first()
        )
        assert note is not None, "reminder note should have been created"
    finally:
        db.close()


async def test_no_reminder_when_offset_absent():
    owner = "tester-" + uuid.uuid4().hex[:6]
    from src.tool_implementations import do_manage_calendar

    payload = {
        "action": "create_event",
        "summary": "No Reminder Event",
        "dtstart": "2030-02-01T10:00:00",
    }
    res = await do_manage_calendar(json.dumps(payload), owner=owner)
    assert res.get("exit_code") == 0, res
    assert "reminder set" not in res.get("response", ""), res


async def test_update_event_can_add_reminder_after_creation():
    owner = "tester-" + uuid.uuid4().hex[:6]
    from src.tool_implementations import do_manage_calendar

    created = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Kindergarten pickup",
        "dtstart": "2030-03-01T15:00:00",
    }), owner=owner)
    assert created.get("exit_code") == 0, created

    updated = await do_manage_calendar(json.dumps({
        "action": "update_event",
        "uid": created["uid"],
        "reminder_minutes": 15,
    }), owner=owner)
    assert updated.get("exit_code") == 0, updated
    assert "reminder set 15 min before" in updated.get("response", ""), updated
    assert updated.get("reminder_note_id")
    assert updated.get("reminder_minutes") == 15, updated
    assert updated.get("dtstart") == "2030-03-01T15:00:00", updated

    db = _TS()
    try:
        note = (
            db.query(Note)
            .filter(Note.owner == owner, Note.title == "Calendar reminder: Kindergarten pickup")
            .first()
        )
        assert note is not None, "update_event should create the reminder note"
    finally:
        db.close()


async def test_reminder_and_duration_parsers_stay_linear_on_digit_and_space_floods():
    """CodeQL py/polynomial-redos: `(\\d+)\\s*unit` rescanned a digit run from
    every offset and `alarm\\s*:?\\s*\\d+` split one whitespace run two ways.
    Tool arguments come from model output, so keep them O(n)."""
    import time
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    started = time.perf_counter()
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Flood",
        "dtstart": "2030-04-01T10:00:00",
        "reminder_minutes": "0" * 40_000 + "x",
        "duration": "0" * 40_000 + "x",
    }), owner=owner)
    assert time.perf_counter() - started < 2.0
    assert res.get("exit_code") == 0, res
    assert "reminder set" not in res.get("response", ""), res

    # A reminder is set, so the "is the description only a reminder?" check runs.
    started = time.perf_counter()
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Flood 2",
        "dtstart": "2030-04-02T10:00:00",
        "description": "alarm" + "\t" * 40_000,
        "reminder_minutes": 5,
    }), owner=owner)
    assert time.perf_counter() - started < 2.0
    assert res.get("exit_code") == 0, res
    assert "reminder 5 min before" in res.get("response", ""), res
