"""Marker tests for the tenth batch of Photoshop-style editor commands.

Implemented items (Filter > Sketch family, the genuinely-missing deterministic
picks):

  * Filter > Sketch > Charcoal…          (``sketch-charcoal``)
  * Filter > Sketch > Chalk & Charcoal…  (``sketch-chalk-charcoal``)
  * Filter > Sketch > Graphic Pen…       (``sketch-graphic-pen``)
  * Filter > Sketch > Photocopy…         (``sketch-photocopy``)
  * Filter > Sketch > Water Paper…       (``sketch-water-paper``)

Every one reuses the existing machinery: a compact menu item drives the shared
live-filter slider dialog (``_applyLiveBlur``) plus a small, pure,
deterministic, side-effect-free grayscale renderer in ``filters/effects.js`` (so
preview and commit agree). Charcoal, Chalk & Charcoal and Water Paper reuse the
module ``valueNoise`` grid for a stable seeded look; no new colours were
introduced. Each filter is aliased by both its hyphenated and non-hyphenated
name in the ``_FILTER_ALIAS`` map.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()
EFFECTS = (ROOT / "static/js/editor/filters/effects.js").read_text()
SW = (ROOT / "static/sw.js").read_text()

IDS = (
    "sketch-charcoal",
    "sketch-chalk-charcoal",
    "sketch-graphic-pen",
    "sketch-photocopy",
    "sketch-water-paper",
)


def test_more7_menu_items_present():
    for fid in IDS:
        assert f'data-filter-action="{fid}"' in TOPBAR, fid


def test_more7_menu_items_are_unique():
    for fid in IDS:
        assert TOPBAR.count(f'data-filter-action="{fid}"') == 1, fid


def test_more7_items_in_correct_submenu_group():
    sketch = TOPBAR.split('data-submenu-panel="filter-sketch"')[1].split("</div>")[0]
    for fid in IDS:
        assert f'data-filter-action="{fid}"' in sketch, fid
    # the pre-existing Sketch items must still be there beside them
    assert 'data-filter-action="sketch-halftone-pattern"' in sketch
    assert 'data-filter-action="sketch-reticulation"' in sketch
    assert 'data-filter-action="sketch-note-paper"' in sketch


def test_more7_filter_wire_branches_present():
    assert "else if (action === 'sketch-charcoal') applyCharcoal?.();" in WIRE
    assert "else if (action === 'sketch-chalk-charcoal') applyChalkCharcoal?.();" in WIRE
    assert "else if (action === 'sketch-graphic-pen') applyGraphicPen?.();" in WIRE
    assert "else if (action === 'sketch-photocopy') applyPhotocopy?.();" in WIRE
    assert "else if (action === 'sketch-water-paper') applyWaterPaper?.();" in WIRE


def test_more7_filter_apply_deps_are_threaded():
    assert "applyCharcoal, applyChalkCharcoal, applyGraphicPen, applyPhotocopy, applyWaterPaper," in WIRE
    for wrapper in (
        "applyCharcoal: () => _applyCharcoal(),",
        "applyChalkCharcoal: () => _applyChalkCharcoal(),",
        "applyGraphicPen: () => _applyGraphicPen(),",
        "applyPhotocopy: () => _applyPhotocopy(),",
        "applyWaterPaper: () => _applyWaterPaper(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_more7_gallery_defines_new_apply_wrappers():
    for name in ("Charcoal", "ChalkCharcoal", "GraphicPen", "Photocopy", "WaterPaper"):
        assert f"function _apply{name}(" in EDITOR, name


def test_more7_effects_module_exports_new_renderers():
    for name in ("charcoal", "chalkCharcoal", "graphicPen", "photocopy", "waterPaper"):
        assert f"export function {name}(snap, v, dst)" in EFFECTS, name


def test_more7_gallery_imports_new_renderers():
    for alias in ("_charcoal", "_chalkCharcoal", "_graphicPen", "_photocopy", "_waterPaper"):
        assert alias in EDITOR, alias


def test_more7_filter_aliases_registered():
    # hyphenated and non-hyphenated forms for the multi-word names
    assert "charcoal: 'sketch-charcoal'" in EDITOR
    assert "'chalk-charcoal': 'sketch-chalk-charcoal'" in EDITOR
    assert "chalkcharcoal: 'sketch-chalk-charcoal'" in EDITOR
    assert "'graphic-pen': 'sketch-graphic-pen'" in EDITOR
    assert "graphicpen: 'sketch-graphic-pen'" in EDITOR
    assert "photocopy: 'sketch-photocopy'" in EDITOR
    assert "'water-paper': 'sketch-water-paper'" in EDITOR
    assert "waterpaper: 'sketch-water-paper'" in EDITOR


def test_more7_renderers_reuse_live_filter_dialog():
    for renderer in ("_charcoal", "_chalkCharcoal", "_graphicPen", "_photocopy", "_waterPaper"):
        assert f"renderer: {renderer}," in EDITOR, renderer


def test_more7_charcoal_is_inverse_luminance_with_seeded_grain():
    assert "const ink = Math.pow(1 - luma, 1 + detail * 0.4);" in EFFECTS
    assert "const grain = valueNoise(w, h, 3, seed);" in EFFECTS


def test_more7_chalk_charcoal_mixes_light_and_dark_areas():
    assert "const dark = Math.pow(1 - luma, 1 + charcoalArea * 0.15);" in EFFECTS
    assert "const light = Math.pow(luma, 1 + chalkArea * 0.15);" in EFFECTS


def test_more7_graphic_pen_is_directional_hatching():
    assert "const slot = ((proj % length) + length) % length;" in EFFECTS
    assert "const inked = dark > (1 - balance) && slot < width;" in EFFECTS


def test_more7_photocopy_is_high_contrast_threshold():
    assert "const cutoff = 255 * (1 - darkness / 32);" in EFFECTS
    assert "const tone = luma - edge * (detail / 12) < cutoff ? 12 : 243;" in EFFECTS


def test_more7_water_paper_reuses_value_noise_grid():
    assert "const nx = valueNoise(w, h, cell, seed);" in EFFECTS
    assert "const sy = clampIndex(y + Math.round((ny[px] - 0.5) * fiber), h - 1);" in EFFECTS


def test_more7_renderers_are_grayscale_and_keep_alpha():
    # each renderer writes the same value to r/g/b and copies the source alpha
    for name in ("charcoal", "chalkCharcoal", "graphicPen", "photocopy", "waterPaper"):
        block = EFFECTS.split(f"export function {name}(snap, v, dst)")[1].split("\nexport function ")[0]
        green_ok = "out[i + 1] = tone" in block or "out[px * 4 + 1] = tone" in block
        alpha_ok = "out[i + 3] = p[i + 3];" in block or "out[px * 4 + 3] = p[px * 4 + 3];" in block
        assert green_ok, name
        assert alpha_ok, name


def test_more7_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
