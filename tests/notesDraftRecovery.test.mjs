import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createNoteSession } from '../frontend/src/features/notes/application/noteSession.js';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';

const note = { id: 'n', title: '', content: 'saved', body_revision: 'r1' };
const server = { ...note, content: 'remote', body_revision: 'r2' };
function setup(api = {}, effects = {}, existing = note, storage = new Map()) {
  const persistence = createNotesPersistence({ storage: {
    getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  } });
  const draft = { title: '', content: 'local' };
  const session = createNoteSession({ api, effects, note: existing, persistence,
    scheduler: { setTimeout: () => 1, clearTimeout() {} }, clock: { now: () => 1 }, collect: () => draft,
  });
  return { session, draft, storage, key: persistence._draftKey(existing?.id) };
}

test('deployed note session matches the maintained source', async () => {
  const read = root => readFile(new URL(`../${root}/features/notes/application/noteSession.js`, import.meta.url));
  assert.deepEqual(await read('static/js'), await read('frontend/src'));
});

test('no-op save preserves another session’s unsaved draft', async () => {
  const first = setup();
  first.draft.content = note.content;
  first.session.markDirty();
  const second = setup({}, {}, note, first.storage);
  second.session.markDirty();
  const otherDraft = first.storage.get(first.key);
  await first.session.flush();
  assert.equal(first.storage.get(first.key), otherDraft);
});

test('Reload preserves the draft until a server copy is available', async () => {
  let reads = 0;
  const { session, storage, key } = setup({
    patchNote: async () => { throw Object.assign(new Error('stale'), { status: 409 }); },
    getNote: async () => { if (++reads < 3) throw new Error('offline'); return server; },
  });
  session.markDirty();
  await session.flush();
  const backup = storage.get(key);
  await session.resolveConflict('reload');
  assert.ok(session.conflict);
  assert.equal(storage.get(key), backup);
  await session.resolveConflict('reload');
  assert.equal(session.conflict, null);
  assert.equal(session.base, 'r2');
  assert.equal(storage.has(key), false);
});

for (const kind of ['created', 'saved']) {
  test(`${kind} callback survives post-save storage cleanup failure`, async () => {
    const storage = new Map();
    storage.delete = () => { throw new Error('storage unavailable'); };
    let acknowledged;
    let failures = 0;
    const { session } = setup({ patchNote: async () => server, saveNote: async () => server }, {
      [kind]: value => { acknowledged = value; }, failed: () => { failures++; },
    }, kind === 'created' ? null : note, storage);
    session.markDirty();
    await session.flush();
    assert.equal(acknowledged, server);
    assert.equal(failures, 0);
    assert.equal(session.dirty, false);
  });
}

test('same-revision concurrent drafts from two tabs remain recoverable', () => {
  const first = setup();
  first.draft.content = 'Draft from Tab 1';
  first.session.markDirty();

  const second = setup({}, {}, note, first.storage);
  second.draft.content = 'Draft from Tab 2';
  second.session.markDirty();

  const primary = JSON.parse(first.storage.get(first.key) || 'null');
  assert.equal(primary.content, 'Draft from Tab 2');

  const recoveryKey = 'odysseus-note-recovery-' + note.id;
  const recoveredList = JSON.parse(first.storage.get(recoveryKey) || '[]');
  assert.equal(recoveredList.length, 1);
  assert.equal(recoveredList[0].content, 'Draft from Tab 1');
  assert.equal(recoveredList[0].body_revision, 'r1');

  const stored = second.session.storedDraft();
  assert.equal(stored.content, 'Draft from Tab 2');
  assert.deepEqual(stored.recovered, [{ title: '', content: 'Draft from Tab 1' }]);
});

test('concurrent new notes in separate sessions have safe identities without collisions', () => {
  const storage = new Map();
  const first = setup({}, {}, null, storage);
  first.draft.title = 'New Note 1';
  first.draft.content = 'Content 1';
  first.session.markDirty();

  const second = setup({}, {}, null, storage);
  second.draft.title = 'New Note 2';
  second.draft.content = 'Content 2';
  second.session.markDirty();

  const values = [...storage.values()];
  assert.ok(values.some(v => v.includes('Content 1')), 'first new note draft is preserved');
  assert.ok(values.some(v => v.includes('Content 2')), 'second new note draft is preserved');
});

test('saving or discarding in one session does not delete another session recoverable data', async () => {
  const storage = new Map();
  const first = setup({ patchNote: async () => server }, {}, note, storage);
  first.draft.content = 'Tab 1 edits';
  first.session.markDirty();

  const second = setup({ patchNote: async () => server }, {}, note, storage);
  second.draft.content = 'Tab 2 edits';
  second.session.markDirty();

  // Second session saves successfully
  await second.session.flush();

  // Tab 1 recoverable data must still exist in storage
  const recoveryKey = 'odysseus-note-recovery-' + note.id;
  const recoveredList = JSON.parse(storage.get(recoveryKey) || '[]');
  assert.ok(recoveredList.some(d => d.content === 'Tab 1 edits'), 'Tab 1 draft survives Tab 2 save');
});

test('legacy draft without writer is preserved when a new writer backs up', () => {
  const storage = new Map();
  const legacyKey = 'odysseus-note-draft-' + note.id;
  storage.set(legacyKey, JSON.stringify({ _ts: 1, title: 'Old Title', content: 'Legacy unsaved draft', body_revision: 'r1' }));

  const session = setup({}, {}, note, storage);
  session.draft.content = 'Brand new typing';
  session.session.markDirty();

  const primary = JSON.parse(storage.get(legacyKey) || 'null');
  assert.equal(primary.content, 'Brand new typing');

  const recoveryKey = 'odysseus-note-recovery-' + note.id;
  const recoveredList = JSON.parse(storage.get(recoveryKey) || '[]');
  assert.ok(recoveredList.some(d => d.content === 'Legacy unsaved draft'), 'legacy draft was moved to recovery');
});

test('draft recovery predicate compares strings and ignores drafts identical to saved state', () => {
  const storage = new Map();
  const legacyKey = 'odysseus-note-draft-' + note.id;
  // Seed a draft identical to saved note content
  storage.set(legacyKey, JSON.stringify({ _ts: 1, title: '', content: 'saved', body_revision: 'r1' }));

  const session = setup({}, {}, note, storage);
  session.draft.content = 'Brand new typing';
  session.session.markDirty();

  const recoveryKey = 'odysseus-note-recovery-' + note.id;
  // Because the previous draft matched saved content exactly, it should not be stored in recovery
  assert.equal(storage.has(recoveryKey), false);
});

test('draft recovery predicate preserves drafts with different content from saved note', () => {
  const storage = new Map();
  const legacyKey = 'odysseus-note-draft-' + note.id;
  storage.set(legacyKey, JSON.stringify({ _ts: 1, title: '', content: 'different unsaved draft', body_revision: 'r1' }));

  const session = setup({}, {}, note, storage);
  session.draft.content = 'Brand new typing';
  session.session.markDirty();

  const recoveryKey = 'odysseus-note-recovery-' + note.id;
  const recoveredList = JSON.parse(storage.get(recoveryKey) || '[]');
  assert.equal(recoveredList.length, 1);
  assert.equal(recoveredList[0].content, 'different unsaved draft');
});

function makeRealisticStorage() {
  const map = new Map();
  return {
    getItem: k => (map.has(String(k)) ? map.get(String(k)) : null),
    setItem: (k, v) => map.set(String(k), String(v)),
    removeItem: (k) => map.delete(String(k)),
    key: (i) => Array.from(map.keys())[i] ?? null,
    get length() { return map.size; },
    _map: map,
  };
}

function setupRealisticSession({ note = null, api = {}, storage = makeRealisticStorage(), clockTime = 1000 } = {}) {
  const persistence = createNotesPersistence({ storage });
  const draft = { title: note?.title || '', content: note?.content || '' };
  let now = clockTime;
  const session = createNoteSession({
    api,
    persistence,
    note,
    clock: { now: () => ++now },
    scheduler: { setTimeout: () => 1, clearTimeout: () => {} },
    collect: () => draft,
  });
  return { session, draft, storage, persistence };
}

test('Finding A 1: two concurrent unsaved new notes have isolated storage keys and preserve both drafts', () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({ note: null, storage, clockTime: 100 });
  sessionA.draft.title = 'Alpha';
  sessionA.draft.content = 'alpha content';
  sessionA.session.markDirty();

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'Bravo';
  sessionB.draft.content = 'bravo content';
  sessionB.session.markDirty();

  const keys = Array.from(storage._map.keys());
  assert.equal(keys.length, 2);
  assert.ok(keys.every(k => k.startsWith('odysseus-note-draft-__new__-')));
  assert.notEqual(keys[0], keys[1]);

  const vals = Array.from(storage._map.values());
  assert.ok(vals.some(v => v.includes('alpha content')));
  assert.ok(vals.some(v => v.includes('bravo content')));
});

test('Finding A 2: abandoning unsaved new note A preserves unsaved new note B', () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({ note: null, storage, clockTime: 100 });
  sessionA.draft.title = 'Alpha';
  sessionA.draft.content = 'alpha content';
  sessionA.session.markDirty();

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'Bravo';
  sessionB.draft.content = 'bravo content';
  sessionB.session.markDirty();

  sessionA.session.abandon();

  const remaining = Array.from(storage._map.values());
  assert.equal(remaining.length, 1);
  assert.ok(remaining[0].includes('bravo content'));
  assert.ok(!remaining.some(v => v.includes('alpha content')));
});

test('Finding A 3: discarding unsaved new note A preserves unsaved new note B', () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({ note: null, storage, clockTime: 100 });
  sessionA.draft.title = 'Alpha';
  sessionA.draft.content = 'alpha content';
  sessionA.session.markDirty();

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'Bravo';
  sessionB.draft.content = 'bravo content';
  sessionB.session.markDirty();

  sessionA.session.discardDraft();

  const remaining = Array.from(storage._map.values());
  assert.equal(remaining.length, 1);
  assert.ok(remaining[0].includes('bravo content'));
  assert.ok(!remaining.some(v => v.includes('alpha content')));
});

test('Finding A 4: saving unsaved new note A preserves unsaved new note B', async () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({
    note: null,
    storage,
    clockTime: 100,
    api: { saveNote: async body => ({ ...body, id: 'created-a', body_revision: 'c1' }) },
  });
  sessionA.draft.title = 'Alpha';
  sessionA.draft.content = 'alpha content';
  sessionA.session.markDirty();

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'Bravo';
  sessionB.draft.content = 'bravo content';
  sessionB.session.markDirty();

  await sessionA.session.flush();

  const remaining = Array.from(storage._map.values());
  assert.equal(remaining.length, 1);
  assert.ok(remaining[0].includes('bravo content'));
  assert.ok(!remaining.some(v => v.includes('alpha content')));
});

test('Finding A 5: restoring unsaved new note A preserves unsaved new note B', () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({ note: null, storage, clockTime: 100 });
  sessionA.draft.title = 'Alpha';
  sessionA.draft.content = 'alpha content';
  sessionA.session.markDirty();

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'Bravo';
  sessionB.draft.content = 'bravo content';
  sessionB.session.markDirty();

  // Session A restores its draft
  const draftA = sessionA.session.storedDraft();
  assert.equal(draftA.content, 'alpha content');
  sessionA.draft.content = draftA.content;
  sessionA.session.markDirty();

  const remaining = Array.from(storage._map.values());
  assert.equal(remaining.length, 2);
  assert.ok(remaining.some(v => v.includes('alpha content')));
  assert.ok(remaining.some(v => v.includes('bravo content')));
});

test('Finding A 6: removing a legacy draft does not remove active writer-specific drafts', () => {
  const storage = makeRealisticStorage();
  storage.setItem('odysseus-note-draft-__new__', JSON.stringify({ title: 'Legacy', content: 'legacy content' }));

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'Bravo';
  sessionB.draft.content = 'bravo content';
  sessionB.session.markDirty();

  // Clear legacy draft without writer
  sessionB.persistence._clearDraft(null);

  assert.equal(storage.getItem('odysseus-note-draft-__new__'), null);
  const remainingDraftKeys = [];
  for (let i = 0; i < storage.length; i++) {
    const k = storage.key(i);
    if (k?.startsWith('odysseus-note-draft-__new__-')) {
      remainingDraftKeys.push(k);
    }
  }
  assert.equal(remainingDraftKeys.length, 1);
  assert.ok(storage.getItem(remainingDraftKeys[0]).includes('bravo content'));
});

test('Finding A 7: repeated cleanup remains safe and idempotent', () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({ note: null, storage, clockTime: 100 });
  sessionA.draft.title = 'Alpha';
  sessionA.draft.content = 'alpha content';
  sessionA.session.markDirty();

  assert.doesNotThrow(() => {
    sessionA.session.abandon();
    sessionA.session.abandon();
    sessionA.session.discardDraft();
    sessionA.session.discardDraft();
  });
  assert.equal(storage.length, 0);
});

test('Finding A 8: existing saved-note recovery continues working alongside writer drafts', () => {
  const storage = makeRealisticStorage();
  const existingNote = { id: 'saved-note-1', title: 'Note 1', content: 'original', body_revision: 'r1' };

  const session1 = setupRealisticSession({ note: existingNote, storage, clockTime: 100 });
  session1.draft.content = 'Edit from Tab 1';
  session1.session.markDirty();

  const session2 = setupRealisticSession({ note: existingNote, storage, clockTime: 200 });
  session2.draft.content = 'Edit from Tab 2';
  session2.session.markDirty();

  // Tab 1 edit was preserved in recovery
  const recoveryKey = 'odysseus-note-recovery-' + existingNote.id;
  const recoveredList = JSON.parse(storage.getItem(recoveryKey) || '[]');
  assert.equal(recoveredList.length, 1);
  assert.equal(recoveredList[0].content, 'Edit from Tab 1');

  // Tab 2 edit is in primary draft key
  const primaryDraft = JSON.parse(storage.getItem('odysseus-note-draft-' + existingNote.id) || 'null');
  assert.equal(primaryDraft.content, 'Edit from Tab 2');
});

test('Finding A Scenario 1: Active foreign draft is preserved when dismissed by another tab', () => {
  const storage = makeRealisticStorage();
  const existingNote = { id: 'saved-note-scen1', title: 'Note 1', content: 'server original', body_revision: 'r1' };

  // Tab A edits
  const tabA = setupRealisticSession({ note: existingNote, storage, clockTime: 100 });
  tabA.draft.content = 'Unsaved typing in Tab A';
  tabA.session.markDirty();

  assert.equal(storage.getItem('odysseus-note-draft-saved-note-scen1') !== null, true);

  // Tab B opens the same note
  const tabB = setupRealisticSession({ note: existingNote, storage, clockTime: 200 });
  const storedInB = tabB.session.storedDraft();
  assert.equal(storedInB.content, 'Unsaved typing in Tab A');
  assert.equal(storedInB.foreign, true);

  // Tab B dismisses recovery
  tabB.session.dismissDraft();

  // Tab A draft must still be in storage
  assert.equal(storage.getItem('odysseus-note-draft-saved-note-scen1') !== null, true);
  const rawA = JSON.parse(storage.getItem('odysseus-note-draft-saved-note-scen1'));
  assert.equal(rawA.content, 'Unsaved typing in Tab A');
});

test('Finding A Scenario 2: Explicit recovery discard destroys foreign draft only when includeForeign is requested', () => {
  const storage = makeRealisticStorage();
  const existingNote = { id: 'saved-note-scen2', title: 'Note 2', content: 'server original', body_revision: 'r1' };

  // Tab A edits
  const tabA = setupRealisticSession({ note: existingNote, storage, clockTime: 100 });
  tabA.draft.content = 'Unsaved typing in Tab A';
  tabA.session.markDirty();

  // Tab B opens note
  const tabB = setupRealisticSession({ note: existingNote, storage, clockTime: 200 });
  assert.equal(tabB.session.storedDraft().foreign, true);

  // Tab B calls normal discardDraft: should NOT destroy Tab A draft
  tabB.session.discardDraft();
  assert.equal(storage.getItem('odysseus-note-draft-saved-note-scen2') !== null, true);

  // Tab B calls explicit discard with includeForeign: true
  tabB.session.discardDraft({ includeForeign: true });
  assert.equal(storage.getItem('odysseus-note-draft-saved-note-scen2'), null);
});

test('Finding A Scenario 3: Independent recovery snapshots from multiple sessions survive discard of another session', () => {
  const storage = makeRealisticStorage();
  // Recovery key for __new__ has entries from Writer A and Writer B
  storage.setItem('odysseus-note-recovery-__new__', JSON.stringify([
    { _draftWriter: 'writerA', title: 'A', content: 'Snapshot A' },
    { _draftWriter: 'writerB', title: 'B', content: 'Snapshot B' },
  ]));

  const persistence = createNotesPersistence({ storage });
  // Writer A clears its recovery
  persistence._clearDraftRecovery('__new__', 'writerA');

  const remaining = JSON.parse(storage.getItem('odysseus-note-recovery-__new__') || '[]');
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0]._draftWriter, 'writerB');
  assert.equal(remaining[0].content, 'Snapshot B');
});

test('Finding A Scenario 4: Concurrent new note drafts and recovery states remain isolated on discard', () => {
  const storage = makeRealisticStorage();
  const sessionA = setupRealisticSession({ note: null, storage, clockTime: 100 });
  sessionA.draft.title = 'New A';
  sessionA.draft.content = 'Content A';
  sessionA.session.markDirty();

  const sessionB = setupRealisticSession({ note: null, storage, clockTime: 200 });
  sessionB.draft.title = 'New B';
  sessionB.draft.content = 'Content B';
  sessionB.session.markDirty();

  // Seed recovery entries for both
  sessionA.persistence._keepDraftForRecovery('__new__', { _draftWriter: 'writer-other', content: 'Preserved snapshot' });

  // Discard A
  sessionA.session.discardDraft();

  // B draft is intact
  const bKeys = [];
  for (let i = 0; i < storage.length; i++) {
    const k = storage.key(i);
    if (k?.startsWith('odysseus-note-draft-__new__-')) bKeys.push(k);
  }
  assert.equal(bKeys.length, 1);
  assert.ok(storage.getItem(bKeys[0]).includes('Content B'));

  // Other recovery entries in __new__ are intact
  const recoveryList = JSON.parse(storage.getItem('odysseus-note-recovery-__new__') || '[]');
  assert.ok(recoveryList.some(e => e.content === 'Preserved snapshot'));
});

test('Finding A Scenario 5: Successful save in one session removes only that session draft and preserves recoverable work', async () => {
  const storage = makeRealisticStorage();
  // Existing recovery entry on saved note
  storage.setItem('odysseus-note-recovery-scen5', JSON.stringify([
    { _draftWriter: 'writer-earlier', title: 'Note 5', content: 'Earlier recovery draft' },
  ]));

  const existingNote = { id: 'scen5', title: 'Note 5', content: 'original', body_revision: 'r1' };
  const sessionA = setupRealisticSession({
    note: existingNote,
    storage,
    clockTime: 100,
    api: { patchNote: async (id, body) => ({ id, ...body, body_revision: 'r2' }) },
  });
  sessionA.draft.content = 'Edited in A';
  sessionA.session.markDirty();

  await sessionA.session.flush();

  // Primary draft for A was removed upon successful save
  assert.equal(storage.getItem('odysseus-note-draft-scen5'), null);
  // Earlier recovery entry remains intact
  const recoveryList = JSON.parse(storage.getItem('odysseus-note-recovery-scen5') || '[]');
  assert.equal(recoveryList.length, 1);
  assert.equal(recoveryList[0].content, 'Earlier recovery draft');
});

test('Finding A Scenario 6: Legacy drafts without _draftWriter are compatible and clear appropriately', () => {
  const storage = makeRealisticStorage();
  const existingNote = { id: 'scen6', title: 'Note 6', content: 'original', body_revision: 'r1' };
  storage.setItem('odysseus-note-draft-scen6', JSON.stringify({ title: 'Legacy Title', content: 'Legacy content' }));

  const session = setupRealisticSession({ note: existingNote, storage, clockTime: 100 });
  const stored = session.session.storedDraft();
  assert.equal(stored.content, 'Legacy content');
  assert.equal(stored.foreign, undefined);

  session.session.discardDraft();
  assert.equal(storage.getItem('odysseus-note-draft-scen6'), null);
});

test('Finding A Scenario 7: Repeated discard, abandonment, and cleanup are idempotent and safe', () => {
  const storage = makeRealisticStorage();
  const session = setupRealisticSession({ note: null, storage, clockTime: 100 });
  session.draft.content = 'Draft text';
  session.session.markDirty();

  assert.doesNotThrow(() => {
    session.session.dismissDraft();
    session.session.dismissDraft();
    session.session.discardDraft();
    session.session.discardDraft();
    session.session.abandon();
    session.session.abandon();
  });
});

test('Finding A Scenario 8: Recoverable drafts remain available after failed saves and reloads', async () => {
  const storage = makeRealisticStorage();
  const existingNote = { id: 'scen8', title: 'Note 8', content: 'original', body_revision: 'r1' };
  const session = setupRealisticSession({
    note: existingNote,
    storage,
    clockTime: 100,
    api: { patchNote: async () => { throw new Error('network down'); } },
  });
  session.draft.content = 'Unsaved offline draft';
  session.session.markDirty();

  await session.session.flush();

  // Save failed: draft must still be stored
  assert.equal(storage.getItem('odysseus-note-draft-scen8') !== null, true);
  assert.ok(storage.getItem('odysseus-note-draft-scen8').includes('Unsaved offline draft'));

  // Reload session
  const reloadedSession = setupRealisticSession({ note: existingNote, storage, clockTime: 200 });
  const stored = reloadedSession.session.storedDraft();
  assert.equal(stored.content, 'Unsaved offline draft');
});
