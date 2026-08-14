"""Behavioral tests for the modular Settings shell.

The Node harness executes the production navigation/lifecycle modules against a
small DOM shim. This keeps the test dependency-free while ensuring the
extracted shell code, rather than a handwritten copy, drives the assertions.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS_HELPER = _REPO / "tests" / "helpers" / "test_settings_shell.js"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_settings_shell_frontend_behavior():
    proc = subprocess.run(
        ["node", str(_JS_HELPER)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, f"Node execution error: {proc.stderr}\n{proc.stdout}"

    results = json.loads(proc.stdout.strip())
    assert results, "Settings shell harness returned no assertions"
    for result in results:
        assert result["pass"] is True, (
            f"Failed JS behavioral test: {result['test']}"
            + (f" ({result.get('detail')})" if result.get("detail") else "")
        )
