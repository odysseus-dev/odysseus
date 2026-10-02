// Tests for the sequential uploaded-audio transcription queue
// (see static/js/sttTranscribeQueue.js).
//
// The queue is DOM-free, so it runs under node:test with injected async
// fakes — no browser, no timers, no network.
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  TRANSCRIBE_CONCURRENCY,
  createSttQueue,
  destinationFor,
} from '../static/js/sttTranscribeQueue.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test('concurrency is exactly 1', () => {
  assert.equal(TRANSCRIBE_CONCURRENCY, 1);
});

test('destination: mic goes to composer, uploads go to documents', () => {
  assert.equal(destinationFor('mic'), 'composer');
  assert.equal(destinationFor('upload'), 'document');
  assert.equal(destinationFor('anything-else'), 'document');
  assert.equal(destinationFor(undefined), 'document');
});

test('jobs run sequentially in FIFO order with max one active', async () => {
  const q = createSttQueue();
  const order = [];
  let active = 0;
  let maxActive = 0;
  const gates = [deferred(), deferred(), deferred()];

  const mkJob = (key, i) => ({
    key,
    transcribe: async () => {
      active++;
      maxActive = Math.max(maxActive, active);
      order.push('start-' + key);
      await gates[i].promise;
      active--;
      order.push('done-' + key);
      return { text: 'text-' + key, language: '' };
    },
    saveDoc: async ({ text }) => ({ id: 'doc-' + key, title: text + '.md' }),
    onState: () => {},
  });

  q.enqueue(mkJob('a', 0));
  q.enqueue(mkJob('b', 1));
  q.enqueue(mkJob('c', 2));
  // Let the first job start; the rest must wait.
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(order, ['start-a']);
  assert.equal(q.activeCount(), 1);
  assert.equal(q.queueSize(), 2);

  gates[0].resolve();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(order, ['start-a', 'done-a', 'start-b']);

  gates[1].resolve();
  gates[2].resolve();
  // Drain.
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.deepEqual(order, ['start-a', 'done-a', 'start-b', 'done-b', 'start-c', 'done-c']);
  assert.equal(maxActive, 1);
});

test('one failure does not abort remaining items', async () => {
  const q = createSttQueue();
  const seen = [];
  const mkJob = (key, fail) => ({
    key,
    transcribe: async () => {
      if (fail) throw new Error('boom-' + key);
      return { text: 'ok-' + key, language: '' };
    },
    saveDoc: async () => ({ id: 'doc-' + key, title: key }),
    onState: (s, info) => seen.push([s, key, info && info.error]),
  });
  q.enqueue(mkJob('a', false));
  q.enqueue(mkJob('b', true));
  q.enqueue(mkJob('c', false));
  for (let i = 0; i < 30 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(q.getState('a'), 'completed');
  assert.equal(q.getState('b'), 'failed');
  assert.equal(q.getState('c'), 'completed');
  const failed = seen.find(([s, k]) => s === 'failed' && k === 'b');
  assert.match(failed[2], /boom-b/);
});

test('duplicate enqueue is prevented; explicit retry runs again', async () => {
  const q = createSttQueue();
  let runs = 0;
  const gate = deferred();
  const mkJob = () => ({
    key: 'same',
    transcribe: async () => { runs++; await gate.promise; return { text: 't', language: '' }; },
    saveDoc: async () => ({ id: 'doc-1', title: 't' }),
    onState: () => {},
  });
  assert.equal(q.enqueue(mkJob()), 'queued');
  assert.equal(q.enqueue(mkJob()), 'duplicate'); // while queued
  await new Promise((r) => setImmediate(r));
  assert.equal(q.enqueue(mkJob()), 'duplicate'); // while running
  gate.resolve();
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(runs, 1);
  assert.equal(q.enqueue(mkJob()), 'duplicate'); // completed, no retry flag
  assert.equal(q.enqueue(mkJob(), { retry: true }), 'queued'); // explicit retry
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(runs, 2);
});

test('newly added audio joins a running queue in order', async () => {
  const q = createSttQueue();
  const doneOrder = [];
  const gate = deferred();
  const mkJob = (key, wait) => ({
    key,
    transcribe: async () => {
      if (wait) await wait.promise;
      return { text: key, language: '' };
    },
    saveDoc: async () => {
      doneOrder.push(key);
      return { id: 'd-' + key, title: key };
    },
    onState: () => {},
  });
  q.enqueue(mkJob('first', gate));
  await new Promise((r) => setImmediate(r));
  q.enqueue(mkJob('late-joiner', null));
  gate.resolve();
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.deepEqual(doneOrder, ['first', 'late-joiner']);
});

test('document is saved before the next item starts, verbatim', async () => {
  const q = createSttQueue();
  const events = [];
  const mkJob = (key, text) => ({
    key,
    transcribe: async () => ({ text, language: 'sk' }),
    saveDoc: async ({ text: t, language }) => {
      events.push(['save', key, t, language]);
      return { id: 'doc-' + key, title: key + '_raw.md' };
    },
    onState: (s) => { if (s === 'transcribing') events.push(['start', key]); },
  });
  q.enqueueAll([mkJob('a', 'prvý text'), mkJob('b', 'druhý text')]);
  for (let i = 0; i < 30 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.deepEqual(events, [
    ['start', 'a'],
    ['save', 'a', 'prvý text', 'sk'],
    ['start', 'b'],
    ['save', 'b', 'druhý text', 'sk'],
  ]);
});

test('identical repeat run reuses the document instead of duplicating', async () => {
  const q = createSttQueue();
  let saves = 0;
  const mkJob = () => ({
    key: 'same-file',
    transcribe: async () => ({ text: 'same words', language: '' }),
    saveDoc: async () => { saves++; return { id: 'doc-1', title: 'x_raw.md' }; },
    onState: () => {},
  });
  q.enqueue(mkJob());
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(saves, 1);
  q.enqueue(mkJob(), { retry: true }); // explicit retry, identical text
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(saves, 1); // reused, no duplicate document
  const outcome = q.getOutcome('same-file');
  assert.equal(outcome.status, 'completed');
  assert.equal(outcome.reusedDoc, true);
  assert.equal(outcome.doc.id, 'doc-1');
});

test('enqueueAll preserves display order and reports counts', () => {
  const q = createSttQueue();
  const mkJob = (key) => ({
    key,
    transcribe: async () => ({ text: key, language: '' }),
    saveDoc: async () => ({ id: key, title: key }),
    onState: () => {},
  });
  const res = q.enqueueAll([mkJob('audio1'), mkJob('audio2'), mkJob('audio1')]);
  assert.deepEqual(res, { added: 2, skipped: 1 });
  assert.equal(q.queueSize() + q.activeCount(), 2);
});

test('queuePosition reports queued slots; isIdle tracks drain', async () => {
  const q = createSttQueue();
  assert.equal(q.isIdle(), true);
  const gate = deferred();
  q.enqueue({ key: 'a', transcribe: async () => { await gate.promise; return { text: 'a', language: '' }; }, saveDoc: async () => ({ id: 'd-a', title: 'a' }), onState: () => {} });
  await new Promise((r) => setImmediate(r));
  assert.equal(q.isIdle(), false);
  q.enqueue({ key: 'b', transcribe: async () => ({ text: 'b', language: '' }), saveDoc: async () => ({ id: 'd-b', title: 'b' }), onState: () => {} });
  assert.deepEqual(q.queuePosition('a'), { position: 1, total: 2 });
  assert.deepEqual(q.queuePosition('b'), { position: 1, total: 2 });
  assert.equal(q.queuePosition('missing'), null);
  assert.equal(q.getState('a'), 'transcribing');
  assert.equal(q.getState('b'), 'queued');
  gate.resolve();
  for (let i = 0; i < 30 && !q.isIdle(); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(q.isIdle(), true);
  assert.equal(q.getState('a'), 'completed');
  assert.equal(q.getState('b'), 'completed');
});

test('transcribing notification carries position and total', async () => {
  const q = createSttQueue();
  const seen = {};
  const gate = deferred();
  const mk = (key, wait) => ({
    key,
    transcribe: async () => { if (wait) await wait.promise; return { text: key, language: '' }; },
    saveDoc: async () => null,
    onState: (s, info) => { if (s === 'transcribing') seen[key] = [info.position, info.total]; },
  });
  q.enqueue(mk('x', gate));
  q.enqueue(mk('y', null));
  assert.deepEqual(seen['x'], [1, 1]); // x starts before y joins
  gate.resolve();
  for (let i = 0; i < 30 && !q.isIdle(); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.deepEqual(seen['y'], [2, 2]);
});

test('failed save surfaces transcript; queue still continues', async () => {
  const q = createSttQueue();
  const order = [];
  q.enqueue({ key: 's1', transcribe: async () => ({ text: 'hello', language: '' }), saveDoc: async () => { throw new Error('doc down'); }, onState: () => {} });
  q.enqueue({ key: 's2', transcribe: async () => ({ text: 'world', language: '' }), saveDoc: async () => { order.push('s2'); return { id: 'd2', title: 't' }; }, onState: () => {} });
  for (let i = 0; i < 30 && !q.isIdle(); i++) {
    await new Promise((r) => setImmediate(r));
  }
  assert.equal(q.getState('s1'), 'failed');
  assert.equal(q.getOutcome('s1').transcript, 'hello');
  assert.deepEqual(order, ['s2']);
  assert.equal(q.getState('s2'), 'completed');
});

test('a hung transcribe times out and the queue moves on', async () => {
  const q = createSttQueue({ transcribeTimeoutMs: 30, saveTimeoutMs: 30 });
  const order = [];
  q.enqueue({
    key: 'stuck',
    transcribe: () => new Promise(() => {}), // never settles
    saveDoc: async () => ({ id: 'never', title: 'never' }),
    onState: () => {},
  });
  q.enqueue({
    key: 'next',
    transcribe: async () => ({ text: 'ok', language: '' }),
    saveDoc: async () => { order.push('saved'); return { id: 'd', title: 't' }; },
    onState: () => {},
  });
  for (let i = 0; i < 100 && !q.isIdle(); i++) {
    await new Promise((r) => setTimeout(r, 10));
  }
  assert.equal(q.isIdle(), true);
  assert.equal(q.getState('stuck'), 'failed');
  assert.match(q.getOutcome('stuck').error, /timed out/);
  assert.deepEqual(order, ['saved']);
  assert.equal(q.getState('next'), 'completed');
});

test('a hung saveDoc times out but keeps the transcript for retry', async () => {
  const q = createSttQueue({ transcribeTimeoutMs: 30, saveTimeoutMs: 30 });
  q.enqueue({
    key: 'slow-save',
    transcribe: async () => ({ text: 'hello', language: '' }),
    saveDoc: () => new Promise(() => {}), // never settles
    onState: () => {},
  });
  for (let i = 0; i < 100 && !q.isIdle(); i++) {
    await new Promise((r) => setTimeout(r, 10));
  }
  assert.equal(q.getState('slow-save'), 'failed');
  assert.equal(q.getOutcome('slow-save').transcript, 'hello');
});

test('queued state carries position and total', async () => {
  const q = createSttQueue();
  const seen = [];
  const gate = deferred();
  const mkJob = (key, wait) => ({
    key,
    transcribe: async () => {
      if (wait) await wait.promise;
      return { text: key, language: '' };
    },
    saveDoc: async () => ({ id: key, title: key }),
    onState: (s, info) => { if (s === 'queued') seen.push([key, info.position, info.total]); },
  });
  q.enqueue(mkJob('one', gate));
  q.enqueue(mkJob('two', null));
  q.enqueue(mkJob('three', null));
  gate.resolve();
  for (let i = 0; i < 20 && (q.activeCount() || q.queueSize()); i++) {
    await new Promise((r) => setImmediate(r));
  }
  // 'three' joins while one is running and two waits ahead of it, so its
  // first position report is 2/3; positions then refresh as items drain.
  const threeFirst = seen.find(([k]) => k === 'three');
  assert.deepEqual(threeFirst, ['three', 2, 3]);
});
