"""Marker tests for the eighth and ninth batches of Photoshop-style editor
commands.

Eighth batch: Filter > Stylize > Diffuse / Wind, Filter > Distort > Ocean
Ripple / Glass and Filter > Pixelate > Mosaic Tiles. Ninth batch: Filter >
Stylize > Trace Contour, the new Sketch flyout (Halftone Pattern /
Reticulation / Note Paper) and Filter > Distort > Shear.

The original eighth-batch notes follow. These assert the wiring for Filter >
Stylize > Diffuse / Wind, Filter > Distort > Ocean Ripple / Glass and Filter >
Pixelate > Mosaic Tiles.
Every one reuses existing machinery: each menu item drives the existing
live-filter slider dialog (`_applyLiveBlur`) plus a small, deterministic,
side-effect-free renderer in `filters/effects.js` (so preview and commit
agree). Diffuse and Glass reuse the module LCG / value-noise grid already
used by Add Noise / Clouds / Fibers; no new colours.
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
    assert 'data-filter-action="stylize-diffuse"' in TOPBAR
    assert 'data-filter-action="distort-ocean-ripple"' in TOPBAR
    assert 'data-filter-action="distort-glass"' in TOPBAR
    assert 'data-filter-action="pixelate-mosaic-tiles"' in TOPBAR
    assert 'data-filter-action="stylize-wind"' in TOPBAR


def test_new_menu_items_in_correct_submenu_groups():
    stylize = TOPBAR.split('data-submenu-panel="filter-stylize"')[1].split("</div>")[0]
    assert 'data-filter-action="stylize-diffuse"' in stylize
    assert 'data-filter-action="stylize-wind"' in stylize
    distort = TOPBAR.split('data-submenu-panel="filter-distort"')[1].split("</div>")[0]
    assert 'data-filter-action="distort-ocean-ripple"' in distort
    assert 'data-filter-action="distort-glass"' in distort
    pixelate = TOPBAR.split('data-submenu-panel="filter-pixelate"')[1].split("</div>")[0]
    assert 'data-filter-action="pixelate-mosaic-tiles"' in pixelate


def test_new_filter_wire_branches_present():
    assert "else if (action === 'stylize-diffuse') applyDiffuse?.();" in WIRE
    assert "else if (action === 'distort-ocean-ripple') applyOceanRipple?.();" in WIRE
    assert "else if (action === 'distort-glass') applyGlass?.();" in WIRE
    assert "else if (action === 'pixelate-mosaic-tiles') applyMosaicTiles?.();" in WIRE
    assert "else if (action === 'stylize-wind') applyWind?.();" in WIRE


def test_new_filter_apply_deps_are_threaded():
    assert "applyDiffuse, applyOceanRipple, applyGlass, applyMosaicTiles, applyWind," in WIRE
    for wrapper in (
        "applyDiffuse: () => _applyDiffuse(),",
        "applyOceanRipple: () => _applyOceanRipple(),",
        "applyGlass: () => _applyGlass(),",
        "applyMosaicTiles: () => _applyMosaicTiles(),",
        "applyWind: () => _applyWind(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_gallery_defines_new_apply_wrappers():
    for name in ("Diffuse", "OceanRipple", "Glass", "MosaicTiles", "Wind"):
        assert f"function _apply{name}(" in EDITOR, name


def test_effects_module_exports_new_renderers():
    for name in ("diffuse", "oceanRipple", "glass", "mosaicTiles", "wind"):
        assert f"export function {name}(" in EFFECTS, name


def test_gallery_imports_new_renderers():
    for alias in ("_diffuse", "_oceanRipple", "_glass", "_mosaicTiles", "_wind"):
        assert alias in EDITOR, alias


def test_new_filter_aliases_registered():
    assert "diffuse: 'stylize-diffuse'" in EDITOR
    assert "'ocean-ripple': 'distort-ocean-ripple'" in EDITOR
    assert "ocean: 'distort-ocean-ripple'" in EDITOR
    assert "glass: 'distort-glass'" in EDITOR
    assert "'mosaic-tiles': 'pixelate-mosaic-tiles'" in EDITOR
    assert "wind: 'stylize-wind'" in EDITOR


def test_new_renderers_reuse_live_filter_dialog():
    assert "renderer: _diffuse," in EDITOR
    assert "renderer: _oceanRipple," in EDITOR
    assert "renderer: _glass," in EDITOR
    assert "renderer: _mosaicTiles," in EDITOR
    assert "renderer: _wind," in EDITOR


def test_diffuse_is_seeded_deterministic_displacement():
    assert "function diffuse(snap, v, dst)" in EFFECTS
    assert "s = (s * 1664525 + 1013904223) & 0xffffffff;" in EFFECTS


def test_ocean_ripple_is_two_overlapping_sine_waves():
    assert "function oceanRipple(snap, v, dst)" in EFFECTS
    assert "const waveA = Math.sin((2 * Math.PI * (x + y)) / size);" in EFFECTS
    assert "const waveB = Math.cos((2 * Math.PI * (x - y)) / (size * 1.5));" in EFFECTS


def test_glass_reuses_value_noise_grid():
    assert "function glass(snap, v, dst)" in EFFECTS
    assert "valueNoise(w, h, tile, seed)" in EFFECTS
    assert "valueNoise(w, h, tile, seed + 41)" in EFFECTS


def test_mosaic_tiles_adds_grout_lines():
    assert "function mosaicTiles(snap, v, dst)" in EFFECTS
    assert "const grout = Math.max(0, Math.min(10, Math.round(Number(v.grout ?? 1))));" in EFFECTS
    assert "const edge = (x - bx) < grout || (y - by) < grout;" in EFFECTS


def test_wind_is_directional_streak():
    assert "function wind(snap, v, dst)" in EFFECTS
    assert "const dx = Math.cos(angle);" in EFFECTS
    assert "const wgt = (strength - k) / strength;" in EFFECTS


def test_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)


# ─────────────────────────────────────────────────────────────────────────────
# Ninth batch: Trace Contour (Stylize) and the new Sketch flyout (Halftone
# Pattern / Reticulation / Note Paper) plus Shear (Distort). Like the earlier
# batches each item drives the shared live-filter slider dialog (`_applyLiveBlur`)
# and a pure, side-effect-free renderer in `filters/effects.js`, so preview and
# commit agree.
#
# Preferred-item outcome:
#   * Stylize > Wind          — already present from the in-flight batch; kept.
#   * Stylize > Trace Contour — implemented below.
#   * Sketch  > Halftone Pattern — implemented below.
#   * Adjustments > Match Color  — SKIPPED: the engine has no `match-color`
#     adjustment type (adjustment-layer TYPES has 18) and matching needs a
#     reference-layer picker the slider dialog cannot provide → substituted with
#     Sketch > Reticulation.
#   * Image Rotation > Arbitrary — SKIPPED: there is no themed arbitrary-angle
#     prompt helper (only a size-specific promptCanvasSize and the raw
#     window.prompt in canvas-context-menu.js) → substituted with Distort > Shear.
# ─────────────────────────────────────────────────────────────────────────────


def test_more6_menu_items_present():
    assert 'data-filter-action="stylize-trace-contour"' in TOPBAR
    assert 'data-filter-action="sketch-halftone-pattern"' in TOPBAR
    assert 'data-filter-action="sketch-reticulation"' in TOPBAR
    assert 'data-filter-action="sketch-note-paper"' in TOPBAR
    assert 'data-filter-action="distort-shear"' in TOPBAR


def test_more6_menu_items_in_correct_submenu_groups():
    assert 'data-submenu="filter-sketch"' in TOPBAR
    stylize = TOPBAR.split('data-submenu-panel="filter-stylize"')[1].split("</div>")[0]
    assert 'data-filter-action="stylize-trace-contour"' in stylize
    sketch = TOPBAR.split('data-submenu-panel="filter-sketch"')[1].split("</div>")[0]
    assert 'data-filter-action="sketch-halftone-pattern"' in sketch
    assert 'data-filter-action="sketch-reticulation"' in sketch
    assert 'data-filter-action="sketch-note-paper"' in sketch
    distort = TOPBAR.split('data-submenu-panel="filter-distort"')[1].split("</div>")[0]
    assert 'data-filter-action="distort-shear"' in distort


def test_more6_filter_wire_branches_present():
    assert "else if (action === 'stylize-trace-contour') applyTraceContour?.();" in WIRE
    assert "else if (action === 'sketch-halftone-pattern') applyHalftonePattern?.();" in WIRE
    assert "else if (action === 'sketch-reticulation') applyReticulation?.();" in WIRE
    assert "else if (action === 'distort-shear') applyShear?.();" in WIRE
    assert "else if (action === 'sketch-note-paper') applyNotePaper?.();" in WIRE


def test_more6_filter_apply_deps_are_threaded():
    assert "applyTraceContour, applyHalftonePattern, applyReticulation, applyShear, applyNotePaper," in WIRE
    for wrapper in (
        "applyTraceContour: () => _applyTraceContour(),",
        "applyHalftonePattern: () => _applyHalftonePattern(),",
        "applyReticulation: () => _applyReticulation(),",
        "applyShear: () => _applyShear(),",
        "applyNotePaper: () => _applyNotePaper(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_more6_gallery_defines_apply_wrappers():
    for name in ("TraceContour", "HalftonePattern", "Reticulation", "Shear", "NotePaper"):
        assert f"function _apply{name}(" in EDITOR, name


def test_more6_effects_module_exports_renderers():
    for name in ("traceContour", "halftonePattern", "reticulation", "shear", "notePaper"):
        assert f"export function {name}(" in EFFECTS, name


def test_more6_gallery_imports_renderers():
    for alias in ("_traceContour", "_halftonePattern", "_reticulation", "_shear", "_notePaper"):
        assert alias in EDITOR, alias


def test_more6_filter_aliases_registered():
    assert "'trace-contour': 'stylize-trace-contour'" in EDITOR
    assert "'halftone-pattern': 'sketch-halftone-pattern'" in EDITOR
    assert "halftone: 'sketch-halftone-pattern'" in EDITOR
    assert "reticulation: 'sketch-reticulation'" in EDITOR
    assert "shear: 'distort-shear'" in EDITOR
    assert "'note-paper': 'sketch-note-paper'" in EDITOR
    assert "notepaper: 'sketch-note-paper'" in EDITOR


def test_more6_renderers_reuse_live_filter_dialog():
    for renderer in ("_traceContour", "_halftonePattern", "_reticulation", "_shear", "_notePaper"):
        assert f"renderer: {renderer}," in EDITOR, renderer


def test_more6_trace_contour_is_luminance_threshold():
    assert "function traceContour(snap, v, dst)" in EFFECTS
    assert "const level = Math.max(0, Math.min(255, Math.round(Number(v.level ?? 128))));" in EFFECTS
    assert "const luma = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];" in EFFECTS


def test_more6_halftone_pattern_is_area_proportional_dot():
    assert "function halftonePattern(snap, v, dst)" in EFFECTS
    assert "const radius = (size / 2) * Math.sqrt(dark);" in EFFECTS


def test_more6_reticulation_is_seeded_value_noise():
    assert "function reticulation(snap, v, dst)" in EFFECTS
    assert "const grain = valueNoise(w, h, 3, seed);" in EFFECTS
    assert "const q = Math.round(clamp255(gray + n) / 32) * 32;" in EFFECTS


def test_more6_shear_is_s_curve_row_shift():
    assert "function shear(snap, v, dst)" in EFFECTS
    assert "const shift = Math.round((amount * Math.sin(2 * Math.PI * (t - 0.5))) / 2);" in EFFECTS


def test_more6_note_paper_is_embossed_grain():
    assert "function notePaper(snap, v, dst)" in EFFECTS
    assert "const grain = valueNoise(w, h, 4, seed + 7);" in EFFECTS
    assert "const slope = (0.299 * p[iDn] + 0.587 * p[iDn + 1] + 0.114 * p[iDn + 2])" in EFFECTS


def test_more6_skipped_preferred_items_are_not_half_wired():
    # Match Color was skipped rather than half-wired.
    assert 'data-image-action="adj-match-color"' not in TOPBAR
    assert "adjustments.matchColor" not in WIRE
    # Arbitrary rotation used to be skipped; it is now fully implemented
    # (Image > Image Rotation > Arbitrary), so it must be wired end to end.
    assert 'data-image-action="rotate-arbitrary"' in TOPBAR
    assert "rotate-arbitrary" in WIRE


def test_more6_preferred_wind_is_present():
    assert 'data-filter-action="stylize-wind"' in TOPBAR
    assert "else if (action === 'stylize-wind') applyWind?.();" in WIRE
    assert "export function wind(snap, v, dst)" in EFFECTS
