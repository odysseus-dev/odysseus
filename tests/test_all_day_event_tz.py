"""All-day calendar events must not roll back a day for +UTC-offset users.

create_event parsed the bare dtstart date through _parse_event_dt
(parse_due_for_user), which tagged "2026-06-10" with the user's tz offset and
converted to UTC. For a positive offset (e.g. Tokyo +9) that stored
2026-06-09 — the event showed on the wrong day. all_day only suppressed
is_utc, not the already-shifted date. All-day dates must be parsed naive.
"""
import json
import tempfile
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import CalendarEvent

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(f"sqlite:///{_TMPDB.name}", connect_args={"check_same_thread": False}, poolclass=NullPool)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bind(monkeypatch):
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    import routes.calendar_routes as cr
    monkeypatch.setattr(cr, "SessionLocal", _TS, raising=False)
    yield


@pytest.fixture
def tokyo(monkeypatch):
    from routes.calendar_routes import set_user_tz_offset
    set_user_tz_offset(540)  # UTC+9
    try:
        yield
    finally:
        set_user_tz_offset(None)


async def _create_all_day(owner, dt):
    from src.tool_implementations import do_manage_calendar
    return await do_manage_calendar(json.dumps({
        "action": "create_event", "summary": "Holiday",
        "dtstart": dt, "all_day": True,
    }), owner=owner)


def test_all_day_keeps_requested_date_for_positive_offset(tokyo):
    owner = "tz-" + uuid.uuid4().hex[:6]
    res = __import__("asyncio").run(_create_all_day(owner, "2026-06-10"))
    assert res.get("exit_code", 0) == 0, res
    db = _TS()
    try:
        ev = db.query(CalendarEvent).filter(CalendarEvent.uid == res["uid"]).first()
        assert ev.dtstart.date() == date(2026, 6, 10)  # not 2026-06-09
    finally:
        db.close()
