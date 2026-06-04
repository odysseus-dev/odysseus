"""Regression coverage for calendar/chat user-local time anchoring.

Calendar chat requests can cross a date boundary when the server runs in UTC
but the browser user is west/east of UTC. Relative phrases like "tomorrow"
must be anchored to the browser user's current date, not the server date.
"""

from datetime import datetime, timezone
from pathlib import Path


def test_calendar_quick_parse_uses_timezone_name_for_local_today():
    from routes.calendar_routes import _calendar_user_now

    fixed_utc = datetime(2026, 6, 4, 2, 30, tzinfo=timezone.utc)

    local_now = _calendar_user_now("America/Los_Angeles", now_utc=fixed_utc)

    assert local_now.strftime("%Y-%m-%d") == "2026-06-03"
    assert local_now.strftime("%H:%M") == "19:30"


def test_calendar_quick_parse_falls_back_to_browser_offset():
    from routes.calendar_routes import _calendar_user_now

    fixed_utc = datetime(2026, 6, 4, 2, 30, tzinfo=timezone.utc)

    local_now = _calendar_user_now("", offset_min=-420, now_utc=fixed_utc)

    assert local_now.strftime("%Y-%m-%d") == "2026-06-03"
    assert local_now.strftime("%H:%M") == "19:30"


def test_agent_date_context_uses_request_timezone_offset():
    from routes.calendar_routes import set_user_tz_offset
    from src.agent_loop import _current_date_time_context

    fixed_utc = datetime(2026, 6, 4, 2, 30, tzinfo=timezone.utc)
    set_user_tz_offset(-420)
    try:
        context = _current_date_time_context(now_utc=fixed_utc)
    finally:
        set_user_tz_offset(None)

    assert "Wednesday, June 3, 2026" in context
    assert "(2026-06-03)" in context
    assert "UTC-07:00" in context
    assert "Thursday, June 4, 2026" not in context


def test_chat_timezone_context_can_be_cleared():
    from routes.calendar_routes import get_user_tz_offset, set_user_tz_offset

    set_user_tz_offset(540)
    assert get_user_tz_offset() == 540

    set_user_tz_offset(None)

    assert get_user_tz_offset() is None


def test_quick_parse_frontend_sends_timezone_name_and_offset():
    calendar_js = Path("static/js/calendar.js").read_text(encoding="utf-8")

    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in calendar_js
    assert "-new Date().getTimezoneOffset()" in calendar_js
    assert "JSON.stringify({ text, tz, tz_offset })" in calendar_js
