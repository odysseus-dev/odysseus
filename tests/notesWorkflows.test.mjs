import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createCardOperations } from '../frontend/src/features/notes/application/cardOperations.js';
import { createChecklistOperations } from '../frontend/src/features/notes/application/checklistOperations.js';
import { createNotesSubmission } from '../frontend/src/features/notes/application/submission.js';
import { createSelectionOperations } from '../frontend/src/features/notes/application/selection.js';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNotesLifecycle } from '../frontend/src/features/notes/ui/lifecycle.js';
import { createText } from '../frontend/src/features/notes/ui/text.js';
import { createDeletion } from '../frontend/src/features/notes/application/deletion.js';

const drain = () => new Promise(setImmediate);
function owner(notes) { const app = createNotesState(); app.replaceNotes(notes); return app; }

test('reorder retains desktop persistence-only and mobile post-response state ordering', async () => {
  const app = owner([{ id: 'a', sort_order: 9 }, { id: 'b', sort_order: 8 }]), requests = [];
  let resolve;
  const operations = createCardOperations({ app, api: { reorder: ids => { requests.push(ids); return new Promise(done => { resolve = done; }); } } });
  const desktop = operations.persistOrder(['b', 'a']);
  resolve({ ok: false }); await desktop;
  assert.deepEqual(app.state.notes.map(note => note.sort_order), [9, 8]);
  const mobile = operations.commitMobileOrder(['b', 'a']);
  assert.deepEqual(app.state.notes.map(note => note.sort_order), [9, 8]);
  resolve({ ok: false }); await mobile;
  assert.deepEqual(app.state.notes.map(note => note.sort_order), [1, 0]);
  assert.deepEqual(requests, [['b', 'a'], ['b', 'a']]);
});

test('editor deletion waits for confirmation and preserves optimistic failure/refetch timing', async () => {
  const app = owner([{ id: 'a' }]), events = [];
  app.setEditingId('a');
  let confirm, fail;
  const operations = createDeletion({ app, state: app.state,
    _deleteNoteApi: id => { events.push('delete:' + id); return new Promise((_resolve, reject) => { fail = reject; }); },
    _fetchNotes: async () => { events.push('fetch'); app.replaceNotes([{ id: 'a' }]); },
    _renderNotes: () => events.push('render'),
    effects: { styledConfirm: () => new Promise(resolve => { confirm = resolve; }), showToast: () => events.push('toast'), showError: error => events.push(error) },
  });
  const deletion = operations.deleteFromEditor('a');
  assert.equal(app.state.notes.length, 1);
  confirm(false); await deletion;
  assert.deepEqual(events, []);
  const accepted = operations.deleteFromEditor('a');
  confirm(true); await accepted;
  assert.equal(app.state.notes.length, 0);
  assert.equal(app.state.editingId, null);
  assert.deepEqual(events, ['render', 'delete:a']);
  fail(Error('403')); await drain();
  assert.equal(app.state.notes[0].id, 'a');
  assert.deepEqual(events, ['render', 'delete:a', 'Failed to delete', 'fetch', 'render']);
});

test('pin and delete failures roll back captured state and order', async () => {
  const app = owner([{ id: 'a', pinned: false, sort_order: 4 }, { id: 'b', pinned: true, sort_order: -3 }]), errors = [];
  const operations = createCardOperations({ app, api: { patchNote: async () => { throw Error('403'); }, deleteNote: async () => { throw Error('403'); } }, effects: { render() {}, showError: error => errors.push(error) } });
  operations.togglePin('a');
  assert.equal(app.state.notes[0].pinned, true);
  assert.equal(app.state.notes[0].sort_order, -4);
  await drain();
  assert.equal(app.state.notes[0].pinned, false);
  assert.equal(app.state.notes[0].sort_order, 4);
  operations.deleteOptimistically('a');
  assert.deepEqual(app.state.notes.map(n => n.id), ['b']);
  await drain();
  assert.deepEqual(app.state.notes.map(n => n.id), ['a', 'b']);
  assert.deepEqual(errors, ['Failed to pin', 'Failed to delete']);
});

test('item removal, quick add and checkbox errors preserve checklist contents', async () => {
  const app = owner([{ id: 'a', items: [{ id: 'first', text: 'A', done: false }, { id: 'second', text: 'B', done: true }] }]);
  const operations = createChecklistOperations({ app, api: { patchNote: async () => { throw Error('offline'); } }, uid: () => 'new', effects: { render() {}, showError() {} } });
  operations.removeItem('a', 0);
  assert.equal(app.state.notes[0].items[0].id, 'second');
  await drain();
  assert.deepEqual(app.state.notes[0].items.map(it => it.id), ['first', 'second']);
  const done = [];
  operations.toggleItem('a', 0, value => done.push(value), () => {});
  await drain();
  assert.deepEqual(done, [true, false]);
  const persist = operations.addItem('a', 'C', () => {});
  assert.equal(app.state.notes[0].items.length, 3);
  persist(); await drain();
  assert.equal(app.state.notes[0].items.length, 2);
  operations.editItem(app.state.notes[0], 1, 'Changed');
  await drain();
  assert.equal(app.state.notes[0].items[1].text, 'B');
});

test('submission preserves top ordering, server ID replacement and newer draft recovery', async () => {
  const app = owner([{ id: 'existing', sort_order: -2 }]), values = new Map();
  const persistence = createNotesPersistence({ storage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } });
  const draftKey = persistence._draftKey('__new__');
  values.set(draftKey, '{"content":"submitted"}');
  let resolve, renders = 0;
  const operations = createNotesSubmission({ app, persistence, clock: { now: () => 0 }, uid: () => 'id', api: { saveNote: () => new Promise(r => { resolve = r; }) }, effects: { render: () => renders++, showError() {} }, fetchNotes: async () => {} });
  operations.submitNote({ title: 'New' }, null, false);
  assert.equal(app.state.notes[0].id, 'tmp_id');
  assert.equal(app.state.notes[0].sort_order, -3);
  values.set(draftKey, '{"content":"newer"}');
  resolve({ id: 'server-id' }); await drain();
  assert.equal(app.state.notes[0].id, 'server-id');
  assert.equal(values.get(draftKey), '{"content":"newer"}');
  assert.equal(renders, 2);
});

test('selection can exit only after synchronous acceptance', () => {
  const app = owner([]), order = [];
  app.selectId('a');
  let accepted = false;
  const operations = createSelectionOperations({ app, sendContext: detail => { order.push('dispatch'); detail.accepted = accepted; }, exitSelection: () => order.push('exit'), effects: { showToast: () => order.push('rejected') } });
  operations.attachSelected();
  assert.deepEqual(order, ['dispatch', 'rejected']);
  accepted = true; operations.attachSelected();
  assert.deepEqual(order, ['dispatch', 'rejected', 'dispatch', 'exit']);
});

test('listener owners release on replacement and disposal is idempotent', () => {
  const previous = { document: globalThis.document, MutationObserver: globalThis.MutationObserver };
  let check, disconnected = 0;
  globalThis.document = { body: {} };
  globalThis.MutationObserver = class { constructor(callback) { check = callback; } observe() {} disconnect() { disconnected++; } };
  try {
    const lifecycle = createNotesLifecycle(), target = new EventTarget(), canvas = { isConnected: true };
    let calls = 0;
    lifecycle.listen(canvas, target, 'mouseup', () => calls++);
    let released = 0;
    lifecycle.onDispose(canvas, () => released++);
    target.dispatchEvent(new Event('mouseup'));
    canvas.isConnected = false; check();
    target.dispatchEvent(new Event('mouseup'));
    lifecycle.dispose(); lifecycle.dispose();
    assert.equal(calls, 1); assert.equal(disconnected, 1);
    assert.equal(released, 1);
  } finally {
    for (const [name, value] of Object.entries(previous)) if (value === undefined) delete globalThis[name]; else globalThis[name] = value;
  }
});

test('render helpers reject script-capable media and escape URL attributes', () => {
  const helpers = createText({ uiModule: { esc: value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;') } });
  for (const value of ['javascript:alert(1)', 'data:image/svg+xml;base64,abc', 'data:text/html;base64,abc']) assert.equal(helpers._safeImgSrc(value), '');
  assert.equal(helpers._safeImgSrc('data:image/png;base64,abc'), 'data:image/png;base64,abc');
  assert.equal(helpers._attrEsc('"\'<>`'), '&quot;&#39;&lt;&gt;&#96;');
  assert.match(helpers._linkify('<script> https://example.org/test'), /&lt;script&gt;/);
  assert.doesNotMatch(helpers._linkify('<script> https://example.org/test'), /<script>/i);
});
