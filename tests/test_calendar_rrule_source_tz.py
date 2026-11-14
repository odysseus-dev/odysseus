"""Imported recurring events must expand in their source timezone.

import_ics and the CalDAV sync store a TZID-bearing DTSTART as naive UTC.
_expand_rrule then evaluated the RRULE against that UTC value, but RFC 5545
evaluates BYDAY and the wall-clock time in DTSTART's own zone. A weekly
Monday 21:00 America/Los_Angeles event is Tuesday 04:00 UTC, so every
occurrence landed on the wrong weekday, the first one disappeared, and the
time drifted by an hour across DST (#6512).
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("icalendar")

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb  # noqa: E402
import routes.calendar_routes as cr  # noqa: E402

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)
_LA = ZoneInfo("America/Los_Angeles")


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    monkeypatch.setattr(cr, "SessionLocal", _TS)
    monkeypatch.setattr(cr, "require_user", lambda request: "tester")
    yield


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


def _ics(uid, rrule):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:Weekly call {uid}\r\n"
        "DTSTART;TZID=America/Los_Angeles:20260601T210000\r\n"
        "DTEND;TZID=America/Los_Angeles:20260601T220000\r\n"
        f"RRULE:{rrule}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def _local_starts(events):
    return [
        datetime.fromisoformat(e["dtstart"].replace("Z", "+00:00")).astimezone(_LA)
        for e in events
    ]


def test_imported_weekly_event_stays_on_its_local_weekday_and_time():
    eps = _endpoints()
    res = asyncio.run(eps["import"](
        _request(), file=_FakeUpload(_ics("weekly-la", "FREQ=WEEKLY;BYDAY=MO")),
        calendar_name="Work",
    ))
    assert res["imported"] == 1

    out = asyncio.run(eps["list"](
        _request(), start="2026-06-01T00:00:00Z", end="2026-12-01T00:00:00Z",
    ))
    starts = _local_starts(out["events"])

    assert starts[0] == datetime(2026, 6, 1, 21, 0, tzinfo=_LA)
    # Every Monday from June 1 through November 23 (November 30 21:00 PST is after the window).
    assert len(starts) == 26
    assert all(s.weekday() == 0 and (s.hour, s.minute) == (21, 0) for s in starts)


def test_imported_event_with_utc_until_expands_in_source_zone():
    eps = _endpoints()
    res = asyncio.run(eps["import"](
        _request(),
        file=_FakeUpload(_ics("until-la", "FREQ=WEEKLY;BYDAY=MO;UNTIL=20260623T040000Z")),
        calendar_name="Work",
    ))

    out = asyncio.run(eps["list"](
        _request(), start="2026-06-01T00:00:00Z", end="2026-07-01T00:00:00Z",
    ))
    starts = _local_starts([e for e in out["events"] if e["series_uid"] in res["event_uids"]])

    assert [s.day for s in starts] == [1, 8, 15, 22]
    assert all((s.hour, s.minute) == (21, 0) for s in starts)


def test_row_without_tzid_keeps_utc_expansion():
    ev = SimpleNamespace(
        uid="legacy",
        summary="Standup",
        dtstart=datetime(2026, 6, 2, 4, 0),
        dtend=datetime(2026, 6, 2, 5, 0),
        all_day=False,
        is_utc=True,
        tzid=None,
        rrule="FREQ=WEEKLY;BYDAY=TU",
        calendar_id="cal",
        calendar=SimpleNamespace(name="Personal", color="#5b8abf"),
        color=None,
        description="",
        location="",
        event_type=None,
        importance="normal",
    )

    results = cr._expand_rrule(ev, datetime(2026, 6, 1), datetime(2026, 6, 15))

    assert [r["uid"] for r in results] == ["legacy::2026-06-02T04:00", "legacy::2026-06-09T04:00"]


@pytest.mark.parametrize(
    "value, expected",
    [
        (datetime(2026, 6, 1, 21, 0, tzinfo=_LA), "America/Los_Angeles"),
        (datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc), None),
        (datetime(2026, 6, 1, 21, 0, tzinfo=ZoneInfo("UTC")), None),
        (datetime(2026, 6, 1, 21, 0), None),
    ],
)
def test_source_tzid(value, expected):
    assert cr._source_tzid(value) == expected
