"""Second editor-polish batch: slider chip editor alignment, action-row nudge,
wand caret styling, crop-close reset, hex copy + favorites."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SLIDER = (ROOT / "static/js/editor/slider-ux.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
PANELS = (ROOT / "static/js/editor/panels/panels.js").read_text(encoding="utf-8")
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")


def test_slider_edit_input_is_chip_sized():
    assert ".ge-eraser-row input.ge-slider-edit," in CSS
    assert ".ge-adj-row input.ge-slider-edit {" in CSS
    i = CSS.index(".ge-eraser-row input.ge-slider-edit,")
    block = CSS[i:i + 220]
    assert "height: 16px;" in block
    assert "box-sizing: border-box;" in block


def test_slider_edit_input_is_vertically_centred():
    assert "inp.style.top = (crect.top - rrect.top + (crect.height - inp.offsetHeight) / 2) + 'px';" in SLIDER
    assert "crect.top - rrect.top - 1" not in SLIDER


def test_action_row_buttons_are_nudged_up():
    assert ".ge-controls--in-options .ge-control-row.ge-actions .ge-btn {" in CSS
    i = CSS.index(".ge-controls--in-options .ge-control-row.ge-actions .ge-btn {")
    block = CSS[i:i + 120]
    assert "position: relative;" in block
    assert "top: -2px;" in block


def test_wand_more_button_has_no_box():
    i = CSS.index(".ge-wand-more-btn {")
    block = CSS[i:i + 200]
    assert "background: transparent;" in block
    assert "border-color: transparent;" in block


def test_wand_caret_and_value_are_nudged_left():
    i = CSS.index(".ge-wand-more-btn {")
    assert "left: -8px;" in CSS[i:i + 120]
    assert "top: -4px;" in CSS[i:i + 120]
    assert "#ge-wand-section .ge-slider-value { position: relative; left: -6px; }" in CSS


def test_crop_close_x_is_nudged_up():
    assert ".ge-crop-close-x { display: block; line-height: 1; transform: translateY(-2px); }" in CSS


def test_crop_close_clears_the_selection():
    assert "pop.querySelector('.ge-crop-close').addEventListener('click', () => _cropTool.reset());" in EDITOR
    assert "_cancelCrop('dismiss')" not in EDITOR


def test_crop_close_button_and_presets_are_nudged_up():
    idx = CSS.index(".ge-crop-close {")
    assert "top: -13px;" in CSS[idx:idx + 120]
    assert ".ge-crop-preset-row { display: flex; gap: 4px; position: relative; top: -4px; }" in CSS


def test_escape_removes_the_crop_like_the_x():
    # Escape routes through _cancelCrop, which now fully resets (not cancel()).
    assert "_cancelCrop('escape')" in EDITOR
    i = EDITOR.index("function _cancelCrop(reason = 'cancel') {")
    block = EDITOR[i:i + 520]
    assert "_cropTool.reset();" in block


def test_hex_row_has_copy_and_favorite_buttons():
    assert "el('button', 'ge-hex-copy')" in PANELS
    assert "el('button', 'ge-hex-copy ge-hex-fav')" in PANELS
    assert "hexRow.append(hexHash, hexInput, hexCopy, hexFav);" in PANELS
    assert "navigator.clipboard?.writeText" in PANELS
    assert ".ge-hex-copy {" in CSS
    assert ".ge-hex-fav.active {" in CSS


def test_color_favorites_are_persisted_and_rendered():
    assert "const FAV_KEY = 'ge-color-favorites';" in PANELS
    assert "const getFavoriteColors = () =>" in PANELS
    assert "const isFavorite = (hex) =>" in PANELS
    assert "favRow.innerHTML = favs.length" in PANELS
    assert "ge-swatch-fav" in PANELS


def test_ctl_range_thumb_is_nudged_up_one_px():
    i = CSS.index(".ge-ctl-range::-webkit-slider-thumb {")
    block = CSS[i:i + 200]
    assert "margin-top: -5px;" in block


def test_wand_tolerance_value_chip_is_always_tinted():
    assert "#ge-wand-section #ge-wand-tol-label.ge-slider-value {" in CSS
    i = CSS.index("#ge-wand-section #ge-wand-tol-label.ge-slider-value {")
    block = CSS[i:i + 160]
    assert "background: color-mix(in srgb, var(--fg) 10%, transparent);" in block
    assert "opacity: 1;" in block


def test_selection_action_row_is_pinned_to_the_right_edge():
    assert ".ge-options-bar .ge-controls.ge-controls--in-options {" in CSS
    i = CSS.index(".ge-options-bar .ge-controls.ge-controls--in-options {")
    assert "flex: 1 1 auto;" in CSS[i:i + 260]
    assert ".ge-controls--in-options .ge-control-row.ge-actions { position: relative; top: -2px; margin-left: auto; }" in CSS
    j = CSS.index(".ge-controls--in-options > div:not([style*=\"display:none\"])")
    assert "flex: 1 1 auto;" in CSS[j:j + 500]


def test_crop_can_expand_beyond_the_canvas():
    # normalizeCropRect must not clamp back inside the image anymore.
    GEO = (ROOT / "static/js/editor/document-geometry.js").read_text(encoding="utf-8")
    i = GEO.index("function normalizeCropRect(")
    block = GEO[i:i + 900]
    assert "editorState.imgWidth - 1" not in block
    assert "Math.max(-MAX, Math.min(MAX, Math.round(rect.x)))" in block
    assert "const MAX = 20000;" in block


def test_crop_tool_allows_dragging_past_the_edge_and_syncs_a_frame():
    CROP = (ROOT / "static/js/editor/tools/crop.js").read_text(encoding="utf-8")
    assert "createCropTool({ composite, showCropApply, snapFrame, syncCropFrame })" in CROP
    assert "x: Math.max(-MAX, Math.min(MAX, end.x))," in CROP
    assert "syncCropFrame?.();" in CROP


def test_crop_frame_outline_is_wired_and_styled():
    assert "function _syncCropFrame()" in EDITOR
    assert "el.className = 'ge-crop-frame';" in EDITOR
    assert "syncCropFrame: () => _syncCropFrame()," in EDITOR
    assert ".ge-crop-frame {" in CSS
    i = CSS.index(".ge-crop-frame {")
    block = CSS[i:i + 260]
    assert "position: absolute;" in block
    assert "pointer-events: none;" in block


def test_crop_can_start_outside_the_canvas_without_panning():
    # The crop tool must be able to begin a rect in the empty area around the
    # image (so it can be pulled out past the edge) instead of the canvas-area
    # pan grabbing the pointer and switching to hand mode.
    CE = (ROOT / "static/js/editor/canvas-events.js").read_text(encoding="utf-8")
    assert "if (state.tool !== 'lasso' && state.tool !== 'crop') return;" in CE
    assert "(state.tool === 'lasso' || state.tool === 'crop') && !directPan" in CE
    # Pressing editor chrome inside the area (e.g. the crop Apply panel) must
    # not start a fresh crop.
    assert ".ge-crop-apply, .ge-adj-popup, .ge-transform-popup, .ge-fx-popup, .ge-inpaint-popup" in CE


def test_crop_apply_has_a_standard_size_dropdown():
    # The crop Apply box (W × H pixel inputs) carries a dropdown of standard
    # crop sizes that set the rect to a fixed pixel size (or the original).
    assert 'class="ge-crop-size-preset"' in EDITOR
    i = EDITOR.index('class="ge-crop-size-preset"')
    block = EDITOR[i:i + 900]
    for v in ('custom', '1080x1080', '1080x1350', '1080x1920', '1200x630',
              '1280x720', '1920x1080', '2048x1536', '3000x2400', '4000x3000', 'original'):
        assert f'value="{v}"' in block
    assert '<option value="custom">Custom</option>' in block
    assert '<option value="original">Original</option>' in block
    # change handler applies the chosen pixel size / original image size
    assert "sizeSel?.addEventListener('change'" in EDITOR
    assert "if (v === 'original') { w = state.imgWidth; h = state.imgHeight; }" in EDITOR
    assert "const m = /^(\\d+)x(\\d+)$/.exec(v);" in EDITOR
    # manual W/H edits reset the dropdown to Custom
    assert "if (sizeSel) sizeSel.value = 'custom';" in EDITOR
    # CSS styles the select like the number inputs and leaves a gap to its right
    assert ".ge-crop-apply select.ge-crop-size-preset {" in CSS
    ci = CSS.index(".ge-crop-apply select.ge-crop-size-preset {")
    assert "margin-right: 8px;" in CSS[ci:ci + 260]


def test_clear_x_is_nudged_down_and_lit_when_a_selection_is_active():
    # The Clear ✕ in the marquee/wand action rows: the glyph sits 1px lower,
    # and the button turns accent while a selection is active.
    i = CSS.index("#ge-marquee-clear svg,")
    block = CSS[i:i + 900]
    assert "#ge-wand-clear svg," in block
    assert "#ge-objectselect-clear svg { position: relative; top: 1px; }" in block
    assert "#ge-marquee-clear.has-selection," in block
    assert "#ge-objectselect-clear.has-selection { color: var(--accent, var(--red));" in block
    assert "querySelectorAll('#ge-marquee-clear, #ge-wand-clear, #ge-lasso-clear, #ge-sam-clear, #ge-quickselect-clear, #ge-objectselect-clear').forEach((btn) => {" in EDITOR
    assert "btn.classList.toggle('has-selection', !!state.wandMask);" in EDITOR


def test_transform_cancel_and_apply_are_right_aligned():
    # The Transform popup's Cancel + Apply share the right edge of the body.
    i = CSS.index(".ge-transform-popup-body #ge-transform-cancel-btn {")
    assert "margin-left: auto;" in CSS[i:i + 120]


def test_clone_source_clear_matches_the_wand_clear_button():
    # The Clone "clear sampled source" ✕ now uses the same SVG cross and
    # button classes as the wand/marquee Clear ✕, and lights up (accent)
    # while a source is sampled.
    assert 'class="ge-btn ge-btn-sm" id="ge-clone-source-clear"' in CONTROLS
    assert '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>' in CONTROLS
    assert "ge-icon-btn\" id=\"ge-clone-source-clear\"" not in CONTROLS
    assert "#ge-clone-source-clear svg { position: relative; top: -1px; }" in CSS
    assert "#ge-clone-source-clear.has-selection { color: var(--accent, var(--red));" in CSS
    assert "clear.classList.toggle('has-selection', hasSource);" in EDITOR


def test_clone_sample_mode_lives_in_the_clone_section():
    # The Sample select was moved out of the brush "More" menu (the custom
    # stroke group) into the dedicated Clone section, sharing the standard
    # control-select style.
    assert 'id="ge-clone-sample-mode" class="ge-ctl-select"' in CONTROLS
    assert '<select id="ge-clone-sample-mode">' not in CONTROLS
    section_at = CONTROLS.index('id="ge-clone-section"')
    select_at = CONTROLS.index('id="ge-clone-sample-mode"')
    assert select_at > section_at


def test_ai_canvas_ascii_wave_uses_the_theme_accent():
    # The flowing ASCII "wave" skeleton painted over the canvas while an image
    # is generated used a hardcoded green; it must follow the theme accent.
    assert ".ge-canvas-area .ge-ai-ascii-fireflies {" in CSS
    block = CSS[CSS.index(".ge-canvas-area .ge-ai-ascii-fireflies {"):]
    block = block[:block.index("}")]
    assert "color: var(--accent, var(--red));" in block
    assert "#b8e6c1" not in block
    assert "rgba(184, 230, 193, 0.20)" not in CSS
    assert "color-mix(in srgb, var(--accent, var(--red)) 20%, transparent)" in CSS
