import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createCardOperations } from '../frontend/src/features/notes/application/cardOperations.js';
import { createChecklistOperations } from '../frontend/src/features/notes/application/checklistOperations.js';
import { createNoteVersions } from '../frontend/src/features/notes/application/noteVersions.js';
import { topSortOrder } from '../frontend/src/features/notes/domain/noteRules.js';
import { createSelectionOperations } from '../frontend/src/features/notes/application/selection.js';
import { createNotesPersistence } from '../frontend/src/features/notes/infrastructure/storage.js';
import { createNotesLifecycle } from '../frontend/src/features/notes/ui/lifecycle.js';
import { createText } from '../frontend/src/features/notes/ui/text.js';
import { createDeletion } from '../frontend/src/features/notes/application/deletion.js';
import { createNotesWorkflows } from '../frontend/src/features/notes/application/workflows.js';

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

test('note window deletion waits for confirmation and preserves optimistic failure/refetch timing', async () => {
  const app = owner([{ id: 'a' }]), events = [];
  let confirm, fail;
  const operations = createDeletion({ app, state: app.state,
    _deleteNoteApi: id => { events.push('delete:' + id); return new Promise((_resolve, reject) => { fail = reject; }); },
    _fetchNotes: async () => { events.push('fetch'); app.replaceNotes([{ id: 'a' }]); },
    _renderNotes: () => events.push('render'),
    effects: { styledConfirm: () => new Promise(resolve => { confirm = resolve; }), showToast: () => events.push('toast'), showError: error => events.push(error) },
  });
  const asked = operations.confirmDelete();
  assert.equal(app.state.notes.length, 1);
  confirm(false);
  assert.equal(await asked, false);
  assert.deepEqual(events, []);
  const accepted = operations.confirmDelete();
  confirm(true);
  assert.equal(await accepted, true);
  operations.deleteNow('a');
  assert.equal(app.state.notes.length, 0);
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

test('failed checklist actions leave the current body in place', async () => {
  const original = { id: 'a', body_revision: 'first', content: '- [ ] A', items: [{ text: 'A', done: false }] };
  const app = owner([original]);
  let reject;
  const offline = async () => { throw Error('offline'); };
  const operations = createChecklistOperations({
    app, api: { patchNote: offline, toggleTask: () => new Promise((_, fail) => { reject = fail; }), appendTask: offline },
    effects: { render() {}, showError() {} },
  });
  await operations.removeItem('a', 0, 'first');
  await operations.addItem('a', 'C', assert.fail);
  await operations.editItem(app.state.notes[0], 0, 'New');
  assert.equal(app.state.notes[0].content, '- [ ] A');
  const toggle = operations.toggleItem('a', 0, assert.fail, assert.fail, 'first');
  app.mergeNote(app.state.notes[0], { body_revision: 'second', content: '- [x] New', items: [{ text: 'New', done: true }] });
  reject(Error('offline'));
  await toggle;
  assert.equal(app.state.notes[0].content, '- [x] New');
  assert.equal(app.state.notes[0].items[0].text, 'New');
});

test('toggle conflict reloads the reordered note and confirmed actions use its revision', async () => {
  const calls = [];
  const first = { id: 'a', body_revision: 'first', content: '- [ ] A\n- [ ] B', items: [{ text: 'A', done: false }, { text: 'B', done: false }] };
  const reordered = { ...first, body_revision: 'reordered', content: '- [ ] B\n- [ ] A', items: first.items.slice().reverse() };
  const appended = { ...reordered, body_revision: 'appended', content: reordered.content + '\n- [ ] C', items: [...reordered.items, { text: 'C', done: false }] };
  const done = { ...appended, body_revision: 'done', content: '- [x] B\n- [ ] A\n- [ ] C', items: [{ text: 'B', done: true }, ...appended.items.slice(1)] };
  const app = owner([first]);
  const api = {
    async getNote(id) { calls.push(['get', id]); return reordered; },
    async toggleTask(id, index, rev) {
      calls.push(['toggle', id, index, rev]);
      if (rev === 'first') throw Object.assign(Error('conflict'), { status: 409 });
      return { note: done };
    },
    async appendTask(id, text) { calls.push(['append', id, text]); return { note: appended }; },
  };
  const operations = createChecklistOperations({ app, api, effects: { render() {}, showError() {} } });
  await operations.toggleItem('a', 0, assert.fail, assert.fail, 'first');
  assert.equal(app.state.notes[0].content, reordered.content);
  await operations.addItem('a', 'C', () => {});
  await operations.toggleTodayStep('a', 0, checked => assert.equal(checked, true), assert.fail, 'appended');
  assert.deepEqual(calls, [['toggle', 'a', 0, 'first'], ['get', 'a'], ['append', 'a', 'C'], ['toggle', 'a', 0, 'appended']]);
  assert.equal(app.state.notes[0].content, done.content);
});

// Server ID replacement and newer-draft recovery for a new note now live in
// the note session (tests/notesDraftAutosave.test.mjs); the ordering rule is here.
test('an edited or new note sorts above every unpinned note, pinned ones ignored', () => {
  const notes = [{ id: 'existing', sort_order: -2 }, { id: 'pinned', pinned: true, sort_order: -9 }, { id: 'self', sort_order: -5 }];
  assert.equal(topSortOrder(notes), -6);
  assert.equal(topSortOrder(notes, 'self'), -3, 'the note being saved does not count against itself');
  assert.equal(topSortOrder([]), -1);
});

test('same-tab writes are recorded so the note window can tell them from remote ones', async () => {
  const app = owner([{ id: 'a', body_revision: 'v1' }]);
  const versions = createNoteVersions({ app });
  const api = versions.track({
    patchNote: async id => ({ id, body_revision: 'v2' }),
    toggleTask: async id => ({ ok: true, note: { id, body_revision: 'v3' } }),
    appendTask: async id => ({ ok: true, note: { id, body_revision: 'v4' } }),
    saveNote: async () => ({ id: 'b', body_revision: 'b1' }),
    listNotes: () => 'untouched',
  });
  await api.patchNote('a', {});
  await api.toggleTask('a', 0);
  await api.appendTask('a', 'x');
  await api.saveNote({});
  assert.deepEqual(['v2', 'v3', 'v4'].map(v => versions.isLocal('a', v)), [true, true, true]);
  assert.equal(versions.isLocal('a', 'v9'), false);
  assert.equal(versions.isLocal('b', 'b1'), true);
  assert.equal(api.listNotes(), 'untouched');
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

test('render helpers escape link attributes', () => {
  const helpers = createText({ uiModule: { esc: value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;') } });
  assert.equal(helpers._attrEsc('"\'<>`'), '&quot;&#39;&lt;&gt;&#96;');
  assert.match(helpers._linkify('<script> https://example.org/test'), /&lt;script&gt;/);
  assert.doesNotMatch(helpers._linkify('<script> https://example.org/test'), /<script>/i);
});

// A delete that fails because the network is down re-reads the list to put
// the card back. That re-read fails too; it used to replace the list with
// nothing, so every card disappeared until a later read succeeded. The card
// goes back in its place and a failed re-read keeps the list, like the 20 s
// re-read does.
test('a delete that fails offline keeps every card on screen', async () => {
  const app = owner([{ id: 'a', title: 'Mine' }, { id: 's', title: 'Second' }, { id: 'b', title: 'Other' }]);
  const offline = async () => { throw new TypeError('Failed to fetch'); };
  const workflows = createNotesWorkflows({ app, api: { listNotes: offline, patchNote: offline, deleteNote: offline, saveNote: offline }, effects: { render() {}, showError() {}, archived() {} } });
  const deletion = createDeletion({ app, state: app.state,
    _deleteNoteApi: workflows._deleteNoteApi, _fetchNotes: workflows._fetchNotes,
    _isPastReminder: () => false, _renderNotes: () => {}, effects: { showToast() {}, showError() {} },
  });
  const original = console.error;
  console.error = () => {};
  try {
    await deletion.deleteNow('a');
    await drain();
  } finally { console.error = original; }
  assert.deepEqual(app.state.notes.map(n => n.id), ['a', 's', 'b'], 'the list should stay as it was, in the same order');
});
