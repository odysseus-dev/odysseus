// Programmatic control surface for the PhotoCraft editor.
//
// This module turns the editor's internal operations into a stable,
// JSON-friendly command API so an MCP server (or the in-editor natural
// language entrypoint) can drive the editor without poking at private
// closures. The heavy lifting lives in galleryEditor.js, which builds the
// handler map from its in-scope functions and installs it on
// window.geEditorControl.
//
// Contract:
//   control.invoke(op, params) -> Promise<{ ok, result } | { ok:false, error }>
//   control.run(steps)         -> Promise<Array<stepResult>>  (sequential)
//   control.list()             -> [{ op, description }]
//   control.describe(op)       -> { op, description } | null
//
// Every op name is namespaced: document.*, layers.*, selection.*,
// adjustments.*, filters.*, transform.*, text.*, history.*, export.*.

export const EDITOR_CONTROL_VERSION = '1.0';

function normalizeError(err) {
  if (!err) return 'Unknown error';
  if (typeof err === 'string') return err;
  return err.message || String(err);
}

export function createEditorControl({ handlers = {}, getState = null, version = EDITOR_CONTROL_VERSION } = {}) {
  const registry = new Map();
  for (const [op, entry] of Object.entries(handlers)) {
    if (!entry) continue;
    if (typeof entry === 'function') {
      registry.set(op, { run: entry, description: '' });
    } else if (typeof entry.run === 'function') {
      registry.set(op, { run: entry.run, description: entry.description || '' });
    }
  }

  function describe(op) {
    const entry = registry.get(op);
    if (!entry) return null;
    return { op, description: entry.description };
  }

  function list() {
    return Array.from(registry.keys())
      .sort()
      .map((op) => ({ op, description: registry.get(op).description }));
  }

  function snapshot() {
    if (typeof getState !== 'function') return {};
    try {
      return getState() || {};
    } catch (_err) {
      return {};
    }
  }

  async function invoke(op, params = {}) {
    const entry = registry.get(op);
    if (!entry) {
      return { ok: false, error: `Unknown editor op: ${op}`, op };
    }
    try {
      const result = await entry.run(params || {}, { state: snapshot(), control });
      return { ok: true, op, result: result === undefined ? null : result };
    } catch (err) {
      return { ok: false, op, error: normalizeError(err) };
    }
  }

  async function run(steps) {
    const list = Array.isArray(steps) ? steps : [];
    const results = [];
    for (const step of list) {
      const op = step && step.op;
      const params = (step && step.params) || {};
      // eslint-disable-next-line no-await-in-loop
      const res = await invoke(op, params);
      results.push(res);
      if (!res.ok) break;
    }
    return results;
  }

  const control = {
    version,
    installed: true,
    list,
    describe,
    invoke,
    run,
  };
  return control;
}

export default createEditorControl;
