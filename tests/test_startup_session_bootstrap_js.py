"""Exercise sessions.js and startupShell.js together at the bootstrap seam.

The dependency-heavy session module is copied unchanged except for redirecting
its static imports to tiny browser stubs. The real loadSessions implementation
and the real startup-shell coordinator then run together under Node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_SESSIONS = _REPO / "static" / "js" / "sessions.js"
_SHELL_URL = (_REPO / "static" / "js" / "startupShell.js").as_uri()
_HAS_NODE = shutil.which("node") is not None

_IMPORT_REWRITES = {
    "import Storage from './storage.js';": "import Storage from './storage.mjs';",
    "import uiModule, { autoResize, styledPrompt } from './ui.js';": (
        "import uiModule, { autoResize, styledPrompt } from './ui.mjs';"
    ),
    "import chatRenderer from './chatRenderer.js?v=20260722ctxheader1';": (
        "import chatRenderer from './chatRenderer.mjs';"
    ),
    "import { providerLogo } from './providers.js';": (
        "import { providerLogo } from './providers.mjs';"
    ),
    "import { initModelPicker, updateModelPicker } from './modelPicker.js?v=20260722ctxheader1';": (
        "import { initModelPicker, updateModelPicker } from './modelPicker.mjs';"
    ),
    "import themeModule from './theme.js';": "import themeModule from './theme.mjs';",
    "import spinnerModule from './spinner.js';": "import spinnerModule from './spinner.mjs';",
}

_STUBS = {
    "storage.mjs": r"""
const Storage = {
  get: (key, fallback = null) => localStorage.getItem(key) ?? fallback,
  set: (key, value) => localStorage.setItem(key, value),
  remove: (key) => localStorage.removeItem(key),
  getJSON: (key, fallback) => {
    try { return JSON.parse(localStorage.getItem(key) ?? JSON.stringify(fallback)); }
    catch (_) { return fallback; }
  },
  setJSON: (key, value) => localStorage.setItem(key, JSON.stringify(value)),
};
export default Storage;
""",
    "ui.mjs": r"""
export const autoResize = () => {};
export const styledPrompt = async () => null;
const ui = {
  el: (id) => document.getElementById(id),
  showError: (message) => globalThis.__sessionErrors.push(String(message)),
  showToast: () => {},
  styledConfirm: async () => true,
};
export default ui;
""",
    "chatRenderer.mjs": (
        "export default { addMessage: () => null, hideWelcomeScreen: () => {} };\n"
    ),
    "providers.mjs": "export const providerLogo = () => '';\n",
    "modelPicker.mjs": (
        "export const initModelPicker = () => {};\n"
        "export const updateModelPicker = () => {};\n"
    ),
    "theme.mjs": "export default {};\n",
    "spinner.mjs": "export default {};\n",
}

_HARNESS = r"""
const SESSIONS_URL = 'SESSIONS_PATH';
const SHELL_URL = 'SHELL_PATH';

function makeStore() {
  const values = new Map();
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}

function makeClassList() {
  const values = new Set();
  return {
    add(...names) { names.forEach(name => values.add(name)); },
    remove(...names) { names.forEach(name => values.delete(name)); },
    contains(name) { return values.has(name); },
    toggle(name, force) {
      const enabled = force === undefined ? !values.has(name) : !!force;
      if (enabled) values.add(name); else values.delete(name);
      return enabled;
    },
  };
}

function makeWorld() {
  const byId = new Map();
  const frames = [];
  const cancelledFrames = new Set();
  const timers = [];
  let nextFrame = 1;
  let historyWrites = 0;

  function makeElement(id = '') {
    let html = '';
    const element = {
      id,
      dataset: {},
      style: {},
      classList: makeClassList(),
      children: [],
      status: null,
      value: '',
      disabled: false,
      removed: false,
      addEventListener() {},
      removeEventListener() {},
      setAttribute(name, value) { this[name] = value; },
      getAttribute(name) { return this[name] ?? null; },
      appendChild(child) { this.children.push(child); return child; },
      insertBefore(child) { this.children.unshift(child); return child; },
      contains() { return false; },
      closest() { return null; },
      querySelector(selector) {
        if (selector === '[data-session-list-status]') return this.status;
        return null;
      },
      querySelectorAll() { return []; },
      focus() { document.activeElement = this; },
      remove() { this.removed = true; if (this.id) byId.delete(this.id); },
    };
    Object.defineProperty(element, 'innerHTML', {
      get() { return html; },
      set(value) {
        html = String(value);
        if (id === 'session-list' && html === '') {
          const row = byId.get('session-list-loading');
          if (row) row.remove();
        }
      },
    });
    return element;
  }

  const document = {
    activeElement: null,
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => makeElement(tag),
    createDocumentFragment: () => makeElement('fragment'),
    addEventListener() {},
  };
  globalThis.document = document;
  globalThis.localStorage = makeStore();
  globalThis.sessionStorage = makeStore();
  Object.defineProperty(globalThis, 'navigator', {
    value: { platform: 'Linux' },
    configurable: true,
  });
  globalThis.history = { replaceState() { historyWrites += 1; } };
  globalThis.window = {
    document,
    innerWidth: 1024,
    innerHeight: 768,
    location: { origin: 'http://odysseus.test', hash: '', pathname: '/', href: '/' },
    addEventListener() {},
    removeEventListener() {},
    chatModule: {
      detachCurrentStream() {},
      showWelcomeScreen() {},
    },
    __odysseusDefaultChat: {
      endpoint_url: 'http://model.test',
      model: 'test/model',
      endpoint_id: 'endpoint-1',
    },
  };
  globalThis.location = window.location;
  globalThis.requestAnimationFrame = (fn) => {
    const id = nextFrame++;
    frames.push({ id, fn });
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => cancelledFrames.add(id);
  globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  globalThis.clearTimeout = () => {};
  globalThis.__sessionErrors = [];

  return {
    add(id, options = {}) {
      const element = makeElement(id);
      if (options.statusText !== undefined) {
        element.status = { textContent: options.statusText };
      }
      if (options.value !== undefined) element.value = options.value;
      byId.set(id, element);
      return element;
    },
    paint(rounds = 1) {
      for (let i = 0; i < rounds; i += 1) {
        const due = frames.splice(0, frames.length);
        for (const frame of due) {
          if (!cancelledFrames.has(frame.id)) frame.fn();
        }
      }
    },
    runTimers() {
      const due = timers.splice(0, timers.length);
      for (const timer of due) timer.fn();
    },
    byId,
    historyWrites: () => historyWrites,
    resetHistoryWrites: () => { historyWrites = 0; },
  };
}

const world = makeWorld();
world.add('session-list');
world.add('sessions-section');
const message = world.add('message', { value: 'draft before seed' });

const responses = [
  {
    ok: true,
    status: 200,
    json: async () => [{ id: 'existing', name: 'Existing', folder: 'Assistant', archived: false }],
  },
  {
    ok: false,
    status: 503,
    json: async () => ({ detail: 'temporarily unavailable' }),
  },
];
let fetchCount = 0;
globalThis.fetch = async () => {
  fetchCount += 1;
  const response = responses.shift();
  if (!response) throw new Error('unexpected fetch');
  return response;
};

const sessions = await import(SESSIONS_URL + '?bootstrap');
const shell = await import(SHELL_URL + '?bootstrap');

const seeded = await sessions.loadSessions();
world.paint(1);
localStorage.setItem('lastSessionId', 'existing');
message.value = 'draft must survive';
document.activeElement = null;
world.resetHistoryWrites();
const loader = world.add('app-loader');
const row = world.add('session-list-loading', { statusText: 'Loading chats…' });
let opened = 0;
shell.deferRouteOpener('/email', () => { opened += 1; });

const hydrated = await shell.settleSessionHydration(() => sessions.loadSessions());
const beforePaint = row.status.textContent;
world.paint(2);
world.runTimers();
const staleRouteRan = shell.runDeferredRouteOpener({ sessionsSettled: true });

const errorsBeforeAuth = __sessionErrors.length;
globalThis.fetch = async () => {
  fetchCount += 1;
  const response = { ok: false, status: 401, json: async () => ({ detail: 'expired' }) };
  window.location.href = '/login'; // app.js global fetch-wrapper behaviour
  return response;
};
const authResult = await sessions.loadSessions();

console.log(JSON.stringify({
  seeded,
  hydrated,
  beforePaint,
  afterPaint: row.status.textContent,
  rowStillPresent: world.byId.has('session-list-loading'),
  loaderRemoved: loader.removed,
  opened,
  staleRouteRan,
  fetchCount,
  sessionIds: sessions.getSessions().map(session => session.id),
  pendingChat: sessions.hasPendingChat(),
  draft: message.value,
  lastSessionId: localStorage.getItem('lastSessionId'),
  historyWrites: world.historyWrites(),
  errors: __sessionErrors,
  authResult,
  authRedirect: window.location.href,
  authAddedError: __sessionErrors.length !== errorsBeforeAuth,
}));
"""


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    if not _HAS_NODE:
        pytest.skip("node is not installed")

    module_dir = tmp_path_factory.mktemp("session-bootstrap-js")
    source = _SESSIONS.read_text(encoding="utf-8")
    for original, replacement in _IMPORT_REWRITES.items():
        assert original in source, f"sessions import changed: {original}"
        source = source.replace(original, replacement, 1)
    sessions_module = module_dir / "sessions.mjs"
    sessions_module.write_text(source, encoding="utf-8")
    for name, stub in _STUBS.items():
        (module_dir / name).write_text(stub, encoding="utf-8")

    harness = _HARNESS.replace("SESSIONS_PATH", sessions_module.as_uri()).replace(
        "SHELL_PATH", _SHELL_URL
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_fulfilled_503_is_not_applied_as_an_empty_session_list(results):
    assert results["seeded"] is True
    assert results["hydrated"] is False
    assert results["sessionIds"] == ["existing"]
    assert results["pendingChat"] is False, "failure created a default direct chat"
    assert results["draft"] == "draft must survive"
    assert results["lastSessionId"] == "existing"
    assert results["historyWrites"] == 0


def test_fulfilled_503_keeps_failure_state_and_route_deferred(results):
    assert results["beforePaint"] == "Loading chats…"
    assert results["afterPaint"] == "Chats unavailable"
    assert results["rowStillPresent"] is True
    assert results["loaderRemoved"] is True
    assert results["opened"] == 0
    assert results["staleRouteRan"] is False
    assert results["errors"] == [
        "Failed to load sessions: temporarily unavailable",
    ]


def test_401_keeps_global_auth_redirect_contract(results):
    assert results["authResult"] is False
    assert results["authRedirect"] == "/login"
    assert results["authAddedError"] is False
    assert results["sessionIds"] == ["existing"]
