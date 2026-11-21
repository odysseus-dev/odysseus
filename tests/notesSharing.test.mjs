import test from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';
import { parseHtml } from './helpers/miniDom.mjs';
import { serializeNodes, joinSegments, splitMarkdownBlocks, storedImageSrc } from '../frontend/src/features/notes/domain/markdownSerializer.js';
import { _hasActiveReminder, isOwnNote, canEditNote, isSharedNote, shareBadge, topSortOrder } from '../frontend/src/features/notes/domain/noteRules.js';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNoteSession } from '../frontend/src/features/notes/application/noteSession.js';

const { mdToHtml } = await loadMarkdown();
const UPLOAD = 'a'.repeat(32) + '.png';
const NOTES = { tasks: 'interactive', images: 'notes', dialect: 'notes' };
const shared = md => mdToHtml(md, { ...NOTES, mediaNote: 'note-1' });
const srcs = html => [...html.matchAll(/<img[^>]* src="([^"]*)"/g)].map(m => m[1]);

test('a shared note loads uploads through the note; an unshared one does not', () => {
  const md = `![](/api/upload/${UPLOAD})\n\n![](/api/upload/${UPLOAD} "w=33")`;
  assert.deepEqual(srcs(shared(md)), [`/api/notes/note-1/media/${UPLOAD}`, `/api/notes/note-1/media/${UPLOAD}`]);
  assert.deepEqual(srcs(mdToHtml(md, NOTES)), [`/api/upload/${UPLOAD}`, `/api/upload/${UPLOAD}`]);
});

test('the media rewrite leaves other images and chat alone', () => {
  const data = 'data:image/png;base64,iVBORw0KGgo=';
  assert.deepEqual(srcs(shared(`![](${data})`)), [data]);
  assert.deepEqual(srcs(shared(`![](/api/upload/${UPLOAD}?thumb=1)`)), [`/api/upload/${UPLOAD}?thumb=1`]);
  assert.match(shared('![](https://evil.example/x.png)'), /<a [^>]*href="https:\/\/evil\.example\/x\.png"/);
  // Chat never passes images:'notes', so mediaNote has no effect there.
  assert.equal(mdToHtml(`![](/api/upload/${UPLOAD})`, { mediaNote: 'note-1' }), mdToHtml(`![](/api/upload/${UPLOAD})`));
  // The note id is a path segment, never markup.
  assert.deepEqual(srcs(mdToHtml(`![](/api/upload/${UPLOAD})`, { ...NOTES, mediaNote: '"><x' })), [`/api/notes/%22%3E%3Cx/media/${UPLOAD}`]);
});

test('edits in a shared note store the upload path, not the note-scoped one', () => {
  assert.equal(storedImageSrc(`/api/notes/note-1/media/${UPLOAD}`), `/api/upload/${UPLOAD}`);
  assert.equal(storedImageSrc(`/api/upload/${UPLOAD}`), `/api/upload/${UPLOAD}`);
  assert.equal(storedImageSrc('/api/notes/note-1/media/../../x'), '/api/notes/note-1/media/../../x');
  for (const md of [`![](/api/upload/${UPLOAD})`, `Before\n\n![receipt](/api/upload/${UPLOAD} "w=66")\n\n- [ ] after`]) {
    const out = joinSegments(splitMarkdownBlocks(md).map((block, group) => ({
      group, lines: serializeNodes(parseHtml(shared(block.source)).childNodes).lines,
    })));
    assert.equal(out, md);
  }
});

test('sharing rules read the payload, and default to mine for old payloads', () => {
  const legacy = { id: 'n' };
  assert.equal(isOwnNote(legacy), true);
  assert.equal(canEditNote(legacy), true);
  assert.equal(isSharedNote(legacy), false);
  assert.equal(shareBadge(legacy), '');
  const mine = { id: 'n', is_owner: true, can_edit: true, is_shared: true, shared_with: [{ username: 'bob' }, { username: 'carol' }] };
  assert.equal(isSharedNote(mine), true);
  assert.equal(shareBadge(mine), 'Shared · 2');
  const viewer = { id: 'n', is_owner: false, can_edit: false, shared_by: 'alice', is_shared: true, shared_with: [] };
  assert.equal(isOwnNote(viewer), false);
  assert.equal(canEditNote(viewer), false);
  assert.equal(shareBadge(viewer), 'alice');
});

test('a shared note never sets my top sort order', () => {
  const notes = [{ id: 'a', sort_order: 0 }, { id: 'b', sort_order: -50, is_owner: false }];
  assert.equal(topSortOrder(notes), -1);
});

test('a past reminder is active on my note only, never on one shared with me', () => {
  const clock = { now: () => Date.parse('2026-10-28T12:00:00') };
  const due = '2026-10-28T11:00';
  assert.equal(_hasActiveReminder({ id: 'a', due_date: due }, clock), true, 'an old payload is mine');
  assert.equal(_hasActiveReminder({ id: 'a', due_date: due, is_owner: true, is_shared: true }, clock), true, 'mine, shared with others');
  assert.equal(_hasActiveReminder({ id: 'b', due_date: due, is_owner: false, can_edit: true }, clock), false, 'the owner\'s reminder on a note shared with me');
  assert.equal(_hasActiveReminder({ id: 'a', due_date: '2026-10-28T13:00' }, clock), false, 'not yet due');
});

test('a view-only session never saves, backs up or conflicts', async () => {
  const calls = [];
  const data = new Map();
  const timers = [];
  const session = createNoteSession({
    api: { patchNote: async (...a) => { calls.push(a); return {}; }, saveNote: async (...a) => { calls.push(a); return {}; } },
    persistence: createNotesPersistence({ storage: { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) } }),
    note: { id: 'note1', title: 'T', content: 'C', updated_at: 'v1' },
    clock: { now: () => 1 },
    scheduler: { setTimeout: f => { timers.push(f); return timers.length; }, clearTimeout: () => {} },
    collect: () => ({ title: 'T', content: 'changed' }),
    readOnly: () => true,
  });
  session.markDirty();
  timers.forEach(f => f());
  await session.flush();
  await session.close();
  assert.deepEqual(calls, []);
  assert.equal(data.size, 0);
  assert.equal(session.dirty, false);
});
