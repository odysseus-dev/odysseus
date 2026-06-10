import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "research" / "jobs.js"
HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_dismissed_set_ignores_non_array_storage():
    js = f"""
    const jobs = await import('{MODULE.as_posix()}');
    const objectValue = [...jobs.dismissedSetFromRaw('{{"abc":true}}')];
    const arrayValue = [...jobs.dismissedSetFromRaw('["abc"]')];
    console.log(JSON.stringify({{ objectValue, arrayValue }}));
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
        "arrayValue": ["abc"],
    }
