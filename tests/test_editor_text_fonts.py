import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def test_self_hosted_font_faces_are_declared():
    css = _read("static/css/00-tokens.css")
    for family in ["Inter", "Roboto", "Playfair Display", "Lobster", "Bebas Neue", "Roboto Mono"]:
        assert f"font-family: '{family}'" in css, family
    for fname in [
        "Inter-Regular.woff2",
        "Roboto-Regular.woff2",
        "PlayfairDisplay-Regular.woff2",
        "Lobster-Regular.woff2",
        "BebasNeue-Regular.woff2",
        "RobotoMono-Regular.woff2",
    ]:
        assert f"/static/fonts/{fname}" in css, fname


def test_font_files_exist_on_disk():
    for fname in [
        "Roboto-Regular.woff2",
        "Roboto-Bold.woff2",
        "PlayfairDisplay-Regular.woff2",
        "PlayfairDisplay-Bold.woff2",
        "Lobster-Regular.woff2",
        "BebasNeue-Regular.woff2",
        "RobotoMono-Regular.woff2",
        "RobotoMono-Bold.woff2",
    ]:
        assert (REPO / "static" / "fonts" / fname).is_file(), fname


def test_text_layer_exposes_font_helpers():
    js = _read("static/js/editor/text-layer.js")
    assert "export const EDITOR_TEXT_FONTS" in js
    assert "'Playfair Display'" in js and "'Bebas Neue'" in js and "'Roboto Mono'" in js
    assert "export function loadEditorFonts()" in js
    assert "export function availableEditorFonts()" in js
    assert "document.fonts.load(" in js
    assert "document.fonts.check(" in js


def test_options_bar_font_select_lists_webfonts():
    js = _read("static/js/editor/build/controls.js")
    for family in ["Inter", "Roboto", "Playfair Display", "Lobster", "Bebas Neue", "Roboto Mono"]:
        assert family in js, family


def test_wire_text_controls_loads_and_prunes_fonts():
    js = _read("static/js/galleryEditor.js")
    assert "_loadEditorFonts" in js
    assert "_availableEditorFonts" in js
    assert "Array.from(_fontSel.options).forEach" in js


def test_sw_version_is_current():
    sw = _read("static/sw.js")
    m = re.search(r"CACHE_NAME = '([^']+)'", sw)
    assert m and re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m
