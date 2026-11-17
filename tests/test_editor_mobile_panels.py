import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RIGHT_PANEL = (ROOT / "static" / "js" / "editor" / "build" / "right-panel.js").read_text()
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_mobile_panel_overlay_mounted():
    assert "'ge-mobile-panels'" in RIGHT_PANEL
    assert "compact: true" in RIGHT_PANEL
    assert "ge-layers-panels-btn" in RIGHT_PANEL
    assert "overlay.hidden = !overlay.hidden;" in RIGHT_PANEL


def test_mobile_panel_overlay_is_phone_scoped():
    # Asserted against the whole stylesheet: the file now has several
    # `@media (max-width: 700px)` blocks, so index-based scoping is brittle.
    assert "@media (max-width: 700px)" in CSS
    assert ".ge-mobile-panels {" in CSS
    assert ".ge-layers-panels-btn {" in CSS
    assert ".ge-mobile-panels .ge-dock-select {" in CSS


def test_sw_cache_bumped():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m, "CACHE_NAME not found"
