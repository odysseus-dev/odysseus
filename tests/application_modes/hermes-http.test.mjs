// Run: node --test tests/application_modes/hermes-http.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { getJson, sharedJson, nextDelay, HttpError } from '../../static/js/hermes-http.js';

const reply = (status, body, type = 'application/json', extra = {}) => ({
  status, ok: status >= 200 && status < 300, redirected: false, url: '', headers: { get: () => type },
  json: async () => { if (body === undefined) throw new SyntaxError('no json'); return body; }, ...extra,
});
const stub = handler => { const calls = []; globalThis.fetch = async (url, opts) => { calls.push(url); return handler(calls.length, opts); }; return calls; };

test('transient failures are retried a bounded number of times, then reported', async () => {
  const calls = stub(() => reply(503, { detail: 'starting' }));
  await assert.rejects(getJson('/x', { retries: 2, backoff: 1 }), e => e.kind === 'unavailable' && e.retryable);
  assert.equal(calls.length, 3);
});

test('recovers when the service comes back during the retries', async () => {
  const calls = stub(n => n < 3 ? reply(502, undefined, 'text/plain') : reply(200, { state: 'ready' }));
  assert.deepEqual(await getJson('/x', { retries: 2, backoff: 1 }), { state: 'ready' });
  assert.equal(calls.length, 3);
});

test('authentication failures are distinct and never retried', async () => {
  for (const status of [401, 403]) {
    const calls = stub(() => reply(status, { detail: 'no' }));
    await assert.rejects(getJson('/x', { retries: 2, backoff: 1 }), e => e.kind === 'unauthorized' && !e.retryable);
    assert.equal(calls.length, 1);
  }
});

test('an HTML reply is never a success', async () => {
  const calls = stub(() => reply(200, undefined, 'text/html'));
  await assert.rejects(getJson('/x', { retries: 1, backoff: 1 }), e => e instanceof HttpError && e.kind === 'invalid');
  assert.equal(calls.length, 2);
});

test('a client error is not retried', async () => {
  const calls = stub(() => reply(404, { detail: 'nope' }));
  await assert.rejects(getJson('/x', { retries: 2, backoff: 1 }), e => e.kind === 'http' && e.message === 'nope');
  assert.equal(calls.length, 1);
});

test('a slow reply is reported as a timeout with its limit', async () => {
  stub((n, opts) => new Promise((_, reject) => opts.signal.addEventListener('abort', () => reject(opts.signal.reason))));
  await assert.rejects(getJson('/x', { timeout: 20, retries: 0 }), e => e.kind === 'timeout' && /within 0 seconds|within \d+ seconds/.test(e.message));
});

test('identical concurrent reads share one request; a caller abort leaves the others intact', async () => {
  const calls = stub(() => new Promise(resolve => setTimeout(() => resolve(reply(200, { ok: 1 })), 30)));
  const controller = new AbortController();
  const a = sharedJson('k', '/x', { retries: 0 });
  const b = sharedJson('k', '/x', { retries: 0, signal: controller.signal });
  controller.abort();
  await assert.rejects(b);
  assert.deepEqual(await a, { ok: 1 });
  assert.equal(calls.length, 1);
});

test('the check interval backs off while failing and is capped', () => {
  assert.equal(nextDelay(0), 15000);
  assert.deepEqual([1, 2, 3, 4, 5, 9].map(n => nextDelay(n)), [3000, 6000, 12000, 24000, 48000, 60000]);
});
