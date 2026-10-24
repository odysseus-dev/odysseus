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
import src.caldav_sync as csync
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

    async def _fake_create(owner, uid):
        recorded.append({"uid": uid, "delete": False, "action": "create"})
        return {"ok": True}

    async def _fake_delete(owner, uid):
        recorded.append({"uid": uid, "delete": True, "action": "delete"})
        return {"ok": True}

    monkeypatch.setattr(csync, "push_event_create", _fake_create)
    monkeypatch.setattr(csync, "push_event_delete", _fake_delete)
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


# --- #6340: deleting one occurrence must survive write-back all the way to the
# iCalendar body. The route below is the real one and only the HTTP socket is faked
# (the DAV client and its calendar), so the exdate bookkeeping, writeback_event() and
# build_event_ical() are all the real code. Before the fix the route answered
# {"ok": true} while the ICAL handed to the server carried no EXDATE at all.


class _FakePrincipal:
    def __init__(self, calendars):
        self._calendars = calendars

    def calendars(self):
        return self._calendars


class _FakeClient:
    """Stands in for the DAVClient. No socket is opened; every calendar is handed to it."""

    def __init__(self, calendars, url="https://caldav.example.com/"):
        self._calendars = calendars
        self.url = url

    def principal(self):
        return _FakePrincipal(self._calendars)

    def close(self):
        pass


class _FakeRemoteEvent:
    def __init__(self, url, on_save):
        self.url = url
        self.etag = '"v1"'
        self.data = "OLD"
        self._on_save = on_save

    def save(self):
        self._on_save(self.data)


class _FakeRemoteCalendar:
    def __init__(self, url, event):
        self.url = url
        self.event = event

    def event_by_uid(self, uid):
        return self.event


@pytest.fixture
def remote(monkeypatch):
    """A CalDAV server that records every iCalendar body it is asked to store."""
    from src.caldav_writeback import _stable_cal_id

    acc_id = "acc-1"
    url = "https://caldav.example.com/principals/u/calendars/home/"
    pushed = []

    def _client(u, username, password):
        event = _FakeRemoteEvent(url + "evt.ics", pushed.append)
        return _FakeClient([_FakeRemoteCalendar(url, event)])

    monkeypatch.setattr(csync, "_load_caldav_accounts",
                        lambda owner: [{"id": acc_id, "url": url, "username": "u", "password": "p"}])
    monkeypatch.setattr(csync, "_build_dav_client", _client)
    # URL validation is SSRF hardening with its own tests (test_caldav_url_hardening.py,
    # test_caldav_url_nonstring.py). This host never resolves because the DAV client is
    # faked, so let it through - the question here is whether the EXDATE reaches the
    # iCalendar body, not which URLs are allowed.
    monkeypatch.setattr(csync, "validate_caldav_url", lambda raw: raw)
    import src.secret_storage as secret_storage
    monkeypatch.setattr(secret_storage, "decrypt", lambda s: s or "")
    import core.database as _cdb
    monkeypatch.setattr(_cdb, "SessionLocal", _TS, raising=False)

    return {"cal_id": _stable_cal_id(url, owner="tester", account_id=acc_id),
            "pushed": pushed}


async def test_occurrence_delete_pushes_an_ical_carrying_the_exdate(remote):
    """The deleted occurrence has to reach the server, not just the local DB."""
    db = _TS()
    try:
        db.add(CalendarCal(id=remote["cal_id"], owner="tester", name="Home", source="caldav"))
        db.commit()
    finally:
        db.close()

    create_event = _endpoint("POST", "/events")
    delete_event = _endpoint("DELETE", "/events/{uid}")

    res = await create_event(_req(), EventCreate(
        summary="Standup", dtstart="2026-06-10T14:00:00Z", dtend="2026-06-10T14:30:00Z",
        calendar_href=remote["cal_id"], rrule="FREQ=WEEKLY;BYDAY=WE"))
    uid = res["uid"]
    remote["pushed"].clear()  # drop the create push; this test is about the delete

    # the compound uid _expand_rrule() hands the frontend for a single instance
    rd = await delete_event(_req(), f"{uid}::2026-06-17T14:00", scope="occurrence")
    assert rd["ok"] is True
    assert rd["scope"] == "occurrence"

    pushed = remote["pushed"]
    assert len(pushed) == 1, "the occurrence delete never reached the CalDAV write path"
    assert "EXDATE" in pushed[0], f"EXDATE dropped from the pushed VEVENT:\n{pushed[0]}"
    assert "20260617T140000Z" in pushed[0]
