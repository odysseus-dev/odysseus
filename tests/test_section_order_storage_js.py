import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "section-management.js"
HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_section_order_ignores_wrong_shapes():
    js = f"""
    const mod = await import('{MODULE.as_posix()}');
    console.log(JSON.stringify({{
      objectValue: mod.sectionOrderFromRaw('{{"a":true}}'),
      arrayValue: mod.sectionOrderFromRaw('["a", 3, "b"]'),
    }}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {
        "objectValue": [],
        "arrayValue": ["a", "b"],
    }
