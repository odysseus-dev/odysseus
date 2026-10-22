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
