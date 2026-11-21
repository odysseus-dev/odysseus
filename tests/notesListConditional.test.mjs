import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesApi } from '../frontend/src/features/notes/infrastructure/http.js';

// The panel's 20 s re-read sends the list's last ETag, so an unchanged list
// costs a 304 with no body; any other read of the list stays unconditional.
test('listNotes sends If-None-Match only when given the last tag', async () => {
  const sent = [];
  const api = createNotesApi({ origin: '', request: async (url, init) => { sent.push([url, init]); return { ok: true, status: 200 }; } });
  await api.listNotes(false, { etag: '"abc"' });
  await api.listNotes(true, { etag: '"def"' });
  await api.listNotes(false);
  await api.listNotes(false, { etag: null });
  assert.deepEqual(sent, [
    ['/api/notes', { credentials: 'same-origin', headers: { 'If-None-Match': '"abc"' } }],
    ['/api/notes?archived=true', { credentials: 'same-origin', headers: { 'If-None-Match': '"def"' } }],
    ['/api/notes', { credentials: 'same-origin' }],
    ['/api/notes', { credentials: 'same-origin' }],
  ]);
});
