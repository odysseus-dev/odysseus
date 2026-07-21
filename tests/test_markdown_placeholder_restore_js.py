"""Run the markdown placeholder-restore regression under CI's pytest.

The JS assertions live in tests/markdown_codefence_placeholder_regression.mjs
(a node vm harness that stubs markdown.js's imports). CI runs pytest, not a
standalone node test runner, so without this wrapper the regression never
executes in CI. This shells out to node and fails if any assertion throws.

Covers the fix where the allowed-HTML / math / mermaid / code-block restore
sites used a *string* replacement in String.replace, so a code sample
containing `$&`, `` $` ``, `$'`, `$$`, or `$1` was corrupted on restore.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MJS = _REPO / "tests" / "markdown_codefence_placeholder_regression.mjs"
_HAS_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_markdown_placeholder_restore_regression():
    assert _MJS.exists(), f"missing regression harness: {_MJS}"
    proc = subprocess.run(
        ["node", str(_MJS)],
        capture_output=True, text=True, cwd=str(_REPO), timeout=60,
    )
    assert proc.returncode == 0, (
        f"markdown placeholder-restore regression failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.stdout.strip().endswith("ok")
