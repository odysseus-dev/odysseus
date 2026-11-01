"""Color Lookup .cube import/export (work-order 3-5).

The engine (static/js/editor/fx/cube-lut.js) is pure and node-runnable, so the
parse/apply/generate maths is exercised for real. The rest is a static contract
over the wiring (pixel-pass, adj-popup, galleryEditor ops, layer-helpers, sw).
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUBE = ROOT / "static/js/editor/fx/cube-lut.js"
PIXEL = ROOT / "static/js/editor/fx/pixel-pass.js"
POPUP = ROOT / "static/js/editor/fx/adj-popup.js"
HELPERS = ROOT / "static/js/editor/layer-helpers.js"
GE = ROOT / "static/js/galleryEditor.js"
SW = ROOT / "static/sw.js"

SWAP_ROWS = "\n".join([
    "0 0 0", "0 1 0", "1 0 0", "1 1 0", "0 0 1", "0 1 1", "1 0 1", "1 1 1",
])
CUBE_TEXT = (
    'TITLE "swap"\n'
    "LUT_3D_SIZE 2\n"
    "DOMAIN_MIN 0 0 0\n"
    "DOMAIN_MAX 1 1 1\n"
    f"{SWAP_ROWS}\n"
)


def _node(expr: str):
    return subprocess.run(
        ["node", "--input-type=module", "-e", expr],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_parse_cube_lut_numeric():
    expr = (
        "const m = await import('./static/js/editor/fx/cube-lut.js');"
        f"const lut = m.parseCubeLut({json.dumps(CUBE_TEXT)});"
        "console.log(JSON.stringify({size:lut.size, len:lut.data.length,"
        " dmin:Array.from(lut.domainMin), dmax:Array.from(lut.domainMax)}));"
    )
    result = _node(expr)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["size"] == 2
    assert data["len"] == 2 * 2 * 2 * 3
    assert data["dmin"] == [0, 0, 0]
    assert data["dmax"] == [1, 1, 1]


def test_apply_cube_identity_and_swap_numeric():
    expr = (
        "const m = await import('./static/js/editor/fx/cube-lut.js');"
        "const id = m.identityLut(2);"
        "const a = new Uint8ClampedArray([0,0,0,255, 128,64,32,255, 255,255,255,255, 10,200,90,255]);"
        "const before = Array.from(a);"
        "m.applyCubeLut(a, 4, 1, id, { strength: 1 });"
        "const identSame = JSON.stringify(Array.from(a)) === JSON.stringify(before);"
        "const swap = m.parseCubeLut(" + json.dumps(CUBE_TEXT) + ");"
        "const b = new Uint8ClampedArray([200,10,90,255]);"
        "m.applyCubeLut(b, 1, 1, swap, { strength: 1 });"
        "const s2 = new Uint8ClampedArray([200,10,90,255]);"
        "m.applyCubeLut(s2, 1, 1, swap, { strength: 0 });"
        "console.log(JSON.stringify({identSame, swapped:Array.from(b), zero:Array.from(s2)}));"
    )
    result = _node(expr)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["identSame"] is True
    assert data["swapped"][:3] == [10, 200, 90]
    assert data["zero"][:3] == [200, 10, 90]  # strength 0 -> unchanged


def test_generate_round_trip_numeric():
    expr = (
        "const m = await import('./static/js/editor/fx/cube-lut.js');"
        "const id = m.identityLut(3);"
        "const text = m.generateCubeLut(id);"
        "const back = m.parseCubeLut(text);"
        "console.log(JSON.stringify({hasTitle:text.includes('LUT_3D_SIZE'),"
        " size:back.size, len:back.data.length, len3:m.identityLut(3).data.length}));"
    )
    result = _node(expr)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["hasTitle"] is True
    assert data["size"] == 3
    assert data["len"] == 3 ** 3 * 3
    assert data["len3"] == 3 ** 3 * 3


def test_engine_is_offline():
    src = CUBE.read_text()
    assert "fetch(" not in src
    assert "XMLHttpRequest" not in src
    assert "export function parseCubeLut(" in src
    assert "export function applyCubeLut(" in src
    assert "export function generateCubeLut(" in src
    assert "export function identityLut(" in src


def test_pixel_pass_applies_the_cube():
    src = PIXEL.read_text()
    assert "import { applyCubeLut } from './cube-lut.js';" in src
    assert "if (cube && cube.size && cube.data) {" in src
    assert "applyCubeLut(d, w, h, lut, { strength });" in src
    assert "new Float64Array(cube.data)" in src


def test_adj_popup_loads_and_exports_and_strength():
    src = POPUP.read_text()
    assert "import { parseCubeLut, generateCubeLut } from './cube-lut.js';" in src
    assert "ge-adj-lut-load" in src
    assert 'accept=".cube,text/plain"' in src
    assert "parseCubeLut(text)" in src
    assert "p.cube = {" in src
    assert "ge-adj-lut-export" in src
    assert "generateCubeLut({" in src
    assert "a.download = 'look.cube'" in src
    assert "sliderRow('strength', 'Strength', 0, 100" in src
    # slider + revert wiring
    assert "p.strength = raw; display = raw + '%';" in src
    assert "'photo-filter', 'color-lookup'" in src


def test_defaults_ops_and_sw():
    assert "return { preset: 'none', strength: 100 };" in HELPERS.read_text(encoding="utf-8")
    ge = GE.read_text()
    assert "import { parseCubeLut as _parseCubeLut, generateCubeLut as _generateCubeLut } from './editor/fx/cube-lut.js';" in ge
    assert "'colorLookup.loadCube':" in ge
    assert "'colorLookup.exportCube':" in ge
    assert "_parseCubeLut(text)" in ge
    assert "_generateCubeLut({" in ge
    sw = SW.read_text()
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", sw)
    assert "/static/js/editor/fx/cube-lut.js" in sw
