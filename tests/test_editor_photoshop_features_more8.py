"""Marker tests for the eleventh batch of Photoshop-style editor commands.

Implemented items (the genuinely-missing deterministic picks):

  * Filter > Sharpen > Sharpen More     (``sharpen-more``)
  * Filter > Artistic > Poster Edges    (``artistic-poster-edges``)
  * Filter > Artistic > Cutout          (``artistic-cutout``)
  * Filter > Brush Strokes > Ink Outlines (``brush-ink-outlines``)
  * Filter > Texture > Stained Glass    (``texture-stained-glass``)

Every one reuses the existing machinery: a compact menu item drives the shared
live-filter slider dialog (``_applyLiveBlur``) plus a small, pure,
deterministic, side-effect-free renderer in ``filters/effects.js`` (so preview
and commit agree). Sharpen More reuses the module ``convolve`` helper and
Stained Glass seeds its Voronoi grid from the same LCG already used by
value-noise; no new colours were introduced. Each filter is aliased by both its
hyphenated and non-hyphenated name in the ``_FILTER_ALIAS`` map where it has a
multi-word name.
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
    "sharpen-more",
    "artistic-poster-edges",
    "artistic-cutout",
    "brush-ink-outlines",
    "texture-stained-glass",
)

GROUPS = {
    "sharpen-more": "filter-sharpen",
    "artistic-poster-edges": "filter-artistic",
    "artistic-cutout": "filter-artistic",
    "brush-ink-outlines": "filter-brush",
    "texture-stained-glass": "filter-texture",
}


def _panel(name):
    return TOPBAR.split(f'data-submenu-panel="{name}"')[1].split("</div>")[0]


def test_more8_menu_items_present():
    for fid in IDS:
        assert f'data-filter-action="{fid}"' in TOPBAR, fid


def test_more8_menu_items_are_unique():
    for fid in IDS:
        assert TOPBAR.count(f'data-filter-action="{fid}"') == 1, fid


def test_more8_new_submenu_parents_present():
    assert 'data-submenu="filter-artistic"' in TOPBAR
    assert 'data-submenu="filter-brush"' in TOPBAR
    assert 'data-submenu="filter-texture"' in TOPBAR


def test_more8_items_in_correct_submenu_group():
    for fid, group in GROUPS.items():
        assert f'data-filter-action="{fid}"' in _panel(group), (fid, group)


def test_more8_sharpen_more_sits_beside_existing_sharpen_items():
    sharpen = _panel("filter-sharpen")
    assert 'data-filter-action="sharpen"' in sharpen
    assert 'data-filter-action="sharpen-edges"' in sharpen
    assert 'data-filter-action="sharpen-more"' in sharpen


def test_more8_filter_wire_branches_present():
    assert "else if (action === 'sharpen-more') applySharpenMore?.();" in WIRE
    assert "else if (action === 'artistic-poster-edges') applyPosterEdges?.();" in WIRE
    assert "else if (action === 'artistic-cutout') applyCutout?.();" in WIRE
    assert "else if (action === 'brush-ink-outlines') applyInkOutlines?.();" in WIRE
    assert "else if (action === 'texture-stained-glass') applyStainedGlass?.();" in WIRE


def test_more8_filter_apply_deps_are_threaded():
    assert "applySharpenMore, applyPosterEdges, applyCutout, applyInkOutlines, applyStainedGlass," in WIRE
    for wrapper in (
        "applySharpenMore: () => _applySharpenMore(),",
        "applyPosterEdges: () => _applyPosterEdges(),",
        "applyCutout: () => _applyCutout(),",
        "applyInkOutlines: () => _applyInkOutlines(),",
        "applyStainedGlass: () => _applyStainedGlass(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_more8_gallery_defines_new_apply_wrappers():
    for name in ("SharpenMore", "PosterEdges", "Cutout", "InkOutlines", "StainedGlass"):
        assert f"function _apply{name}(" in EDITOR, name


def test_more8_effects_module_exports_new_renderers():
    for name in ("sharpenMore", "posterEdges", "cutout", "inkOutlines", "stainedGlass"):
        assert f"export function {name}(snap, v, dst)" in EFFECTS, name


def test_more8_gallery_imports_new_renderers():
    for alias in ("_sharpenMore", "_posterEdges", "_cutout", "_inkOutlines", "_stainedGlass"):
        assert alias in EDITOR, alias


def test_more8_filter_aliases_registered():
    assert "'sharpen-more': 'sharpen-more'" in EDITOR
    assert "'poster-edges': 'artistic-poster-edges'" in EDITOR
    assert "posteredges: 'artistic-poster-edges'" in EDITOR
    assert "cutout: 'artistic-cutout'" in EDITOR
    assert "'ink-outlines': 'brush-ink-outlines'" in EDITOR
    assert "inkoutlines: 'brush-ink-outlines'" in EDITOR
    assert "'stained-glass': 'texture-stained-glass'" in EDITOR
    assert "stainedglass: 'texture-stained-glass'" in EDITOR


def test_more8_renderers_reuse_live_filter_dialog():
    for renderer in ("_sharpenMore", "_posterEdges", "_cutout", "_inkOutlines", "_stainedGlass"):
        assert f"renderer: {renderer}," in EDITOR, renderer


def test_more8_sharpen_more_is_stronger_fixed_kernel():
    assert "[0, -2, 0, -2, 9, -2, 0, -2, 0]" in EFFECTS


def test_more8_poster_edges_is_posterised_with_edge_ink():
    assert "const ink = Math.hypot(gx, gy) * (edge / 2);" in EFFECTS
    assert "out[i + c] = clamp255(Math.round(p[i + c] / step) * step - ink);" in EFFECTS


def test_more8_cutout_is_per_channel_posterisation():
    assert "p[i] = Math.round(Math.round(p[i] / step) * step);" in EFFECTS
    assert "p[i + 1] = Math.round(Math.round(p[i + 1] / step) * step);" in EFFECTS
    assert "p[i + 2] = Math.round(Math.round(p[i + 2] / step) * step);" in EFFECTS


def test_more8_ink_outlines_is_laplacian_darkening():
    assert "const lap = 4 * luma(x, y) - luma(Math.max(0, x - 1), y) - luma(Math.min(w - 1, x + 1), y)" in EFFECTS
    assert "const ink = clamp255(lap) * (dark / 25);" in EFFECTS


def test_more8_stained_glass_is_seeded_voronoi_with_borders():
    assert "const rand = () => { s = (s * 1664525 + 1013904223) & 0xffffffff; return (s >>> 8) / 0xffffff; };" in EFFECTS
    assert "isBorder = (x > 0 && owner[px - 1] !== o) || (x < w - 1 && owner[px + 1] !== o)" in EFFECTS


def test_more8_renderers_preserve_alpha():
    for name in ("posterEdges", "inkOutlines", "stainedGlass"):
        block = EFFECTS.split(f"export function {name}(snap, v, dst)")[1].split("\nexport function ")[0]
        assert "p[i + 3]" in block, name
    # Cutout mutates the source buffer in place, touching only r/g/b.
    cutout_block = EFFECTS.split("export function cutout(snap, v, dst)")[1].split("\nexport function ")[0]
    assert "p[i + 3] =" not in cutout_block
    # Sharpen More delegates to the module `convolve` helper, which carries the
    # source alpha through unchanged.
    assert "out[di + 3] = src[di + 3];" in EFFECTS


def test_more8_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
