"""Regression for issue #1475 — pressing Enter with no model selected left the
typed message in the composer.

The send flow clears the composer only after it gets past the no-session guard;
with no model/session that guard shows a "No chat session active" bubble and
returns early, so the text stayed in the box. The early-return paths now clear
the composer too.

The first cut of that fix put the clear in a module-scope `_clearComposer()` that
called a bare `el('message')` — but chat.js only aliases `const el = uiModule.el`
*inside* its send functions, so at module scope `el` was undefined and the helper
threw `ReferenceError` on exactly the no-model path it was meant to fix (defeating
it and leaving the send button stuck). A source-only test missed it because it
never ran the JS.

So the clear logic now lives in a portable module (static/js/composerClear.js)
that takes the `el` lookup as a parameter, and this test actually EXECUTES it
under node — the way to catch a runtime ReferenceError, not just a string match.
Same node-driven approach as tests/test_compare_js.py.
"""

import json
import re
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


def test_clear_composer_runs_without_throwing(node_available):
    """Execute clearComposer with a working `el` lookup: it must empty the value,
    reset the autosized height, and dispatch an `input` event — and crucially NOT
    throw (the ReferenceError that defeated the original fix would surface here)."""
    script = textwrap.dedent("""
        const { clearComposer } = await import('./static/js/composerClear.js');
        const mi = { value: 'leftover draft', style: { height: '120px' }, _events: [] };
        mi.dispatchEvent = (e) => { mi._events.push(e.type); return true; };
        const el = (id) => (id === 'message' ? mi : null);
        let threw = null;
        try { clearComposer(el); } catch (e) { threw = String(e); }
        console.log(JSON.stringify({
          threw,
          value: mi.value,
          height: mi.style.height,
          events: mi._events,
        }));
    """)
    out = _run_node(script)
    assert out["threw"] is None, f"clearComposer must not throw: {out['threw']}"
    assert out["value"] == "", "composer value must be cleared"
    assert out["height"] == "", "autosized height must be reset"
    assert out["events"] == ["input"], "must dispatch an input event so listeners update"


def test_clear_composer_no_op_when_missing(node_available):
    """If the element isn't present, clearComposer must return quietly, never throw."""
    script = textwrap.dedent("""
        const { clearComposer } = await import('./static/js/composerClear.js');
        let threw = null;
        try { clearComposer(() => null); } catch (e) { threw = String(e); }
        console.log(JSON.stringify({ threw }));
    """)
    out = _run_node(script)
    assert out["threw"] is None


def test_chatjs_delegates_with_uimodule_el():
    """chat.js's `_clearComposer` must delegate to the portable helper passing
    `uiModule.el` — not reference a bare module-scope `el` (the bug). Guards
    against the regression reappearing in the wrapper."""
    text = (_REPO / "static/js/chat.js").read_text(encoding="utf-8")
    start = text.index("function _clearComposer()")
    body = text[start: start + 120]
    assert "clearComposer(uiModule.el)" in body, "must delegate via uiModule.el"
    # No bare `el(` call inside the wrapper (would be an out-of-scope reference).
    assert not re.search(r"[^.\w]el\(", body), "wrapper must not call a bare el(...)"
