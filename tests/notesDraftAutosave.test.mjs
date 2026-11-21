import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNoteSession, taskFlips } from '../frontend/src/features/notes/application/noteSession.js';
import { createNoteVersions } from '../frontend/src/features/notes/application/noteVersions.js';
import { chipLabel } from '../frontend/src/features/notes/ui/noteWindow.js';
import { sizeRefusal } from '../frontend/src/features/notes/domain/noteRules.js';

// The note window's edit session, driven with a fake API, clock and timers.
function harness({ note = { id: 'note1', title: 'Title', content: 'Original', updated_at: 'v1', body_revision: 'v1' }, patch, save, toggle, server = null } = {}) {
  const data = new Map(), timers = new Map(), calls = [], events = [];
  let nextTimer = 0, now = 1000;
  const storage = { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) };
  const persistence = createNotesPersistence({ storage });
  const view = { title: note?.title || '', content: note?.content || '' };
  let version = 1;
  const api = {
    patchNote: patch || (async (id, body) => { calls.push(['PUT', id, body]); version += 1; return { id, title: body.title ?? view.title, content: body.content ?? view.content, ...body, updated_at: `v${version}`, body_revision: `v${version}` }; }),
    saveNote: save || (async body => { calls.push(['POST', body]); return { ...body, id: 'created1', updated_at: 'c1', body_revision: 'c1' }; }),
    getNote: async id => { calls.push(['GET', id]); if (!server) throw new Error('offline'); return server; },
    toggleTask: toggle || (async (id, index, text, done, revision) => { calls.push(['TOGGLE', id, index, text, done, revision]); version += 1; return { ok: true, note: { id, title: view.title, content: view.content, updated_at: `v${version}`, body_revision: `v${version}` } }; }),
  };
  const session = createNoteSession({
    api, persistence, note,
    clock: { now: () => (now += 1) },
    scheduler: { setTimeout: f => { timers.set(++nextTimer, f); return nextTimer; }, clearTimeout: t => timers.delete(t) },
    collect: () => ({ ...view }),
    effects: { saved: n => events.push(['saved', n]), created: n => events.push(['created', n]), conflict: c => events.push(['conflict', c]), failed: e => events.push(['failed', e.message]), reloaded: n => events.push(['reloaded', n]), gone: () => events.push(['gone']), viewOnly: () => events.push(['viewOnly']), unsaved: held => events.push(['unsaved', held]) },
  });
  return {
    session, view, calls, events, data,
    type: (content, title) => { view.content = content; if (title !== undefined) view.title = title; session.markDirty(); },
    drain: async () => { for (const f of [...timers.values()]) f(); timers.clear(); for (let i = 0; i < 5; i++) await new Promise(setImmediate); },
    // A new note's draft is keyed by the window that wrote it (`__new__-<writer>`).
    draft: id => {
      const key = id === '__new__' ? [...data.keys()].find(k => k.startsWith('odysseus-note-draft-__new__')) : 'odysseus-note-draft-' + (id || 'note1');
      return JSON.parse((key && data.get(key)) || 'null');
    },
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
  assert.deepEqual(h.calls, [['PUT', 'note1', { title: 'New title', content: 'abc', replace_content: true, body_revision: 'v1', base_updated_at: 'v1' }]]);
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
  assert.deepEqual(sent[1], { title: 'Title', content: 'Still typing during the conflict', replace_content: true, body_revision: 'v9', base_updated_at: 'v1' });
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
  assert.equal(h.session.revision, 'v9');
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
  assert.equal(h.draft('__new__'), null, 'no new-note draft left behind');
  h.type('Buy bread and milk');
  await h.drain();
  assert.deepEqual(h.calls[1], ['PUT', 'created1', { title: '', content: 'Buy bread and milk', replace_content: true, body_revision: 'c1', base_updated_at: 'c1' }]);
});

// A to-do list is a note whose body the toolbar's Checklist turned into a
// task. Its empty first task is saved as shown, so the session never holds a
// text the server does not (a bug came from that split): a colour,
// tag or reminder picked next still moves the base, and the first task typed
// meets no conflict.
for (const [what, patchBody] of [['colour', { color: 'red' }], ['tag', { label: 'home' }], ['reminder', { due_date: '2026-11-20' }]]) {
  test(`a new note made a checklist takes a ${what} and then its first task without a conflict`, async () => {
    // A server that keeps what it is sent and refuses a text write on a stale base.
    const stored = { id: 'created1', title: '', content: '', updated_at: 'c1' };
    let version = 1;
    const calls = [];
    const write = body => { Object.assign(stored, body); delete stored.base_updated_at; stored.updated_at = `s${++version}`; return { ...stored }; };
    const h = harness({
      note: null,
      save: async body => { calls.push(['POST', body]); Object.assign(stored, body, { updated_at: 'c1' }); return { ...stored }; },
      patch: async (id, body) => {
        calls.push(['PUT', id, body]);
        if (body.base_updated_at && body.base_updated_at !== stored.updated_at) {
          throw Object.assign(new Error('conflict'), { status: 409, body: { detail: { note: { ...stored } } } });
        }
        return write(body);
      },
    });
    h.type('- [ ]', 'Groceries');
    await h.drain();
    assert.deepEqual(calls, [['POST', { title: 'Groceries', content: '- [ ]' }]], 'the note is saved as shown');
    await h.session.patchMeta(patchBody);
    assert.equal(h.session.base, stored.updated_at, 'the base follows the meta write');
    h.type('- [ ] milk');
    await h.drain();
    assert.deepEqual(h.events.filter(([kind]) => kind === 'conflict'), [], 'no "changed elsewhere" banner');
    assert.equal(stored.content, '- [ ] milk', 'the first task is saved');
    assert.equal(stored.title, 'Groceries');
    await h.session.close();
    assert.equal(calls.length, 3, 'closing writes nothing more');
  });
}

test('a new checklist with only its empty task and no title creates nothing', async () => {
  const h = harness({ note: null });
  h.type('- [ ]');
  await h.drain();
  await h.session.close();
  assert.deepEqual(h.calls, []);
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
    { title: 'Title', content: 'Edited', replace_content: true, body_revision: 'v1', base_updated_at: 'v1' },
    { color: 'blue' }, { label: 'home' }, { due_date: '2026-10-28T09:00', repeat: 'none' }, { archived: true },
  ]);
  for (const [, , body] of h.calls.slice(1)) assert.equal('body_revision' in body || 'base_updated_at' in body, false);
});

test('a checkbox flip saves pending text first, then uses the toggle endpoint', async () => {
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', body_revision: 'v1' } });
  h.type('- [ ] a\n- [ ] b');
  const result = await h.session.toggleTask(() => 1);
  assert.deepEqual(h.calls.map(c => c[0]), ['PUT', 'TOGGLE']);
  assert.equal(h.calls[1][2], 1);
  assert.equal(h.calls[1][3], 'b', 'the tick names the task the box showed');
  assert.equal(h.calls[1][4], true, 'and the state it is going to, not a flip');
  assert.ok(result.ok);
  assert.equal(h.session.base, 'v3');

  const clean = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', body_revision: 'v1' } });
  await clean.session.toggleTask(() => 0);
  assert.deepEqual(clean.calls.map(c => c[0]), ['TOGGLE'], 'no whole-content PUT for a checkbox');
});

test('a checkbox in the window asks the server for the state it is going to', async () => {
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [x] a\n- [ ] b', updated_at: 'v1' } });
  await h.session.toggleTask(() => 0);
  await h.session.toggleTask(() => 1);
  assert.deepEqual(h.calls.map(c => c.slice(2)), [[0, 'a', false, null], [1, 'b', true, 'v2']], 'and the body revision, the first answer\'s for the second tick');
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

test('a 404 marks the note gone: one attempt, no retries, no draft kept', async () => {
  const sent = [];
  const h = harness({ patch: async (id, body) => { sent.push(body); const e = new Error('Failed to update note'); e.status = 404; throw e; } });
  h.type('Typed after the owner was deleted');
  await h.drain();
  assert.deepEqual(h.events, [['gone']]);
  assert.equal(h.session.gone, true);
  assert.equal(h.session.dirty, false);
  assert.equal(h.draft(), null, 'no note will ever offer this draft back');
  h.type('Still typing');
  await h.drain();
  await h.session.close();
  assert.equal(sent.length, 1, 'nothing is retried once the note is gone');
  assert.equal(h.draft(), null);
  assert.deepEqual(h.events, [['gone']], 'reported once, never as a conflict or a failure');
});

test('a 404 on a checkbox flip marks the note gone too', async () => {
  const h = harness({ toggle: async () => { const e = new Error('Failed to toggle item'); e.status = 404; throw e; } });
  await assert.rejects(h.session.toggleTask(() => 0));
  assert.equal(h.session.gone, true);
  assert.deepEqual(h.events, [['gone']]);
  assert.equal(await h.session.toggleTask(() => 0), null, 'no further toggles are sent');
});

test('a 404 while creating a note is an ordinary failure, not gone', async () => {
  const h = harness({ note: null, save: async () => { const e = new Error('Failed to save note'); e.status = 404; throw e; } });
  h.type('New note text', 'New');
  await h.drain();
  assert.equal(h.session.gone, false);
  assert.equal(h.events[0][0], 'failed');
  assert.equal(h.draft('__new__').content, 'New note text');
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
  assert.deepEqual(h.calls[0], ['TOGGLE', 'note1', 0, 'a', true, 'r1']);
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
  assert.equal(h.session.revision, 'r2');
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
  assert.equal(h.session.revision, 'r3');
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
  assert.equal(h.session.revision, 'r1');

  // Another same-tab component toggles a task
  await trackedApi.toggleTask('note1', 0);
  assert.equal(versions.isLocal('note1', 'r2'), true);

  // Sync check: because editor is dirty, base must not advance
  assert.equal(h.session.revision, 'r1');

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
  assert.equal(h.session.revision, 'r1');

  const updatedServerNote = { id: 'note1', title: 'Title', content: '- [x] task 1', body_revision: 'r2' };
  h.session.replace(updatedServerNote);

  assert.equal(h.session.revision, 'r2');
  assert.equal(h.session.saved.content, '- [x] task 1');
  assert.equal(h.session.dirty, false);
});

// A refused tick (409) is the same conflict choice as a refused save: the
// server copy the 409 carries is offered with Reload or Keep mine, and the
// window is not changed under the person until they pick.
test('a checkbox flip refused because the note changed offers the server copy it carries', async () => {
  const server = { id: 'note1', title: 'T', content: '- [ ] new\n- [ ] a', updated_at: 'v9', body_revision: 'r9' };
  const toggle = async (...args) => { h.calls.push(['TOGGLE', ...args]); throw Object.assign(new Error('changed'), { status: 409, body: { detail: { message: 'changed', note: server } } }); };
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a', updated_at: 'v1', body_revision: 'r1' }, toggle });
  assert.equal(await h.session.toggleTask(() => 0), null);
  assert.deepEqual(h.calls.map(call => call[0]), ['TOGGLE'], 'the carried copy is used: no second read');
  assert.deepEqual(h.session.conflict.server, server);
  assert.equal(h.session.base, 'v1', 'nothing moves until the person chooses');
  await h.session.resolveConflict('reload');
  assert.equal(h.session.saved.content, '- [ ] new\n- [ ] a');
  assert.equal(h.session.base, 'v9');
  assert.equal(h.session.revision, 'r9');
});

test('text typed while a refused tick was out is kept, and offered against the newer copy as a conflict', async () => {
  const server = { id: 'note1', title: 'T', content: '- [ ] new\n- [ ] a', updated_at: 'v9', body_revision: 'r9' };
  let typeNow;
  const toggle = async () => { typeNow(); throw Object.assign(new Error('changed'), { status: 409, body: { detail: { note: server } } }); };
  const patch = async (id, body) => { h.calls.push(['PUT', id, body]); return { id, ...body, updated_at: 'v10', body_revision: 'r10' }; };
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a\n\ntail', updated_at: 'v1', body_revision: 'r1' }, toggle, patch });
  typeNow = () => h.type('- [x] a\n\ntail typed');
  assert.equal(await h.session.toggleTask(() => 0), null);
  assert.equal(h.session.dirty, true);
  assert.equal(h.session.base, 'v1', 'the base stays on the copy the typing started from');
  assert.equal(h.session.conflict.local.content, '- [x] a\n\ntail typed');
  assert.equal(h.draft().content, '- [x] a\n\ntail typed');
  await h.drain();
  assert.deepEqual(h.calls, [], 'nothing is written until the person chooses');
  assert.equal(h.events.filter(([kind]) => kind === 'conflict').length, 1, 'Keep mine or Reload is offered');
  await h.session.resolveConflict('keep');
  assert.equal(h.calls.at(-1)[2].content, '- [x] a\n\ntail typed');
  assert.equal(h.calls.at(-1)[2].base_updated_at, 'v9', 'Keep mine writes over the newer copy knowingly');
  assert.equal(h.calls.at(-1)[2].body_revision, 'r9');
});

test('text typed while a tick was out is saved on top of the tick', async () => {
  let typeNow;
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a\n\ntail', updated_at: 'v1' }, toggle: async (id) => {
    typeNow();
    return { ok: true, note: { id, title: 'T', content: '- [x] a\n\ntail', updated_at: 'v2' } };
  } });
  typeNow = () => h.type('- [x] a\n\ntail typed');
  await h.session.toggleTask(() => 0);
  assert.equal(h.session.dirty, true);
  assert.equal(h.session.saved.content, '- [x] a\n\ntail');
  await h.drain();
  assert.deepEqual(h.calls.at(-1), ['PUT', 'note1', { title: 'T', content: '- [x] a\n\ntail typed', replace_content: true, base_updated_at: 'v2' }]);
});

test('text typed while a tick was out does not write over a collaborator edit the tick answer carries', async () => {
  // The collaborator appended a paragraph this window has not received; the
  // tick answer carries it. The typing was done on the copy without it.
  const server = { id: 'note1', title: 'T', content: '- [x] a\n\ntail\n\nfrom bob', updated_at: 'v3' };
  let typeNow;
  const patch = async (id, body) => { h.calls.push(['PUT', id, body]); throw Object.assign(new Error('stale'), { status: 409, body: { detail: { note: server } } }); };
  const h = harness({ note: { id: 'note1', title: 'T', content: '- [ ] a\n\ntail', updated_at: 'v1' }, patch, toggle: async () => {
    typeNow();
    return { ok: true, note: server };
  } });
  typeNow = () => h.type('- [x] a\n\ntail typed');
  await h.session.toggleTask(() => 0);
  assert.equal(h.session.dirty, true);
  assert.equal(h.session.base, 'v1', 'the base stays on the copy the typing started from');
  await h.drain();
  assert.equal(h.calls.at(-1)[2].base_updated_at, 'v1');
  assert.equal(h.events.filter(([kind]) => kind === 'conflict').length, 1, 'Keep mine or Reload is offered');
});

test('a damaged or empty stored draft is ignored, and a draft without a title keeps the saved one', () => {
  const h = harness();
  for (const stored of ['{not json', 'null', '"text"', JSON.stringify({ _ts: 1, title: 'Title' })]) {
    h.data.set('odysseus-note-draft-note1', stored);
    assert.equal(h.session.storedDraft(), null, stored);
  }
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, content: 'Typed offline' }));
  assert.deepEqual(h.session.storedDraft(), { title: 'Title', content: 'Typed offline', stale: false, recovered: [] });
});

test('an edit typed back to what was saved writes nothing and drops its draft', async () => {
  const h = harness();
  h.type('Original, then more');
  assert.ok(h.draft(), 'the edit is backed up');
  h.type('Original');
  await h.drain();
  assert.deepEqual(h.calls, []);
  assert.equal(h.draft(), null);
  assert.equal(h.session.dirty, false);
});

test('a full or blocked localStorage is reported once per backup and the save still goes out', async () => {
  const calls = [];
  let unavailable = 0;
  const persistence = createNotesPersistence({ storage: { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); }, removeItem() {} } });
  const view = { title: 'Title', content: 'Original' };
  const timers = [];
  const session = createNoteSession({
    api: { patchNote: async (id, body) => { calls.push(body); return { id, ...body, updated_at: 'v2' }; } },
    persistence, note: { id: 'note1', ...view, updated_at: 'v1' },
    clock: { now: () => 1 },
    scheduler: { setTimeout: f => timers.push(f), clearTimeout() {} },
    collect: () => ({ ...view }),
    effects: { backupUnavailable: () => { unavailable += 1; } },
  });
  view.content = 'Typed with a full disk';
  session.markDirty();
  await session.flush();
  assert.ok(unavailable >= 1);
  assert.deepEqual(calls.map(c => c.content), ['Typed with a full disk']);
});

test('a 409 without the server copy still stops autosave; Keep mine then writes over it', async () => {
  let fail = true;
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    if (fail) { fail = false; throw Object.assign(new Error('conflict'), { status: 409, body: {} }); }
    return { id, ...body, updated_at: 'v3' };
  } });
  h.type('Mine');
  await h.drain();
  assert.deepEqual(h.session.conflict, { local: { title: 'Title', content: 'Mine' }, server: null });
  await h.session.resolveConflict('keep');
  // The 409 carried no copy: the session tried to read the note (offline here) each time.
  const puts = h.calls.filter(call => call[0] === 'PUT');
  assert.equal(puts.length, 2);
  assert.equal(puts[1][2].base_updated_at, 'v1', 'no newer copy known: the old base is sent again');
  assert.equal(await h.session.resolveConflict('keep'), undefined, 'no conflict left: nothing to resolve');
});

test('an agent edit arriving under unsaved typing is not taken; the save then meets the conflict, text kept', async () => {
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    // The server has the agent's version: the base the window sends is older.
    throw Object.assign(new Error('conflict'), { status: 409, body: { detail: { note: { id, title: 'Title', content: 'Agent text', updated_at: 'v2' } } } });
  } });
  h.type('Typed before the agent finished');
  h.session.replace({ id: 'note1', title: 'Title', content: 'Agent text', updated_at: 'v2' });
  assert.equal(h.session.base, 'v1', 'the typing is not overwritten, nor its base moved');
  await h.drain();
  assert.equal(h.calls[0][2].base_updated_at, 'v1');
  assert.equal(h.session.conflict.local.content, 'Typed before the agent finished');
  assert.equal(h.session.conflict.server.content, 'Agent text');
  assert.equal(h.draft().content, 'Typed before the agent finished', 'the typing stays in the draft');
});

test('an edit the editor could not write is held on this device and said so, never dropped', async () => {
  const h = harness();
  // The window kept the block's stored text; `unsaved` is the note with the typed edit.
  h.view.unsaved = 'Original TYPED';
  h.type('Original');
  await h.drain();
  assert.deepEqual(h.calls, [], 'nothing else changed, nothing to send');
  assert.deepEqual(h.events, [['unsaved', true]]);
  assert.equal(h.draft().content, 'Original TYPED');
  await h.session.close();
  await h.drain();
  assert.equal(h.session.storedDraft()?.content, 'Original TYPED', 'offered back on the next open');

  // The rest of the note still saves; the draft keeps the held edit.
  h.view.unsaved = 'Original TYPED\n\nmore';
  h.type('Original\n\nmore');
  await h.drain();
  assert.deepEqual(h.calls.map(call => call[2].content), ['Original\n\nmore']);
  assert.equal(h.draft().content, 'Original TYPED\n\nmore');

  // Fixed (in the source, say): saved, the draft goes, the window is told.
  h.view.unsaved = null;
  h.type('Original TYPED\n\nmore');
  await h.drain();
  assert.equal(h.calls.at(-1)[2].content, 'Original TYPED\n\nmore');
  assert.deepEqual(h.events.filter(([name]) => name === 'unsaved'), [['unsaved', true], ['unsaved', false]]);
  assert.equal(h.draft(), null);
});

// a tick on the card while the window held typed text was only
// rebased, never drawn, so the next keystroke saved the old box over it.
test('a card tick behind a window whose typing is saved is taken, and the next save keeps it', async () => {
  const h = harness({ note: { id: 'note1', title: 'Title', content: '- [ ] one\n- [ ] two\n\ntail text', updated_at: 'v1' } });
  h.type('- [ ] one\n- [ ] two\n\ntail text A');
  await h.drain();
  assert.equal(h.session.base, 'v2');
  const ticked = { id: 'note1', title: 'Title', content: '- [x] one\n- [ ] two\n\ntail text A', updated_at: 'v3' };
  assert.equal(h.session.follow(ticked, { title: 'Title', content: '- [ ] one\n- [ ] two\n\ntail text A' }), 'take');
  assert.equal(h.session.base, 'v3');
  assert.equal(h.session.saved.content, ticked.content, 'the window redraws from the card\'s version');
  // The window drew the tick; the next keystroke goes on top of it.
  h.type('- [x] one\n- [ ] two\n\ntail text AB');
  await h.drain();
  assert.equal(h.calls.at(-1)[2].content, '- [x] one\n- [ ] two\n\ntail text AB');
  assert.equal(h.calls.at(-1)[2].base_updated_at, 'v3');
});

test('a card tick behind unsaved typing keeps the old base, so that save gets the conflict choice', () => {
  const h = harness({ note: { id: 'note1', title: 'Title', content: '- [ ] one\n\ntail', updated_at: 'v1' } });
  h.type('- [ ] one\n\ntail typed');
  const ticked = { id: 'note1', title: 'Title', content: '- [x] one\n\ntail', updated_at: 'v2' };
  assert.equal(h.session.follow(ticked, { title: 'Title', content: '- [ ] one\n\ntail typed' }), 'keep');
  assert.equal(h.session.base, 'v1', 'never moved on silently: the save meets the 409');
  assert.equal(h.session.saved.content, '- [ ] one\n\ntail');
});

test('a card write that left the text as saved only moves the base', () => {
  const h = harness();
  h.type('Original typed');
  assert.equal(h.session.follow({ id: 'note1', title: 'Title', content: 'Original', updated_at: 'v2' }, { title: 'Title', content: 'Original typed' }), 'base');
  assert.equal(h.session.base, 'v2');
  assert.equal(h.session.dirty, true);
});

// Reviewer item: the window reads a stored note back without its CRLF and
// without the blank lines at either end, so an idle window over such a note
// looked edited and a card tick behind it was never drawn.
for (const [label, stored, shown] of [
  ['ending in a newline', '- [ ] one\n- [ ] two\n\ntail\n', '- [ ] one\n- [ ] two\n\ntail'],
  ['using CRLF', '- [ ] one\r\n- [ ] two\r\n\r\ntail', '- [ ] one\n- [ ] two\n\ntail'],
  ['using CRLF, with blank lines around and between', '\r\n- [ ] one\r\n- [ ] two\r\n\r\n\r\n  \r\ntail\r\n\r\n', '- [ ] one\n- [ ] two\n\ntail'],
]) {
  test(`a card tick behind an idle window over a note ${label} is taken, its bytes as stored`, () => {
    const h = harness({ note: { id: 'note1', title: 'Title', content: stored, updated_at: 'v1' } });
    const ticked = { id: 'note1', title: 'Title', content: stored.replace('- [ ] one', '- [x] one'), updated_at: 'v2' };
    assert.equal(h.session.follow(ticked, { title: 'Title', content: shown }), 'take');
    assert.equal(h.session.base, 'v2');
    assert.equal(h.session.saved.content, ticked.content, 'nothing rewritten: the server copy is kept byte for byte');
    assert.equal(h.session.dirty, false);
    assert.deepEqual(h.calls, [], 'taking the tick writes nothing');
  });
}

test('a real edit in the window over a CRLF note still keeps the base', () => {
  const stored = '- [ ] one\r\n\r\ntail\r\n';
  const h = harness({ note: { id: 'note1', title: 'Title', content: stored, updated_at: 'v1' } });
  const ticked = { id: 'note1', title: 'Title', content: '- [x] one\r\n\r\ntail\r\n', updated_at: 'v2' };
  assert.equal(h.session.follow(ticked, { title: 'Title', content: '- [ ] one\n\ntail typed' }), 'keep');
  // A blank line typed between two blocks is an edit the editor can show.
  assert.equal(h.session.follow(ticked, { title: 'Title', content: '- [ ] one\n\ntail\n\n- [ ] new' }), 'keep');
  assert.equal(h.session.base, 'v1');
  assert.equal(h.session.saved.content, stored);
});

test('taskFlips names the boxes that changed, and only when nothing else did', () => {
  assert.deepEqual(taskFlips('- [ ] a\n- [x] b\n\ntext', '- [x] a\n- [ ] b\n\ntext'), [0, 1]);
  assert.deepEqual(taskFlips('- [ ] a', '- [ ] a'), []);
  assert.equal(taskFlips('- [ ] a', '- [x] a\n- [ ] b'), null, 'an item was added');
  assert.equal(taskFlips('- [ ] a\n\ntext', '- [x] a\n\nother'), null, 'the text changed too');
  assert.equal(taskFlips('- [ ] a', '- [ ] renamed'), null);
});

test('typing while a new note is being created leaves no new-note draft to restore as a second note', async () => {
  let answer;
  const h = harness({ note: null, save: body => { h.calls.push(['POST', body]); return new Promise(resolve => { answer = resolve; }); } });
  h.type('Buy bread');
  await h.drain();
  assert.equal(h.calls.length, 1, 'the create request is out');
  h.type('Buy bread and milk');
  answer({ title: '', content: 'Buy bread', id: 'created1', updated_at: 'c1' });
  for (let i = 0; i < 5; i++) await new Promise(setImmediate);
  assert.equal(h.draft('__new__'), null, 'no new-note window will offer it back');
  assert.equal(h.draft('created1').content, 'Buy bread and milk', 'the typing is kept under the note it belongs to');
  assert.equal(h.draft('created1').base_updated_at, 'c1', 'and builds on the version just created');
  await h.drain();
  assert.deepEqual(h.calls[1], ['PUT', 'created1', { title: '', content: 'Buy bread and milk', replace_content: true, base_updated_at: 'c1' }]);
  assert.equal(h.draft('created1'), null);
  const fresh = harness({ note: null });
  for (const [k, v] of h.data) fresh.data.set(k, v);
  assert.equal(fresh.session.storedDraft(), null, 'a new note opened later has nothing to restore');
});

test('a held edit on a new note moves to the created note, not to the next new note', async () => {
  const h = harness({ note: null });
  h.view.unsaved = 'Kept text';
  h.type('Saved text');
  await h.drain();
  assert.equal(h.session.id, 'created1');
  assert.equal(h.draft('__new__'), null);
  assert.equal(h.draft('created1').content, 'Kept text');
});

test('a restored draft is saved against the version it was typed on, so a newer server copy meets the conflict choice', async () => {
  const h = harness({
    note: { id: 'note1', title: 'Title', content: 'Someone else rewrote this', updated_at: 'v3' },
    patch: async (id, body) => {
      h.calls.push(['PUT', id, body]);
      if (body.base_updated_at !== 'v3') throw Object.assign(new Error('conflict'), { status: 409, body: { detail: { note: { id, title: 'Title', content: 'Someone else rewrote this', updated_at: 'v3' } } } });
      return { id, ...body, updated_at: 'v4' };
    },
  });
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', content: 'Typed offline on v1', base_updated_at: 'v1' }));
  const draft = h.session.storedDraft();
  assert.deepEqual(draft, { title: 'Title', content: 'Typed offline on v1', stale: false, recovered: [] });
  h.session.restoreDraft();
  h.type(draft.content, draft.title);
  await h.drain();
  assert.equal(h.calls[0][2].base_updated_at, 'v1', 'sent with the base the draft was typed on');
  assert.equal(h.events.at(-1)[0], 'conflict');
  assert.equal(h.draft().content, 'Typed offline on v1', 'the restored text is still kept');
  await h.session.resolveConflict('mine');
  assert.equal(h.calls[1][2].base_updated_at, 'v3', 'Keep mine then writes over the newer copy, knowingly');
});

test('restoring a draft with no recorded base keeps the loaded base', async () => {
  const h = harness();
  h.data.set('odysseus-note-draft-note1', JSON.stringify({ _ts: 1, title: 'Title', items: [{ text: 'old form row', done: false }] }));
  const draft = h.session.storedDraft();
  h.session.restoreDraft();
  h.type(draft.content, draft.title);
  await h.drain();
  assert.equal(h.calls[0][2].base_updated_at, 'v1');
});

test('a save refused as too long hands the window its reason and keeps the text on this device', async () => {
  const refusal = Object.assign(new Error('Failed to update note'), { status: 413, body: { detail: { message: 'The note\'s title is too long to save: 1,001 characters, the limit is 1,000.' } } });
  const h = harness({ patch: async () => { throw refusal; } });
  h.type('kept', 'T'.repeat(1001));
  await h.drain();
  assert.deepEqual(h.events, [['failed', 'Failed to update note']]);
  assert.equal(sizeRefusal(refusal), 'The note\'s title is too long to save: 1,001 characters, the limit is 1,000.');
  assert.equal(sizeRefusal({ status: 413, body: null }), 'This note is too long to save.');
  assert.equal(sizeRefusal({ status: 500 }), null);
  assert.equal(h.draft().title, 'T'.repeat(1001));
  assert.equal(h.draft().content, 'kept');
  assert.equal(h.session.dirty, true, 'still dirty, so a shorter edit saves');
});

test('a 409 where only the colour or tag moved on saves the text on the newer version, without a conflict', async () => {
  const recoloured = { id: 'note1', title: 'Title', content: 'Original', color: 'blue', updated_at: 'v5' };
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    if (body.base_updated_at !== 'v5') throw Object.assign(new Error('Failed to update note'), { status: 409, body: { detail: { note: recoloured } } });
    return { ...recoloured, ...body, updated_at: 'v6' };
  } });
  h.type('Original typed');
  await h.drain();
  assert.deepEqual(h.calls.map(call => call[2].base_updated_at), ['v1', 'v5']);
  assert.equal(h.calls[1][2].content, 'Original typed');
  assert.equal(h.session.conflict, null);
  assert.deepEqual(h.events.map(event => event[0]), ['saved']);
  assert.equal(h.session.base, 'v6');
  assert.equal(h.draft(), null);
});

test('a 409 whose copy has the same body_revision is retried once on the newer base; a moved revision is the conflict choice', async () => {
  const recoloured = { id: 'note1', title: 'Title', content: 'Original', color: 'blue', updated_at: 'v5', body_revision: 'v1' };
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    if (body.base_updated_at !== 'v5') throw Object.assign(new Error('Failed to update note'), { status: 409, body: { detail: { note: recoloured } } });
    return { ...recoloured, ...body, updated_at: 'v6', body_revision: 'v2' };
  } });
  h.type('Original typed');
  await h.drain();
  assert.deepEqual(h.calls.map(call => [call[2].base_updated_at, call[2].body_revision]), [['v1', 'v1'], ['v5', 'v1']]);
  assert.equal(h.session.conflict, null);
  assert.equal(h.session.revision, 'v2', 'the answer\'s revision is the next write\'s');

  // Same text on the server, but its body_revision moved on: not retried.
  const rewritten = { id: 'note1', title: 'Title', content: 'Original', updated_at: 'v5', body_revision: 'other' };
  const g = harness({ patch: async (id, body) => { g.calls.push(['PUT', id, body]); throw Object.assign(new Error('x'), { status: 409, body: { detail: { note: rewritten } } }); } });
  g.type('Original typed');
  await g.drain();
  assert.equal(g.calls.length, 1);
  assert.deepEqual(g.session.conflict.server, rewritten);
});

test('a second 409 after moving the base is the conflict choice, not a loop', async () => {
  const same = { id: 'note1', title: 'Title', content: 'Original', updated_at: 'v5' };
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    throw Object.assign(new Error('Failed to update note'), { status: 409, body: { detail: { note: same } } });
  } });
  h.type('Mine');
  await h.drain();
  assert.equal(h.calls.length, 2, 'one move of the base, not a loop');
  assert.equal(h.events.at(-1)[0], 'conflict');
  assert.equal(h.draft().content, 'Mine');
});

test('a save refused because the note is now view only says so once, keeps the text on this device and stops saving', async () => {
  const h = harness({ patch: async (id, body) => {
    h.calls.push(['PUT', id, body]);
    throw Object.assign(new Error('Failed to update note'), { status: 403, body: { detail: 'This note is shared with you as view only' } });
  } });
  h.type('Original bob');
  await h.drain();
  assert.deepEqual(h.events, [['viewOnly']], 'not the "kept on this device, will retry" failure');
  assert.equal(h.session.dirty, false);
  assert.equal(h.draft().content, 'Original bob');
  await h.session.close();
  assert.equal(h.calls.length, 1, 'no retry');
});
