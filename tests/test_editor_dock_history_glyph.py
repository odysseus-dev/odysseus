import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_tab_undock_glyph_is_hover_and_keyboard_only():
    # The per-tab undock glyph must not persist on a merely-clicked (focused) tab.
    assert ".ge-dock-tab:focus-visible ~ .ge-dock-tab-pop" in CSS
    assert ":focus-within .ge-dock-tab-pop" not in CSS


def test_group_undock_has_divider_and_margin():
    start = CSS.index(".ge-dock-detach {")
    block = CSS[start : start + 400]
    assert "margin-left: 2px;" in block
    assert "border-left: 1px solid" in block


def test_docked_history_has_no_open_in_window_button():
    panels = (ROOT / "static/js/editor/panels/studio-panels.js").read_text(encoding="utf-8")
    assert "ge-history-popout" not in panels
    assert "Open in window" not in panels


def test_sw_cache_name_versions():
    m = re.search(r"CACHE_NAME = '([^']+)'", SW)
    assert m is not None
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
