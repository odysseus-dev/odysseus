"""Pin adjustment key generation against missing adjustment payloads.

Driven through `node --input-type=module`; skips without node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "editor" / "layer-helpers.js"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_adjustments_key_tolerates_missing_adjustments():
    js = f"""
    import {{ adjustmentsKey }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify([
      adjustmentsKey(null),
      adjustmentsKey(undefined),
      adjustmentsKey("bad")
    ]));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    keys = json.loads(proc.stdout.strip())
    assert keys == [keys[0], keys[0], keys[0]]
