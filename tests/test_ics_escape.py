"""Tests for iCalendar TEXT escaping in calendar export (RFC 5545 §3.3.11)."""
from tests.test_null_owner_gates import _import_calendar_helpers


def _esc():
    return _import_calendar_helpers()._ics_escape


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
