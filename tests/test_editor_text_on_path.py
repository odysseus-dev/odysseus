"""Static contract tests for Text on a Path.

A text layer can be laid along a vector path layer: each glyph advances by its
measured width and is rotated to the local tangent of the path. The renderer
lives in text-layer.js, the attach/detach glue and control ops in
galleryEditor.js, and the Type > Path menu entries in the topbar/wiring. These
are plain JavaScript, so the tests assert the source-level contract.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEXT = ROOT / "static/js/editor/text-layer.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
EXTRA_WIRE = ROOT / "static/js/editor/wire-extra-menus.js"
SW = ROOT / "static/sw.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_text_data_model_has_path():
    src = _read(TEXT)
    assert "  path: null," in src
    # normalizeTextData keeps only finite {x,y} points, else null.
    assert "path: Array.isArray(value.path)" in src
    assert ".filter((point) => Number.isFinite(Number(point?.x)) && Number.isFinite(Number(point?.y)))" in src
    assert ".map((point) => ({ x: Number(point.x), y: Number(point.y) }))" in src


def test_path_renderer_lays_glyphs_along_tangents():
    src = _read(TEXT)
    assert "export function pathTextPadding(text)" in src
    assert "export function hasTextPath(value)" in src
    assert "export function pathTextCanvas(text)" in src
    # Arc-length lookup over the polyline.
    assert "segments.push({ a, dx, dy, len, start: total });" in src
    assert "const atDistance = (distance) => {" in src
    assert "angle: Math.atan2(seg.dy, seg.dx)" in src
    # Each glyph advances by its measured width (+ letterSpacing) and rotates.
    assert "const glyphWidth = ctx.measureText(char).width;" in src
    assert "const mid = distance + glyphWidth / 2;" in src
    assert "ctx.translate(pos.x, pos.y);" in src
    assert "ctx.rotate(pos.angle);" in src
    assert "distance += glyphWidth + (Number(text.letterSpacing) || 0);" in src


def test_renderer_prefers_path_over_warp():
    src = _read(TEXT)
    assert "const base = hasTextPath(layer.text)" in src
    assert "? pathTextCanvas(layer.text)" in src
    assert ": warpCanvas(baseTextCanvas(layer.text), layer.text.warp, layer.text.warpAmount);" in src


def test_gallery_editor_glue_and_ops():
    src = _read(GALLERY)
    assert "pathTextPadding as _pathTextPadding," in src
    assert "function _attachTextToPath(textLayer, pathLayer) {" in src
    assert "function _detachTextFromPath(textLayer) {" in src
    # Attach copies the path into the text frame and offsets the layer to match.
    assert "textLayer.text = _normalizeTextData({ ...textLayer.text, path: local });" in src
    assert "state.layerOffsets.set(textLayer.id, { x: Math.round(pathOffset.x + minX - pad), y: Math.round(pathOffset.y + minY - pad) });" in src
    for op in ["'path.add':", "'text.attachToPath':", "'text.detachFromPath':"]:
        assert op in src, op
    assert "window.geEditorControl" not in src or "_attachTextToPath(textLayer, pathLayer)" in src
    assert "const result = _attachTextToPath(textLayer, pathLayer);" in src


def test_type_menu_and_wiring():
    menu = _read(TOPBAR)
    assert '<div class="ge-menu-label">Path</div>' in menu
    assert 'data-type-action="text-on-path"' in menu
    assert 'data-type-action="text-off-path"' in menu
    wire = _read(EXTRA_WIRE)
    assert "window.geEditorControl?.invoke('text.attachToPath', {});" in wire
    assert "window.geEditorControl?.invoke('text.detachFromPath', {});" in wire


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", _read(SW))
