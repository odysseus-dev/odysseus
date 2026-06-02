"""Issue #1320 — the agent's manage_calendar tool can create a recurring event.

The create_event handler already persists `rrule`, but it wasn't documented in the
tool schema, so the agent took "a roundabout way". This pins the end-to-end path:
calling do_manage_calendar with an rrule stores a single event carrying that RRULE.
"""

import json
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import CalendarEvent

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    # do_manage_calendar does `from core.database import SessionLocal` at call
    # time, so patch the module attribute to our temp DB — via monkeypatch so it
    # is RESTORED after each test and can't leak into later tests in the process.
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


async def test_create_event_with_rrule_persists_recurrence():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    rrule = "FREQ=WEEKLY;BYDAY=MO"
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Standup",
        "dtstart": "2026-06-08T09:00:00Z",
        "rrule": rrule,
    }), owner=owner)
    assert res.get("exit_code", 0) == 0, res
    uid = res.get("uid")
    assert uid, res

    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
        assert ev is not None
        assert ev.rrule == rrule  # ONE event carrying the recurrence rule
        assert ev.summary == "Standup"
    finally:
        db.close()


async def test_create_event_without_rrule_is_single():
    from src.tool_implementations import do_manage_calendar

    owner = "tester-" + uuid.uuid4().hex[:6]
    res = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "One-off",
        "dtstart": "2026-06-09T10:00:00Z",
    }), owner=owner)
    assert res.get("exit_code", 0) == 0, res
    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == res["uid"]).first()
        assert ev is not None and (ev.rrule or "") == ""
    finally:
        db.close()
