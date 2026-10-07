from pathlib import Path
import re

from tests.helpers.stylesheets import app_css, stylesheet_cache_version
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parent.parent


def test_mobile_notes_checklist_rows_remain_tappable():
    css = app_css()
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "body.notes-mobile-mode .note-card .note-checkbox {" in css
    assert "pointer-events: auto;" in css
    assert "touch-action: manipulation;" in css
    assert "body.notes-mobile-mode .note-card .note-checkbox,\nbody.notes-mobile-mode .note-card .note-checkbox-rm" not in css
    assert "if (_selectMode) return; // let card-level handler take over\n      e.preventDefault();" in notes


def test_mobile_edit_checklist_toggle_marks_form_dirty():
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "container.dispatchEvent(new Event('input', { bubbles: true }));" in notes


def test_completed_mobile_todo_uses_finish_action():
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "const _isFinishedTodo = () =>" in notes
    assert "const label = _isFinishedTodo() ? 'Finish' : 'Archive';" in notes
    assert "if (_isFinishedTodo()) _enterArchive();" in notes


def test_mobile_long_press_enters_select_mode_for_that_note():
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")

    assert "function _enterSelectMode(initialId = null)" in notes
    assert "_suppressNextNoteTapId = card.dataset.noteId;" in notes
    assert "_enterSelectMode(card.dataset.noteId);" in notes


def test_mobile_select_mode_has_subtle_jiggle_feedback():
    css = app_css()

    assert "@keyframes notes-select-jiggle" in css
    assert "body.notes-mobile-mode .note-card-selectmode" in css
    assert "prefers-reduced-motion: reduce" in css


def test_notes_mobile_checklist_asset_versions_are_bumped():
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert re.search(r"notes\.js\?v=[A-Za-z0-9_-]+", app)
    app_versions = re.findall(r"/static/app\.js\?v=([A-Za-z0-9_-]+)", html)
    assert app_versions and len(set(app_versions)) == 1
    assert stylesheet_cache_version() == app_versions[0]


def test_drawing_edits_mark_notes_dirty_and_keep_one_gallery_image():
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")
    upload = (ROOT / "routes" / "upload_routes.py").read_text(encoding="utf-8")
    note_routes = (ROOT / "routes" / "note" / "note_routes.py").read_text(encoding="utf-8")

    assert "const _markCanvasDirty = () => container.dispatchEvent(new Event('input', { bubbles: true }));" in notes
    assert "_uploadCanvasAsPng(canvas, note?.gallery_id || null)" in notes
    assert "fd.append('gallery_id', galleryId)" in notes
    assert "if gallery_id:" in upload
    assert "note.gallery_id = body.gallery_id" in note_routes


def test_calendar_email_attachments_have_a_calendar_import_action():
    email = email_library_source()
    calendar = (ROOT / "static" / "js" / "calendar.js").read_text(encoding="utf-8")

    assert "email-attachment-calendar-open" in email
    assert "const _CALENDAR_RE = /\\.(calendar|ics|ical)$/i" in email
    assert "fetch(`${API_BASE}/api/calendar/import`" in email
    assert 'accept=".calendar,.ics,.ical"' in calendar


def test_mobile_bulk_select_long_press_is_shared_across_card_types():
    helper = (ROOT / "static" / "js" / "mobileBulkSelect.js").read_text(encoding="utf-8")
    css = app_css()

    assert ".memory-item[data-memory-id]" in helper
    assert ".skill-card[data-skill-name]" in helper
    assert ".task-card[data-id]" in helper
    assert "const HOLD_MS = 450" in helper
    shared_buttons = (
        "#memory-select-btn,", "#skills-select-btn,",
        "#notes-select-btn,", "#tasks-select-btn,",
    )
    shared_css = css.split("/* Shared bulk-selection trigger.", 1)[1]
    selectors = re.findall(r":is\(([^)]*)\)(?:\.active)?::before", shared_css)
    assert len(selectors) == 2
    for selector in selectors:
        assert all(button in selector for button in shared_buttons)
