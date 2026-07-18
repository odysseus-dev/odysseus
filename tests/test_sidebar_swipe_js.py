"""Regression coverage for the mobile swipe-to-open-sidebar gesture guard.

The mobile "swipe from the edge to open the sidebar" gesture lives in
``_initChatSwipeToOpenSidebar`` in ``static/js/sidebar-layout.js``. It used to
arm for any horizontal drag that started anywhere over the chat content, so
dragging a finger to extend a text selection in a message (to copy it) was
captured as an edge-swipe and yanked the drawer open.

These tests load the real browser module under Node, stub just enough of the
DOM/BOM for the gesture handlers to run, dispatch synthetic ``touchstart`` /
``touchmove`` events, and assert whether the drawer opened. They follow the same
Node-subprocess pattern as ``test_markdown_rendering_js.py``.
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


# One synthetic gesture, driven entirely by a small scenario dict:
#   startX            - clientX of the touchstart (px from the left edge)
#   moveX             - clientX of the touchmove
#   selectionAtStart  - is a text selection already active at touchstart?
#   selectionAtMove   - does a selection become/stay active by the touchmove?
# The harness reports whether the swipe opened the drawer, which side, whether
# the sidebar stayed hidden, and how much text remained selected.
_HARNESS = r"""
import fs from 'node:fs';

const S = JSON.parse(process.argv[1]);

// Records the listeners the module wires on `document`.
const listeners = {};

function classList(initial) {
  const set = new Set(initial || []);
  return {
    contains: (c) => set.has(c),
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
  };
}

const sidebarEl = { classList: classList(['hidden']) };       // drawer starts closed
const chatContainerEl = { classList: classList([]) };          // not compare-active

// The selection text the page would report. Mutable so a scenario can make a
// selection appear between touchstart and touchmove.
let selectionText = S.selectionAtStart ? 'The quick brown fox jumps over the lazy dog.' : '';

let opened = false;
let openedSide = null;

globalThis.window = {
  innerWidth: 390,                 // mobile width (< 768 mobile gate)
  _chipDragging: false,
  getSelection: () => ({
    isCollapsed: selectionText.length === 0,
    toString: () => selectionText,
  }),
  _odyOpenSidebar: (side) => {
    opened = true;
    openedSide = side;
    sidebarEl.classList.remove('hidden');
  },
};

globalThis.getComputedStyle = () => ({ display: 'block' });

globalThis.document = {
  readyState: 'complete',          // so the module auto-wires on import
  body: { classList: classList([]) },
  addEventListener: (type, handler) => {
    (listeners[type] = listeners[type] || []).push(handler);
  },
  getElementById: (id) => {
    if (id === 'sidebar') return sidebarEl;
    if (id === 'chat-container') return chatContainerEl;
    return null;
  },
  querySelectorAll: () => [],      // no open modals
};

// A touch that lands on a message body: inside #chat-container, but not inside
// any of the EXCLUDE regions the gesture already ignores.
const messageBody = {
  closest: (sel) => (sel === '#chat-container' ? chatContainerEl : null),
};

const source = fs.readFileSync('./static/js/sidebar-layout.js', 'utf8');
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
await import(moduleUrl);

const fire = (type, ev) => (listeners[type] || []).forEach((h) => h(ev));

fire('touchstart', {
  target: messageBody,
  touches: [{ clientX: S.startX, clientY: 300 }],
});

if (S.selectionAtMove) {
  selectionText = 'The quick brown fox jumps over the lazy dog.';
}

fire('touchmove', {
  cancelable: true,
  preventDefault() {},
  touches: [{ clientX: S.moveX, clientY: 300 }],
});

console.log(JSON.stringify({
  opened,
  openedSide,
  drawerHidden: sidebarEl.classList.contains('hidden'),
  selectionLen: (window.getSelection().toString() || '').length,
}));
"""


def _run_gesture(**scenario):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(_HARNESS), json.dumps(scenario)],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])


def test_drag_to_select_in_message_does_not_open_drawer(node_available):
    # The core bug: a horizontal drag that STARTS over a message body (to grow a
    # text selection) must not be captured as an edge-swipe.
    r = _run_gesture(startX=195, moveX=335, selectionAtStart=True, selectionAtMove=True)
    assert r["opened"] is False
    assert r["drawerHidden"] is True
    # The native selection is left untouched (the gesture never fought it).
    assert r["selectionLen"] > 0


def test_active_selection_blocks_even_an_edge_start_swipe(node_available):
    # Starting right at the left edge would normally arm the gesture, but a live
    # selection means the user is copying text — the selection guard must refuse.
    r = _run_gesture(startX=8, moveX=148, selectionAtStart=True, selectionAtMove=True)
    assert r["opened"] is False
    assert r["drawerHidden"] is True


def test_selection_growing_mid_drag_bails_out(node_available):
    # No selection at touchstart (so the swipe arms), but a selection appears
    # during the drag: the touchmove guard must bail before opening.
    r = _run_gesture(startX=8, moveX=148, selectionAtStart=False, selectionAtMove=True)
    assert r["opened"] is False
    assert r["drawerHidden"] is True


def test_genuine_left_edge_swipe_still_opens_drawer(node_available):
    # A real edge-swipe with no selection active must still open the drawer.
    r = _run_gesture(startX=8, moveX=160, selectionAtStart=False, selectionAtMove=False)
    assert r["opened"] is True
    assert r["drawerHidden"] is False
    assert r["openedSide"] == "left"


def test_genuine_right_edge_swipe_still_opens_drawer(node_available):
    # The mirror gesture from the right edge is preserved too (both sides arm).
    r = _run_gesture(startX=382, moveX=230, selectionAtStart=False, selectionAtMove=False)
    assert r["opened"] is True
    assert r["openedSide"] == "right"
