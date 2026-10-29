import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def test_self_hosted_font_faces_are_declared():
    css = _read("static/css/00-tokens.css")
    for family in ["Inter", "Roboto", "Playfair Display", "Lobster", "Bebas Neue", "Roboto Mono",
                   "Space Grotesk", "Fraunces", "Syne", "Bungee", "Instrument Serif"]:
        assert f"font-family: '{family}'" in css, family
    for fname in [
        "Inter-Regular.woff2",
        "Roboto-Regular.woff2",
        "PlayfairDisplay-Regular.woff2",
        "Lobster-Regular.woff2",
        "BebasNeue-Regular.woff2",
        "RobotoMono-Regular.woff2",
        "SpaceGrotesk-Regular.woff2",
        "Fraunces-Regular.woff2",
        "Syne-Regular.woff2",
        "Bungee-Regular.woff2",
        "InstrumentSerif-Regular.woff2",
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
        "SpaceGrotesk-Regular.woff2",
        "SpaceGrotesk-Bold.woff2",
        "Fraunces-Regular.woff2",
        "Fraunces-Bold.woff2",
        "Syne-Regular.woff2",
        "Syne-Bold.woff2",
        "Bungee-Regular.woff2",
        "InstrumentSerif-Regular.woff2",
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
    for family in ["Inter", "Roboto", "Playfair Display", "Lobster", "Bebas Neue", "Roboto Mono",
                   "Space Grotesk", "Fraunces", "Syne", "Bungee", "Instrument Serif"]:
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


def test_text_box_width_survives_the_position_loop():
    js = _read("static/js/editor/text-edit-overlay.js")
    # The per-frame position() used to rewrite the width unconditionally, which
    # stomped the native resize handle before the ResizeObserver saw it.
    assert "const targetW = Math.max(80, text.frameWidth * scaleX);" in js
    assert "if (!resizing && Math.abs(targetW - appliedWidth) > 0.5) {" in js
    assert "input.style.minWidth = '40px';" in js
    assert "let appliedWidth = 0;" in js
    assert "appliedWidth = 0;" in js


def test_text_box_has_a_visible_corner_resize_handle():
    js = _read("static/js/editor/text-edit-overlay.js")
    css = _read("static/css/13-image-editor.css")
    # The native textarea grip is invisible and tiny in this theme, so the
    # editor exposes its own 14px grab square that drives the frame width.
    assert "handle.className = 'ge-direct-text-handle';" in js
    assert "handle.addEventListener('pointerdown'" in js
    assert "handle.addEventListener('pointermove'" in js
    assert "input.style.width = `${w}px`;" in js
    assert "onInput?.(layer, input.value, input, w / Math.max(0.01, dragStart.scale));" in js
    assert "handle.innerHTML = '<svg width=\"12\" height=\"12\"" in js
    assert ".ge-direct-text-handle {" in css
    assert "cursor: nwse-resize;" in css
    assert "width: 16px;" in css
    handle_block = css[css.index(".ge-direct-text-handle {"):]
    handle_block = handle_block[:handle_block.index("}")]
    assert "position: fixed;" in handle_block
    assert "z-index: 10021;" in handle_block
    assert "background: transparent;" in handle_block
    assert "opacity: 0.55;" in handle_block
    # The editing surface is see-through so the artwork stays visible while typing.
    editor_block = css[css.index(".ge-direct-text-editor {"):]
    editor_block = editor_block[:editor_block.index("}")]
    assert "background: transparent;" in editor_block
    # `body.ge-editor-open textarea` is more specific, so the transparent
    # surface is restated for the on-canvas text field.
    override = css[css.index("body.ge-editor-open textarea.ge-direct-text-editor {"):]
    override = override[:override.index("}")]
    assert "background: transparent;" in override
    assert "border: 1px dashed var(--red);" in override
