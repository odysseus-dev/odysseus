"""Marker tests for the added Photoshop-style editor commands.

These assert the wiring for Image > Adjustments (Desaturate / Auto Tone /
Auto Contrast / Auto Color), Image > Trim, Image Rotation 90 CCW and
Filter > Sharpen > Smart Sharpen, all of which reuse existing ops.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()


def test_image_menu_items_present():
    for action in (
        'data-image-action="desaturate"',
        'data-image-action="auto-tone"',
        'data-image-action="auto-contrast"',
        'data-image-action="auto-color"',
        'data-image-action="trim"',
        'data-image-action="rotate-ccw"',
    ):
        assert action in TOPBAR, action


def test_smart_sharpen_filter_button_present():
    assert 'data-filter-action="smart-sharpen"' in TOPBAR


def test_wire_branches_present():
    assert "action === 'desaturate'" in WIRE
    assert "action === 'auto-tone'" in WIRE
    assert "action === 'auto-contrast'" in WIRE
    assert "action === 'auto-color'" in WIRE
    assert "action === 'trim'" in WIRE
    assert "action === 'rotate-ccw'" in WIRE
    assert "rotateAllLayers(270)" in WIRE
    assert "'adjustments.apply', { type: 'hue-saturation', params: { saturation: 0 } }" in WIRE
    assert "'adjustments.apply', { type: 'levels', params: { inBlack: 12, inWhite: 243 } }" in WIRE
    assert "'photo.autoTone'" in WIRE
    assert "'document.trim'" in WIRE


def test_document_trim_op_present():
    assert "'document.trim': {" in EDITOR
    assert "_cropDocument(state, rect);" in EDITOR


def test_smart_sharpen_alias_present():
    assert "'smart-sharpen': 'unsharp-mask'" in EDITOR
