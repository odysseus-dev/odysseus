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
