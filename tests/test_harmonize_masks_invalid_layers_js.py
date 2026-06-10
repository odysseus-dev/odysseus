"""Pin harmonize mask helpers against invalid layer lists.

Driven through `node --input-type=module`; skips without node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "editor" / "harmonize-masks.js"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_layer_union_alpha_returns_null_for_non_array_layers():
    js = f"""
    import {{ layerUnionAlpha, seamMask, layerBodyMask }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify([
      layerUnionAlpha(10, 10, null),
      seamMask(10, 10, {{"bad": true}}),
      layerBodyMask(10, 10, "bad")
    ]));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == [None, None, None]
