"""The brush options strip fits on one line and hides its secondary controls
(Spacing / Smoothing / Blend / pen-pressure) behind a caret ("v") dropdown,
matching the wand's "More" treatment."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
OPTIONS = (ROOT / "static/js/editor/build/options-bar.js").read_text(encoding="utf-8")
STROKE = (ROOT / "static/js/editor/stroke-tool-sliders.js").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_brush_strip_has_a_caret_more_dropdown():
    assert '<span class="ge-brush-more-wrap">' in CONTROLS
    assert 'id="ge-brush-more"' in CONTROLS
    assert 'aria-haspopup="true" aria-expanded="false"' in CONTROLS
    assert "ge-brush-more-btn" in CONTROLS
    assert '<path d="m6 9 6 6 6-6"/>' in CONTROLS
    assert 'class="ge-brush-more-menu" id="ge-brush-more-menu" hidden' in CONTROLS


def test_secondary_brush_controls_live_after_the_menu_open():
    i = CONTROLS.index('id="ge-brush-more-menu"')
    j = CONTROLS.index('ge-brush-preset-row', i)
    block = CONTROLS[i:j]
    for marker in ('id="ge-brush-spacing"', 'id="ge-brush-smoothing"',
                   'id="ge-brush-blend"', 'ge-brush-pressure-row',
                   'id="ge-brush-flow-row"'):
        assert marker in block, marker
    assert 'id="ge-brush-softness-row"' not in block
    assert CONTROLS.index('id="ge-brush-softness-row"') < i


def test_brush_size_label_drops_the_redundant_word():
    # The px value stays but the redundant "Size" word next to the brush is gone.
    assert '<label><span class="ge-size-label">' in CONTROLS
    assert ">Size <span" not in CONTROLS
    assert "nodeValue = (toolId === 'eraser') ? 'Brush Size ' : 'Size ';" not in GALLERY


def test_stroke_groups_are_gated_per_tool():
    # Every brush-family tool shares one strip; the caret menu swaps the
    # secondary sliders by data-stroke-tool group.
    assert "const strokeTool = (toolId === 'brush' || toolId === 'pixel' || toolId === 'dodge' || toolId === 'burn') ? 'brush'" in GALLERY
    assert "document.querySelectorAll('#ge-brush-more-menu [data-stroke-tool]')" in GALLERY
    assert "row.dataset.strokeTool === strokeTool ? '' : 'none';" in GALLERY
    for group in ('eraser', 'clone', 'smudge'):
        assert f'class="ge-brush-stroke-group" data-stroke-tool="{group}"' in CONTROLS, group
    # The former standalone sections are gone.
    assert 'id="ge-brush-section"' not in CONTROLS
    assert 'id="ge-eraser-section"' not in CONTROLS


def test_every_stroke_group_is_a_titled_card():
    # Each brush-family tool's rows ride the same titled card as Eraser so every
    # caret menu looks and reads the same, just with more or fewer rows.
    for group in ('brush', 'eraser', 'clone', 'smudge'):
        assert f'class="ge-brush-stroke-group" data-stroke-tool="{group}"' in CONTROLS, group
    assert '<div class="ge-section-title" id="ge-stroke-title-brush">Brush</div>' in CONTROLS
    assert '<div class="ge-section-title" id="ge-stroke-title-clone">Clone</div>' in CONTROLS
    assert '<div class="ge-section-title">Eraser</div>' in CONTROLS
    assert '<div class="ge-section-title">Smudge</div>' in CONTROLS
    # The shared brush card takes the active tool's name (Pixel / Dodge / Burn);
    # the clone card reads Healing for the heal brush.
    assert "const brushStrokeTitle = document.getElementById('ge-stroke-title-brush');" in GALLERY
    assert "brushStrokeTitle.textContent = _TOOLS[toolId]?.label || 'Brush';" in GALLERY
    assert "const cloneStrokeTitle = document.getElementById('ge-stroke-title-clone');" in GALLERY
    assert "cloneStrokeTitle.textContent = toolId === 'heal' ? 'Healing' : 'Clone';" in GALLERY
    # The rows no longer carry data-stroke-tool themselves — the card does.
    assert 'id="ge-brush-flow-row" data-stroke-tool="brush"' not in CONTROLS
    assert 'id="ge-brush-softness-row" data-stroke-tool="brush"' not in CONTROLS
    assert 'id="ge-brush-opacity-row" data-stroke-tool="brush"' not in CONTROLS


def test_generic_options_more_button_is_retired():
    assert "const showMore = false;" in OPTIONS
    assert "moreBtn.hidden = !showMore;" in OPTIONS


def test_brush_more_menu_is_wired():
    assert "document.getElementById('ge-brush-more')" in STROKE
    assert "document.getElementById('ge-brush-more-menu')" in STROKE
    assert "const closeBrushMore = () => {" in STROKE
    assert "if (event.key === 'Escape') closeBrushMore();" in STROKE


def test_brush_more_menu_css_and_overflow_release():
    assert ".ge-brush-more-wrap { position: relative; display: inline-flex; }" in CSS
    assert ".ge-brush-more-menu {" in CSS
    assert ".ge-brush-more-menu[hidden] { display: none; }" in CSS
    assert '.ge-brush-more-btn[aria-expanded="true"] svg { transform: rotate(180deg); }' in CSS
    assert ".ge-options-bar:has(#ge-brush-more-menu:not([hidden])) { overflow: visible; }" in CSS


def test_brush_strip_fits_and_smudge_row_respects_inline_hide():
    # The Opacity/Flow/Softness sliders are wound tighter so the strip fits.
    assert ".ge-controls--in-options #ge-brush-section { gap: 6px; }" in CSS
    # The brush controls hug their content so the sliders sit next to the caret.
    assert ".ge-controls--in-options #ge-brush-controls { flex: 0 0 auto; flex-direction: row;" in CSS
    # The smudge Strength row is hidden with an inline display:none on other
    # tools; the `.ge-eraser-row { display:flex !important }` rule must not
    # override that.
    assert '.ge-eraser-row[style*="display:none"],' in CSS
    assert '.ge-eraser-row[style*="display: none"] { display: none !important; }' in CSS
    # The preset Save + × sit a touch high to line up with the dropdown.
    assert ".ge-brush-preset-row .ge-btn { flex: 0 0 auto; height: 24px; display: inline-flex; align-items: center; position: relative; top: -3px; }" in CSS
    # ...and [hidden] must beat that inline-flex so a built-in preset hides them.
    assert ".ge-brush-preset-row .ge-btn[hidden] { display: none !important; }" in CSS


def test_caret_sits_in_the_size_row_next_to_the_slider():
    # The caret ("v") is inside the Size row, right after the size slider.
    row_i = CONTROLS.index('class="ge-control-row ge-brush-size-row"')
    preset_i = CONTROLS.index('ge-brush-preset-row', row_i)
    row = CONTROLS[row_i:preset_i]
    assert "ge-size-slider" in row
    assert "ge-brush-more-wrap" in row
    assert row.index("ge-size-slider") < row.index("ge-brush-more-wrap")
    assert ".ge-brush-size-row .ge-brush-more-wrap { margin-left: 4px; }" in CSS


def test_custom_slot_and_save_button_visibility():
    # Save/delete only appear for a user-defined preset, never for a built-in.
    assert '<option value="">Custom</option>' in STROKE
    assert "const syncPresetButtons = () => {" in STROKE
    assert "const isBuiltIn = !!preset && !!preset.builtIn;" in STROKE
    assert "const isUserCustom = !!preset && !preset.builtIn;" in STROKE
    assert "if (presetSaveBtn) presetSaveBtn.hidden = isBuiltIn;" in STROKE
    assert "if (presetDeleteBtn) presetDeleteBtn.hidden = !isUserCustom;" in STROKE
    # The dropdown remembers the last picked preset instead of defaulting to Custom.
    assert "savedPresetId = localStorage.getItem('ge-brush-preset') || '';" in STROKE
    assert "renderPresets(presets.some(item => item.id === savedPresetId && !item.pixel) ? savedPresetId : '');" in STROKE
    assert "localStorage.setItem('ge-brush-preset', presetSelect.value || '');" in STROKE


def test_custom_slot_drops_the_pixel_preset():
    # Selecting "Custom" clears pixel mode so the 1px Pixel preset is escapable.
    assert "if (!preset) {" in STROKE
    assert "state.brushPixel = false;" in STROKE


def test_opacity_row_moved_into_the_caret_menu():
    i = CONTROLS.index('id="ge-brush-more-menu"')
    j = CONTROLS.index('ge-brush-preset-row', i)
    block = CONTROLS[i:j]
    assert 'id="ge-brush-opacity-row"' in block
    assert 'id="ge-brush-opacity-label"' in block
    assert 'id="ge-brush-opacity"' in block
    # Opacity rides the brush stroke group, shown for brush/dodge/burn.
    assert "data-stroke-tool=\"brush\"" in block
    assert "row.dataset.strokeTool === strokeTool ? '' : 'none';" in GALLERY


def test_spacing_and_smoothing_have_preview_dots():
    assert 'class="ge-eraser-preview" id="ge-brush-preview-spacing"' in CONTROLS
    assert 'class="ge-eraser-preview" id="ge-brush-preview-smoothing"' in CONTROLS
    assert "const spacingPrev = document.getElementById('ge-brush-preview-spacing');" in STROKE
    assert "const smoothingPrev = document.getElementById('ge-brush-preview-smoothing');" in STROKE
    assert "spacingPrev.style.opacity =" in STROKE
    assert "smoothingPrev.style.background =" in STROKE


def test_brush_colour_syncs_to_the_sidebar_swatch():
    assert "const _syncFgSwatch = (hex) => { if (fgSwatch) fgSwatch.value = hex; };" in GALLERY
    assert "_syncFgSwatch(e.target.value);" in GALLERY


def test_blend_row_is_right_aligned():
    assert ".ge-brush-more-menu #ge-brush-blend { margin-left: auto; }" in CSS


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
