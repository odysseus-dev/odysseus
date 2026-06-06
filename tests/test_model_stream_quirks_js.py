"""Pin modelStreamQuirks.js pattern matching (node --input-type=module)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "model" / "modelStreamQuirks.js"
_HAS_NODE = shutil.which("node") is not None


def _match(model):
    js = (
        f"import {{ matchModelStreamQuirk }} from '{_HELPER.as_uri()}';"
        f"console.log(JSON.stringify(matchModelStreamQuirk({json.dumps(model)})));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    raw = proc.stdout.strip()
    return json.loads(raw) if raw != "null" else None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_gemma4_e4b_exact():
    out = _match("gemma4:e4b")
    assert out["pattern"] == "gemma4:e4b"
    assert out["quirk"]["thinkingOnlyStallMs"] == 15_000


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_gemma4_wildcard():
    out = _match("gemma4:12b")
    assert out["pattern"] == "gemma4:*"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unknown_model():
    assert _match("qwen3:8b") is None
