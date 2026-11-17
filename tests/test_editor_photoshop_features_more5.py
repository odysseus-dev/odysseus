"""Marker tests for the seventh batch of Photoshop-style editor commands.

These assert the wiring for Filter > Other > Custom,
Image > Adjustments > Replace Color, Filter > Render > Lens Flare / Fibers,
Filter > Stylize > Oil Paint and Filter > Distort > Lens Correction. Every one
reuses existing machinery: Custom and the Image action and the filter actions
drive the existing live-filter slider dialog (`_applyLiveBlur`) plus a small,
deterministic, side-effect-free renderer in `filters/effects.js` (so preview
and commit agree). No new colours, no randomness beyond the seeded value-noise
grid already used by Clouds / Difference Clouds / Fibers.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()
EFFECTS = (ROOT / "static/js/editor/filters/effects.js").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_new_menu_items_present():
    assert 'data-image-action="replace-color"' in TOPBAR
    assert 'data-filter-action="render-lens-flare"' in TOPBAR
    assert 'data-filter-action="render-fibers"' in TOPBAR
    assert 'data-filter-action="stylize-oil-paint"' in TOPBAR
    assert 'data-filter-action="distort-lens-correction"' in TOPBAR


def test_other_custom_chain_present():
    assert 'data-filter-action="other-custom"' in TOPBAR
    assert "else if (action === 'other-custom') applyCustom?.();" in WIRE
    assert "applyCustom: () => _applyCustom()," in EDITOR
    assert "function _applyCustom(" in EDITOR
    assert "export function customKernel(" in EFFECTS
    assert "customKernel as _customKernel," in EDITOR
    assert "custom: 'other-custom'" in EDITOR


def test_image_replace_color_wire_branch_present():
    assert "else if (action === 'replace-color') applyReplaceColor?.();" in WIRE


def test_new_filter_wire_branches_present():
    assert "else if (action === 'render-lens-flare') applyLensFlare?.();" in WIRE
    assert "else if (action === 'render-fibers') applyFibers?.();" in WIRE
    assert "else if (action === 'stylize-oil-paint') applyOilPaint?.();" in WIRE
    assert "else if (action === 'distort-lens-correction') applyLensCorrection?.();" in WIRE


def test_new_filter_apply_deps_are_threaded():
    assert "applyLensFlare, applyFibers, applyOilPaint, applyLensCorrection, applyReplaceColor," in WIRE
    for wrapper in (
        "applyLensFlare: () => _applyLensFlare(),",
        "applyFibers: () => _applyFibers(),",
        "applyOilPaint: () => _applyOilPaint(),",
        "applyLensCorrection: () => _applyLensCorrection(),",
        "applyReplaceColor: () => _applyReplaceColor(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_gallery_defines_new_apply_wrappers():
    for name in ("LensFlare", "Fibers", "OilPaint", "LensCorrection", "ReplaceColor"):
        assert f"function _apply{name}(" in EDITOR, name


def test_effects_module_exports_new_renderers():
    for name in ("replaceColor", "lensFlare", "fibers", "oilPaint", "lensCorrection"):
        assert f"export function {name}(" in EFFECTS, name


def test_gallery_imports_new_renderers():
    for alias in ("_replaceColor", "_lensFlare", "_fibers", "_oilPaint", "_lensCorrection"):
        assert alias in EDITOR, alias


def test_new_filter_aliases_registered():
    assert "'lens-flare': 'render-lens-flare'" in EDITOR
    assert "flare: 'render-lens-flare'" in EDITOR
    assert "fibers: 'render-fibers'" in EDITOR
    assert "'oil-paint': 'stylize-oil-paint'" in EDITOR
    assert "'lens-correction': 'distort-lens-correction'" in EDITOR


def test_new_renderers_reuse_live_filter_dialog():
    assert "renderer: _lensFlare," in EDITOR
    assert "renderer: _fibers," in EDITOR
    assert "renderer: _oilPaint," in EDITOR
    assert "renderer: _lensCorrection," in EDITOR
    assert "renderer: _replaceColor," in EDITOR


def test_replace_color_is_deterministic_per_pixel_math():
    assert "function hexToRgb(hex)" in EFFECTS
    assert "Math.sqrt(dr * dr + dg * dg + db * db)" in EFFECTS
    assert "function replaceColor(snap, v, dst)" in EFFECTS


def test_fibers_reuses_anisotropic_value_noise_grid():
    assert "function valueNoise(w, h, cell, seed, cellY = cell)" in EFFECTS
    assert "valueNoise(w, h, variance, seed, variance * 12)" in EFFECTS


def test_oil_paint_and_lens_correction_are_self_contained():
    assert "function oilPaint(snap, v, dst)" in EFFECTS
    assert "function lensCorrection(snap, v, dst)" in EFFECTS
    assert "function lensFlare(snap, v, dst)" in EFFECTS
    assert "const glows = [" in EFFECTS
    assert "function clampIndex(n, max)" in EFFECTS


def test_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
