import assert from 'node:assert/strict';
import test from 'node:test';

import { downloadBlob } from '../../frontend/src/shared/platform/downloads.js';
import { debounceTrailing } from '../../frontend/src/shared/platform/debounce.js';

function trackTimers(t) {
  const timers = [];
  t.mock.method(globalThis, 'setTimeout', (fn, ms) => {
    timers.push({ fn, ms });
    return timers.length;
  });
  t.mock.method(globalThis, 'clearTimeout', id => {
    if (timers[id - 1]) timers[id - 1].fn = null;
  });
  return timers;
}

for (const failed of [false, true]) {
  test(`download detaches its anchor and releases the URL after a ${failed ? 'failed' : 'successful'} click`, t => {
    const timers = trackTimers(t);
    const order = [];
    const error = new Error('download unavailable');
    const anchor = {
      click() {
        order.push(['click', this.href, this.download]);
        if (failed) throw error;
      },
      remove() { order.push(['remove']); },
    };
    t.mock.method(URL, 'createObjectURL', () => 'blob:test');
    t.mock.method(URL, 'revokeObjectURL', url => order.push(['revoke', url]));
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: {
        body: { appendChild: el => order.push(['append', el]) },
        createElement: tag => { assert.equal(tag, 'a'); return anchor; },
      },
    });
    t.after(() => { delete globalThis.document; });

    if (failed) assert.throws(() => downloadBlob(new Blob(), 'file.txt', 1500), err => err === error);
    else downloadBlob(new Blob(), 'file.txt');
    assert.deepEqual(order, [['append', anchor], ['click', 'blob:test', 'file.txt'], ['remove']]);
    assert.equal(timers[0].ms, failed ? 1500 : 1000);
    timers[0].fn();
    assert.deepEqual(order.at(-1), ['revoke', 'blob:test']);
  });
}

test('trailing debounce retains the latest arguments and receiver and cancels pending work', t => {
  const timers = trackTimers(t);
  const calls = [];
  const first = { run: debounceTrailing(function (value) { calls.push([this, value]); }, '25') };
  const second = { run: first.run };
  first.run('old');
  second.run('new');
  assert.equal(timers[0].fn, null);
  assert.equal(timers[1].ms, '25');
  timers[1].fn();
  assert.deepEqual(calls, [[second, 'new']]);
  first.run('cancelled');
  first.run.cancel();
  first.run.cancel();
  assert.equal(timers[2].fn, null);
});
