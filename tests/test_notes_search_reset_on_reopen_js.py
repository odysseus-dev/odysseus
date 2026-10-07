"""Issue #2919 — openPanel must reset _searchQuery so a reopened Notes panel
doesn't keep filtering by a stale query (the rebuilt search box renders empty).

notes.js is a browser ES module with a heavy import chain (can't node-import in
isolation), so — per the repo's DOM-coupled-guard convention — this asserts the
reset is present in openPanel, beside the existing _editingId reset.
"""
import re
from pathlib import Path

SRC = Path("static/js/notes.js").read_text(encoding="utf-8")


def _open_panel_body():
    start = SRC.index("export function openPanel()")
    rest = SRC[start + len("export function openPanel()"):]
    m = re.search(r"\n(?:export\s+)?(?:async\s+)?function ", rest)
    return rest[: m.start()] if m else rest


def test_open_panel_resets_search_query():
    body = _open_panel_body()
    assert "_searchQuery = ''" in body, body[:400]
    # reset must sit with the other open-time state resets, before render
    assert body.index("_searchQuery = ''") < body.index("_renderNotes") if "_renderNotes" in body else True


def test_module_still_declares_search_query():
    assert "let _searchQuery = ''" in SRC


def test_notes_label_chips_can_be_collapsed_like_calendar_tags():
    assert "NOTES_LABELS_COLLAPSED_KEY = 'odysseus-notes-labels-collapsed'" in SRC
    assert "_labelsCollapsed = !_labelsCollapsed" in SRC
    assert "localStorage.setItem(NOTES_LABELS_COLLAPSED_KEY" in SRC
    assert "toggle.textContent = _labelsCollapsed ? '+ Tags' : '− Tags';" in SRC
    assert "bar.style.display = 'none';" in SRC


def test_notes_tag_toggle_sits_in_search_toolbar_before_select():
    assert "const toggleHost = pane?.querySelector('.notes-search-bar') || _body;" in SRC
    assert "const selectButton = toggleHost.querySelector('#notes-select-btn');" in SRC
    assert "toggleHost.insertBefore(toggle, selectButton || null);" in SRC


def test_expanded_tags_strip_hides_when_no_useful_filters_exist():
    assert "const hasUsefulFilters = sortedLabels.length > 0 || reminderCount > 0 || goalCount > 0;" in SRC
    assert "if (!hasUsefulFilters && !hasActiveFilter)" in SRC
    assert "bar.style.display = 'none';" in SRC


def test_drawing_preview_opens_its_note_editor():
    assert "note.note_type === 'draw' ? ' note-card-drawing' : ''" in SRC
    assert "body.querySelectorAll('.note-card-drawing .note-card-image')" in SRC
    assert "tapToEditOrSelect(el.closest('.note-card'))" in SRC


def test_drawing_defaults_follow_theme_surface_and_highlight():
    assert "const surfaceColor = resolveThemeColor('var(--panel, var(--bg))'" in SRC
    assert "resolveThemeColor('var(--accent, var(--red))'" in SRC
    assert "if (colorInput) colorInput.value = highlightColor;" in SRC
    assert "ctx.strokeStyle = erasing ? surfaceColor" in SRC
    assert "destination-out" not in SRC
