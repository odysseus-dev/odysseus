"""Pin modal backdrop open-guard helpers in ui.js (#4938)."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return json.loads(out_lines[-1])


def test_backdrop_guard_blocks_then_expires(node_available):
    script = textwrap.dedent("""
        import {
          armModalBackdropGuard,
          shouldSuppressBackdropClose,
          MODAL_BACKDROP_GUARD_MS,
        } from './static/js/modalBackdropGuard.js';

        const modal = { dataset: {} };
        const t0 = 1000;
        armModalBackdropGuard(modal, MODAL_BACKDROP_GUARD_MS, t0);
        const during = shouldSuppressBackdropClose(modal, t0);
        const after = shouldSuppressBackdropClose(modal, t0 + MODAL_BACKDROP_GUARD_MS + 1);
        console.log(JSON.stringify({ during, after, ms: MODAL_BACKDROP_GUARD_MS }));
    """)
    out = _run_node(script)
    assert out["during"] is True
    assert out["after"] is False
    assert out["ms"] == 350