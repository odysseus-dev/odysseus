import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesApi } from '../frontend/src/features/notes/infrastructure/http.js';
import { createNotesPersistence, REMINDER_FIRED_KEY } from '../frontend/src/features/notes/infrastructure/storage.js';

test('list, reorder, upload, reminder and agent transports retain exact requests', async () => {
  const calls = [], result = { ok: false, status: 403 };
  const api = createNotesApi({ origin: 'https://notes.invalid', request: async (...args) => { calls.push(args); return result; } });
  const body = { opaque: true };
  assert.equal(await api.listNotes(true), result);
  await api.listNotes(false); await api.reorder(['b', 'a']); await api.upload(body);
  await api.fireReminder({ note_id: 'a' }); await api.defaultChat();
  await api.createSession(body); await api.startChat(body);
  assert.deepEqual(calls.map(([url, o]) => [url, o.method || 'GET', o.credentials]), [
    ['https://notes.invalid/api/notes?archived=true', 'GET', 'same-origin'],
    ['https://notes.invalid/api/notes', 'GET', 'same-origin'],
    ['https://notes.invalid/api/notes/reorder', 'POST', 'same-origin'],
    ['https://notes.invalid/api/upload', 'POST', 'same-origin'],
    ['/api/notes/fire-reminder', 'POST', 'same-origin'],
    ['https://notes.invalid/api/default-chat', 'GET', 'same-origin'],
    ['https://notes.invalid/api/session', 'POST', 'same-origin'],
    ['https://notes.invalid/api/chat_stream', 'POST', 'same-origin'],
  ]);
  assert.equal(calls[2][1].body, '{"ids":["b","a"]}');
  assert.equal(calls[3][1].body, body);
  assert.equal(api.uploadUrl('file'), 'https://notes.invalid/api/upload/file');
});

test('draft and marker persistence retain keys, formats and malformed fallback', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const persistence = createNotesPersistence({ storage });
  assert.equal(persistence._draftKey(null), 'odysseus-note-draft-__new__');
  persistence._saveFiredReminders(new Set(['a', 'b']));
  assert.equal(values.get(REMINDER_FIRED_KEY), '["a","b"]');
  assert.deepEqual([...persistence._loadFiredReminders()], ['a', 'b']);
  values.set(REMINDER_FIRED_KEY, '{broken');
  assert.equal(persistence._loadFiredReminders().size, 0);
  storage.setItem(persistence._draftKey('a'), '{"content":"","_ts":1}');
  assert.deepEqual(persistence._loadDraft('a'), { content: '', _ts: 1 });
  persistence._clearDraft('a');
  assert.equal(persistence._loadDraft('a'), null);
  const failing = createNotesPersistence({ storage: { getItem() { throw Error('blocked'); }, setItem() { throw Error('full'); } } });
  assert.equal(failing._loadDraft('a'), null);
  assert.doesNotThrow(() => failing._saveFiredReminders(new Set(['a'])));
  assert.throws(() => failing.setItem('odysseus-note-draft-a', '{}'), /full/, 'draft caller must observe backup failure');
});

// The workflows read `status` and `body` off a failed call: a 409 carries the
// newer note, a 404 means the note is gone for this account.
function fakeServer(answers) {
  const calls = [];
  const request = async (url, init = {}) => {
    calls.push([init.method || 'GET', url.replace('https://notes.invalid', ''), init.body ?? null]);
    const answer = answers.shift() ?? { status: 200, body: {} };
    return { ok: answer.status < 400, status: answer.status, json: async () => { if (answer.body === undefined) throw new SyntaxError('no body'); return answer.body; } };
  };
  return { calls, api: createNotesApi({ origin: 'https://notes.invalid', request }) };
}

test('every note, task, history and sharing call goes to its route with its body', async () => {
  const { calls, api } = fakeServer([
    { status: 200, body: { id: 'n1' } }, { status: 200, body: { id: 'n1' } }, { status: 200, body: { id: 'n1' } },
    { status: 200, body: { ok: true } }, { status: 200, body: { ok: true } }, { status: 200, body: { ok: true } },
    { status: 200, body: { ok: true } },
    { status: 200, body: { id: 'n1' } }, { status: 200, body: { id: 'n1' } }, { status: 200, body: { versions: [] } },
    { status: 200, body: { id: 3 } }, { status: 200, body: { id: 'n1' } }, { status: 204 },
    { status: 200, body: { users: ['bob'], directory: false } }, { status: 200, body: { shared_with: [{ username: 'bob' }] } },
    { status: 200, body: {} }, { status: 200, body: { ok: true } },
  ]);
  await api.saveNote({ title: 'New' });
  await api.saveNote({ id: 'n1', title: 'Old' });
  await api.patchNote('n1', { color: 'red' });
  await api.toggleTask('n1', 2, 'Buy milk');
  await api.toggleTask('n1', 2, 'Buy milk', true);
  await api.toggleTask('n1', 2);
  await api.appendTask('n1', 'Call');
  await api.getNote('n1');
  await api.agentUndo('n1');
  await api.listVersions('n1');
  await api.getVersion('n1', 3);
  await api.restoreVersion('n1', 3);
  await api.deleteNote('n1');
  assert.deepEqual(await api.shareTargets(), { users: ['bob'], directory: false });
  assert.deepEqual(await api.listShares('n1'), [{ username: 'bob' }]);
  assert.deepEqual(await api.share('n1', ['bob'], 'edit'), [], 'an answer without shared_with is an empty list');
  await api.unshare('n1', 'bob smith');
  assert.deepEqual(calls, [
    ['POST', '/api/notes', '{"title":"New"}'],
    ['PUT', '/api/notes/n1', '{"id":"n1","title":"Old"}'],
    ['PUT', '/api/notes/n1', '{"color":"red"}'],
    ['POST', '/api/notes/n1/items/2/toggle', '{"text":"Buy milk"}'],
    ['POST', '/api/notes/n1/items/2/toggle', '{"text":"Buy milk","done":true}'],
    ['POST', '/api/notes/n1/items/2/toggle', null],
    ['POST', '/api/notes/n1/items', '{"text":"Call","indent":0}'],
    ['GET', '/api/notes/n1', null],
    ['POST', '/api/notes/n1/agent-undo', null],
    ['GET', '/api/notes/n1/versions', null],
    ['GET', '/api/notes/n1/versions/3', null],
    ['POST', '/api/notes/n1/versions/3/restore', '{"base_updated_at":null}'],
    ['DELETE', '/api/notes/n1', null],
    ['GET', '/api/notes/share-targets', null],
    ['GET', '/api/notes/n1/shares', null],
    ['POST', '/api/notes/n1/share', '{"users":["bob"],"permission":"edit"}'],
    ['DELETE', '/api/notes/n1/share/bob%20smith', null],
  ]);
});

test('a tick and a content save name the body revision they were made on, and a 409 keeps the server copy', async () => {
  const conflict = { detail: { message: 'Note changed elsewhere', note: { id: 'n1', updated_at: 'u9', body_revision: 'r9' } } };
  const { calls, api } = fakeServer([{ status: 200, body: { ok: true } }, { status: 200, body: { id: 'n1' } }, { status: 409, body: conflict }]);
  await api.toggleTask('n1', 2, 'Buy milk', true, 'r7');
  await api.patchNote('n1', { content: 'x', replace_content: true, body_revision: 'r7', base_updated_at: 'u7' });
  await assert.rejects(api.toggleTask('n1', 2, 'Buy milk', false, 'r7'), error => error.status === 409 && error.body.detail.note.body_revision === 'r9');
  assert.deepEqual(calls.slice(0, 2), [
    ['POST', '/api/notes/n1/items/2/toggle', '{"text":"Buy milk","done":true,"body_revision":"r7"}'],
    ['PUT', '/api/notes/n1', '{"content":"x","replace_content":true,"body_revision":"r7","base_updated_at":"u7"}'],
  ]);
});

test('share targets: the account list only when the server says it is a directory', async () => {
  const { api } = fakeServer([{ status: 200, body: { users: [], directory: false } }, { status: 200, body: { users: [{ username: 'bob' }], directory: true } }, { status: 200, body: {} }]);
  assert.deepEqual(await api.shareTargets(), { users: [], directory: false });
  assert.deepEqual(await api.shareTargets(), { users: [{ username: 'bob' }], directory: true });
  assert.deepEqual(await api.shareTargets(), { users: [], directory: true }, 'an older server without the flag still lists');
});

test('a failed call keeps its status and body for the workflow, or a null body when there is none', async () => {
  const newer = { detail: { message: 'Note changed elsewhere', note: { id: 'n1', updated_at: 'v9' } } };
  const failing = [
    ['patchNote', ['n1', {}], 'Failed to update note'], ['toggleTask', ['n1', 0, 'x'], 'Failed to toggle item'],
    ['getNote', ['n1'], 'Failed to load note'], ['agentUndo', ['n1'], 'Failed to undo the agent edit'],
    ['listVersions', ['n1'], 'Failed to load the history'], ['getVersion', ['n1', 1], 'Failed to load the version'],
    ['restoreVersion', ['n1', 1, 'v1'], 'Failed to restore the version'], ['shareTargets', [], 'Failed to load users'],
    ['listShares', ['n1'], 'Failed to load sharing'], ['share', ['n1', ['bob'], 'view'], 'Failed to share note'],
    ['unshare', ['n1', 'bob'], 'Failed to update sharing'],
  ];
  for (const [name, args, message] of failing) {
    const { api } = fakeServer([{ status: 409, body: newer }]);
    await assert.rejects(api[name](...args), error => error.message === message && error.status === 409 && error.body === newer, name);
    const { api: bare } = fakeServer([{ status: 404, body: undefined }]);
    await assert.rejects(bare[name](...args), error => error.status === 404 && error.body === null, `${name} without a body`);
  }
  // These three only say that they failed.
  for (const [name, args, message] of [['saveNote', [{ title: 'x' }], 'Failed to save note'], ['appendTask', ['n1', 'x'], 'Failed to add item'], ['deleteNote', ['n1'], 'HTTP 500']]) {
    const { api } = fakeServer([{ status: 500, body: {} }]);
    await assert.rejects(api[name](...args), { message });
  }
});

test('the list re-read sends the last ETag so an unchanged list costs a 304', async () => {
  const seen = [];
  const api = createNotesApi({ origin: '', request: async (url, init) => { seen.push(init.headers || null); return { ok: true }; } });
  await api.listNotes(false, { etag: 'W/"abc"' });
  await api.listNotes(false);
  assert.deepEqual(seen, [{ 'If-None-Match': 'W/"abc"' }, null]);
});

test('a save or an added task refused as too long keeps the 413 and the server\'s reason', async () => {
  const tooLong = { detail: { message: 'The note\'s text is too long to save: 1,000,001 characters, the limit is 1,000,000.', field: 'content', limit: 1000000 } };
  for (const [name, args] of [['saveNote', [{ title: 'x' }]], ['saveNote', [{ id: 'n1', title: 'x' }]], ['appendTask', ['n1', 'x']]]) {
    const { api } = fakeServer([{ status: 413, body: tooLong }]);
    await assert.rejects(api[name](...args), error => error.status === 413 && error.body === tooLong, name);
  }
});
