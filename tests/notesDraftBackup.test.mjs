import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNoteSession, DRAFT_BACKUP_INTERVAL_MS, AUTOSAVE_DELAY_MS } from '../frontend/src/features/notes/application/noteSession.js';

// Each backup serializes the whole note (collect() reads the editor), which
// on a long note cost more than a frame per keystroke. The first edit is
// backed up at once; a burst of edits is backed up once more when it ends.
function harness({ patch } = {}) {
  const data = new Map(), timers = new Map();
  let nextTimer = 0, now = 0, collects = 0;
  const view = { title: 'Title', content: 'Original' };
  const session = createNoteSession({
    api: { patchNote: patch || (async (id, body) => ({ id, ...body, updated_at: 'v2' })), saveNote: async b => b, toggleTask: async () => null },
    persistence: createNotesPersistence({ storage: { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) } }),
    note: { id: 'note1', title: 'Title', content: 'Original', updated_at: 'v1' },
    clock: { now: () => now },
    scheduler: {
      setTimeout: (f, ms) => { timers.set(++nextTimer, { f, at: now + ms }); return nextTimer; },
      clearTimeout: t => timers.delete(t),
    },
    collect: () => { collects += 1; return { ...view }; },
  });
  // Advance the fake clock, firing due timers in order.
  const advance = async ms => {
    const end = now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]);
      now = due[1].at;
      due[1].f();
      for (let i = 0; i < 3; i++) await new Promise(setImmediate);
    }
    now = end;
  };
  return {
    session, view, data, advance,
    collects: () => collects,
    type: text => { view.content = text; session.markDirty(); },
    draft: () => JSON.parse(data.get('odysseus-note-draft-note1') || 'null'),
  };
}

test('a burst of keystrokes is backed up twice, not once per key', async () => {
  const h = harness();
  h.type('a');
  assert.equal(h.draft().content, 'a', 'the first edit is backed up at once');
  for (const text of ['ab', 'abc', 'abcd', 'abcde', 'abcdef']) { await h.advance(20); h.type(text); }
  assert.equal(h.collects(), 1, 'the keys that follow are not serialized one by one');
  await h.advance(DRAFT_BACKUP_INTERVAL_MS);
  assert.equal(h.draft().content, 'abcdef', 'the end of the burst is backed up');
  assert.equal(h.collects(), 2);
  await h.advance(AUTOSAVE_DELAY_MS);
  assert.equal(h.draft(), null, 'the save then clears the draft');
});

test('a flush, a close or the page going away writes the pending backup first', async () => {
  const h = harness({ patch: async () => { throw new Error('offline'); } });
  h.type('one');
  await h.advance(10);
  h.type('one two');
  assert.equal(h.draft().content, 'one');
  h.session.flushBackup();
  assert.equal(h.draft().content, 'one two', 'pagehide keeps the last edit');
  h.type('one two three');
  await h.session.close();
  assert.equal(h.draft().content, 'one two three', 'a failed save on close keeps the draft');
});

test('nothing is backed up after the note is abandoned', async () => {
  const h = harness();
  h.type('x');
  await h.advance(10);
  h.type('xy');
  h.session.abandon();
  await h.advance(DRAFT_BACKUP_INTERVAL_MS * 2);
  assert.equal(h.draft(), null);
});
