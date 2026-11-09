import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_float_dock_svg_is_16px():
    body = _read("static/js/editor/build/dock.js")
    assert '<svg width="16" height="16"' in body
    assert '<svg width="12" height="12"' not in body
    # Two float-dock buttons (panel + group) plus the collapse/more head
    # buttons, all on the shared 16px glyph.
    assert body.count('<svg width="16" height="16"') >= 2


def test_float_dock_button_css_is_larger():
    css = _read("static/css/13-image-editor.css")
    assert ".ge-float-dock {" in css
    assert "width: 28px;" in css
    assert "height: 26px;" in css
    assert "padding: 0;" in css
    assert ".ge-float-dock svg { width: 16px; height: 16px; flex: 0 0 auto; }" in css


def test_sw_cache_name_version_agnostic():
    body = _read("static/sw.js")
    m = re.search(r"const CACHE_NAME = '([^']+)';", body)
    assert m
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
