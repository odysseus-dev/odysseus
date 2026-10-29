"""Selection marching-ants must keep a constant on-screen size regardless of zoom.

The selection outline canvas is rendered at SCREEN resolution (image size ×
zoom) and the painter strokes a flat 1px outline in screen pixels, so there is
no bitmap resampling: the outline keeps the same thickness AND opacity at every
zoom level. The crop overlay got the same zoom compensation.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASK = (ROOT / "static/js/editor/selection-mask.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_selection_boundary_helper_returns_flags():
    assert "export function selectionBoundary(mask, threshold = 128) {" in MASK
    assert "flags" in MASK
    # Rows outside the selection are skipped instead of scanning the whole
    # canvas, which keeps closing a pen path fast on large documents.
    assert "const firsts = new Int32Array(height).fill(-1);" in MASK
    assert "const lasts = new Int32Array(height).fill(-1);" in MASK
    assert "for (let x = first; x <= last; x += 1) {" in MASK


def test_overlay_renders_at_screen_resolution():
    # The overlay bitmap must match its CSS box (image size × zoom) so the
    # strokes are never up-scaled (which is what made opacity drift).
    assert "const dispW = Math.max(1, Math.round(state.imgWidth * state.zoom));" in EDITOR
    assert "overlay.style.width = `${dispW}px`;" in EDITOR


def test_painter_draws_flat_screen_pixels():
    assert "export function paintSelectionBoundary(ctx, pixels, width, height, phase = 0, zoom = 1, flags = null) {" in MASK
    assert "ctx.lineWidth = 1;" in MASK
    assert "const x0 = x * scale;" in MASK
    assert "new Path2D()" in MASK


def test_overlay_passes_zoom_and_flags():
    assert "selectionBoundary as _selectionBoundary," in EDITOR
    assert (
        "_paintSelectionBoundary(ctx, cache.pixels, cache.width, cache.height, "
        "_selectionAnimationPhase, state.zoom, cache.flags);" in EDITOR
    )
    assert "..._selectionBoundary(mask)," in EDITOR


def test_crop_overlay_is_zoom_compensated():
    assert "state.mainCtx.lineWidth = 1 / state.zoom;" in EDITOR
    assert "state.mainCtx.setLineDash([4 / state.zoom, 4 / state.zoom]);" in EDITOR


def test_sw_cache_name_bumped():
    import re
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
