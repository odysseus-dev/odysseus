"""Overnight editor-polish batch contracts.

Covers the UI/UX fixes applied in one pass: Brightness/Contrast presets,
clearer selection-mode icons, marquee action-row icons, the crop-apply
dismiss button, unified dock/layer icon sizing, the footer zoom glyph
nudge, the histogram repaint hook, the bucket dodge/burn fixes.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


CSS = _read("static/css/13-image-editor.css")
LH = _read("static/js/editor/layer-helpers.js")
OPTIONS = _read("static/js/editor/build/options-bar.js")
CONTROLS = _read("static/js/editor/build/controls.js")
EDITOR = _read("static/js/galleryEditor.js")
PANELS = _read("static/js/editor/panels/panels.js")
STROKE = _read("static/js/editor/stroke-pipeline.js")
BUCKET = _read("static/js/editor/tools/bucket.js")
SW = _read("static/sw.js")


def test_brightness_contrast_has_extra_presets():
    m = re.search(r"'brightness-contrast': \[([^\]]*)\]", LH)
    assert m, "brightness-contrast preset list missing"
    opts = m.group(1)
    for name in ("Brighten", "Darken", "Punch", "High Contrast", "Low Contrast", "Faded"):
        assert name in opts, name
    for case in (
        "brightness-contrast:Brighten",
        "brightness-contrast:Darken",
        "brightness-contrast:Punch",
        "brightness-contrast:Low Contrast",
        "brightness-contrast:Faded",
    ):
        assert f"case '{case}':" in LH, case


def test_intersect_mode_uses_overlap_icon():
    assert "const INTERSECT_ICON =" in OPTIONS
    assert "['intersect', INTERSECT_ICON]" in OPTIONS
    icon = '<circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/>'
    assert icon in OPTIONS
    # every wand/selection intersect button in the controls markup uses it
    assert CONTROLS.count(icon) >= 6
    assert 'data-wand-mode="intersect"' in CONTROLS


def test_marquee_action_row_has_svg_icons():
    assert 'class="ge-btn ge-btn-sm" id="ge-marquee-clear"' in CONTROLS
    assert 'aria-label="Clear selection"' in CONTROLS
    for gid in ("ge-marquee-invert", "ge-marquee-delete", "ge-marquee-copy", "ge-marquee-mask"):
        assert f'id="{gid}"' in CONTROLS, gid


def test_crop_apply_has_corner_dismiss():
    assert 'class="ge-crop-close"' in EDITOR
    # The × clears the committed crop via the tool reset (cancel() is a no-op
    # once the gesture has ended).
    assert "_cropTool.reset()" in EDITOR
    assert ".ge-crop-close {" in CSS
    assert "border-radius: 50%;" in CSS[CSS.index(".ge-crop-close {"):CSS.index(".ge-crop-close {") + 320]


def test_crop_dismiss_x_is_nudged_up():
    # × glyph up 4px inside the round button; the button itself up 5px.
    assert 'class="ge-crop-close-x"' in EDITOR
    assert ".ge-crop-close-x { display: block; line-height: 1; transform: translateY(-2px); }" in CSS
    idx = CSS.index(".ge-crop-close {")
    assert "top: -13px;" in CSS[idx:idx + 120]


def test_gradient_swatch_applies_the_gradient():
    # Clicking a preset selects the gradient tool and paints it across the layer.
    assert "document.querySelector('[data-tool=\"gradient\"]')?.click();" in PANELS
    assert "applyGradient?.();" in PANELS
    assert "applyGradient: () => _applyFullGradient()," in EDITOR
    assert "function _applyFullGradient()" in EDITOR
    assert "_gradientTool.begin(from)" in EDITOR


def test_dock_and_layer_icon_sizes_unified():
    idx = CSS.index(".ge-layer-btn svg,")
    block = CSS[idx:idx + 360]
    assert ".ge-layer-group-toggle svg {" in block
    assert "width: 14px;" in block
    assert "height: 14px;" in block


def test_adj_row_label_is_right_aligned():
    idx = CSS.index(".ge-adj-row > label {")
    block = CSS[idx:idx + 160]
    assert "text-align: right;" in block


def test_footer_zoom_glyph_nudged_down():
    assert ".ge-footer-zoom .ge-zoom-glyph { left: 0; top: 4px; }" in CSS


def test_histogram_picks_a_paintable_layer():
    assert "const histogramLayer = ()" in PANELS
    assert "drawHistogram(histCanvas, layer)" in PANELS


def test_dodge_and_burn_cap_per_dab_strength():
    # Dodge/burn strength is now driven by the Intensity slider (default 25%,
    # scaled to 0.5 so even 100% builds gradually) instead of a fixed clamp.
    assert STROKE.count("const intensity = Number.isFinite(state.dodgeBurnIntensity)") >= 2
    assert STROKE.count("alpha = Math.max(0.004, Math.min(0.6, alpha * intensity * 0.5));") >= 2
    assert "operation = 'screen';" in STROKE
    assert "operation = 'multiply';" in STROKE


def test_bucket_falls_back_to_a_paintable_layer():
    assert "function paintableLayer()" in BUCKET
    assert "'Select a pixel layer to fill'" in BUCKET


def test_sw_cache_name_matches():
    assert re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
