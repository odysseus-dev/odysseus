"""Marker tests for the twelfth batch of Photoshop-style editor commands.

Implemented items (the genuinely-missing deterministic picks):

  * Filter > Texture > Grain          (``texture-grain``)
  * Filter > Texture > Texturizer     (``texture-texturizer``)
  * Filter > Texture > Patchwork      (``texture-patchwork``)
  * Filter > Texture > Craquelure     (``texture-craquelure``)
  * Filter > Brush Strokes > Crosshatch (``brush-crosshatch``)

Every one reuses the existing machinery: a compact menu item inside the already
existing ``filter-texture`` / ``filter-brush`` flyout drives the shared
live-filter slider dialog (``_applyLiveBlur``) plus a small, pure, deterministic,
side-effect-free renderer in ``filters/effects.js`` (so preview and commit
agree). Grain seeds its LCG, Texturizer/Craquelure reuse the module
``valueNoise`` helper, and Patchwork averages tiles; no new colours were
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
    "texture-grain",
    "texture-texturizer",
    "texture-patchwork",
    "texture-craquelure",
    "brush-crosshatch",
)

GROUPS = {
    "texture-grain": "filter-texture",
    "texture-texturizer": "filter-texture",
    "texture-patchwork": "filter-texture",
    "texture-craquelure": "filter-texture",
    "brush-crosshatch": "filter-brush",
}


def _panel(name):
    return TOPBAR.split(f'data-submenu-panel="{name}"')[1].split("</div>")[0]


def test_more9_menu_items_present():
    for fid in IDS:
        assert f'data-filter-action="{fid}"' in TOPBAR, fid


def test_more9_menu_items_are_unique():
    for fid in IDS:
        assert TOPBAR.count(f'data-filter-action="{fid}"') == 1, fid


def test_more9_reuses_existing_submenu_parents():
    assert 'data-submenu="filter-texture"' in TOPBAR
    assert 'data-submenu="filter-brush"' in TOPBAR


def test_more9_items_in_correct_submenu_group():
    for fid, group in GROUPS.items():
        assert f'data-filter-action="{fid}"' in _panel(group), (fid, group)


def test_more9_texture_items_sit_beside_stained_glass():
    texture = _panel("filter-texture")
    assert 'data-filter-action="texture-stained-glass"' in texture
    for fid in ("texture-grain", "texture-texturizer", "texture-patchwork", "texture-craquelure"):
        assert f'data-filter-action="{fid}"' in texture, fid


def test_more9_crosshatch_sits_beside_ink_outlines():
    brush = _panel("filter-brush")
    assert 'data-filter-action="brush-ink-outlines"' in brush
    assert 'data-filter-action="brush-crosshatch"' in brush


def test_more9_filter_wire_branches_present():
    assert "else if (action === 'texture-grain') applyGrain?.();" in WIRE
    assert "else if (action === 'texture-texturizer') applyTexturizer?.();" in WIRE
    assert "else if (action === 'texture-patchwork') applyPatchwork?.();" in WIRE
    assert "else if (action === 'texture-craquelure') applyCraquelure?.();" in WIRE
    assert "else if (action === 'brush-crosshatch') applyCrosshatch?.();" in WIRE


def test_more9_filter_apply_deps_are_threaded():
    assert "applyGrain, applyTexturizer, applyPatchwork, applyCraquelure, applyCrosshatch," in WIRE
    for wrapper in (
        "applyGrain: () => _applyGrain(),",
        "applyTexturizer: () => _applyTexturizer(),",
        "applyPatchwork: () => _applyPatchwork(),",
        "applyCraquelure: () => _applyCraquelure(),",
        "applyCrosshatch: () => _applyCrosshatch(),",
    ):
        assert wrapper in EDITOR, wrapper


def test_more9_gallery_defines_new_apply_wrappers():
    for name in ("Grain", "Texturizer", "Patchwork", "Craquelure", "Crosshatch"):
        assert f"function _apply{name}(" in EDITOR, name


def test_more9_effects_module_exports_new_renderers():
    for name in ("grain", "texturizer", "patchwork", "craquelure", "crosshatch"):
        assert f"export function {name}(snap, v, dst)" in EFFECTS, name


def test_more9_gallery_imports_new_renderers():
    for alias in ("_grain", "_texturizer", "_patchwork", "_craquelure", "_crosshatch"):
        assert alias in EDITOR, alias


def test_more9_filter_aliases_registered():
    for line in (
        "'texture-grain': 'texture-grain'",
        "grain: 'texture-grain'",
        "'texture-texturizer': 'texture-texturizer'",
        "texturizer: 'texture-texturizer'",
        "'texture-patchwork': 'texture-patchwork'",
        "patchwork: 'texture-patchwork'",
        "'texture-craquelure': 'texture-craquelure'",
        "craquelure: 'texture-craquelure'",
        "'brush-crosshatch': 'brush-crosshatch'",
        "crosshatch: 'brush-crosshatch'",
    ):
        assert line in EDITOR, line


def test_more9_renderers_reuse_live_filter_dialog():
    for renderer in ("_grain", "_texturizer", "_patchwork", "_craquelure", "_crosshatch"):
        assert f"renderer: {renderer}," in EDITOR, renderer


def test_more9_grain_is_seeded_monochrome_lcg():
    assert "const rand = () => { s = (s * 1664525 + 1013904223) & 0xffffffff; return (s >>> 8) / 0xffffff; };" in EFFECTS
    block = EFFECTS.split("export function grain(snap, v, dst)")[1].split("\nexport function ")[0]
    for channel in ("p[i]", "p[i + 1]", "p[i + 2]"):
        assert f"{channel} = clamp255({channel} + n);" in block, channel


def test_more9_texturizer_is_seeded_value_noise_relief():
    block = EFFECTS.split("export function texturizer(snap, v, dst)")[1].split("\nexport function ")[0]
    assert "const n = valueNoise(w, h, scale, seed);" in block
    assert "const shade = d * relief * 32;" in block


def test_more9_patchwork_averages_tiles_with_bevel():
    block = EFFECTS.split("export function patchwork(snap, v, dst)")[1].split("\nexport function ")[0]
    assert "const bevel = (x === tx || y === ty) ? 20 : ((x === xe - 1 || y === ye - 1) ? -20 : 0);" in block
    assert "const ar = n ? r / n : 0; const ag = n ? g / n : 0; const ab = n ? b / n : 0;" in block


def test_more9_craquelure_traces_noise_contours():
    block = EFFECTS.split("export function craquelure(snap, v, dst)")[1].split("\nexport function ")[0]
    assert "const n = valueNoise(w, h, crack, seed);" in block
    assert "const edge = (n[px] - level) * (n[right] - level) < 0" in block


def test_more9_crosshatch_is_crossed_diagonal_strokes():
    block = EFFECTS.split("export function crosshatch(snap, v, dst)")[1].split("\nexport function ")[0]
    assert "const a = ((x + y) % period + period) % period;" in block
    assert "const b = ((x - y) % period + period) % period;" in block
    assert "const line = (a < length || b < length) ? 1 : 0;" in block


def test_more9_renderers_preserve_alpha():
    for name in ("texturizer", "patchwork", "craquelure", "crosshatch"):
        block = EFFECTS.split(f"export function {name}(snap, v, dst)")[1].split("\nexport function ")[0]
        assert "p[i + 3]" in block, name
    # Grain mutates the source buffer in place, adding the same noise to r/g/b
    # only, so alpha is untouched.
    grain_block = EFFECTS.split("export function grain(snap, v, dst)")[1].split("\nexport function ")[0]
    assert "p[i + 3] =" not in grain_block


def test_more9_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
