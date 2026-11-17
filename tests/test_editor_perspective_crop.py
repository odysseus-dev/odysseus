"""Perspective Crop (3-4) — pure-engine numeric tests + static contract.

The pixel engine (perspective-crop.js + perspective-warp.js `warpRgba`) is
node-runnable, so the size/normalization maths is exercised for real. The rest
is a static contract over the wiring (galleryEditor, topbar, wire, CSS, sw).
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CROP = ROOT / "static/js/editor/perspective-crop.js"
TOOL = ROOT / "static/js/editor/tools/perspective-crop.js"
GE = ROOT / "static/js/galleryEditor.js"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
WIRE = ROOT / "static/js/editor/wire-topbar-menus.js"
CSS = ROOT / "static/css/13-image-editor.css"
SW = ROOT / "static/sw.js"


def _node(expr: str):
    return subprocess.run(
        ["node", "--input-type=module", "-e", expr],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_perspective_crop_size_and_normalize_numeric():
    expr = (
        "const m = await import('./static/js/editor/perspective-crop.js');"
        "const size = m.perspectiveCropSize(["
        "{x:10,y:20},{x:110,y:20},{x:110,y:70},{x:10,y:70}],200,100);"
        "const norm = m.normalizeCropCorners(["
        "{x:10,y:20},{x:110,y:20},{x:110,y:70},{x:10,y:70}],200,100);"
        "console.log(JSON.stringify({size, norm}));"
    )
    result = _node(expr)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["size"] == {"width": 100, "height": 50}
    assert abs(data["norm"][0]["x"] - 0.05) < 1e-6
    assert abs(data["norm"][2]["y"] - 0.7) < 1e-6


def test_warp_identity_and_quad_scale_numeric():
    expr = (
        "const w = await import('./static/js/editor/perspective-warp.js');"
        "const src = new Uint8ClampedArray(4*4*4);"
        "for (let i=0;i<4*4;i++){ src[i*4]=255; src[i*4+3]=255; }"
        "const full = w.warpRgba(src,4,4,[{x:0,y:0},{x:1,y:0},{x:1,y:1},{x:0,y:1}],4,4);"
        "const half = w.warpRgba(src,4,4,[{x:0,y:0},{x:0.5,y:0},{x:0.5,y:0.5},{x:0,y:0.5}],2,2);"
        "console.log(JSON.stringify({full0:Array.from(full.slice(0,4)),halfLen:half.length,half0:Array.from(half.slice(0,4))}));"
    )
    result = _node(expr)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["full0"][0] == 255 and data["full0"][3] == 255
    assert data["halfLen"] == 2 * 2 * 4
    assert data["half0"][3] == 255


def test_engine_exports_and_forward_homography():
    src = CROP.read_text()
    assert "export function perspectiveCropSize(" in src
    assert "export function normalizeCropCorners(" in src
    assert "export function perspectiveCropCanvas(" in src
    assert "export function defaultPerspectiveCorners(" in src
    # Straighten uses the FORWARD homography (quad -> axis-aligned output),
    # not the inverse warp that left the result skewed.
    assert "solveSquareToQuad" in src
    assert "import { solveSquareToQuad } from './perspective-warp.js';" in src
    assert "warpRgba(" not in src
    assert "export function solveSquareToQuad(" in (ROOT / "static/js/editor/perspective-warp.js").read_text()


def test_forward_map_sends_output_corners_to_the_quad_numeric():
    # The quad's four corners must land EXACTLY on the output unit-square
    # corners (so the quad fills the whole output rectangle => straighten).
    quad = [
        {"x": 0.2, "y": 0.1}, {"x": 0.8, "y": 0.2},
        {"x": 0.7, "y": 0.9}, {"x": 0.15, "y": 0.8},
    ]
    expr = (
        "const w = await import('./static/js/editor/perspective-warp.js');"
        f"const quad = {json.dumps(quad)};"
        "const [a,b,c,d,e,f,g,h] = w.solveSquareToQuad(quad);"
        "const map = (u,v) => { const ww = g*u+h*v+1;"
        "  return {x:(a*u+b*v+c)/ww, y:(d*u+e*v+f)/ww}; };"
        "console.log(JSON.stringify([map(0,0),map(1,0),map(1,1),map(0,1)]));"
    )
    result = _node(expr)
    assert result.returncode == 0, result.stderr
    mapped = json.loads(result.stdout.strip().splitlines()[-1])
    for got, want in zip(mapped, quad):
        assert abs(got["x"] - want["x"]) < 1e-9, (got, want)
        assert abs(got["y"] - want["y"]) < 1e-9, (got, want)


def test_tool_overlay_module():
    src = TOOL.read_text()
    assert "export function createPerspectiveCropTool(" in src
    assert "ge-pcrop-overlay" in src
    assert 'data-pcrop="apply"' in src
    assert 'data-pcrop="cancel"' in src
    assert "data-corner" in src
    assert "onApply?." in src


def test_gallery_editor_wiring():
    src = GE.read_text()
    assert "import { createPerspectiveCropTool } from './editor/tools/perspective-crop.js';" in src
    assert "import { perspectiveCropSize as _perspectiveCropSize, perspectiveCropCanvas as _perspectiveCropCanvas }" in src
    assert "const _perspectiveCropTool = createPerspectiveCropTool(" in src
    assert "async function _applyPerspectiveCrop(corners)" in src
    assert "_perspectiveCropSize(corners, state.imgWidth, state.imgHeight)" in src
    assert "'document.perspectiveCrop':" in src
    assert "openPerspectiveCrop: () => _openPerspectiveCrop()," in src


def test_menu_and_handler_and_css_and_sw():
    assert 'data-image-action="perspective-crop"' in TOPBAR.read_text()
    wire = WIRE.read_text()
    assert "openPerspectiveCrop" in wire
    assert "else if (action === 'perspective-crop') openPerspectiveCrop?.();" in wire
    assert ".ge-pcrop-overlay" in CSS.read_text()
    sw = SW.read_text()
    import re
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", sw)
    assert "/static/js/editor/perspective-crop.js" in sw
    assert "/static/js/editor/tools/perspective-crop.js" in sw
