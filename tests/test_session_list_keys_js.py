"""Pin the sidebar session-list keydown intent classifier in
static/js/sessionListKeys.js.

Driven through `node --input-type=module` so we exercise the real JS without a
full Vitest/Jest setup (same approach as test_esc_menu_stack_js.py /
test_keybind_altgr_js.py). Skips when `node` is not installed rather than
failing.

The module source is inlined into the eval'd module body (rather than imported
by path) so the test runs identically on Windows and POSIX — the repo has no
`"type": "module"` in package.json, so a path import of a `.js` file is treated
as CommonJS by node and rejects the ES `export`s. sessionListKeys.js has no
imports of its own, so inlining is exact.

Bug being pinned: double-clicking a session to rename renders an <input> INSIDE
the focused `.list-item[data-session-id]`, so a bare keydown still bubbles up to
the list-level handler (`_onSessionListKeydown` in sessions.js). Before the fix,
pressing Backspace to edit the chat's name fell through to the
Delete/Backspace branch and popped the "Delete this session?" confirm. The
classifier now returns 'ignore' for any editable target, so Backspace edits text
instead of deleting the chat. These tests lock in that contract plus the rest of
the navigation intents.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "sessionListKeys.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _HELPER.read_text(encoding="utf-8") if _HELPER.exists() else ""

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(body: str) -> str:
    """Run `body` as a module with the classifier's exports already in scope."""
    js = _SRC + "\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _classify(key: str, *, tag: str = "DIV", content_editable: bool = False,
              on_row: bool = True) -> str:
    """Return classifySessionListKey({key, target}) for a synthetic event.

    `on_row` controls whether target.closest('.list-item[data-session-id]')
    resolves — i.e. whether the keystroke happened on a focused session row.
    """
    js = f"""
    const target = {{
      tagName: {json.dumps(tag)},
      isContentEditable: {json.dumps(content_editable)},
      closest: (sel) => (sel === '.list-item[data-session-id]' && {json.dumps(on_row)}) ? {{}} : null,
    }};
    const ev = {{ key: {json.dumps(key)}, target }};
    console.log(JSON.stringify(classifySessionListKey(ev)));
    """
    return json.loads(_run(js))


# --- The actual bug: Backspace while renaming must NOT delete the chat --------

def test_backspace_in_rename_input_is_ignored():
    # The reported bug: an <input> (the inline rename field) is focused inside
    # the session row. Backspace there must edit text, never trigger delete.
    assert _classify("Backspace", tag="INPUT") == "ignore"


def test_delete_key_in_rename_input_is_ignored():
    # Same protection for the literal Delete key while editing the name.
    assert _classify("Delete", tag="INPUT") == "ignore"


def test_textarea_target_is_ignored():
    assert _classify("Backspace", tag="TEXTAREA") == "ignore"


def test_contenteditable_target_is_ignored():
    # Editable via contenteditable rather than a form field.
    assert _classify("Backspace", tag="DIV", content_editable=True) == "ignore"


# --- The intended shortcuts still classify correctly on a real row ------------

def test_backspace_on_row_deletes():
    # Focused session row (not an input): Backspace/Delete IS the delete shortcut.
    assert _classify("Backspace") == "delete"
    assert _classify("Delete") == "delete"


def test_arrows_navigate():
    assert _classify("ArrowDown") == "nav-down"
    assert _classify("ArrowUp") == "nav-up"


def test_enter_on_row_opens():
    assert _classify("Enter") == "open"


def test_unhandled_key_returns_null():
    # A key we don't act on passes through untouched (no preventDefault).
    assert _classify("a") is None


def test_off_row_target_is_ignored():
    # Keystroke not on a session row (closest() misses) → nothing to do.
    assert _classify("Backspace", on_row=False) == "ignore"
