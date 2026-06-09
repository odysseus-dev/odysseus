"""Issue #800 — the calendar write handlers actually trigger CalDAV write-back.

Route-level: proves POST/DELETE /api/calendar/events fire writeback_event for a
CalDAV-backed calendar and not for a local one.

Calls the async route handlers DIRECTLY (extracted from the router) rather than
through Starlette's TestClient — the TestClient middleware-app + threadpool could
hang in some environments; a direct call with a minimal fake request keeps the
same coverage and completes reliably.
"""

import tempfile
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.calendar_routes as croutes
import src.caldav_writeback as wb
from core.database import CalendarCal
from routes.calendar_routes import EventCreate

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
croutes.SessionLocal = _TS


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    async def _fake_writeback(owner, source, cal_id, ev, *, delete=False):
        recorded.append({"source": source, "cal_id": cal_id, "uid": ev.get("uid"), "delete": delete})
        return {"ok": True}

    monkeypatch.setattr(wb, "writeback_event", _fake_writeback)
    return recorded


def _req():
    return SimpleNamespace(state=SimpleNamespace(current_user="tester"))


def _endpoint(method, suffix):
    router = croutes.setup_calendar_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith(suffix) and method in getattr(r, "methods", set()):
            return r.endpoint
    raise RuntimeError(f"{method} *{suffix} not found")


def _make_cal(source):
    cid = ("caldav-" if source == "caldav" else "loc-") + uuid.uuid4().hex[:10]
    db = _TS()
    try:
        db.add(CalendarCal(id=cid, owner="tester", name="C", source=source))
        db.commit()
        return cid
    finally:
        db.close()


async def test_create_on_caldav_calendar_pushes_to_remote(calls):
    create_event = _endpoint("POST", "/events")
    cal_id = _make_cal("caldav")
    res = await create_event(_req(), EventCreate(
        summary="Dentist", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id))
    assert res["ok"] is True
    assert len(calls) == 1
    assert calls[0]["source"] == "caldav" and calls[0]["cal_id"] == cal_id
    assert calls[0]["delete"] is False


async def test_create_on_local_calendar_does_not_push(calls):
    create_event = _endpoint("POST", "/events")
    cal_id = _make_cal("local")
    res = await create_event(_req(), EventCreate(
        summary="Local", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id))
    assert res["ok"] is True
    assert calls == []


async def test_delete_on_caldav_calendar_pushes_delete(calls):
    create_event = _endpoint("POST", "/events")
    delete_event = _endpoint("DELETE", "/events/{uid}")
    cal_id = _make_cal("caldav")
    res = await create_event(_req(), EventCreate(
        summary="Temp", dtstart="2026-06-10T14:00:00Z", calendar_href=cal_id))
    uid = res["uid"]
    calls.clear()
    rd = await delete_event(_req(), uid)
    assert rd["ok"] is True
    assert len(calls) == 1 and calls[0]["delete"] is True and calls[0]["uid"] == uid
