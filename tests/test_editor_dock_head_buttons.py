import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCK_JS = (ROOT / "static/js/editor/build/dock.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_dock_head_button_glyphs_are_16px():
    # The group-head buttons (collapse/expand, more) use the same 16px glyph
    # as the float-window dock button. (The group detach glyph was removed.)
    assert DOCK_JS.count('width="16" height="16"') >= 4
    assert 'width="11" height="11"' not in DOCK_JS


def test_dock_head_buttons_match_float_dock_size():
    for cls in (".ge-dock-collapse {", ".ge-dock-detach {", ".ge-dock-tabs-more {"):
        idx = CSS.index(cls)
        block = CSS[idx:idx + 240]
        assert "width: 26px;" in block, cls
        assert "height: 26px;" in block, cls
        assert "padding: 0;" in block, cls


def test_dock_head_svg_rule_present():
    assert (
        ".ge-dock-collapse svg, .ge-dock-detach svg, .ge-dock-tabs-more svg "
        "{ width: 16px; height: 16px; flex: 0 0 auto; }" in CSS
    )


def test_sw_cache_name_bumped():
    m = re.search(r"CACHE_NAME\s*=\s*'([^']+)'", SW)
    assert m and re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
