"""Pin the self-termination contract of the canvas spinners in static/js/spinner.js.

Background: the whirlpool spinner drives itself with requestAnimationFrame and
decides whether to keep going by looking at `element.isConnected`. It used to
re-arm forever whenever the element had *never* been connected, on the theory
that start() runs before the caller appends the element. Callers that start a
spinner and then take an early return - an aborted request, a panel that
resolved from cache before the loading row was inserted - therefore left a rAF
loop redrawing an 84-segment spiral into a detached canvas until the tab closed.
Measured on an idle app: ~110 whirlpool frames per second with zero canvases in
the document.

These tests lock in all four exits (never attached, attached-then-removed,
stop(), tab hidden) and, just as importantly, the one case that must NOT stop:
a spinner that is actually on screen.

Driven through `node --input-type=module` so the real module runs, same idiom as
test_esc_menu_stack_js.py. The module source is inlined rather than imported by
path because the repo has no `"type": "module"` in package.json; spinner.js has
no imports of its own, so inlining is exact. A fake clock and a manual frame
pump replace performance.now()/requestAnimationFrame, so nothing here depends on
wall-clock time or real frame timing.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "spinner.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _MODULE.read_text(encoding="utf-8") if _MODULE.exists() else ""

# Browser stand-ins, installed before the module body runs. `clock` is advanced
# only by pump(), so every timing decision in the module is deterministic.
_STUBS = r"""
let clock = 0;
Object.defineProperty(globalThis, 'performance', {
  value: { now: () => clock }, configurable: true, writable: true,
});

const pending = new Map();
let nextFrameId = 1;
let framesRun = 0;
globalThis.requestAnimationFrame = (cb) => {
  const id = nextFrameId++;
  pending.set(id, cb);
  return id;
};
globalThis.cancelAnimationFrame = (id) => { pending.delete(id); };

/** Advance the clock `steps` frames of `msPerFrame` and run whatever is queued. */
function pump(steps, msPerFrame = 16) {
  for (let i = 0; i < steps; i++) {
    clock += msPerFrame;
    const due = [...pending.values()];
    pending.clear();
    for (const cb of due) { framesRun++; cb(); }
  }
}
function framesPending() { return pending.size; }
function framesSince(mark) { return framesRun - mark; }
function frameMark() { return framesRun; }

function makeCtx() {
  const noop = () => {};
  return {
    clearRect: noop, beginPath: noop, arc: noop, moveTo: noop, lineTo: noop,
    stroke: noop, fill: noop, save: noop, restore: noop,
    strokeStyle: '', fillStyle: '', lineWidth: 0, globalAlpha: 1,
    lineCap: '', lineJoin: '',
  };
}

function makeElement(tag) {
  const el = {
    tagName: tag, className: '', textContent: '', innerHTML: '',
    width: 0, height: 0, isConnected: false, parentNode: null,
    style: { cssText: '' },
    children: [],
    classList: { add: () => {}, remove: () => {}, contains: () => false },
    getContext: () => makeCtx(),
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      child.parentNode = null;
      return child;
    },
  };
  return el;
}

const docListeners = [];
globalThis.document = {
  hidden: false,
  documentElement: makeElement('html'),
  createElement: makeElement,
  createTextNode: (t) => ({ textContent: t }),
  addEventListener: (type, fn) => { docListeners.push([type, fn]); },
  removeEventListener: (type, fn) => {
    const i = docListeners.findIndex(([t, f]) => t === type && f === fn);
    if (i >= 0) docListeners.splice(i, 1);
  },
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });

function visibilityListeners() {
  return docListeners.filter(([t]) => t === 'visibilitychange').length;
}
function fireVisibility(hidden) {
  document.hidden = hidden;
  for (const [t, fn] of [...docListeners]) if (t === 'visibilitychange') fn();
}

/** A started whirlpool spinner whose element is not in the document. */
function startedWhirlpool() {
  const sp = new Spinner('', 'clean', 'whirlpool');
  sp.createElement();
  sp.start();
  return sp;
}
"""


def _run(body: str) -> dict:
    """Run `body` with the real spinner module and the browser stubs in scope."""
    js = _STUBS + "\n" + _SRC + "\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_never_attached_whirlpool_stops_itself():
    # The leak: element created, spinner started, element never inserted. Past
    # the grace window it must give up rather than re-arm forever.
    body = """
    const sp = startedWhirlpool();
    pump(30);                       // 480 ms - inside the grace window
    const early = { running: sp.isRunning, pending: framesPending() };
    pump(120);                      // ~2.4 s total - past the grace window
    const mark = frameMark();
    pump(60);                       // nothing should be left to run
    console.log(JSON.stringify({
      early,
      running: sp.isRunning,
      rafId: sp.rafId,
      pending: framesPending(),
      framesAfterStop: framesSince(mark),
    }));
    """
    out = _run(body)
    assert out["early"] == {"running": True, "pending": 1}, "gave up during the grace window"
    assert out["running"] is False
    assert out["rafId"] is None
    assert out["pending"] == 0
    assert out["framesAfterStop"] == 0, "loop kept drawing after it gave up"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_attached_whirlpool_keeps_running_past_the_grace_window():
    # The converse guard: the fix must not kill spinners that are on screen.
    body = """
    const sp = new Spinner('', 'clean', 'whirlpool');
    sp.createElement();
    sp.element.isConnected = true;
    sp.start();
    pump(400);                      // ~6.4 s, far past the grace window
    const mark = frameMark();
    pump(10);
    console.log(JSON.stringify({
      running: sp.isRunning,
      pending: framesPending(),
      framesDrawn: framesSince(mark),
    }));
    """
    out = _run(body)
    assert out["running"] is True
    assert out["pending"] == 1
    assert out["framesDrawn"] == 10, "a visible spinner stopped animating"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_attached_then_removed_whirlpool_stops():
    # The pre-existing exit - a loading row replaced by results - still works.
    body = """
    const sp = new Spinner('', 'clean', 'whirlpool');
    sp.createElement();
    sp.element.isConnected = true;
    sp.start();
    pump(200);
    const whileAttached = sp.isRunning;
    sp.element.isConnected = false; // results arrived, row swapped out
    pump(3);
    const mark = frameMark();
    pump(20);
    console.log(JSON.stringify({
      whileAttached,
      running: sp.isRunning,
      pending: framesPending(),
      framesAfterRemoval: framesSince(mark),
    }));
    """
    out = _run(body)
    assert out["whileAttached"] is True
    assert out["running"] is False
    assert out["pending"] == 0
    assert out["framesAfterRemoval"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_loading_row_helper_stops_when_the_row_is_never_inserted():
    # createLoadingRow() starts the spinner for the caller and hands back a
    # detached row, so a caller that early-returns is the real leak shape.
    body = """
    const row = createLoadingRow('Loading...', 16);
    pump(200);
    const mark = frameMark();
    pump(40);
    console.log(JSON.stringify({
      pending: framesPending(),
      framesAfterStop: framesSince(mark),
      rowHasChildren: row.children.length > 0,
    }));
    """
    out = _run(body)
    assert out["rowHasChildren"] is True, "harness built the wrong row"
    assert out["pending"] == 0
    assert out["framesAfterStop"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_stop_cancels_the_pending_frame_and_releases_the_listener():
    # stop() must be authoritative: no queued frame survives it, and it leaves
    # no visibilitychange listener behind on a dead spinner.
    body = """
    const before = visibilityListeners();
    const sp = new Spinner('', 'clean', 'whirlpool');
    sp.createElement();
    sp.element.isConnected = true;
    sp.start();
    const armed = visibilityListeners();
    sp.stop();
    const mark = frameMark();
    pump(20);
    console.log(JSON.stringify({
      before, armed, after: visibilityListeners(),
      running: sp.isRunning,
      rafId: sp.rafId,
      pending: framesPending(),
      framesAfterStop: framesSince(mark),
    }));
    """
    out = _run(body)
    assert (out["before"], out["armed"], out["after"]) == (0, 1, 0)
    assert out["running"] is False
    assert out["rafId"] is None
    assert out["pending"] == 0
    assert out["framesAfterStop"] == 0


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_hidden_tab_pauses_frames_and_showing_resumes_them():
    body = """
    const sp = new Spinner('', 'clean', 'whirlpool');
    sp.createElement();
    sp.element.isConnected = true;
    sp.start();
    pump(5);
    fireVisibility(true);
    const hiddenMark = frameMark();
    pump(30);
    const whileHidden = { drawn: framesSince(hiddenMark), pending: framesPending() };
    fireVisibility(false);
    const shownMark = frameMark();
    pump(10);
    console.log(JSON.stringify({
      whileHidden,
      running: sp.isRunning,
      drawnAfterShow: framesSince(shownMark),
    }));
    """
    out = _run(body)
    assert out["whileHidden"] == {"drawn": 0, "pending": 0}, "kept drawing in a hidden tab"
    assert out["running"] is True
    assert out["drawnAfterShow"] == 10, "did not resume when the tab came back"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_restarted_spinner_gets_a_fresh_grace_window():
    # The grace deadline is per-run. A spinner reused after stop() must not
    # inherit the previous run's timestamp and die on its first frame.
    body = """
    const sp = startedWhirlpool();
    pump(200);                      // times out, never attached
    const stopped = sp.isRunning;
    sp.element.isConnected = true;  // now inserted for real
    sp.start();
    pump(30);
    console.log(JSON.stringify({
      stopped,
      running: sp.isRunning,
      pending: framesPending(),
    }));
    """
    out = _run(body)
    assert out["stopped"] is False
    assert out["running"] is True
    assert out["pending"] == 1
