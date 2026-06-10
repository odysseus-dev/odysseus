"""Pin the lasso outward-normal direction in lassoOffsetPoints.

`lassoOffsetPoints(points, grow)` documents "Positive = expand outward",
but it picked the perpendicular sign as `area > 0 ? 1 : -1`. In the canvas
y-down coordinate space that points the offset INWARD, so a positive grow
contracted the polygon (and a negative grow expanded it) - the lasso
"Edge stroke"/feather preview moved the wrong way. The sign must be flipped.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "editor" / "tools" / "lasso-mask.js"
_HAS_NODE = shutil.which("node") is not None


def _corner_distance(points, grow):
    arr = json.dumps(points)
    js = f"""
    import {{ lassoOffsetPoints }} from '{_HELPER.as_posix()}';
    const pts = {arr};
    const cx = pts.reduce((a, p) => a + p.x, 0) / pts.length;
    const cy = pts.reduce((a, p) => a + p.y, 0) / pts.length;
    const out = lassoOffsetPoints(pts, {grow});
    const d = Math.hypot(out[0].x - cx, out[0].y - cy);
    console.log(JSON.stringify(d));
    """
    proc = subprocess.run(["node", "--input-type=module"], input=js,
                          capture_output=True, text=True, cwd=str(_REPO), timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


_SQUARE_CW = [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 100}, {"x": 0, "y": 100}]
_SQUARE_CCW = [{"x": 0, "y": 0}, {"x": 0, "y": 100}, {"x": 100, "y": 100}, {"x": 100, "y": 0}]
_BASELINE = 70.7106  # corner-to-center distance of the 100x100 square


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_positive_grow_expands_both_windings():
    assert _corner_distance(_SQUARE_CW, 10) > _BASELINE + 5
    assert _corner_distance(_SQUARE_CCW, 10) > _BASELINE + 5


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_negative_grow_contracts():
    assert _corner_distance(_SQUARE_CW, -10) < _BASELINE - 5
