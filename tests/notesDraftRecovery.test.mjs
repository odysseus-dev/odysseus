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
