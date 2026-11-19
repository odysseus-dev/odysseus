// Module hooks for tests/helpers/settings_scope_runner.mjs.
//
// settings/api.js imports ui.js, which needs a browser DOM. The model-defaults
// store only needs its postSettings() contract, so the test replaces that one
// module with a stub that forwards to globalThis.__postSettings.

const STUB_URL = 'odysseus-test:settings-api';

export async function resolve(specifier, context, nextResolve) {
  if (specifier === './api.js' && String(context.parentURL || '').endsWith('/static/js/settings/modelDefaults.js')) {
    return { url: STUB_URL, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url === STUB_URL) {
    return {
      format: 'module',
      shortCircuit: true,
      source: 'export function postSettings(body) { return globalThis.__postSettings(body); }',
    };
  }
  return nextLoad(url, context);
}
