import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "windowResize.js"
HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_saved_window_size_rejects_invalid_dimensions():
    js = f"""
    const mod = await import('{MODULE.as_posix()}');
    console.log(JSON.stringify({{
      good: mod.savedWindowSize({{ w: 500, h: 400 }}, 450, 350, 320, 200),
      badString: mod.savedWindowSize({{ w: "500", h: 400 }}, 800, 600),
      badObject: mod.savedWindowSize({{ w: {{}}, h: 400 }}, 800, 600),
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
        "good": {"w": 450, "h": 350},
        "badString": None,
        "badObject": None,
    }
