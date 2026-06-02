"""Pin the pure hexToRgb helper (static/js/color/hex.js).

Driven through `node --input-type=module` (same approach as test_compare_js.py);
skips when `node` is not installed.

Regression: theme.js parsed hex with fixed substring(0,2)/(2,4)/(4,6) slices, so
a 3-digit shorthand like "#abc" produced NaN channels (the color picker already
expanded shorthand correctly — theme parsing did not).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "color" / "hex.js"
_HAS_NODE = shutil.which("node") is not None


def _rgb(hex_str: str):
    js = (
        f"import {{ hexToRgb }} from '{_HELPER.as_posix()}';"
        f"console.log(JSON.stringify(hexToRgb({json.dumps(hex_str)})));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_shorthand_expands():
    assert _rgb("#abc") == {"r": 0xAA, "g": 0xBB, "b": 0xCC}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_full_form_and_no_hash():
    assert _rgb("#ff8800") == {"r": 255, "g": 136, "b": 0}
    assert _rgb("ff8800") == {"r": 255, "g": 136, "b": 0}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_invalid_returns_null():
    assert _rgb("nothex") is None
    assert _rgb("") is None
