from pathlib import Path
import re

from tests.helpers.stylesheets import app_css
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parents[1]


def test_calendar_tool_cards_link_to_events_in_live_and_saved_renderers():
    live_src = (ROOT / "static/js/chat.js").read_text()
    renderer_src = (ROOT / "static/js/chatRenderer.js").read_text()

    assert "renderToolIcon(json.tool, cmd, json)" in live_src
    assert "_calendarEventUidFromToolData" in renderer_src
    assert "_target('event', uid, 'Open calendar event')" in renderer_src
    assert "`#${kind}-${safeId}`" in renderer_src
    assert "CALENDAR_ICON" in renderer_src


def test_successful_calendar_tool_output_is_suppressed_in_live_and_saved_renderers():
    for rel in ("static/js/chat.js", "static/js/chatRenderer.js"):
        src = (ROOT / rel).read_text()
        assert "manage_calendar" in src
        assert "return true;" in src


def test_calendar_chat_event_links_fetch_uid_and_show_title_time():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()
    routes_src = (ROOT / "routes/calendar_routes.py").read_text()
    app_src = (ROOT / "static/app.js").read_text()
    renderer_src = (ROOT / "static/js/chatRenderer.js").read_text()
    inbox_src = (ROOT / "static/js/emailInbox.js").read_text()
    library_src = email_library_source()

    assert '@router.get("/events/{uid}")' in routes_src
    assert "async function _fetchEventByUid" in calendar_src
    assert "/api/calendar/events/${encodeURIComponent(id)}" in calendar_src
    assert "const ev = await _fetchEventByUid(targetStr);" in calendar_src
    assert "await _fetchEvents(range[0], range[1], true);" in calendar_src
    assert "_selectedDay = _ds(now);" in calendar_src
    assert "_selectedDay = _ds(dt);" in calendar_src
    assert "_selectedDay = new Date(dt.getFullYear()" not in calendar_src
    assert "function _parseTitleTime" in calendar_src
    assert "function _fmtTimeSeconds" in calendar_src
    assert "function _applyPendingEventHighlight" in calendar_src
    assert "cal-event-link-target" in calendar_src
    assert "scrollIntoView({ behavior: 'smooth', block: 'center'" in calendar_src
    assert "cal-event-link-target-flash" in style_src
    versions = []
    for src in (app_src, renderer_src, inbox_src, library_src):
        match = re.search(r"calendar\.js\?v=([A-Za-z0-9_-]+)", src)
        assert match
        versions.append(match.group(1))
    assert len(set(versions)) == 1


def test_recurring_occurrence_delete_does_not_fall_back_to_series_delete():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    routes_src = (ROOT / "routes/calendar_routes.py").read_text()

    assert "const deleteOccurrenceOnly = scope === 'occurrence';" in calendar_src
    assert "const scopeParam = scope === 'occurrence' ? '?scope=occurrence' : '';" in calendar_src
    assert "Occurrence delete requires a recurring occurrence uid" in routes_src


def test_memory_used_pill_reuses_sidebar_brain_icon():
    renderer_src = (ROOT / "static/js/chatRenderer.js").read_text()

    assert "memory-used-pill-text" in renderer_src
    assert "M12 5a3 3 0 1 0-5.997.125" in renderer_src
    assert "M12 2a7 7 0 0 1 7 7" not in renderer_src
