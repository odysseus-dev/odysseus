"""Pin the DOM-free Escape-dismissal registry in static/js/escMenuStack.js.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same spirit as test_reply_recipients_js.py). Skips when
`node` is not installed rather than failing.

The module source is inlined into the eval'd module body (rather than imported
by path) so the test runs identically on Windows and POSIX — the repo has no
`"type": "module"` in package.json, so a path import of a `.js` file is treated
as CommonJS by node and rejects the ES `export`s. escMenuStack.js has no
imports of its own, so inlining is exact.

Background: ad-hoc dropdowns/popups (document-library card menus, chat context
popups, cookbook serve menus, calendar event menus, compare pane menus) live
outside the .modal system, so the global Escape arbiter in ui.js couldn't see
them. They register a dismiss callback here while open; the arbiter calls
dismissTopMenu() to close the most-recently-opened one. These tests lock in the
LIFO contract and the "exactly one menu per Escape, never get stuck" guarantees
the arbiter relies on.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "escMenuStack.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _HELPER.read_text(encoding="utf-8") if _HELPER.exists() else ""


def _run(body: str) -> str:
    """Run `body` as a module with the registry's functions already in scope."""
    js = _SRC + "\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_empty_stack_dismiss_is_noop():
    # Nothing open: returns false so the arbiter can fall through to modals.
    body = "console.log(JSON.stringify([dismissTopMenu(), _openMenuCount()]));"
    assert json.loads(_run(body)) == [False, 0]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_dismiss_is_lifo_and_closes_exactly_one():
    body = """
    const order = [];
    registerMenuDismiss(() => order.push('A'));
    registerMenuDismiss(() => order.push('B'));
    const r1 = dismissTopMenu();   // closes B (most recent)
    const r2 = dismissTopMenu();   // closes A
    const r3 = dismissTopMenu();   // nothing left
    console.log(JSON.stringify({ order, r1, r2, r3, left: _openMenuCount() }));
    """
    out = json.loads(_run(body))
    assert out["order"] == ["B", "A"]            # LIFO
    assert [out["r1"], out["r2"], out["r3"]] == [True, True, False]
    assert out["left"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unregister_removes_entry_without_firing():
    body = """
    let fired = false;
    const unreg = registerMenuDismiss(() => { fired = true; });
    unreg();                       // menu closed itself via outside-click
    const r = dismissTopMenu();    // Escape should now find nothing
    console.log(JSON.stringify({ fired, r, left: _openMenuCount() }));
    """
    # Unregistering must not invoke the callback and must leave the stack empty.
    assert json.loads(_run(body)) == {"fired": False, "r": False, "left": 0}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unregister_targets_correct_entry_when_interleaved():
    body = """
    const order = [];
    const unregA = registerMenuDismiss(() => order.push('A'));
    registerMenuDismiss(() => order.push('B'));
    unregA();                      // remove the older entry, keep B
    dismissTopMenu();              // should fire B, not A
    console.log(JSON.stringify({ order, left: _openMenuCount() }));
    """
    out = json.loads(_run(body))
    assert out["order"] == ["B"]
    assert out["left"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_throwing_dismiss_still_pops_and_reports_handled():
    body = """
    registerMenuDismiss(() => { throw new Error('boom'); });
    const r = dismissTopMenu();    // must swallow the error...
    console.log(JSON.stringify({ r, left: _openMenuCount() }));
    """
    # A misbehaving menu must not wedge the stack or crash the arbiter.
    assert json.loads(_run(body)) == {"r": True, "left": 0}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_non_function_registration_is_ignored():
    body = """
    const unreg = registerMenuDismiss(null);
    console.log(JSON.stringify({ left: _openMenuCount(), unregType: typeof unreg }));
    """
    # Bad input must not enter the stack, and must still return a callable.
    assert json.loads(_run(body)) == {"left": 0, "unregType": "function"}
