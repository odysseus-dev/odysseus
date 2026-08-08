import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const start = source.indexOf('/* LIVE_THINKING_THROTTLE_START */');
const end = source.indexOf('/* LIVE_THINKING_THROTTLE_END */');
assert.ok(start >= 0 && end > start, 'live-thinking throttle markers must exist');
const helperSource = source.slice(start, end) + '\nglobalThis.createThrottle = _createLiveThinkingThrottle;';
const sandbox = {};
vm.runInNewContext(helperSource, sandbox);
const createThrottle = sandbox.createThrottle;

function fakeTimers() {
  let nextId = 1;
  const callbacks = new Map();
  const delays = [];
  return {
    schedule(callback, delay) {
      const id = nextId++;
      callbacks.set(id, callback);
      delays.push(delay);
      return id;
    },
    cancel(id) {
      callbacks.delete(id);
    },
    run(id) {
      const callback = callbacks.get(id);
      assert.ok(callback, `missing timer ${id}`);
      callbacks.delete(id);
      callback();
    },
    pendingIds() {
      return [...callbacks.keys()];
    },
    delays,
  };
}

test('coalesces a burst and commits only the latest text after 100 ms', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createThrottle((value) => commits.push(value), timers);

  throttle.update('a');
  throttle.update('ab');
  throttle.update('abc');

  assert.deepEqual(commits, []);
  assert.deepEqual(timers.delays, [100]);
  const [timer] = timers.pendingIds();
  timers.run(timer);
  assert.deepEqual(commits, ['abc']);
});

test('flush synchronously preserves trailing text and cancels the pending callback', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createThrottle((value) => commits.push(value), timers);

  throttle.update('trailing text');
  assert.equal(throttle.flush(), true);
  assert.deepEqual(commits, ['trailing text']);
  assert.deepEqual(timers.pendingIds(), []);
  assert.equal(throttle.flush(), false, 'clean flush must not duplicate the commit');
});

test('cancel discards pending work without a late DOM commit', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createThrottle((value) => commits.push(value), timers);

  throttle.update('stale session text');
  throttle.cancel();
  assert.deepEqual(timers.pendingIds(), []);
  assert.deepEqual(commits, []);
});
