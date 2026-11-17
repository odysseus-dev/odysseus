from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_color_mode_module_shape():
    src = read("static/js/editor/color-mode.js")
    for token in [
        "export const COLOR_MODES",
        "export const BIT_DEPTHS",
        "export function isConvertibleMode(",
        "export function rgbaToGrayscale(",
        "export function convertCanvasToMode(",
        "export function applyColorMode(",
        "export function supportsBitDepth(",
    ]:
        assert token in src


def test_state_has_color_mode_metadata():
    src = read("static/js/editor/state.js")
    assert "colorMode: 'rgb'" in src
    assert "bitDepth: 8" in src


def test_image_menu_exposes_modes_and_bits():
    src = read("static/js/editor/build/topbar.js")
    for token in [
        'data-image-action="mode-rgb"',
        'data-image-action="mode-grayscale"',
        'data-image-action="mode-cmyk"',
        'data-image-action="bits-8"',
        'data-image-action="bits-16"',
    ]:
        assert token in src


def test_wire_dispatches_color_modes():
    src = read("static/js/editor/wire-topbar-menus.js")
    assert "setColorMode" in src
    assert "setColorMode?.('rgb')" in src
    assert "setColorMode?.('grayscale')" in src


def test_gallery_wires_set_color_mode():
    src = read("static/js/galleryEditor.js")
    for token in [
        "from './editor/color-mode.js'",
        "function _setColorMode(mode)",
        "setColorMode: (mode) => _setColorMode(mode)",
    ]:
        assert token in src


def test_color_mode_module_is_precached():
    src = read("static/sw.js")
    assert "/static/js/editor/color-mode.js" in src


def test_codec_persists_color_mode():
    src = read("static/js/editor/document-codec.js")
    assert "colorMode: state.colorMode || 'rgb'" in src
    assert "colorMode: migrated.colorMode === 'grayscale' ? 'grayscale' : 'rgb'" in src
