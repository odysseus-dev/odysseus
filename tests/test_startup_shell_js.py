"""Pin the startup shell contract (static/js/startupShell.js).

Driven through `node --input-type=module` against a stub DOM and a manually
pumped frame/timer clock, so the real module runs without a browser (same
approach as test_composer_arrow_up_recall_js.py). Skips when `node` is absent.

Locks in the behaviour #5926 asks for: the shell is revealed one paint after
wiring and does not wait on /api/sessions; the loader node survives hydration
as a startup sentinel but is always retired once hydration settles; the sidebar
owns its own loading/failure row and a successful zero-session render never
shows a false failure; and a URL route opens only after the data it actually
needs is authoritatively available.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "startupShell.js"
_MODULE_URL = _MODULE.as_uri()
_HAS_NODE = shutil.which("node") is not None

_HARNESS = r"""
const MODULE_URL = 'MODULE_PATH';

// ── Stub DOM + a clock we pump by hand ────────────────────────────────────
function makeWorld() {
  const byId = new Map();
  const frames = [];
  const timers = [];
  const world = {
    byId,
    waveStops: 0,
    addElement(id, { statusText = null } = {}) {
      const el = {
        id,
        dataset: {},
        style: {},
        attrs: {},
        removed: false,
        status: null,
        setAttribute(k, v) { this.attrs[k] = v; },
        getAttribute(k) { return this.attrs[k]; },
        remove() { this.removed = true; byId.delete(this.id); },
        querySelector(sel) {
          return sel === '[data-session-list-status]' ? this.status : null;
        },
      };
      if (statusText !== null) el.status = { textContent: statusText };
      byId.set(id, el);
      return el;
    },
    // One "paint" = one round of already-queued rAF callbacks. afterNextPaint
    // chains two, so a committed paint takes two rounds.
    paint(rounds = 1) {
      for (let i = 0; i < rounds; i++) {
        const due = frames.splice(0, frames.length);
        for (const fn of due) fn();
      }
    },
    runTimers() {
      const due = timers.splice(0, timers.length);
      for (const t of due) t.fn();
    },
    pendingTimers() { return timers.length; },
  };
  globalThis.document = { getElementById: (id) => byId.get(id) || null };
  globalThis.window = { __odysseusLoaderWaveStop: () => { world.waveStops += 1; } };
  globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  return world;
}

// Fresh module instance per case so deferred-route state cannot leak.
let _instance = 0;
async function loadModule() {
  _instance += 1;
  return import(MODULE_URL + '?case=' + _instance);
}

function loaderSnapshot(loader) {
  return {
    revealed: loader.dataset.shellRevealed === 'true',
    opacity: loader.style.opacity ?? null,
    pointerEvents: loader.style.pointerEvents ?? null,
    ariaHidden: loader.getAttribute('aria-hidden') ?? null,
    removed: loader.removed,
  };
}

const cases = {};

cases.reveal_waits_one_paint_then_keeps_node = async () => {
  const w = makeWorld();
  const loader = w.addElement('app-loader');
  const shell = await loadModule();
  shell.revealApplicationShellAfterPaint();
  const beforePaint = loaderSnapshot(loader);
  w.paint(1);
  const afterOneFrame = loaderSnapshot(loader);
  w.paint(1);
  return {
    beforePaint,
    afterOneFrame,
    afterPaint: loaderSnapshot(loader),
    waveStops: w.waveStops,
    stillInDocument: w.byId.has('app-loader'),
  };
};

cases.reveal_is_idempotent = async () => {
  const w = makeWorld();
  const loader = w.addElement('app-loader');
  const shell = await loadModule();
  shell.revealApplicationShellAfterPaint();
  shell.revealApplicationShellAfterPaint();
  w.paint(2);
  shell.revealApplicationShellAfterPaint();
  w.paint(2);
  return { waveStops: w.waveStops, snapshot: loaderSnapshot(loader) };
};

cases.remove_retires_the_loader_node = async () => {
  const w = makeWorld();
  const loader = w.addElement('app-loader');
  const shell = await loadModule();
  shell.removeApplicationLoader();
  const beforeTimers = loaderSnapshot(loader);
  w.runTimers();
  return { beforeTimers, afterTimers: loaderSnapshot(loader) };
};

cases.failed_hydration_marks_sidebar_row = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const row = w.addElement('session-list-loading', { statusText: 'Loading chats…' });
  const shell = await loadModule();
  await shell.settleSessionHydration(() => Promise.reject(new Error('boom')));
  const beforePaint = row.status.textContent;
  w.paint(2);
  w.runTimers();
  return {
    beforePaint,
    afterPaint: row.status.textContent,
    loaderRemoved: !w.byId.has('app-loader'),
  };
};

// A successful load with zero sessions must not schedule a failure write.
cases.zero_session_success_shows_no_failure = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const row = w.addElement('session-list-loading', { statusText: 'Loading chats…' });
  const shell = await loadModule();
  await shell.settleSessionHydration(() => Promise.resolve(true));
  w.paint(1);
  row.remove();            // renderSessionList() clearing #session-list
  w.paint(1);
  return { statusText: row.status.textContent, rowRemoved: row.removed };
};

// The whole point is getting /api/sessions off the critical path, not later.
cases.hydration_starts_synchronously = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const shell = await loadModule();
  let started = false;
  const done = shell.settleSessionHydration(() => { started = true; return Promise.resolve(true); });
  const startedBeforeAwait = started;
  await done;
  return { startedBeforeAwait };
};

cases.synchronous_load_failure_still_settles = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const row = w.addElement('session-list-loading', { statusText: 'Loading chats…' });
  const shell = await loadModule();
  let opened = 0;
  shell.deferRouteOpener('/email', () => { opened += 1; });
  let threw = false;
  let succeeded = true;
  try {
    succeeded = await shell.settleSessionHydration(() => { throw new Error('module blew up'); });
  } catch (_) { threw = true; }
  w.paint(2);
  w.runTimers();
  return {
    threw,
    succeeded,
    opened,
    statusText: row.status.textContent,
    loaderRemoved: !w.byId.has('app-loader'),
    ranAfterFailure: shell.runDeferredRouteOpener({ sessionsSettled: true }),
  };
};

cases.route_without_session_data_opens_before_hydration = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const shell = await loadModule();
  let opened = 0;
  shell.deferRouteOpener('/notes', () => { opened += 1; });
  const ranEarly = shell.runDeferredRouteOpener();
  const openedAfterEarly = opened;
  const ranAgain = shell.runDeferredRouteOpener({ sessionsSettled: true });
  return { ranEarly, openedAfterEarly, ranAgain, opened };
};

cases.route_with_session_data_waits_for_hydration = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const shell = await loadModule();
  let opened = 0;
  shell.deferRouteOpener('/email', () => { opened += 1; });
  const ranEarly = shell.runDeferredRouteOpener();
  const openedAfterEarly = opened;
  const succeeded = await shell.settleSessionHydration(() => Promise.resolve(true));
  return {
    ranEarly,
    openedAfterEarly,
    openedAfterHydration: opened,
    succeeded,
    needsSessions: [shell.routeNeedsSessionData('/email'), shell.routeNeedsSessionData('/notes')],
  };
};

cases.missing_session_module_keeps_route_deferred = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const row = w.addElement('session-list-loading', { statusText: 'Loading chats…' });
  const shell = await loadModule();
  let opened = 0;
  shell.deferRouteOpener('/email', () => { opened += 1; });
  const succeeded = await shell.settleSessionHydration(null);
  w.paint(2);
  w.runTimers();
  return {
    opened,
    succeeded,
    statusText: row.status.textContent,
    loaderRemoved: !w.byId.has('app-loader'),
    ranAfterFailure: shell.runDeferredRouteOpener({ sessionsSettled: true }),
  };
};

cases.throwing_route_opener_is_contained = async () => {
  const w = makeWorld();
  w.addElement('app-loader');
  const shell = await loadModule();
  shell.deferRouteOpener('/notes', () => { throw new Error('opener blew up'); });
  let threw = false;
  let ran = false;
  try { ran = shell.runDeferredRouteOpener(); } catch (_) { threw = true; }
  return { threw, ran, ranAgain: shell.runDeferredRouteOpener({ sessionsSettled: true }) };
};

const out = {};
for (const [name, fn] of Object.entries(cases)) out[name] = await fn();
console.log(JSON.stringify(out));
""".replace("MODULE_PATH", _MODULE_URL)


@pytest.fixture(scope="module")
def results():
    if not _HAS_NODE:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _HARNESS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_module_exists():
    assert _MODULE.is_file(), f"missing {_MODULE}"


def test_shell_is_revealed_one_paint_after_wiring(results):
    r = results["reveal_waits_one_paint_then_keeps_node"]
    assert r["beforePaint"]["revealed"] is False, "revealed before any frame ran"
    assert r["afterOneFrame"]["revealed"] is False, "revealed before the paint committed"
    assert r["afterPaint"] == {
        "revealed": True,
        "opacity": "0",
        "pointerEvents": "none",
        "ariaHidden": "true",
        "removed": False,
    }
    assert r["waveStops"] == 1, "loader wave interval kept running after reveal"


def test_revealed_loader_stays_as_startup_sentinel(results):
    # sessions.js / sidebar-layout.js read #app-loader as "startup in progress".
    r = results["reveal_waits_one_paint_then_keeps_node"]
    assert r["stillInDocument"] is True
    assert r["afterPaint"]["removed"] is False


def test_reveal_is_idempotent(results):
    r = results["reveal_is_idempotent"]
    assert r["waveStops"] == 1, "reveal ran its side effects more than once"
    assert r["snapshot"]["revealed"] is True


def test_loader_node_is_retired_after_the_fade(results):
    r = results["remove_retires_the_loader_node"]
    assert r["beforeTimers"]["revealed"] is True, "removal should hide immediately"
    assert r["beforeTimers"]["removed"] is False, "removal should wait for the fade"
    assert r["afterTimers"]["removed"] is True, "loader node outlived hydration"


def test_failed_session_load_marks_the_sidebar_row(results):
    r = results["failed_hydration_marks_sidebar_row"]
    assert r["beforePaint"] == "Loading chats…", "failure written before the render frame"
    assert r["afterPaint"] == "Chats unavailable"
    assert r["loaderRemoved"] is True, "a failed load must still free the shell"


def test_zero_session_success_never_shows_a_failure(results):
    r = results["zero_session_success_shows_no_failure"]
    assert r["rowRemoved"] is True
    assert r["statusText"] == "Loading chats…", "false 'Chats unavailable' on empty success"


def test_hydration_request_starts_synchronously(results):
    r = results["hydration_starts_synchronously"]
    assert r["startedBeforeAwait"] is True, "/api/sessions start was deferred a microtask"


def test_synchronous_load_failure_still_settles(results):
    r = results["synchronous_load_failure_still_settles"]
    assert r["threw"] is False, "a throwing loadSessions must not escape"
    assert r["succeeded"] is False
    assert r["opened"] == 0, "session-dependent route opened without session data"
    assert r["ranAfterFailure"] is False, "failed startup left a stale route opener"
    assert r["statusText"] == "Chats unavailable"
    assert r["loaderRemoved"] is True


def test_route_needing_no_session_data_opens_before_hydration(results):
    r = results["route_without_session_data_opens_before_hydration"]
    assert r["ranEarly"] is True, "/notes waited on /api/sessions it does not read"
    assert r["openedAfterEarly"] == 1
    assert r["ranAgain"] is False, "route opener fired twice"
    assert r["opened"] == 1


def test_route_needing_session_data_waits_for_hydration(results):
    r = results["route_with_session_data_waits_for_hydration"]
    assert r["ranEarly"] is False, "/email opened before the session list was there"
    assert r["openedAfterEarly"] == 0
    assert r["openedAfterHydration"] == 1
    assert r["succeeded"] is True
    assert r["needsSessions"] == [True, False]


def test_missing_session_module_still_settles_without_opening_data_route(results):
    r = results["missing_session_module_keeps_route_deferred"]
    assert r["succeeded"] is False
    assert r["opened"] == 0, "route opened without the session module it depends on"
    assert r["ranAfterFailure"] is False, "missing module left a stale route opener"
    assert r["statusText"] == "Chats unavailable"
    assert r["loaderRemoved"] is True


def test_throwing_route_opener_is_contained(results):
    r = results["throwing_route_opener_is_contained"]
    assert r["threw"] is False
    assert r["ran"] is True
    assert r["ranAgain"] is False, "a failed opener must not be retried"
