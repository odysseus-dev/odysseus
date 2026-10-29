import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUCKET = (ROOT / "static/js/editor/tools/bucket.js").as_uri()


def run_node(body: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(body)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _solid(w, h, rgb=None, mask=None):
    data = bytearray(w * h * 4)
    if rgb:
        for i in range(w * h):
            data[i * 4] = rgb[0]
            data[i * 4 + 1] = rgb[1]
            data[i * 4 + 2] = rgb[2]
            data[i * 4 + 3] = 255
    return data


def test_flood_fill_fills_only_the_contiguous_region():
    # 6x3 white field split in the middle by a one-pixel black wall.
    w, h = 6, 3
    data = _solid(w, h, (255, 255, 255))
    for y in range(h):
        i = (y * w + 3) * 4
        data[i] = data[i + 1] = data[i + 2] = 0
    # Fill the left half only (start at 0,0).
    body = f"""
    import {{ floodFillImageData }} from {json.dumps(BUCKET)};
    const w = {w}, h = {h};
    const data = new Uint8ClampedArray({list(data)});
    const count = floodFillImageData(data, w, h, 0, 0, [255,0,0], {{ tolerance: 0 }});
    console.log(JSON.stringify({{
      count,
      left: [data[0], data[1], data[2]],
      right: [data[(w-1)*4], data[(w-1)*4+1], data[(w-1)*4+2]],
      wall: [data[3*4], data[3*4+1], data[3*4+2]],
    }}));
    """
    out = run_node(body)
    assert out["count"] == 9  # 3 columns x 3 rows
    assert out["left"] == [255, 0, 0]
    assert out["right"] == [255, 255, 255]  # other side untouched
    assert out["wall"] == [0, 0, 0]


def test_flood_fill_tolerance_and_selection_mask():
    w, h = 4, 1
    # Two near-white pixels then a far pixel; tolerance decides the spill.
    data = bytearray(w * h * 4)
    for x, c in enumerate([250, 245, 200, 250]):
        i = x * 4
        data[i] = data[i + 1] = data[i + 2] = c
        data[i + 3] = 255
    body = f"""
    import {{ floodFillImageData }} from {json.dumps(BUCKET)};
    const w = {w}, h = {h};
    const base = new Uint8ClampedArray({list(data)});
    const tight = new Uint8ClampedArray(base);
    const tightCount = floodFillImageData(tight, w, h, 0, 0, [0,0,0], {{ tolerance: 10 }});
    const loose = new Uint8ClampedArray(base);
    const looseCount = floodFillImageData(loose, w, h, 0, 0, [0,0,0], {{ tolerance: 60 }});
    // A mask that only allows the first pixel confines the fill.
    const mask = new Uint8ClampedArray(w * h * 4);
    mask[3] = 255;
    const masked = new Uint8ClampedArray(base);
    const maskedCount = floodFillImageData(masked, w, h, 0, 0, [0,0,0], {{ tolerance: 60, mask }});
    console.log(JSON.stringify({{ tightCount, looseCount, maskedCount }}));
    """
    out = run_node(body)
    assert out["tightCount"] == 2   # 250 and 245 within 10; 200 is not
    assert out["looseCount"] == 4   # tolerance 60 reaches everything
    assert out["maskedCount"] == 1  # mask confines to the single eligible pixel


def test_bucket_tool_is_registered_and_wired():
    toolbar = (ROOT / "static/js/editor/build/toolbar.js").read_text()
    assert "bucket: { label: 'Paint Bucket'" in toolbar
    assert "['gradient', 'bucket']" in toolbar

    editor = (ROOT / "static/js/galleryEditor.js").read_text()
    assert "import { createBucketTool } from './editor/tools/bucket.js';" in editor
    assert "const _bucketTool = createBucketTool({" in editor
    assert "if (state.tool === 'bucket') return _bucketTool.fill(e);" in editor
    assert "'fill.flood':" in editor
    assert "_pixelPaintTools = new Set(['brush', 'pixel', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn', 'gradient', 'bucket', 'liquify'])" in editor

    state = (ROOT / "static/js/editor/state.js").read_text()
    assert "bucketTolerance: 32" in state


def test_paint_tools_require_confirmation_before_rasterizing():
    editor = (ROOT / "static/js/galleryEditor.js").read_text()
    assert "async function _confirmRasterizeLayer(layer) {" in editor
    assert '_saveState(`Rasterize "${layer.name}"`);' in editor
    assert "if (_pixelPaintTools.has(state.tool) && !_getActiveMaskLayer() && !state.quickMaskActive &&" in editor
    assert "void _confirmRasterizeLayer(layer);" in editor


def test_smudge_and_dodge_icons_are_distinct():
    toolbar = (ROOT / "static/js/editor/build/toolbar.js").read_text()
    # Smudge is a pointing hand/finger, not a swirl.
    assert "M12.5 12V5.5a1.75 1.75 0 1 1 3.5 0V12" in toolbar
    assert "M16 12v-1.5a1.5 1.5 0 0 1 3 0V16a5 5 0 0 1-5 5h-2.2" in toolbar
    # Dodge is the mirrored, filled dot.
    assert "m9.5 14.5-6 6" in toolbar
    assert '<circle cx="14" cy="10" r="6" fill="currentColor"/>' in toolbar
