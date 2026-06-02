"""Regression for issue #1496 — the Documents editor line-number gutter stops
scrolling with the content near the bottom of long files (reported: a ~1250-line
file froze the counter at row 1229).

Root cause: the gutter uses `white-space: pre` (one row per logical line) while
the textarea uses `white-space: pre-wrap` (long lines wrap onto extra rows), so
the textarea's scrollable height exceeds the gutter's the moment anything wraps.
`syncGutterScroll` used to do `gutter.scrollTop = textarea.scrollTop`, which the
browser clamps at the gutter's smaller maximum — the numbers freeze for the whole
final stretch. The fix maps the textarea's scroll *ratio* onto the gutter's own
range (static/js/docGutterSync.js).

`document.js` pulls in browser-only modules so it can't load under node; the pure
mapping lives in docGutterSync.js, which is portable and tested directly here —
same approach as tests/test_compare_js.py.
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
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out_lines:
        raise AssertionError("node produced no stdout")
    return json.loads(out_lines[-1])


def test_no_wrap_is_identity(node_available):
    """When no line wraps the gutter and textarea have equal scrollable ranges,
    so the mapping must be the identity — single-line files behave as before."""
    script = textwrap.dedent("""
        const { gutterScrollTop } = await import('./static/js/docGutterSync.js');
        // Equal ranges: scrollHeight 5000, clientHeight 1000 on both.
        const at = (t) => gutterScrollTop(t, 5000, 1000, 5000, 1000);
        console.log(JSON.stringify({
          top: at(0),
          mid: at(2000),
          bottom: at(4000),
        }));
    """)
    out = _run_node(script)
    assert out == {"top": 0, "mid": 2000, "bottom": 4000}


def test_wrapped_gutter_reaches_bottom_and_never_freezes(node_available):
    """The #1496 case: lines wrap, so the gutter range (gMax) is smaller than the
    textarea range (taMax). The mapping must (a) reach the gutter's own bottom
    exactly when the textarea is at its bottom, and (b) keep moving across the
    final stretch instead of pinning early like the old raw copy did."""
    script = textwrap.dedent("""
        const { gutterScrollTop } = await import('./static/js/docGutterSync.js');
        // textarea range 5000 (content wraps), gutter range only 4000.
        const taSH = 6000, taCH = 1000;   // taMax = 5000
        const gSH = 5000, gCH = 1000;     // gMax = 4000
        const at = (t) => gutterScrollTop(t, taSH, taCH, gSH, gCH);
        const taMax = taSH - taCH, gMax = gSH - gCH;
        // Sample across the WHOLE range including the final stretch (4000..5000)
        // where the old `gutter.scrollTop = textarea.scrollTop` was already
        // clamped at gMax and therefore frozen.
        const samples = [];
        for (let t = 0; t <= taMax; t += 250) samples.push(at(t));
        let monotonic = true;
        for (let i = 1; i < samples.length; i++) if (samples[i] < samples[i-1]) monotonic = false;
        // How many of the final-stretch samples (taScrollTop > gMax) still move?
        // Old behaviour: min(t, gMax) -> all pinned at gMax (frozen). New: strictly increasing.
        const finalStretch = [];
        for (let t = gMax + 250; t <= taMax; t += 250) finalStretch.push(at(t));
        const finalMoves = finalStretch.length > 1
          && finalStretch[finalStretch.length - 1] > finalStretch[0];
        console.log(JSON.stringify({
          bottom: at(taMax),
          gMax,
          reaches_bottom: at(taMax) === gMax,
          monotonic,
          final_stretch_moves: finalMoves,
          overshoots: at(taMax) > gMax,
        }));
    """)
    out = _run_node(script)
    assert out["reaches_bottom"] is True, "gutter must reach its last number at the bottom"
    assert out["monotonic"] is True, "mapping must be non-decreasing"
    assert out["final_stretch_moves"] is True, "numbers must keep moving in the final stretch, not freeze"
    assert out["overshoots"] is False, "must never scroll the gutter past its own content"


def test_clamps_out_of_range_input(node_available):
    """Defensive: zero/over-scroll inputs map into [0, gMax]; degenerate ranges
    (no scroll) return 0 rather than NaN/Infinity."""
    script = textwrap.dedent("""
        const { gutterScrollTop } = await import('./static/js/docGutterSync.js');
        console.log(JSON.stringify({
          negative: gutterScrollTop(-100, 6000, 1000, 5000, 1000),
          over: gutterScrollTop(99999, 6000, 1000, 5000, 1000),
          no_ta_scroll: gutterScrollTop(0, 1000, 1000, 5000, 1000),
          no_gutter_scroll: gutterScrollTop(500, 6000, 1000, 1000, 1000),
        }));
    """)
    out = _run_node(script)
    assert out["negative"] == 0
    assert out["over"] == 4000  # clamped to gMax
    assert out["no_ta_scroll"] == 0
    assert out["no_gutter_scroll"] == 0
