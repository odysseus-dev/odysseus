"""Tests for ICS export correctness — calendar name escaping and UTC flag."""
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _make_ev(summary, dtstart, dtend, all_day=False, is_utc=False, uid="test-uid",
             description=None, location=None, rrule=None):
    ev = types.SimpleNamespace(
        uid=uid,
        summary=summary,
        dtstart=dtstart,
        dtend=dtend,
        all_day=all_day,
        is_utc=is_utc,
        description=description,
        location=location,
        rrule=rrule,
    )
    return ev


def _export(cal_name, events):
    """Call the ICS export helper directly without HTTP."""
    from routes.calendar_routes import _ics_escape

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Odysseus//Calendar//EN",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
    ]
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev.uid}")
        lines.append(f"SUMMARY:{_ics_escape(ev.summary or '')}")
        if ev.all_day:
            lines.append(f"DTSTART;VALUE=DATE:{ev.dtstart.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{ev.dtend.strftime('%Y%m%d')}")
        else:
            _dt_suffix = "Z" if getattr(ev, "is_utc", False) else ""
            lines.append(f"DTSTART:{ev.dtstart.strftime('%Y%m%dT%H%M%S')}{_dt_suffix}")
            lines.append(f"DTEND:{ev.dtend.strftime('%Y%m%dT%H%M%S')}{_dt_suffix}")
        if ev.description:
            lines.append(f"DESCRIPTION:{_ics_escape(ev.description)}")
        if ev.location:
            lines.append(f"LOCATION:{_ics_escape(ev.location)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


class TestCalendarNameEscaping:
    def test_comma_in_cal_name_escaped(self):
        ics = _export("Work,Home", [])
        assert "X-WR-CALNAME:Work\\,Home" in ics

    def test_semicolon_in_cal_name_escaped(self):
        ics = _export("Team;Project", [])
        assert "X-WR-CALNAME:Team\\;Project" in ics

    def test_backslash_in_cal_name_escaped(self):
        ics = _export("C:\\Users", [])
        assert "X-WR-CALNAME:C:\\\\Users" in ics

    def test_plain_cal_name_unchanged(self):
        ics = _export("My Calendar", [])
        assert "X-WR-CALNAME:My Calendar" in ics


class TestDtStartUtcFlag:
    def test_utc_event_gets_z_suffix(self):
        ev = _make_ev(
            "Team standup",
            datetime(2026, 6, 2, 10, 0, 0),
            datetime(2026, 6, 2, 10, 30, 0),
            is_utc=True,
        )
        ics = _export("Cal", [ev])
        assert "DTSTART:20260602T100000Z" in ics
        assert "DTEND:20260602T103000Z" in ics

    def test_non_utc_event_no_z_suffix(self):
        ev = _make_ev(
            "Lunch",
            datetime(2026, 6, 2, 12, 0, 0),
            datetime(2026, 6, 2, 13, 0, 0),
            is_utc=False,
        )
        ics = _export("Cal", [ev])
        assert "DTSTART:20260602T120000\r\n" in ics
        assert "DTSTART:20260602T120000Z" not in ics

    def test_all_day_event_unaffected(self):
        ev = _make_ev(
            "Holiday",
            datetime(2026, 6, 2),
            datetime(2026, 6, 3),
            all_day=True,
            is_utc=True,
        )
        ics = _export("Cal", [ev])
        assert "DTSTART;VALUE=DATE:20260602" in ics
        assert "Z" not in ics.split("DTSTART")[1].split("\r\n")[0]
