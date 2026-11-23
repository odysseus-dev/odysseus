from tests.helpers.notes_source import notes_source
from pathlib import Path
import re

from tests.helpers.stylesheets import app_css, stylesheet_cache_version
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parent.parent


def test_mobile_notes_checklist_rows_remain_tappable():
    css = app_css()
    notes = notes_source("ui/cardChecklist", "ui/mobileReorder", "ui/selection", "ui/drawing")

    assert "body.notes-mobile-mode .note-card .note-checkbox {" in css
    assert "pointer-events: auto;" in css
    assert "touch-action: manipulation;" in css
    assert "body.notes-mobile-mode .note-card .note-checkbox,\nbody.notes-mobile-mode .note-card .note-checkbox-rm" not in css
    assert re.search(r"if \(state.selectMode\) return; // let card-level handler take over\n\s+e.preventDefault\(\);", notes)


# The fullscreen mobile form is gone. Its checklist toggles (formerly checked
# here as "marks the form dirty") are now note-window toggles through the
# toggle endpoint, exercised in tests/notesDraftAutosave.test.mjs and on a
# phone viewport in tests/browser/notesNative.mjs. The mobile Finish/Archive
# morph of the form's save button went with the form; archive is the window
# footer's button.


def test_mobile_long_press_enters_select_mode_for_that_note():
    notes = notes_source("ui/cardChecklist", "ui/mobileReorder", "ui/selection", "ui/drawing")

    assert "function _enterSelectMode(initialId = null)" in notes
    assert "app.setSuppressNextNoteTapId(card.dataset.noteId);" in notes
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


def test_drawing_edits_mark_the_canvas_dirty_and_uploads_keep_their_gallery():
    notes = notes_source("ui/drawing")
    upload = (ROOT / "routes" / "upload_routes.py").read_text(encoding="utf-8")
    note_routes = (ROOT / "routes" / "note" / "note_routes.py").read_text(encoding="utf-8")

    assert "const _markCanvasDirty = () => container.dispatchEvent(new Event('input', { bubbles: true }));" in notes
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
