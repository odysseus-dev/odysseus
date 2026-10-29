"""Static contract tests for three editor layout fixes:

1. The side rail must collapse when every dock panel is hidden (no blank gap),
   and the canvas must reclaim the freed width when Layers/Tool Options are also
   hidden.
2. The floating History panel must clamp itself on-screen (its trigger lives in
   the footer, so anchoring below used to run off the bottom of the viewport).
3. The Select menu never received its max-height/overflow treatment because a
   `.ge-select-menu` class typo did not match the real `.ge-selection-menu`
   element, so the Modify flyout was clipped.

These are plain JavaScript/CSS, so the tests assert the source-level contract.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULE = ROOT / "static/js/editor/panel-visibility.js"
HISTORY = ROOT / "static/js/editor/history-panel.js"
CSS = ROOT / "static/css/13-image-editor.css"
SW = ROOT / "static/sw.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_apply_collapses_dock_and_rail_when_hidden():
    src = _read(MODULE)
    # Every dock is hidden once no group has a visible member.
    assert "const anyDockVisible = DOCK_GROUPS.some(group =>" in src
    assert "document.querySelectorAll('.ge-dock').forEach(el => markHidden(el, !anyDockVisible));" in src
    # The whole rail collapses only when the dock AND the pinned strips are gone.
    assert "const railEmpty = !anyDockVisible && hidden.has('layers') && hidden.has('options');" in src
    assert "markHidden(el, railEmpty);" in src
    # A still-visible rail is told to stop stretching to the full column height.
    assert "if (!anyDockVisible) el.setAttribute('data-panel-dock-hidden', '1');" in src
    assert "else el.removeAttribute('data-panel-dock-hidden');" in src


def test_css_collapses_dock_and_rail():
    src = _read(CSS)
    assert '.ge-dock[data-panel-hidden="1"],' in src
    assert '.ge-right-panel[data-panel-hidden="1"] { display: none !important; }' in src
    assert '.ge-right-panel[data-panel-dock-hidden="1"] { align-self: flex-start; height: auto; }' in src
    assert '.ge-right-panel[data-panel-dock-hidden="1"] > .ge-layers { flex: 0 0 auto; max-height: 100%; }' in src


def test_grid_field_no_longer_crowds_menu_edge():
    src = _read(CSS)
    # Roomier right padding on the View > Grid size field row.
    assert "padding: 4px 14px 4px 8px" in src


def test_history_panel_clamps_on_screen():
    src = _read(HISTORY)
    # Content is filled before the height is measured.
    assert "refreshHistoryPanelIfOpen();" in src
    assert "const ph = panel.offsetHeight || 200;" in src
    assert "const pw = panel.offsetWidth || 240;" in src
    # Flips above the footer button and clamps both edges when it would overflow.
    assert "if (top + ph > window.innerHeight - 8) {" in src
    assert "top = Math.max(8, r.top - ph - 6);" in src
    assert "if (top < 8) top = Math.max(8, window.innerHeight - ph - 8);" in src
    assert "let left = Math.max(8, Math.min(r.left, window.innerWidth - pw - 8));" in src


def test_select_menu_overflow_rule_uses_real_class():
    src = _read(CSS)
    # The typo is gone everywhere.
    assert "ge-select-menu" not in src
    # The real Select menu carries the scrollbar/overflow treatment...
    assert ".ge-selection-menu," in src
    assert ".ge-selection-menu::-webkit-scrollbar," in src
    # ...and the submenu flyout releases the menu's clip while hovered/open.
    assert ".ge-selection-menu:has(.ge-menu-parent.open)," in src
    assert ".ge-selection-menu:has(.ge-menu-parent:hover)," in src
    assert ".ge-selection-menu:has(.ge-submenu:hover) {" in src
    assert "overflow: visible !important;" in src


def test_sw_cache_name_bumped():
    src = _read(SW)
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", src)
