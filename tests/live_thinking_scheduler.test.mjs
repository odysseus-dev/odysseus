// Tests for the live-thinking throttle that bounds DOM work during long
// reasoning streams (see static/js/liveThinkingThrottle.js).
//
// The throttle's contract is what the terminal paths in chat.js lean on:
// a burst of deltas becomes ONE commit carrying the latest text; flush()
// lands trailing text synchronously and cannot double-commit; cancel()
// guarantees nothing lands after a stream is finished or backgrounded.
//
// Timers are injected, so this runs with no DOM and no real clock.
import assert from 'node:assert/strict';
import test from 'node:test';

import { createLiveThinkingThrottle } from '../static/js/liveThinkingThrottle.js';

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
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), timers);

  throttle.update('a');
  throttle.update('ab');
  throttle.update('abc');

  assert.deepEqual(commits, []);
  assert.deepEqual(timers.delays, [100], 'a burst must schedule exactly one commit');
  const [timer] = timers.pendingIds();
  timers.run(timer);
  assert.deepEqual(commits, ['abc']);
});

test('commit count stays flat as the stream grows', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), timers);

  // 500 deltas arriving inside one window is the regression this guards:
  // the old code committed once per delta, so work grew with stream length.
  let text = '';
  for (let i = 0; i < 500; i++) {
    text += 'token ';
    throttle.update(text);
  }
  assert.deepEqual(commits, []);
  assert.equal(timers.pendingIds().length, 1);
  timers.run(timers.pendingIds()[0]);
  assert.equal(commits.length, 1);
  assert.equal(commits[0], text);
});

test('flush synchronously preserves trailing text and cancels the pending callback', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), timers);

  throttle.update('trailing text');
  assert.equal(throttle.flush(), true);
  assert.deepEqual(commits, ['trailing text']);
  assert.deepEqual(timers.pendingIds(), []);
  assert.equal(throttle.flush(), false, 'clean flush must not duplicate the commit');
});

test('cancel discards pending work without a late DOM commit', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), timers);

  throttle.update('stale session text');
  throttle.cancel();
  assert.deepEqual(timers.pendingIds(), []);
  assert.deepEqual(commits, []);
});

test('a cancelled throttle accepts new work again', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), timers);

  throttle.update('discarded');
  throttle.cancel();
  throttle.update('fresh');
  assert.equal(throttle.flush(), true);
  assert.deepEqual(commits, ['fresh']);
});

test('coerces nullish updates instead of committing undefined', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), timers);

  throttle.update(null);
  throttle.flush();
  assert.deepEqual(commits, ['']);
});
