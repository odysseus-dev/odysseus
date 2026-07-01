"""Pin canvasCoords (static/js/editor/canvas-coords.js) against an empty
touch list. Driven through `node --input-type=module` (same approach as
tests/test_markdown_table_row_js.py); skips when `node` is missing.

Regression: a touch event whose `touches` list is present but EMPTY (a
real mobile race — the finger is already lifted when the handler runs)
made `e.touches[0].clientX` throw \"Cannot read properties of undefined\".
The guard falls back to the event's own clientX/clientY in that case.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MOD = _REPO / "static" / "js" / "editor" / "canvas-coords.js"
_HAS_NODE = shutil.which("node") is not None

_CANVAS = "{width:800,height:600,getBoundingClientRect:()=>({width:400,height:300,left:100,top:50})}"


def _coords(event_js):
    js = f"""
    import {{ canvasCoords }} from '{_MOD.as_posix()}';
    const canvas = {_CANVAS};
    console.log(JSON.stringify(canvasCoords({event_js}, canvas)));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_empty_touch_list_falls_back_to_client_xy():
    # scaleX = 800/400 = 2; (200-100)*2 = 200, (100-50)*2 = 100
    assert _coords("{touches:[],clientX:200,clientY:100}") == {"x": 200, "y": 100}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_mouse_event_unaffected():
    assert _coords("{clientX:200,clientY:100}") == {"x": 200, "y": 100}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_touch_with_finger_still_used():
    assert _coords("{touches:[{clientX:200,clientY:100}]}") == {"x": 200, "y": 100}
