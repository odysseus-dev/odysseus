"""Pin modelStreamQuirks.js universal policy (node --input-type=module)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "model" / "modelStreamQuirks.js"
_HAS_NODE = shutil.which("node") is not None


def _eval(js_expr):
    js = (
        f"import * as m from '{_HELPER.as_uri()}';"
        f"console.log(JSON.stringify({js_expr}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    raw = proc.stdout.strip()
    return json.loads(raw) if raw else None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_universal_policy_constants():
    out = _eval("({ nudge: m.THINKING_ONLY_NUDGE_MS, timeout: m.THINKING_ONLY_TIMEOUT_MS })")
    assert out["nudge"] == 12_000
    assert out["timeout"] == 25_000


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_resolve_policy_any_model():
    out = _eval("m.resolveThinkingStallPolicy('qwen3:14b')")
    assert out["nudgeMs"] == 12_000
    assert out["timeoutMs"] == 25_000
    assert out["autoContinueOnThinkingOnly"] is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_no_override_by_default():
    assert _eval("m.matchModelStreamQuirk('gemma4:e4b')") is None
