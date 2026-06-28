"""Imported events with a non-positive duration must not vanish from the list.

list_events selects events that overlap the query window with
``dtstart < end AND dtend > start``. An import that stores ``dtend == dtstart``
(a single-day all-day event whose source wrote DTEND equal to DTSTART, treating
it as an inclusive bound) is therefore silently dropped — the event never shows
on the calendar even though it was imported. import_ics now clamps such an end
to a positive span, matching the default used when DTEND is absent.
"""
import asyncio
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("icalendar")

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb  # noqa: E402
import routes.calendar_routes as cr  # noqa: E402
from core.database import CalendarCal, CalendarEvent  # noqa: E402
from routes.calendar_routes import _ensure_positive_duration  # noqa: E402

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    monkeypatch.setattr(cr, "SessionLocal", _TS)
    monkeypatch.setattr(cr, "require_user", lambda request: "tester")
    yield


# ---- pure helper -----------------------------------------------------------

def test_all_day_same_date_end_clamped_to_one_day():
    start = datetime(2026, 6, 20)
    assert _ensure_positive_duration(start, start, True) == datetime(2026, 6, 21)


def test_timed_non_positive_end_clamped_to_one_hour():
    start = datetime(2026, 6, 20, 9, 0)
    assert _ensure_positive_duration(start, start, False) == datetime(2026, 6, 20, 10, 0)
    # reversed end (dtend < dtstart) is also normalized
    earlier = datetime(2026, 6, 20, 8, 0)
    assert _ensure_positive_duration(start, earlier, False) == datetime(2026, 6, 20, 10, 0)


def test_positive_duration_end_is_unchanged():
    start = datetime(2026, 6, 20, 9, 0)
    end = datetime(2026, 6, 20, 17, 0)
    assert _ensure_positive_duration(start, end, False) is end


# ---- behavioral: import -> list -------------------------------------------

def _ics(dtstart_date, dtend_date):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        "BEGIN:VEVENT\r\nUID:holiday-1\r\nSUMMARY:Public Holiday\r\n"
        f"DTSTART;VALUE=DATE:{dtstart_date}\r\nDTEND;VALUE=DATE:{dtend_date}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


class _FakeUpload:
    def __init__(self, content, filename="cal.ics"):
        self._content = content
        self.filename = filename

    async def read(self, n=-1):
        return self._content


def _endpoints():
    router = cr.setup_calendar_routes()
    eps = {}
    for route in router.routes:
        if route.path == "/api/calendar/import" and "POST" in route.methods:
            eps["import"] = route.endpoint
        if route.path == "/api/calendar/events" and "GET" in route.methods:
            eps["list"] = route.endpoint
    return eps


def _request():
    return SimpleNamespace(state=SimpleNamespace(current_user="tester"))


def test_single_day_all_day_event_with_same_date_end_appears_in_list():
    eps = _endpoints()
    res = asyncio.run(eps["import"](
        _request(), file=_FakeUpload(_ics("20260620", "20260620")), calendar_name="A",
    ))
    assert res["imported"] == 1

    out = asyncio.run(eps["list"](
        _request(), start="2026-06-20T00:00:00", end="2026-06-23T00:00:00",
    ))
    assert [e["summary"] for e in out["events"]] == ["Public Holiday"]


def test_normal_multi_day_all_day_event_still_appears():
    # Regression: a well-formed exclusive DTEND must keep working.
    eps = _endpoints()
    res = asyncio.run(eps["import"](
        _request(), file=_FakeUpload(_ics("20260710", "20260711")), calendar_name="B",
    ))
    assert res["imported"] == 1

    out = asyncio.run(eps["list"](
        _request(), start="2026-07-10T00:00:00", end="2026-07-12T00:00:00",
    ))
    assert [e["summary"] for e in out["events"]] == ["Public Holiday"]


def test_reimport_repairs_legacy_zero_duration_row():
    # A row persisted by an import that predates the duration clamp has
    # dtend == dtstart and is invisible to list_events. Re-importing the same
    # ICS hits the duplicate branch; it must repair the stored row in place
    # rather than skip past it, so the event becomes visible.
    eps = _endpoints()
    db = cr.SessionLocal()
    try:
        cal = CalendarCal(id="legacy-cal", owner="tester", name="C", source="import")
        db.add(cal)
        db.add(CalendarEvent(
            uid="legacy-row",
            calendar_id="legacy-cal",
            summary="Public Holiday",
            dtstart=datetime(2026, 8, 1),
            dtend=datetime(2026, 8, 1),  # zero duration: the legacy bug
            all_day=True,
        ))
        db.commit()
    finally:
        db.close()

    # Confirm the seeded row is invisible (proves the bug it repairs).
    before = asyncio.run(eps["list"](
        _request(), start="2026-08-01T00:00:00", end="2026-08-04T00:00:00",
    ))
    assert before["events"] == []

    res = asyncio.run(eps["import"](
        _request(), file=_FakeUpload(_ics("20260801", "20260801")), calendar_name="C",
    ))
    # Duplicate, so nothing new is imported, but the stale row is repaired.
    assert res["imported"] == 0
    assert res["skipped"] == 1
    assert res["repaired"] == 1

    after = asyncio.run(eps["list"](
        _request(), start="2026-08-01T00:00:00", end="2026-08-04T00:00:00",
    ))
    assert [e["summary"] for e in after["events"]] == ["Public Holiday"]

    # Re-importing once more is a no-op: the row is already positive-duration.
    res2 = asyncio.run(eps["import"](
        _request(), file=_FakeUpload(_ics("20260801", "20260801")), calendar_name="C",
    ))
    assert res2["repaired"] == 0
    assert res2["skipped"] == 1
