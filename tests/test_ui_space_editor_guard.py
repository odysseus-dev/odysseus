"""Space in the image editor must type/pan, never toggle a hovered card."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")


def test_space_toggles_are_disabled_while_the_editor_is_open():
    start = UI.index("document.addEventListener('keydown', (e) => {\n    if (e.code !== 'Space'")
    block = UI[start:start + 900]
    guard = block.index("if (document.body?.classList.contains('ge-editor-open')) return;")
    card = block.index("if (hoveredToggleCard && _isSpaceVisible(hoveredToggleCard)) {")
    assert guard < card, "the editor guard must run before any card toggle"


def test_space_always_types_in_a_text_field_even_outside_the_hovered_surface():
    start = UI.index("function _spaceIsBlocked(e, surface) {")
    end = UI.index("function _activateSpaceCard(card) {")
    block = UI[start:end]
    assert "if (_isTextEditingTarget(target)) return true;" in block
    assert "_isTextEditingTarget(target)) return !surface" not in block
