// Behavioral scenarios for the Settings scope modules, printed as JSON for
// tests/test_settings_scope_js.py.

import { register } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

register('./settings_scope_loader.mjs', import.meta.url);

const JS = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../static/js');
const load = relative => import(pathToFileURL(path.join(JS, relative)).href);

const { createModelDefaultsStore, modelDefaultSaveError, MODEL_DEFAULT_KEYS } = await load('settings/modelDefaults.js');
const { describeSectionScope } = await load('settings/scope.js');
const { settingsAccessFromStatus } = await load('authStatus.js');

const results = {};

function harness({ prefsStatus = 200, settingsStatus = 200, prefs = {}, settings = {}, delays = {} } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || 'GET';
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    const delay = delays[`${method} ${url}`];
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    if (url === '/api/prefs') {
      const status = typeof prefsStatus === 'function' ? prefsStatus(method) : prefsStatus;
      return { ok: status < 400, status, json: async () => prefs };
    }
    if (url === '/api/auth/settings') {
      return { ok: settingsStatus < 400, status: settingsStatus, json: async () => settings };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };
  globalThis.__postSettings = async patch => {
    calls.push({ url: '/api/auth/settings', method: 'POST', body: patch });
    if (settingsStatus >= 400) {
      const error = new Error('Failed to save');
      error.status = settingsStatus;
      throw error;
    }
    return { ok: true, status: 200 };
  };
  return calls;
}

// Personal store: reads prefs, writes one PATCH, empty values become null.
{
  const calls = harness({ prefs: { default_endpoint_id: 'ep-a', default_model: 'm', theme: 'dark', default_model_fallbacks: [] } });
  const store = createModelDefaultsStore({ personal: true, clearOwnOverrides: true });
  const loaded = await store.load();
  await store.save({ default_endpoint_id: '', default_model: '', utility_model_fallbacks: [] });
  results.personal = {
    personal: store.personal,
    loaded,
    calls: calls.map(call => `${call.method} ${call.url}`),
    patch: calls[1].body,
  };
}

// Server store: POSTs settings, then clears the caller's own overrides.
{
  const calls = harness({ settings: { default_endpoint_id: 'ep-g', default_model: 'g', brave_api_key: 'secret' } });
  const store = createModelDefaultsStore({ personal: false, clearOwnOverrides: true });
  const loaded = await store.load();
  await store.save({ default_endpoint_id: 'ep-b', default_model: 'b' });
  results.server = {
    loaded,
    calls: calls.map(call => `${call.method} ${call.url}`),
    posted: calls[1].body,
    cleared: calls[2].body,
  };
}

// Auth-disabled operator: server write only, no preference clearing.
{
  const calls = harness();
  const store = createModelDefaultsStore({ personal: false, clearOwnOverrides: false });
  await store.save({ research_endpoint_id: 'ep', research_model: 'm' });
  results.operator = calls.map(call => `${call.method} ${call.url}`);
}

// Clearing failure after a successful server save is reported as such.
{
  const calls = harness({ prefsStatus: method => (method === 'PATCH' ? 500 : 200) });
  const store = createModelDefaultsStore({ personal: false, clearOwnOverrides: true });
  let error = null;
  try { await store.save({ default_model: 'x' }); } catch (caught) { error = caught; }
  results.clearFailure = {
    serverSaved: error && error.serverSaved === true,
    message: modelDefaultSaveError(error),
    posted: calls.some(call => call.method === 'POST'),
  };
}

// Failures: personal 500 and server 403 reject with the status; no partial
// server write is attempted after a refused POST.
{
  harness({ prefsStatus: 500 });
  const personal = createModelDefaultsStore({ personal: true });
  let personalError = null;
  try { await personal.save({ default_model: 'x' }); } catch (caught) { personalError = caught; }
  let loadError = null;
  try { await personal.load(); } catch (caught) { loadError = caught; }

  const calls = harness({ settingsStatus: 403 });
  const server = createModelDefaultsStore({ personal: false, clearOwnOverrides: true });
  let serverError = null;
  try { await server.save({ default_model: 'x' }); } catch (caught) { serverError = caught; }
  results.failures = {
    personalStatus: personalError && personalError.status,
    loadStatus: loadError && loadError.status,
    serverStatus: serverError && serverError.status,
    serverMessage: modelDefaultSaveError(serverError),
    genericMessage: modelDefaultSaveError(personalError),
    clearedAfterRefusal: calls.some(call => call.method === 'PATCH'),
  };
}

// Unknown keys are refused before any request.
{
  const calls = harness();
  const store = createModelDefaultsStore({ personal: true });
  let error = null;
  try { await store.save({ foreground_fallback_enabled: true }); } catch (caught) { error = caught; }
  results.unknownKey = { refused: Boolean(error), requests: calls.length };
}

// Saves are serialized in call order, and a failed save does not block the next.
{
  let attempt = 0;
  const calls = harness({
    delays: { 'PATCH /api/prefs': 20 },
    prefsStatus: method => {
      if (method !== 'PATCH') return 200;
      attempt += 1;
      return attempt === 1 ? 500 : 200;
    },
  });
  const store = createModelDefaultsStore({ personal: true });
  const first = store.save({ default_model: 'first' }).then(() => 'ok', () => 'failed');
  const second = store.save({ default_model: 'second' }).then(() => 'ok', () => 'failed');
  results.queue = {
    outcomes: [await first, await second],
    order: calls.filter(call => call.method === 'PATCH').map(call => call.body.values.default_model),
  };
}

results.keys = MODEL_DEFAULT_KEYS;

const accessCases = {
  admin: { auth_enabled: true, can_manage_settings: true, is_admin: true },
  member: { auth_enabled: true, can_manage_settings: false, is_admin: false },
  operator: { auth_enabled: false, can_manage_settings: true, is_admin: false },
  legacyAdmin: { is_admin: true },
  legacyMember: { is_admin: false },
  empty: {},
  invalid: null,
};
results.access = Object.fromEntries(Object.entries(accessCases).map(([name, status]) => [name, { ...settingsAccessFromStatus(status) }]));

const scopeCases = {};
for (const [name, access] of Object.entries(results.access)) {
  scopeCases[name] = {
    modelDefault: describeSectionScope('model-default', access),
    server: describeSectionScope('server', access),
  };
}
scopeCases.unknownAccess = { modelDefault: describeSectionScope('model-default', null), server: describeSectionScope('server', null) };
scopeCases.noScope = describeSectionScope('', results.access.member);
results.scope = scopeCases;

console.log(JSON.stringify(results));
