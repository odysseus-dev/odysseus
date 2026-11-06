"""Drive ``static/js/fileHandler.js`` under node with a stubbed browser.

``fileHandler.js`` owns chat attachment uploads, but it imports ``ui.js`` and
``spinner.js`` (both DOM entry modules), so it cannot be imported straight from
node. This helper copies the shipped file verbatim into a temp dir and rewrites
*only* those two import specifiers to stubs; the real
``static/js/uploadFeedback.js`` stays in the graph so the reason round-trips
through the shipping code.

Why a copy instead of a source-text assertion: the #6235 review rejected a test
that asserted on source text ("the new test checks only that the server detail
is used in the first path"). The rules that matter here — one failure produces
one message, the reason survives — are behaviour, so they are tested by calling
``uploadPending()`` and watching the toast channel.

Used by ``tests/test_upload_failure_notification.py`` (#6235) and
``tests/test_upload_error_surfaced.py`` (#1346). Same idiom as
``tests/test_app_config_shared_fetch_js.py``: ``node --input-type=module`` with
every browser global stubbed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS_DIR = REPO / "static" / "js"
FILE_HANDLER = JS_DIR / "fileHandler.js"
FEEDBACK = JS_DIR / "uploadFeedback.js"

HAS_NODE = shutil.which("node") is not None

# Browser stand-ins. The attachment-strip toast is the only notification
# channel the module may still use on its own, so every call is recorded: a
# failure path that both records a reason AND toasts re-creates the duplicate
# this suite exists to prevent.
DOM_STUBS = r"""
const toasts = [];
globalThis.__toasts = toasts;

function _el() {
  const node = {
    children: [], textContent: '', title: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: {},
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { node.children.push(c); return c; },
    removeChild(c) { node.children = node.children.filter((x) => x !== c); return c; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    setAttribute() {}, getAttribute() { return null; }, focus() {}, remove() {},
    get firstChild() { return node.children.length ? node.children[0] : null; },
  };
  return node;
}

const strip = _el();
globalThis.document = {
  getElementById: (id) => (id === 'attach-strip' ? strip : null),
  createElement: () => _el(),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: { appendChild() {} },
};
globalThis.window = {
  showToast: (msg) => { toasts.push(String(msg)); },
  matchMedia: () => ({ matches: false }),
  dispatchEvent() {},
  addEventListener() {},
  innerWidth: 1400,
  _updateSendBtnIcon() {},
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
// Node's native FormData rejects non-Blob values; pending entries here are
// plain file-shaped objects, so record the appended fields instead.
globalThis.__formData = [];
globalThis.FormData = class {
  append(key, value, name) { globalThis.__formData.push([key, name]); }
};
globalThis.URL.createObjectURL = () => 'blob:stub';
globalThis.URL.revokeObjectURL = () => {};
globalThis.CustomEvent = class {
  constructor(type, init) { this.type = type; this.detail = init && init.detail; }
};

let responses = [];
globalThis.__queue = (fn) => { responses.push(fn); };
globalThis.fetch = (url, opts) => {
  const next = responses.shift();
  if (!next) throw new Error('unexpected fetch');
  return next(opts);
};
globalThis.__ok = (value) => () => Promise.resolve({
  ok: true, status: 200, json: () => Promise.resolve(value),
});
globalThis.__failResponse = (status, value) => () => Promise.resolve({
  ok: false, status, json: () => Promise.resolve(value),
});
globalThis.__brokenJson = (status) => () => Promise.resolve({
  ok: false, status, json: () => Promise.reject(new Error('not json')),
});
globalThis.__networkError = (msg) => () => Promise.reject(new Error(msg));
// A request that never settles until its AbortSignal fires — what cancelling an
// in-flight upload looks like to the module.
globalThis.__hang = () => (opts) => new Promise((_, reject) => {
  const signal = opts && opts.signal;
  const abort = () => {
    const err = new Error('aborted');
    err.name = 'AbortError';
    reject(err);
  };
  if (signal && signal.aborted) abort();
  else if (signal) signal.addEventListener('abort', abort);
});
"""


def module_copy(tmp: Path) -> Path:
    """``fileHandler.js`` with its two browser-only imports pointed at stubs."""
    source = FILE_HANDLER.read_text(encoding="utf-8")
    (tmp / "uiStub.js").write_text(
        "export default { esc: (s) => String(s) };\n", encoding="utf-8"
    )
    (tmp / "spinnerStub.js").write_text(
        "export default { create: () => ({"
        " createElement: () => ({}), start() {}, stop() {} }) };\n",
        encoding="utf-8",
    )
    source = source.replace(
        "from './ui.js?v=20260916largetoolscroll1'", "from './uiStub.js'"
    ).replace("from './spinner.js'", "from './spinnerStub.js'")
    source = source.replace(
        "from './uploadFeedback.js'", f"from '{FEEDBACK.as_posix()}'"
    )
    target = tmp / "fileHandler.copy.js"
    target.write_text(source, encoding="utf-8")
    return target


def run_script(body: str) -> dict:
    """Run *body* with the real module bound as ``fh``; return its JSON line."""
    with tempfile.TemporaryDirectory() as tmpdir:
        module = module_copy(Path(tmpdir))
        js = DOM_STUBS + f"\nconst fh = await import('{module.as_posix()}');\n" + body
        proc = subprocess.run(
            ["node", "--input-type=module"],
            input=js, capture_output=True, text=True, encoding="utf-8",
            cwd=str(REPO), timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])


def run_plain_script(body: str) -> dict:
    """Run *body* as a plain node ESM program (no fileHandler stubs)."""
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=body, capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


ATTACH_ONE_VIDEO = (
    "await fh.addFiles([{ name: 'clip.mp4', size: 29360128, type: 'video/mp4' }]);\n"
)
