"""Static contract tests for curved Text on a Path.

The editor stores paths as polylines. To lay text along a *curve* we flatten
analytic curves (ellipse/arc, quadratic and cubic Bezier, Catmull-Rom smoothing)
into a dense polyline (path-geometry.js); the arc-length text renderer in
text-layer.js then centres the run on the path and rotates each glyph to the
local tangent. These are plain JavaScript, so the tests assert the source-level
contract.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GEOMETRY = ROOT / "static/js/editor/path-geometry.js"
TEXT = ROOT / "static/js/editor/text-layer.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
EXTRA_WIRE = ROOT / "static/js/editor/wire-extra-menus.js"
SW = ROOT / "static/sw.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_curve_helpers_are_offline_and_exported():
    src = _read(GEOMETRY)
    for name in ("ellipsePoints", "cubicBezierPoints", "quadraticBezierPoints", "smoothPathPoints"):
        assert f"export function {name}(" in src, name
    # Pure geometry only: no network / AI, no DOM dependencies.
    assert "fetch(" not in src
    assert "XMLHttpRequest" not in src
    assert "document." not in src


def test_ellipse_flattens_to_a_dense_ring():
    src = _read(GEOMETRY)
    # Full sweep by default and a closed ring (the +1 point re-joins the start).
    assert "start = 0, end = Math.PI * 2" in src
    assert "for (let i = 0; i <= n; i += 1) {" in src
    # Subdivision grows with arc length so a large curve stays smooth.
    assert "Math.ceil(arcLength / stepSize(Math.max(radiusX, radiusY)))" in src
    assert "points.push({ x: centerX + dx, y: centerY + dy });" in src


def test_bezier_uses_de_casteljau():
    src = _read(GEOMETRY)
    assert "export function cubicBezierPoints(p0, p1, p2, p3, count = 0)" in src
    assert "const u = 1 - t;" in src
    assert "u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x" in src
    assert "export function quadraticBezierPoints(p0, p1, p2, count = 0)" in src
    # Catmull-Rom smoothing for coarse hand-drawn points.
    assert "export function smoothPathPoints(points, count = 0)" in src


def test_path_text_centres_the_run_on_the_path():
    src = _read(TEXT)
    # Pre-pass measures the whole run, then the starting distance is centred.
    assert "const glyphs = [...String(text.content || '')];" in src
    assert "let textWidth = 0;" in src
    assert "let distance = Math.max(0, (total - textWidth) / 2);" in src
    # The per-glyph advance and tangent rotation are unchanged.
    assert "const mid = distance + glyphWidth / 2;" in src
    assert "ctx.rotate(pos.angle);" in src
    assert "distance += glyphWidth + (Number(text.letterSpacing) || 0);" in src


def test_gallery_editor_wires_curve_ops():
    src = _read(GALLERY)
    assert "cubicBezierPoints as _cubicBezierPoints" in src
    assert "ellipsePoints as _ellipsePoints" in src
    assert "smoothPathPoints as _smoothPathPoints" in src
    for op in ["'path.addCurve':", "'text.onCircle':", "'text.attachToPath':"]:
        assert op in src, op
    # addCurve flattens each supported analytic curve type.
    assert "throw new Error('unknown curve type: ' + type)" in src
    assert "_cubicBezierPoints(c[0], c[1], c[2], c[3])" in src
    assert "_quadraticBezierPoints(c[0], c[1], c[2])" in src
    assert "_smoothPathPoints(Array.isArray(p.points) ? p.points : [])" in src
    # onCircle builds a ring and attaches the text.
    assert "const pts = _ellipsePoints(cx, cy, radius, radius, { start: Math.PI / 2, end: Math.PI / 2 + Math.PI * 2 });" in src
    assert "const result = _attachTextToPath(textLayer, layer);" in src


def test_type_menu_and_wiring_for_circle():
    menu = _read(TOPBAR)
    assert 'data-type-action="text-on-circle"' in menu
    assert "<span>Text on a Circle</span>" in menu
    wire = _read(EXTRA_WIRE)
    assert "window.geEditorControl?.invoke('text.onCircle', {});" in wire


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", _read(SW))
