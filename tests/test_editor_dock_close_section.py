"""Each docked section's "..." menu closes that whole section.

Audit/request (2026-10-13): a dock section (Color, Adjustments, Panels,
History) has no way to be dismissed from itself — you had to hunt through
Window > panel toggles. The section's "..." (tabs-more) menu now carries a
"Close <group>" row (Photoshop's "Close Tab Group") that hides every panel
in the group; the group re-collapses out of the rail and returns when one of
its panels is re-enabled from the Window menu.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DOCK = (ROOT / "static/js/editor/build/dock.js").read_text(encoding="utf-8")
RIGHT = (ROOT / "static/js/editor/build/right-panel.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_dock_accepts_and_uses_onCloseGroup():
    assert "onCloseGroup = null" in DOCK
    assert "typeof onCloseGroup === 'function'" in DOCK
    assert "onCloseGroup(group.name)" in DOCK


def test_tabs_menu_offers_close_section():
    assert "ge-dock-tabs-menu-close" in DOCK
    assert "ge-dock-tabs-menu-sep" in DOCK
    i = DOCK.index("ge-dock-tabs-menu-close")
    block = DOCK[i - 400:i + 200]
    assert "Close ${group.name}" in block


def test_more_button_shown_when_a_close_handler_exists():
    i = DOCK.index("const overflowing = tabsEl.scrollWidth")
    block = DOCK[i:i + 400]
    assert "typeof onCloseGroup === 'function'" in block


def test_right_panel_forwards_onCloseGroup():
    assert "onCloseGroup = null" in RIGHT
    i = RIGHT.index("onCloseGroup,")
    block = RIGHT[i:i + 60]
    assert "});" in block


def test_composer_closes_every_panel_in_the_group():
    i = EDITOR.index("onCloseGroup: (groupName) =>")
    block = EDITOR[i:i + 260]
    assert "panelVisibility.PANELS" in block
    assert "p.group === groupName" in block
    assert "panelVisibility.setVisible(p.id, false)" in block


def test_close_menu_styling_present():
    assert ".ge-dock-tabs-menu-close {" in CSS
    assert ".ge-dock-tabs-menu-sep {" in CSS


def test_sw_cache_bumped():
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
