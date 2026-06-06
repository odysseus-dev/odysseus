"""Regression for issue #1568 — installing a heavy dependency (vllm) in the
Cookbook crashes in a "stale — restarting" loop.

The download/install watchdog (static/js/cookbookRunning.js) decides a task is
stalled when its progress signal stays unchanged for STALE_PROGRESS_MS. That
signal used to be the downloaded-byte counter only, which freezes during the long
no-byte-counter phases of a dependency install — pip dependency resolution and
the native CUDA build — so the watchdog falsely declared the install stale and
restarted it mid-build, looping forever.

computeProgressSignal (cookbookProgressSignal.js) keeps the byte signal for the
download phase (so a genuinely stuck download is still caught) and falls back to
the output tail when there's no byte counter, so build/resolver output counts as
progress. Pure function → executed under node here (cookbookRunning.js pulls in
browser-only modules and can't load).
"""

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
        cwd=_REPO, capture_output=True, timeout=15, text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out:
        raise AssertionError("node produced no stdout")
    return json.loads(out[-1])


def test_download_phase_uses_byte_counter_and_ignores_animated_tail(node_available):
    """During a download the byte counter is the signal; a stuck download whose
    only the ETA/spinner keeps animating must yield the SAME signal (so a real
    download stall is still detected)."""
    script = textwrap.dedent("""
        const { computeProgressSignal } = await import('./static/js/cookbookProgressSignal.js');
        // Same downloaded bytes, different animated ETA/spinner in the tail.
        const a = computeProgressSignal('1.81G', null, '73', 'Downloading 73%| 1.81G/2.49G [eta 0:05:11]');
        const b = computeProgressSignal('1.81G', null, '73', 'Downloading 73%| 1.81G/2.49G [eta 0:09:42] -');
        // Bytes climb -> different.
        const c = computeProgressSignal('2.10G', null, '84', 'Downloading 84%| 2.10G/2.49G');
        console.log(JSON.stringify({ a, b, stuck_same: a === b, climbed_diff: a !== c }));
    """)
    out = _run_node(script)
    assert out["a"] == "1.81G"
    assert out["stuck_same"] is True, "a stuck download (only ETA animating) must stay the same signal"
    assert out["climbed_diff"] is True, "climbing bytes must change the signal"


def test_build_phase_progresses_on_new_output(node_available):
    """The #1568 case: no byte counter (pip resolve / CUDA build). New build
    output must change the signal so it isn't falsely declared stale — whereas a
    byte-only signal would read '0' for both and trip the stall timer."""
    script = textwrap.dedent("""
        const { computeProgressSignal } = await import('./static/js/cookbookProgressSignal.js');
        const s1 = computeProgressSignal(null, null, null, 'Building wheel for vllm ... compiling csrc/attention.cu');
        const s2 = computeProgressSignal(null, null, null, 'Building wheel for vllm ... compiling csrc/cache_kernels.cu');
        const hung1 = computeProgressSignal(null, null, null, 'Building wheel for vllm ... (no output)');
        const hung2 = computeProgressSignal(null, null, null, 'Building wheel for vllm ... (no output)');
        console.log(JSON.stringify({
          build_progresses: s1 !== s2,
          true_hang_stays: hung1 === hung2,
        }));
    """)
    out = _run_node(script)
    assert out["build_progresses"] is True, "new build output must count as progress (#1568)"
    assert out["true_hang_stays"] is True, "a genuinely frozen tail must still read as stalled"
