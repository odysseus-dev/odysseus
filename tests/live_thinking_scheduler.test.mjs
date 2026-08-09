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

import {
  createIncrementalDisplayProjector,
  createLiveThinkingThrottle,
  createThinkingAnalysisGate,
  stripLiveThinkingTags,
} from '../static/js/liveThinkingThrottle.js';

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

test('prepares a 200K cumulative stream only at scheduled commit cadence', () => {
  const timers = fakeTimers();
  const commits = [];
  let prepareCalls = 0;
  let scannedCharacters = 0;
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), {
    ...timers,
    prepare(value) {
      prepareCalls += 1;
      scannedCharacters += value.length;
      return stripLiveThinkingTags(value);
    },
  });

  const delta = 'reasoning '.repeat(10); // 100 characters
  let cumulative = '';
  for (let i = 0; i < 2000; i++) {
    cumulative += delta;
    throttle.update(cumulative);
  }

  assert.equal(cumulative.length, 200_000);
  assert.equal(prepareCalls, 0, 'cumulative extraction must not run per delta');
  assert.equal(timers.pendingIds().length, 1);
  timers.run(timers.pendingIds()[0]);
  assert.equal(prepareCalls, 1);
  assert.equal(scannedCharacters, 200_000);
  assert.deepEqual(commits, [cumulative]);
});

test('ordinary answers and reasoning deltas do not request cumulative analysis', () => {
  const startsReasoning = (text) => /^\s*thinking(?:\s+process)?\s*:/i.test(text);
  const ordinaryGate = createThinkingAnalysisGate({ startsWithReasoningPrefix: startsReasoning });
  let ordinary = '';
  let ordinaryAnalyses = 0;
  for (let i = 0; i < 2000; i++) {
    ordinary += i === 0 ? 'Here is the answer. ' : 'answer '.repeat(10);
    if (ordinaryGate.shouldAnalyze(ordinary)) ordinaryAnalyses += 1;
  }
  assert.equal(ordinaryAnalyses, 0);

  const thinkingGate = createThinkingAnalysisGate({ startsWithReasoningPrefix: startsReasoning });
  let thinking = 'Thin';
  assert.equal(thinkingGate.shouldAnalyze(thinking), false);
  thinking += 'king: inspect the problem';
  assert.equal(thinkingGate.shouldAnalyze(thinking), true);
  for (let i = 0; i < 2000; i++) {
    thinking += ' reasoning'.repeat(10);
    assert.equal(thinkingGate.shouldAnalyze(thinking, { isThinking: true, nonTagThinking: true }), false);
  }
  thinking += '\n\nHere is the answer';
  assert.equal(thinkingGate.shouldAnalyze(thinking, { isThinking: true, nonTagThinking: true }), true);

  const whitespaceGate = createThinkingAnalysisGate({ startsWithReasoningPrefix: startsReasoning });
  let whitespaceThinking = ' '.repeat(250);
  assert.equal(whitespaceGate.shouldAnalyze(whitespaceThinking), false);
  whitespaceThinking += 'Thinking: bounded probe';
  assert.equal(whitespaceGate.shouldAnalyze(whitespaceThinking), true);
});

test('split namespaced closes and false-close deadlines request analysis', () => {
  let clock = 100;
  const gate = createThinkingAnalysisGate({ now: () => clock });
  let text = '<mm:think>x</mm:';
  assert.equal(gate.shouldAnalyze(text, { isThinking: true }), true, 'fresh opening tag is analyzed');
  text += 'think>answer';
  assert.equal(gate.shouldAnalyze(text, { isThinking: true }), true, 'split namespaced close is analyzed');

  text += ' still waiting';
  assert.equal(gate.shouldAnalyze(text, { isThinking: true, recheckAt: 500 }), false);
  clock = 500;
  text += ' next delta';
  assert.equal(gate.shouldAnalyze(text, { isThinking: true, recheckAt: 500 }), true);

  const attributedGate = createThinkingAnalysisGate();
  let attributed = `<think data-provider="${'x'.repeat(400)}"`;
  assert.equal(attributedGate.shouldAnalyze(attributed), false);
  attributed += '>reasoning';
  assert.equal(attributedGate.shouldAnalyze(attributed), true, 'bounded carry preserves split tag attributes');
});

test('display projection is append-only and filters a structured tail once', () => {
  let filterCalls = 0;
  let filteredCharacters = 0;
  const projector = createIncrementalDisplayProjector((text) => {
    filterCalls += 1;
    filteredCharacters += text.length;
    return text.replace(/\[TOOL_CALL\][\s\S]*$/i, '');
  });

  let text = '';
  for (let i = 0; i < 2000; i++) {
    const delta = i === 0 ? 'Here is the answer. ' : 'ordinary text ';
    text += delta;
    assert.equal(projector.append(delta, text), text);
  }
  assert.equal(filterCalls, 0, 'ordinary deltas never run the cumulative filter');

  text += '[TOOL_';
  projector.append('[TOOL_', text);
  text += 'CALL]{"name":"read"}';
  const beforeToolPayload = projector.append('CALL]{"name":"read"}', text);
  for (let i = 0; i < 2000; i++) {
    const delta = 'payload ';
    text += delta;
    assert.equal(projector.append(delta, text), beforeToolPayload);
  }
  assert.equal(filterCalls, 1, 'structured payload filtering happens only at its boundary');
  assert.ok(filteredCharacters < text.length, 'filter work is bounded by the first structured boundary');
});

test('literal escaped tags survive and malformed live tags retain trailing text', () => {
  assert.equal(
    stripLiveThinkingTags('&lt;think&gt;literal&lt;/think&gt;'),
    '&lt;think&gt;literal&lt;/think&gt;',
  );
  assert.equal(
    stripLiveThinkingTags('<think>first</think> middle <thinking mode="deep">trailing'),
    'first middle trailing',
  );
  assert.equal(stripLiveThinkingTags('answer with 2 < 3 and 5 > 4'), 'answer with 2 < 3 and 5 > 4');
});

test('terminal flush prepares and commits the complete trailing cumulative text', () => {
  const timers = fakeTimers();
  const commits = [];
  const throttle = createLiveThinkingThrottle((value) => commits.push(value), {
    ...timers,
    prepare: stripLiveThinkingTags,
  });

  throttle.update('<think>reasoning without a closing tag');
  assert.equal(throttle.flush(), true);
  assert.deepEqual(commits, ['reasoning without a closing tag']);
  assert.deepEqual(timers.pendingIds(), []);
});

test('independent throttles cannot commit cancelled text into another session', () => {
  const timers = fakeTimers();
  const commits = [];
  const first = createLiveThinkingThrottle((value) => commits.push(['first', value]), timers);
  const second = createLiveThinkingThrottle((value) => commits.push(['second', value]), timers);

  first.update('stale first-session text');
  second.update('current second-session text');
  first.cancel();
  assert.equal(second.flush(), true);

  assert.deepEqual(timers.pendingIds(), []);
  assert.deepEqual(commits, [['second', 'current second-session text']]);
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
