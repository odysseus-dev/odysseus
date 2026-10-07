from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_notes_sidebar_new_action_matches_library_structure_and_animation():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    css = app_css()

    assert 'class="list-item-plus-btn sidebar-new-item-btn" id="notes-new-note-btn"' in html
    assert '<span class="list-item-plus-label">new</span>' in html[html.index('id="notes-new-note-btn"'):]
    assert "#tool-notes-btn:hover #notes-new-note-btn" in css
    assert "#notes-new-note-btn:hover svg" in css


def test_notes_sidebar_new_action_opens_a_note_without_toggling_panel_closed():
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    notes = (ROOT / "static/js/notes.js").read_text(encoding="utf-8")

    assert "event.stopPropagation()" in app[app.index("const notesNewNoteBtn"):]
    assert "notesModule?.newNote?.()" in app
    assert "export function newNote()" in notes
    assert "requestAnimationFrame(() => _createNote('note'))" in notes
    assert "openNote, newNote, openNotes" in notes


def test_sidebar_new_note_focuses_note_body_instead_of_title():
    notes = (ROOT / "static/js/notes.js").read_text(encoding="utf-8")

    assert "form.querySelector('.note-form-content')" in notes
    assert "initialField?.focus()" in notes


def test_notes_reminder_dot_stays_before_new_note_action():
    notes = (ROOT / "static/js/notes.js").read_text(encoding="utf-8")

    assert "sidebarBtn.querySelector('#notes-new-note-btn')" in notes
    assert "sidebarBtn.insertBefore(dot, newNoteBtn || null)" in notes


def test_checklist_preview_starts_at_first_unfinished_item():
    notes = (ROOT / "static/js/notes.js").read_text(encoding="utf-8")
    start = notes.index("function _bindCardEvents(body)")
    end = notes.index("const tapToEditOrSelect", start)
    setup = notes[start:end]

    assert ".note-checkbox:not(.done)" in setup
    assert "preview.scrollTop" in setup


def test_checklist_preview_releases_scroll_at_its_boundaries():
    notes = (ROOT / "static/js/notes.js").read_text(encoding="utf-8")
    css = app_css()

    assert "el.addEventListener('wheel'" in notes
    assert "el.closest('.notes-pane-body')" in notes
    assert "pane.scrollTop += e.deltaY" in notes
    assert "overscroll-behavior-y: auto" in css


def test_notes_tags_toggle_is_nudged_down():
    css = app_css()
    rule = css[css.index(".notes-search-bar .notes-label-toggle {"):]
    rule = rule[:rule.index("}")]

    assert "top: 4px" in rule


def test_notes_body_and_tag_arrow_are_nudged_down():
    css = app_css()

    assert css.count("padding: 10px 8px 8px") >= 2
    assert ".notes-pane-body .doclib-chip-scroll-arrow.right" in css
    assert "top: calc(50% - 6px) !important" in css[css.index(".notes-pane-body .doclib-chip-scroll-arrow.right"):]


def test_notes_tag_strip_cannot_grow_into_blank_space():
    css = app_css()
    rule = css[css.index(".notes-pane-body > .doclib-chip-scroll-frame:has(> .notes-labels-bar) {"):]
    rule = rule[:rule.index("}")]

    assert "flex: 0 0 auto" in rule
    assert "width: 100%" in rule
