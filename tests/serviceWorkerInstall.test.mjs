// Executes the real static/sw.js install handler against fake browser APIs.
//
// Install must stay non-atomic (one bad URL cannot stop the worker from
// installing) yet must say which URLs were not cached, instead of dropping
// them silently.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/sw.js', import.meta.url), 'utf8');

async function runInstall({ failing }) {
  const listeners = {};
  const stored = new Map();
  const warnings = [];
  let skipped = false;
  const sandbox = {
    console: { warn: message => warnings.push(String(message)), log() {}, error() {} },
    self: {
      addEventListener: (name, listener) => { listeners[name] = listener; },
      skipWaiting: () => { skipped = true; },
      location: { origin: 'http://odysseus.test' },
      clients: { claim: async () => {} },
    },
    caches: {
      open: async () => ({ put: async (url, response) => { stored.set(url, response); } }),
      keys: async () => [],
      delete: async () => true,
    },
    fetch: async url => {
      const behavior = failing[url];
      if (behavior === 'network') throw new Error('offline');
      return { ok: behavior !== 404, status: behavior === 404 ? 404 : 200 };
    },
    URL, Response: class {}, Promise,
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const urls = vm.runInContext('[...PRECACHE, ...PANEL_PRECACHE]', sandbox);
  const cacheName = vm.runInContext('CACHE_NAME', sandbox);
  let installing;
  listeners.install({ waitUntil: promise => { installing = promise; } });
  await installing;
  return { urls, cacheName, stored, warnings, skipped };
}

test('install reports every URL that could not be cached and still completes', async () => {
  const probe = ['/static/js/settings/shell.js', '/static/js/settings/registry.js'];
  const { urls, cacheName, stored, warnings, skipped } = await runInstall({
    failing: { [probe[0]]: 404, [probe[1]]: 'network' },
  });
  assert.ok(urls.includes(probe[0]) && urls.includes(probe[1]));
  assert.equal(skipped, true);
  assert.equal(stored.size, new Set(urls).size - probe.length);
  assert.equal(warnings.length, 1);
  assert.ok(warnings[0].includes(cacheName));
  assert.ok(warnings[0].includes(`${probe[0]} (HTTP 404)`));
  assert.ok(warnings[0].includes(`${probe[1]} (offline)`));
});

test('a fully successful install stays quiet and caches every URL', async () => {
  const { urls, stored, warnings } = await runInstall({ failing: {} });
  assert.deepEqual([...stored.keys()].sort(), [...new Set(urls)].sort());
  assert.deepEqual(warnings, []);
});
