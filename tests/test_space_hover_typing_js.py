"""Issue #4856 — Space must reach the field you are typing in, not the card you hover.

ui.js activates a hovered card / dock chip / window on Space. `_spaceIsBlocked`
decided whether to leave the key alone, and for a text-editing target it only
did so when that field lived *inside* the hovered surface. Typing in the chat
composer while the pointer rested over an email tab therefore swallowed the
space and closed the tab mid-sentence.

The four declarations under test are pure once `closest`/`contains` exist, so
they run against a hand-made target object instead of a DOM.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_UI_JS = _REPO / "static" / "js" / "ui.js"
_HAS_NODE = shutil.which("node") is not None

_WANTED = (
    r"const SPACE_BLOCKED_SELECTOR = \[[\s\S]*?\.join\(', '\);",
    r"function _isTextEditingTarget\(target\) \{[\s\S]*?\n\}",
    r"function _targetEl\(target\) \{[\s\S]*?\n\}",
    r"function _spaceIsBlocked\(e, surface\) \{[\s\S]*?\n\}",
)

_HARNESS = """
// A keydown target that answers closest() for its own tag, and a hovered
// surface that either contains it or does not.
function target(tag) {
  return {
    nodeType: 1,
    closest(sel) {
      return sel.split(',').map(s => s.trim()).includes(tag) ? this : null;
    },
  };
}
const surface = (holdsTarget) => ({ contains: () => holdsTarget });
const blocked = (tag, holdsTarget) =>
  _spaceIsBlocked({ target: target(tag) }, surface(holdsTarget));
"""


def _run(body: str):
    source = _UI_JS.read_text(encoding="utf-8")
    chunks = []
    for pattern in _WANTED:
        match = re.search(pattern, source)
        assert match, f"ui.js no longer defines {pattern!r}"
        chunks.append(match.group(0))
    js = "\n".join(chunks) + _HARNESS + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("tag", ["input", "textarea", "select", '[contenteditable="true"]'])
def test_typing_outside_the_hovered_surface_keeps_the_space(tag):
    """The reported bug: composer focused, pointer over an unrelated window."""
    assert _run(f'console.log(JSON.stringify(blocked({tag!r}, false)));') is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("tag", ["input", "textarea", '[contenteditable="true"]'])
def test_typing_inside_the_hovered_surface_still_keeps_the_space(tag):
    assert _run(f'console.log(JSON.stringify(blocked({tag!r}, true)));') is True


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_hover_shortcut_still_fires_when_nothing_is_being_edited():
    assert _run('console.log(JSON.stringify(blocked("div", false)));') is False


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_focused_button_inside_the_hovered_surface_still_wins():
    assert _run('console.log(JSON.stringify(blocked("button", true)));') is True
