"""Marker tests for the fifth batch of Photoshop-style editor commands.

These assert the wiring for Layer > Vector Mask (Reveal All / Hide All),
Filter > Blur > Box Blur / Surface Blur, Filter > Other > Custom, Edit >
Define Brush Preset and View > Screen Mode. Every one reuses existing
machinery: Vector Mask drives the existing `layers.addMaskFromSelection`
op, Box/Surface Blur reuse the pure `boxBlurRGB` retouch helper (and the
live-filter dialog), Custom reuses the `convolve` kernel helper, Define
Brush Preset clicks the existing brush-preset save button and Screen Mode
clicks the existing maximize toggle.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EXTRA = (ROOT / "static/js/editor/wire-extra-menus.js").read_text()
VIEW = (ROOT / "static/js/editor/wire-view-menu.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()
EFFECTS = (ROOT / "static/js/editor/filters/effects.js").read_text()
RETOUCH = (ROOT / "static/js/editor/retouch.js").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_view_screen_mode_menu_item_present():
    assert 'data-view-action="screen-mode"' in TOPBAR


def test_layer_vector_mask_menu_items_present():
    assert 'data-layer-action="vector-mask-reveal"' in TOPBAR
    assert 'data-layer-action="vector-mask-hide"' in TOPBAR


def test_filter_blur_and_custom_menu_items_present():
    assert 'data-filter-action="blur-box"' in TOPBAR
    assert 'data-filter-action="blur-surface"' in TOPBAR
    assert 'data-filter-action="other-custom"' in TOPBAR


def test_edit_define_brush_menu_item_present():
    assert 'data-edit-action="define-brush"' in TOPBAR


def test_view_screen_mode_wire_branch_present():
    assert "action === 'screen-mode'" in VIEW
    assert "document.getElementById('ge-maximize-toggle')?.click()" in VIEW


def test_layer_vector_mask_wire_branches_present():
    assert "action === 'vector-mask-reveal'" in EXTRA
    assert "action === 'vector-mask-hide'" in EXTRA
    assert "action === 'vector-mask-reveal') window.geEditorControl?.invoke('layers.addMaskFromSelection', {})" in EXTRA
    assert "action === 'vector-mask-hide') window.geEditorControl?.invoke('layers.addMaskFromSelection', { invert: true })" in EXTRA


def test_edit_define_brush_wire_branch_present():
    assert "action === 'define-brush'" in EXTRA
    assert "action === 'define-brush') click('ge-brush-preset-save');" in EXTRA


def test_filter_wire_branches_present():
    assert "action === 'blur-box'" in WIRE
    assert "action === 'blur-surface'" in WIRE
    assert "action === 'other-custom'" in WIRE
    assert "else if (action === 'blur-box') applyBoxBlur?.();" in WIRE
    assert "else if (action === 'blur-surface') applySurfaceBlur?.();" in WIRE
    assert "else if (action === 'other-custom') applyCustom?.();" in WIRE


def test_filter_apply_deps_are_threaded():
    assert "applyOffset, applyBoxBlur, applySurfaceBlur, applyCustom," in WIRE
    assert "applyBoxBlur: () => _applyBoxBlur()," in EDITOR
    assert "applySurfaceBlur: () => _applySurfaceBlur()," in EDITOR
    assert "applyCustom: () => _applyCustom()," in EDITOR


def test_box_and_surface_blur_ops_reuse_live_filter_and_box_helper():
    assert "function _boxBlur(snap, v, dst)" in EDITOR
    assert "function _surfaceBlur(snap, v, dst)" in EDITOR
    assert "function _applyBoxBlur()" in EDITOR
    assert "function _applySurfaceBlur()" in EDITOR
    assert "_boxBlurRGB(src.data, w, h, radius)" in EDITOR
    assert "boxBlurRGB as _boxBlurRGB" in EDITOR
    assert "export function boxBlurRGB(src, width, height, radius)" in RETOUCH


def test_custom_filter_reuses_convolve_kernel():
    assert "function _applyCustom()" in EDITOR
    assert "customKernel as _customKernel" in EDITOR
    assert "export function customKernel(snap, v, dst)" in EFFECTS
    assert "convolve(readSource(snap), k, divisor, bias, 1)" in EFFECTS


def test_blur_and_custom_filter_aliases_registered():
    assert "'box-blur': 'blur-box'" in EDITOR
    assert "'surface-blur': 'blur-surface'" in EDITOR
    assert "custom: 'other-custom'" in EDITOR


def test_vector_mask_reuses_existing_mask_op():
    assert "'layers.addMaskFromSelection': {" in EDITOR


def test_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)


# ── Sixth batch: Filter > Other > Maximum / Minimum, Filter > Noise >
# Median, Filter > Distort > Polar Coordinates and Filter > Render >
# Difference Clouds. Every one reuses the live-filter slider dialog plus
# a small deterministic, side-effect-free renderer in filters/effects.js.

def test_morphology_median_polar_and_difference_clouds_menu_items_present():
    assert 'data-filter-action="other-maximum"' in TOPBAR
    assert 'data-filter-action="other-minimum"' in TOPBAR
    assert 'data-filter-action="noise-median"' in TOPBAR
    assert 'data-filter-action="distort-polar"' in TOPBAR
    assert 'data-filter-action="render-difference-clouds"' in TOPBAR


def test_new_filter_wire_branches_present():
    assert "else if (action === 'other-maximum') applyMaximum?.();" in WIRE
    assert "else if (action === 'other-minimum') applyMinimum?.();" in WIRE
    assert "else if (action === 'noise-median') applyMedian?.();" in WIRE
    assert "else if (action === 'distort-polar') applyPolarCoordinates?.();" in WIRE
    assert "else if (action === 'render-difference-clouds') applyDifferenceClouds?.();" in WIRE


def test_new_filter_apply_deps_are_threaded():
    assert "applyMaximum, applyMinimum, applyMedian, applyPolarCoordinates, applyDifferenceClouds," in WIRE
    for wrapper in (
        "applyMaximum: () => _applyMaximum(),",
        "applyMinimum: () => _applyMinimum(),",
        "applyMedian: () => _applyMedian(),",
        "applyPolarCoordinates: () => _applyPolarCoordinates(),",
        "applyDifferenceClouds: () => _applyDifferenceClouds(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_gallery_defines_new_apply_wrappers():
    for name in ("Maximum", "Minimum", "Median", "PolarCoordinates", "DifferenceClouds"):
        assert f"function _apply{name}(" in EDITOR, name


def test_effects_module_exports_new_renderers():
    for name in ("maximum", "minimum", "median", "polarCoordinates", "differenceClouds"):
        assert f"export function {name}(" in EFFECTS, name


def test_gallery_imports_new_renderers():
    for alias in ("_maximum", "_minimum", "_median", "_polarCoordinates", "_differenceClouds"):
        assert alias in EDITOR, alias


def test_new_filter_aliases_registered():
    assert "maximum: 'other-maximum'" in EDITOR
    assert "minimum: 'other-minimum'" in EDITOR
    assert "median: 'noise-median'" in EDITOR
    assert "'polar-coordinates': 'distort-polar'" in EDITOR
    assert "'difference-clouds': 'render-difference-clouds'" in EDITOR


def test_new_renderers_are_deterministic_and_self_contained():
    assert "function morphology(snap, radius, mode)" in EFFECTS
    assert "const out = pass(pass(src, true), false);" in EFFECTS
    assert "function median(snap, v, dst)" in EFFECTS
    assert "function polarCoordinates(snap, v, dst)" in EFFECTS
    assert "function differenceClouds(snap, v, dst)" in EFFECTS
    assert "valueNoise(w, h, scale, seed)" in EFFECTS
    assert "function clampIndex(n, max)" in EFFECTS


def test_new_filters_reuse_live_filter_dialog():
    assert "_applyLiveBlur({" in EDITOR
    assert "renderer: _maximum," in EDITOR
    assert "renderer: _minimum," in EDITOR
    assert "renderer: _median," in EDITOR
    assert "renderer: _polarCoordinates," in EDITOR
    assert "renderer: _differenceClouds," in EDITOR
