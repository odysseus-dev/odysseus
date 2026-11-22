import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNoteSession } from '../frontend/src/features/notes/application/noteSession.js';
import { createNoteVersions } from '../frontend/src/features/notes/application/noteVersions.js';
import { chipLabel } from '../frontend/src/features/notes/ui/noteWindow.js';

// The note window's edit session, driven with a fake API, clock and timers.
function harness({ note = { id: 'note1', title: 'Title', content: 'Original', body_revision: 'v1' }, patch, save, toggle, server = null } = {}) {
  const data = new Map(), timers = new Map(), calls = [], events = [];
  let nextTimer = 0, now = 1000;
  const storage = { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) };
  const persistence = createNotesPersistence({ storage });
  const view = { title: note?.title || '', content: note?.content || '' };
  let version = 1;
  const api = {
    patchNote: patch || (async (id, body) => { calls.push(['PUT', id, body]); version += 1; return { ...body, id, title: body.title ?? view.title, content: body.content ?? view.content, body_revision: `v${version}` }; }),
    saveNote: save || (async body => { calls.push(['POST', body]); return { ...body, id: 'created1', body_revision: 'c1' }; }),
    getNote: async id => { calls.push(['GET', id]); if (!server) throw new Error('offline'); return server; },
    toggleTask: toggle || (async (id, index, revision) => { calls.push(['TOGGLE', id, index, revision]); version += 1; return { ok: true, note: { id, title: view.title, content: view.content, body_revision: `v${version}` } }; }),
  };
  const session = createNoteSession({
    api, persistence, note,
    clock: { now: () => (now += 1) },
    scheduler: { setTimeout: f => { timers.set(++nextTimer, f); return nextTimer; }, clearTimeout: t => timers.delete(t) },
    collect: () => ({ ...view }),
    effects: { saved: n => events.push(['saved', n]), created: n => events.push(['created', n]), conflict: c => events.push(['conflict', c]), failed: e => events.push(['failed', e.message]), reloaded: n => events.push(['reloaded', n]) },
  });
  return {
    session, view, calls, events, data,
    type: (content, title) => { view.content = content; if (title !== undefined) view.title = title; session.markDirty(); },
    drain: async () => { for (const f of [...timers.values()]) f(); timers.clear(); for (let i = 0; i < 5; i++) await new Promise(setImmediate); },
    draft: id => JSON.parse(data.get('odysseus-note-draft-' + (id || 'note1')) || 'null'),
  };
}

test('opening and closing a note writes nothing', async () => {
  const h = harness();
  await h.session.close();
  await h.drain();
  assert.deepEqual(h.calls, []);
  assert.equal(h.draft(), null);
});

test('a blur or close without a real edit never saves, even if collect() differs', async () => {
  const h = harness();
  h.view.content = 'Normalised by the serializer';
  await h.session.flush();
  await h.session.close();
  assert.deepEqual(h.calls, []);
});

test('typing is backed up synchronously, before the autosave timer', () => {
  const h = harness();
  h.type('Just typed');
  assert.equal(h.draft().content, 'Just typed');
  assert.equal(h.draft().body_revision, 'v1');
  assert.deepEqual(h.calls, []);
});

test('one debounced PUT carries title, content and the base it was loaded at', async () => {
  const h = harness();
  h.type('a'); h.type('ab'); h.type('abc', 'New title');
  await h.drain();
  assert.deepEqual(h.calls, [['PUT', 'note1', { title: 'New title', content: 'abc', replace_content: true, body_revision: 'v1' }]]);
  assert.equal(h.session.base, 'v2');
  assert.equal(h.draft(), null, 'a saved draft is cleared');
  h.type('abcd');
  await h.drain();
  assert.equal(h.calls[1][2].body_revision, 'v2', 'the next save builds on the server copy it just wrote');
});

test('close flushes a pending edit immediately', async () => {
  const h = harness();
  h.type('quick then close');
  await h.session.close();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][2].content, 'quick then close');
});

test('failed network save keeps recoverable edits, including empty text', async () => {
  const h = harness({ patch: async () => { throw new Error('offline'); } });
  h.type('', '');
  await h.drain();
  assert.equal(h.draft().content, '');
  assert.equal(h.draft().title, '');
  assert.deepEqual(h.events, [['failed', 'offline']]);
  assert.equal(h.session.dirty, true, 'still dirty, so the next flush retries');
});

test('older save cannot clear newer draft and writes are serialized', async () => {
  const releases = [], writes = [];
  const h = harness({ patch: (id, body) => { writes.push(body.content); return new Promise(r => releases.push(() => r({ id, ...body, body_revision: `r${writes.length}` }))); } });
  h.type('First'); await h.drain();
  h.type('Second'); await h.drain();
  assert.deepEqual(writes, ['First']);
  releases.shift()(); await h.drain();
  assert.equal(h.draft().content, 'Second');
  assert.deepEqual(writes, ['First', 'Second']);
  releases.shift()(); await h.drain();
  assert.equal(h.draft(), null);
});

test('a 409 keeps the local text, reports the server copy and stops autosaving', async () => {
  const server = { id: 'note1', title: 'Title', content: 'Changed on the phone', body_revision: 'v9' };
  let fail = true;
  const sent = [];
  // The 409 carries no copy of the note: the session reads it.
  const h = harness({ server, patch: async (id, body) => {
    sent.push(body);
    if (fail) { const e = new Error('Failed to update note'); e.status = 409; throw e; }
    return { ...body, id, body_revision: 'v10' };
  } });
  h.type('Typed on the laptop');
  await h.drain();
  assert.equal(h.events[0][0], 'conflict');
  assert.deepEqual(h.events[0][1].server, server);
  assert.equal(h.draft().content, 'Typed on the laptop', 'the local text survives in draft storage');
  h.type('Still typing during the conflict');
  await h.drain();
  assert.equal(sent.length, 1, 'no further PUT until the conflict is resolved');
  assert.equal(h.draft().content, 'Still typing during the conflict');

  fail = false;
  await h.session.resolveConflict('keep');
  assert.equal(sent.length, 2);
  assert.deepEqual(sent[1], { title: 'Title', content: 'Still typing during the conflict', replace_content: true, body_revision: 'v9' });
  assert.equal(h.draft(), null);
});

test('reloading after a 409 adopts the server copy without writing', async () => {
  const server = { id: 'note1', title: 'Theirs', content: 'Server text', body_revision: 'v9' };
  const h = harness({ server, patch: async () => { const e = new Error('x'); e.status = 409; throw e; } });
  h.type('Mine');
  await h.drain();
  await h.session.resolveConflict('reload');
  assert.deepEqual(h.events.at(-1), ['reloaded', server]);
  assert.deepEqual(h.session.saved, { title: 'Theirs', content: 'Server text' });
  assert.equal(h.session.base, 'v9');
  assert.equal(h.draft(), null);
});

test('a new note closed untouched creates nothing; the first real edit POSTs once', async () => {
  const empty = harness({ note: null });
  empty.session.patchMeta({ color: 'blue' });
  await empty.session.close();
  assert.deepEqual(empty.calls, []);

  const h = harness({ note: null });
  await h.session.patchMeta({ color: 'green', label: 'home' });
  h.type('', '   ');
  await h.drain();
  assert.deepEqual(h.calls, [], 'whitespace is not a note');
  h.type('- [ ] \n  - [ ]', '');
  await h.drain();
  assert.deepEqual(h.calls, [], 'nor are empty checkboxes');
  h.type('Buy bread');
  await h.drain();
  assert.deepEqual(h.calls, [['POST', { color: 'green', label: 'home', title: '', content: 'Buy bread' }]]);
  assert.equal(h.session.id, 'created1');
  assert.equal(h.events[0][0], 'created');
  assert.equal(h.data.get('odysseus-note-draft-__new__'), undefined);
  h.type('Buy bread and milk');
  await h.drain();
  assert.deepEqual(h.calls[1], ['PUT', 'created1', { title: '', content: 'Buy bread and milk', replace_content: true, body_revision: 'c1' }]);
});

test('meta writes move the base only while the content is still ours', async () => {
  const h = harness();
  await h.session.patchMeta({ color: 'red' });
  assert.equal(h.session.base, 'v2');
  const other = harness({ patch: async (id, body) => ({ id, title: 'Title', content: 'Someone else wrote this', body_revision: 'v5', ...body }) });
  await other.session.patchMeta({ color: 'red' });
  assert.equal(other.session.base, 'v1', 'a changed body keeps the old base so the next content save conflicts');
});

test('colour, tag, reminder and archive patches carry no base; content saves do', async () => {
  const h = harness();
  h.type('Edited');
  await h.drain();
  for (const patch of [{ color: 'blue' }, { label: 'home' }, { due_date: '2026-10-28T09:00', repeat: 'none' }, { archived: true }]) await h.session.patchMeta(patch);
  assert.deepEqual(h.calls.map(call => call[2]), [
    { title: 'Title', content: 'Edited', replace_content: true, body_revision: 'v1' },
    { color: 'blue' }, { label: 'home' }, { due_date: '2026-10-28T09:00', repeat: 'none' }, { archived: true },
  ]);
  for (const [, , body] of h.calls.slice(1)) assert.equal('body_revision' in body, false);
});

test('a checkbox flip saves pending text first, then uses the toggle endpoint', async () => {
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', body_revision: 'v1' } });
  h.type('- [ ] a\n- [ ] b');
  const result = await h.session.toggleTask(() => 1);
  assert.deepEqual(h.calls.map(c => c[0]), ['PUT', 'TOGGLE']);
  assert.equal(h.calls[1][2], 1);
  assert.ok(result.ok);
  assert.equal(h.session.base, 'v3');

  const clean = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', body_revision: 'v1' } });
  await clean.session.toggleTask(() => 0);
  assert.deepEqual(clean.calls.map(c => c[0]), ['TOGGLE'], 'no whole-content PUT for a checkbox');
});

test('an earlier unsaved draft on this device is offered back, an identical one is dropped', () => {
  const h = harness();
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Unsaved from yesterday' }));
  assert.deepEqual(h.session.storedDraft(), { title: 'Title', content: 'Unsaved from yesterday', stale: false, recovered: [] });
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Original' }));
  assert.equal(h.session.storedDraft(), null);
  assert.equal(h.draft(), null);
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, note_type: 'todo', title: 'Title', items: [{ text: 'old form row', done: false }] }));
  assert.deepEqual(h.session.storedDraft(), { title: 'Title', content: '- [ ] old form row', stale: false, recovered: [] }, 'drafts left by the old form still restore');
});

test('the first write carries the one-off fields, later ones do not', async () => {
  const data = new Map(), calls = [];
  let timer = null;
  const session = (await import('../frontend/src/features/notes/application/noteSession.js')).createNoteSession({
    api: { patchNote: async (id, body) => { calls.push(body); return { id, ...body, body_revision: `v${calls.length + 1}` }; } },
    persistence: createNotesPersistence({ storage: { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) } }),
    clock: { now: () => 1 }, scheduler: { setTimeout: f => (timer = f), clearTimeout: () => {} },
    note: { id: 'n', title: 't', content: 'a', body_revision: 'v1' },
    collect: () => ({ title: 't', content: `edit ${calls.length}` }),
    firstWrite: () => ({ sort_order: -7 }),
  });
  session.markDirty(); await session.flush();
  session.markDirty(); await session.flush();
  assert.equal(calls[0].sort_order, -7);
  assert.equal('sort_order' in calls[1], false);
});

test('a checkbox flip whose pending text failed to save is left to the content path', async () => {
  const h = harness({ patch: async () => { throw new Error('offline'); } });
  h.type('- [ ] a typed');
  let asked = false;
  const result = await h.session.toggleTask(() => { asked = true; return 0; });
  assert.equal(result, null);
  assert.equal(asked, false, 'the index is never resolved against unsaved text');
  assert.deepEqual(h.calls, []);
});

test('a minimised note window is labelled with its title, 28 characters at most', () => {
  assert.equal(chipLabel('Weekend'), 'Note: Weekend');
  assert.equal(chipLabel('   '), 'Note: Untitled');
  assert.equal(chipLabel(null), 'Note: Untitled');
  const long = chipLabel('A very long title that goes past the limit');
  assert.equal(long, 'Note: A very long title tha…');
  assert.equal(long.length, 28);
});

test('a draft another tab left against another revision is kept for recovery, not overwritten', () => {
  const h = harness();
  const foreign = { _ts: 1, _draftWriter: 'another tab', title: 'Title', content: 'Typed in the other tab', body_revision: 'v0' };
  h.data.set('odysseus-note-draft-note1', JSON.stringify(foreign));
  h.type('Typed here');
  assert.equal(h.draft().content, 'Typed here');
  assert.deepEqual(JSON.parse(h.data.get('odysseus-note-recovery-note1')), [foreign]);
  const stored = h.session.storedDraft();
  assert.deepEqual(stored.recovered, [{ title: 'Title', content: 'Typed in the other tab' }]);
  h.session.discardDraft();
  assert.equal(h.data.get('odysseus-note-recovery-note1'), undefined, 'Discard clears the recovered drafts too');
});

test('reopening an edited note marks a draft typed against an older revision stale, and keeps it', () => {
  const h = harness({ note: { id: 'note1', title: 'Title', content: 'Server update', body_revision: 'newer' } });
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Local edits', body_revision: 'older' }));
  const stored = h.session.storedDraft();
  assert.equal(stored.stale, true, 'the window offers it to copy, never to restore over the newer note');
  assert.equal(stored.content, 'Local edits');
  assert.equal(h.draft().content, 'Local edits', 'still there until discarded');
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Local edits', body_revision: 'newer' }));
  assert.equal(h.session.storedDraft().stale, false, 'a draft typed against this revision restores as before');
});

test('a checkbox flip sends the revision the window saw', async () => {
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', body_revision: 'r1' } });
  await h.session.toggleTask(() => 0);
  assert.deepEqual(h.calls[0], ['TOGGLE', 'note1', 0, 'r1']);
});

test('checkbox flip rejected with 409 enters conflict state and offers reload and keep mine', async () => {
  const serverNote = { id: 'note1', title: 'Title', content: '- [x] a on server', body_revision: 'r2' };
  const h = harness({
    note: { id: 'note1', title: 'Title', content: '- [ ] a', body_revision: 'r1' },
    toggle: async () => {
      const err = new Error('conflict');
      err.status = 409;
      throw err;
    },
    server: serverNote,
  });

  const result = await h.session.toggleTask(() => 0);
  assert.equal(result, null);
  assert.ok(h.session.conflict, 'session entered conflict state');
  assert.deepEqual(h.session.conflict.server, serverNote);
  assert.equal(h.session.conflict.local.content, '- [ ] a');
  assert.ok(h.events.some(([event]) => event === 'conflict'), 'conflict effect was triggered');

  // Reload adopts server state
  await h.session.resolveConflict('reload');
  assert.equal(h.session.conflict, null);
  assert.equal(h.session.base, 'r2');
  assert.equal(h.session.saved.content, '- [x] a on server');
});

test('checkbox flip keep mine after 409 saves local text over server revision', async () => {
  const serverNote = { id: 'note1', title: 'Title', content: '- [x] a on server', body_revision: 'r2' };
  let patchedBody = null;
  const h = harness({
    note: { id: 'note1', title: 'Title', content: '- [ ] a', body_revision: 'r1' },
    toggle: async () => {
      const err = new Error('conflict');
      err.status = 409;
      throw err;
    },
    patch: async (id, body) => {
      patchedBody = body;
      return { id, ...body, body_revision: 'r3' };
    },
    server: serverNote,
  });

  await h.session.toggleTask(() => 0);
  assert.ok(h.session.conflict);

  await h.session.resolveConflict('keep');
  assert.equal(h.session.conflict, null);
  assert.equal(patchedBody.body_revision, 'r2');
  assert.equal(patchedBody.content, '- [ ] a');
  assert.equal(patchedBody.replace_content, true);
  assert.equal(h.session.base, 'r3');
});

test('checkbox flip network error throws and does not enter conflict state', async () => {
  const h = harness({
    note: { id: 'note1', title: 'T', content: '- [ ] a', body_revision: 'r1' },
    toggle: async () => {
      const err = new Error('network down');
      err.status = 500;
      throw err;
    },
  });

  await assert.rejects(async () => {
    await h.session.toggleTask(() => 0);
  }, /network down/);

  assert.equal(h.session.conflict, null, 'network error is not treated as conflict');
});

test('repeated toggles during conflict return null and do not issue duplicate requests', async () => {
  let toggleCount = 0;
  const serverNote = { id: 'note1', title: 'Title', content: '- [x] a on server', body_revision: 'r2' };
  const h = harness({
    note: { id: 'note1', title: 'Title', content: '- [ ] a', body_revision: 'r1' },
    toggle: async () => {
      toggleCount++;
      const err = new Error('conflict');
      err.status = 409;
      throw err;
    },
    server: serverNote,
  });

  await h.session.toggleTask(() => 0);
  assert.equal(toggleCount, 1);
  assert.ok(h.session.conflict);

  const second = await h.session.toggleTask(() => 0);
  assert.equal(second, null);
  assert.equal(toggleCount, 1, 'no second toggle was sent');
});

test('same-tab checklist update does not advance dirty session revision and triggers 409 conflict on save', async () => {
  const app = { state: { notes: [] } };
  const versions = createNoteVersions({ app });
  const serverNote = { id: 'note1', title: 'Title', content: '- [x] task 1\n- [ ] task 2', body_revision: 'r2' };

  let patchedBody = null;
  const rawApi = {
    patchNote: async (id, body) => {
      patchedBody = body;
      if (body.body_revision && body.body_revision !== serverNote.body_revision) {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      }
      return { ...body, id, body_revision: 'r3' };
    },
    getNote: async () => serverNote,
    toggleTask: async () => ({ ok: true, note: serverNote }),
  };
  const trackedApi = versions.track(rawApi);

  const h = harness({
    note: { id: 'note1', title: 'Title', content: '- [ ] task 1\n- [ ] task 2', body_revision: 'r1' },
    patch: trackedApi.patchNote,
    server: serverNote,
  });

  // User modifies note in editor
  h.type('- [ ] task 1\n- [ ] task 2 dirty local edit');
  assert.equal(h.session.dirty, true);
  assert.equal(h.session.base, 'r1');

  // Another same-tab component toggles a task
  await trackedApi.toggleTask('note1', 0);
  assert.equal(versions.isLocal('note1', 'r2'), true);

  // Sync check: because editor is dirty, base must not advance
  assert.equal(h.session.base, 'r1');

  // Next autosave/flush
  await h.session.flush();

  // Save must have targeted r1 and received 409 conflict, rather than overwriting r2
  assert.equal(patchedBody.body_revision, 'r1');
  assert.ok(h.session.conflict, 'session entered conflict state instead of silently overwriting');
  assert.equal(h.session.conflict.local.content, '- [ ] task 1\n- [ ] task 2 dirty local edit');
  assert.equal(h.session.conflict.server.content, '- [x] task 1\n- [ ] task 2');
});

test('same-tab update safely replaces clean session content and revision', () => {
  const h = harness({
    note: { id: 'note1', title: 'Title', content: '- [ ] task 1', body_revision: 'r1' },
  });
  assert.equal(h.session.dirty, false);
  assert.equal(h.session.base, 'r1');

  const updatedServerNote = { id: 'note1', title: 'Title', content: '- [x] task 1', body_revision: 'r2' };
  h.session.replace(updatedServerNote);

  assert.equal(h.session.base, 'r2');
  assert.equal(h.session.saved.content, '- [x] task 1');
  assert.equal(h.session.dirty, false);
});
