"""Static contract tests for the Dehaze (Haze Removal) filter.

The math lives in static/js/editor/filters/dehaze.js (dark-channel prior), the
renderer + non-interactive control op in galleryEditor.js, and the Filter menu
entry in the topbar/wiring. These are plain JavaScript, so the tests assert the
source-level contract.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULE = ROOT / "static/js/editor/filters/dehaze.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
WIRE = ROOT / "static/js/editor/wire-topbar-menus.js"
SW = ROOT / "static/sw.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_module_is_offline_and_deterministic():
    src = _read(MODULE)
    assert "export function dehazeImageData(src, width, height, options = {})" in src
    assert "fetch(" not in src
    # Block-downsample + separable minimum filter (dark channel).
    assert "function minFilter2D(values, width, height, radius)" in src
    assert "const filtered = minFilter2D(dark, sw, sh, radius);" in src


def test_dark_channel_and_atmospheric_light():
    src = _read(MODULE)
    assert "const mn = Math.min(r, g, b);" in src
    assert "if (mn < best) { best = mn; br = r; bg = g; bb = b; }" in src
    # Atmospheric light = mean colour of the brightest 0.1% of the dark channel.
    assert "const topCount = Math.max(1, Math.round(total * 0.001));" in src
    assert "const ordered = Array.from(order).sort((a, b) => filtered[b] - filtered[a]);" in src
    assert "const aLum = Math.max(1, Math.max(ar, ag, ab));" in src


def test_transmission_recovery_and_refinement():
    src = _read(MODULE)
    # Small patch (7-15px) + t0 clamp of 0.1.
    assert "const patch = Math.max(3, Math.min(31, Math.round(Number(options.patch) || 15)));" in src
    assert "const t0 = clamp(Number(options.t0 ?? 0.1), 0.02, 1);" in src
    # Raw transmission clamped to >= t0, then guided-filter refined.
    assert "rawT[k] = clamp(1 - omega * (filtered[k] / aLum), t0, 1);" in src
    assert "const refined = guidedFilter(lum, rawT, sw, sh, guideRadius, eps);" in src
    assert "out[i] = clamp((src[i] - ar) / t + ar, 0, 255);" in src
    assert "out[i + 1] = clamp((src[i + 1] - ag) / t + ag, 0, 255);" in src
    assert "out[i + 2] = clamp((src[i + 2] - ab) / t + ab, 0, 255);" in src


def test_guided_filter_is_present():
    src = _read(MODULE)
    assert "function boxFilter(src, width, height, radius)" in src
    assert "function guidedFilter(guide, p, width, height, radius, eps)" in src
    assert "a[i] = covGP / (varG + eps);" in src
    assert "q[i] = meanA[i] * guide[i] + meanB[i];" in src
    assert "const guideRadius = Math.max(1, Math.round(Number(options.guideRadius) || 40));" in src


def test_smooth_upsample_of_transmission():
    src = _read(MODULE)
    assert "bctx.imageSmoothingEnabled = true;" in src
    assert "bctx.imageSmoothingQuality = 'high';" in src
    assert "bctx.drawImage(sm, 0, 0, width, height);" in src


def test_gallery_editor_renderer_and_ops():
    src = _read(GALLERY)
    assert "import { dehazeImageData as _dehazeImageData } from './editor/filters/dehaze.js';" in src
    assert "function _dehazeRenderer(snap, values, ctx) {" in src
    assert "function _applyDehaze() {" in src
    assert "applyDehaze: () => _applyDehaze()," in src
    # Non-interactive control-op branch + alias.
    assert "if (id === 'dehaze') {" in src
    assert "dehaze: 'dehaze', 'haze-removal': 'dehaze', haze: 'dehaze'," in src
    assert "return { id: 'dehaze', direct: true, amount: amt };" in src


def test_menu_and_wiring():
    menu = _read(TOPBAR)
    assert 'data-filter-action="dehaze"' in menu
    assert "Haze Removal…" in menu
    wire = _read(WIRE)
    assert "applyDehaze" in wire.split("export function wireTopbarMenus")[0] or "applyDehaze" in wire
    assert "else if (action === 'dehaze') applyDehaze?.();" in wire


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", _read(SW))
