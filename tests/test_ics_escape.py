"""Tests for iCalendar TEXT escaping in calendar export (RFC 5545 §3.3.11)."""
from tests.helpers.calendar_routes import import_calendar_routes


def _esc():
    return import_calendar_routes()._ics_escape


def test_escapes_comma_and_semicolon():
    # Regression: SUMMARY/LOCATION escaped nothing, so a comma/semicolon
    # (structural in iCal TEXT values) corrupted the field in other clients.
    assert _esc()("Lunch, dinner; meeting") == "Lunch\\, dinner\\; meeting"


def test_escapes_backslash_first():
    assert _esc()("path C:\\tmp") == "path C:\\\\tmp"


def test_newlines_become_literal_backslash_n():
    assert _esc()("line1\nline2\r\nline3") == "line1\\nline2\\nline3"


def test_empty_and_none_safe():
    assert _esc()("") == ""
    assert _esc()(None) == ""


def test_safe_ics_filename_strips_header_metacharacters():
    safe_filename = import_calendar_routes()._safe_ics_filename

    assert (
        safe_filename('Work\r\nX-Injected: yes";/..\\evil')
        == "Work__X-Injected__yes___.._evil.ics"
    )


def test_safe_ics_filename_falls_back_for_empty_names():
    safe_filename = import_calendar_routes()._safe_ics_filename

    assert safe_filename("////") == "calendar.ics"
    assert safe_filename(None) == "calendar.ics"
