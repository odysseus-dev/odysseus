"""Swatches panel: inline picker + hex field + suggestions/recent (no big swatch)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_swatch_input_is_not_shown():
    js = _read("static/js/editor/panels/panels.js")
    assert "swatchBody.appendChild(colorInput);" not in js
    # The popover trigger is gone with it.
    assert "attachColorPicker(colorInput)" not in js


def test_hex_code_field_is_wired():
    js = _read("static/js/editor/panels/panels.js")
    assert "const hexRow = el('div', 'ge-hex-row');" in js
    assert "const hexInput = el('input', 'ge-hex-input');" in js
    assert "hexRow.append(hexHash, hexInput, hexCopy, hexFav);" in js
    assert "swatchBody.appendChild(hexRow);" in js
    assert "const _setHex = (hex) => {" in js
    assert "hexInput.addEventListener('input'," in js


def test_inline_picker_and_suggestions_still_present():
    js = _read("static/js/editor/panels/panels.js")
    assert "const picker = el('div', 'ge-inline-picker');" in js
    assert "'ge-swatch-row ge-swatch-suggest'" in js
    assert "'ge-swatch-row ge-swatch-recent'" in js


def test_hex_css_present():
    css = _read("static/css/13-image-editor.css")
    assert ".ge-hex-row {" in css
    assert ".ge-hex-hash {" in css
    assert ".ge-hex-input {" in css


def test_adjustment_grid_is_tightened():
    css = _read("static/css/13-image-editor.css")
    idx = css.index(".ge-adjustment-grid {")
    block = css[idx:idx + 160]
    assert "gap: 3px;" in block
    assert "padding: 4px 6px;" in block


def test_colour_pickers_forced_round():
    css = _read("static/css/documents-gallery-editor.css")
    assert "input.ge-color-picker," in css
    assert "input.ge-color-picker::-webkit-color-swatch {" in css
    assert "border-radius: 50% !important;" in css


def test_dock_undock_controls_removed():
    js = _read("static/js/editor/build/dock.js")
    assert "ge-dock-tab-pop" not in js
    assert "ge-dock-detach" not in js


def test_sw_cache_name_versioned():
    m = re.search(r"CACHE_NAME = '([^']+)'", _read("static/sw.js"))
    assert m and re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
