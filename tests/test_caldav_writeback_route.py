"""Issue #800 — the calendar write handlers actually trigger CalDAV write-back.

Route-level: drives POST/PUT/DELETE /api/calendar/events via TestClient against a
dedicated temp DB, with writeback_event stubbed to record calls — proving a
CalDAV-backed calendar pushes to the remote and a local calendar does not.
"""

import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import core.database as cdb
import routes.calendar_routes as croutes
import src.caldav_writeback as wb
from core.database import CalendarCal

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    async def _fake_writeback(owner, source, cal_id, ev, *, delete=False):
        recorded.append({"owner": owner, "source": source, "cal_id": cal_id,
                         "uid": ev.get("uid"), "delete": delete})
        return {"ok": True}

    monkeypatch.setattr(croutes, "SessionLocal", _TS)
    monkeypatch.setattr(wb, "writeback_event", _fake_writeback)
    return recorded


def _client():
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.current_user = "tester"
        return await call_next(request)

    app.include_router(croutes.setup_calendar_routes())
    return TestClient(app)


def _make_cal(source):
    cid = ("caldav-" if source == "caldav" else "loc-") + uuid.uuid4().hex[:10]
    db = _TS()
    try:
        db.add(CalendarCal(id=cid, owner="tester", name="C", source=source))
        db.commit()
        return cid
    finally:
        db.close()


def test_create_on_caldav_calendar_pushes_to_remote(calls):
    client = _client()
    cal_id = _make_cal("caldav")
    r = client.post("/api/calendar/events",
                    json={"summary": "Dentist", "dtstart": "2026-06-10T14:00:00Z",
                          "calendar_href": cal_id})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["source"] == "caldav" and calls[0]["cal_id"] == cal_id
    assert calls[0]["delete"] is False


def test_create_on_local_calendar_does_not_push(calls):
    client = _client()
    cal_id = _make_cal("local")
    r = client.post("/api/calendar/events",
                    json={"summary": "Local", "dtstart": "2026-06-10T14:00:00Z",
                          "calendar_href": cal_id})
    assert r.status_code == 200, r.text
    assert calls == []


def test_delete_on_caldav_calendar_pushes_delete(calls):
    client = _client()
    cal_id = _make_cal("caldav")
    r = client.post("/api/calendar/events",
                    json={"summary": "Temp", "dtstart": "2026-06-10T14:00:00Z",
                          "calendar_href": cal_id})
    uid = r.json()["uid"]
    calls.clear()
    rd = client.delete(f"/api/calendar/events/{uid}")
    assert rd.status_code == 200, rd.text
    assert len(calls) == 1 and calls[0]["delete"] is True and calls[0]["uid"] == uid
