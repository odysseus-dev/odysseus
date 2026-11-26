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

test('item removal, quick add and checkbox errors preserve checklist contents', async () => {
  const content = 'Intro\n- [ ] A\n- [x] B';
  const app = owner([{ id: 'a', content, items: [{ id: 'first', text: 'A', done: false }, { id: 'second', text: 'B', done: true }] }]);
  const offline = async () => { throw Error('offline'); };
  const operations = createChecklistOperations({ app, api: { patchNote: offline, toggleTask: offline, appendTask: offline }, uid: () => 'new', effects: { render() {}, showError() {} } });
  operations.removeItem('a', 0);
  assert.equal(app.state.notes[0].items[0].id, 'second');
  assert.equal(app.state.notes[0].content, 'Intro\n- [x] B');
  await drain();
  assert.deepEqual(app.state.notes[0].items.map(it => it.id), ['first', 'second']);
  assert.equal(app.state.notes[0].content, content);
  const done = [];
  operations.toggleItem('a', 0, value => done.push(value), () => {});
  assert.equal(app.state.notes[0].content, 'Intro\n- [x] A\n- [x] B');
  await drain();
  assert.deepEqual(done, [true, false]);
  assert.equal(app.state.notes[0].content, content);
  const persist = operations.addItem('a', 'C', () => {});
  assert.equal(app.state.notes[0].items.length, 3);
  assert.equal(app.state.notes[0].content, content + '\n- [ ] C');
  persist(); await drain();
  assert.equal(app.state.notes[0].items.length, 2);
  assert.equal(app.state.notes[0].content, content);
  operations.editItem(app.state.notes[0], 1, 'Changed');
  assert.equal(app.state.notes[0].content, 'Intro\n- [ ] A\n- [x] Changed');
  await drain();
  assert.equal(app.state.notes[0].items[1].text, 'B');
  assert.equal(app.state.notes[0].content, content);
});

// A failed tick must not put back the body it started from over a newer copy
// that arrived while it was out (PR #7313 review).
test('a failed card tick leaves a body that changed meanwhile in place', async () => {
  const app = owner([{ id: 'a', body_revision: 'first', content: '- [ ] A', items: [{ text: 'A', done: false }] }]);
  let reject;
  const operations = createChecklistOperations({
    app, api: { toggleTask: () => new Promise((_, fail) => { reject = fail; }) }, uid: () => 'new',
    effects: { render() {}, showError() {} },
  });
  const changed = [];
  const toggle = operations.toggleItem('a', 0, value => changed.push(value), () => {});
  await drain();
  app.mergeNote(app.state.notes[0], { body_revision: 'second', content: '- [x] New', items: [{ text: 'New', done: true }] });
  reject(Error('offline'));
  await toggle;
  assert.equal(app.state.notes[0].content, '- [x] New');
  assert.equal(app.state.notes[0].items[0].text, 'New');
  assert.equal(app.state.notes[0].body_revision, 'second');
  assert.deepEqual(changed, [true], 'the box is not flipped back over the newer copy');
});

test('a card tick refused without a copy reads the note again, and the next writes use its revision', async () => {
  const calls = [];
  const first = { id: 'a', body_revision: 'first', content: '- [ ] A\n- [ ] B', items: [{ text: 'A', done: false }, { text: 'B', done: false }] };
  const reordered = { ...first, body_revision: 'reordered', content: '- [ ] B\n- [ ] A', items: first.items.slice().reverse() };
  const appended = { ...reordered, body_revision: 'appended', content: reordered.content + '\n- [ ] C', items: [...reordered.items, { text: 'C', done: false }] };
  const done = { ...appended, body_revision: 'done', content: '- [x] B\n- [ ] A\n- [ ] C', items: [{ text: 'B', done: true }, ...appended.items.slice(1)] };
  const app = owner([first]);
  const api = {
    async getNote(id) { calls.push(['get', id]); return reordered; },
    async toggleTask(id, index, text, state, rev) {
      calls.push(['toggle', id, index, text, state, rev]);
      if (rev === 'first') throw Object.assign(Error('conflict'), { status: 409, body: { detail: 'Note changed' } });
      return { note: done };
    },
    async appendTask(id, text) { calls.push(['append', id, text]); return { note: appended }; },
  };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError() {} } });
  await operations.toggleItem('a', 0, () => {}, () => {});
  assert.equal(app.state.notes[0].content, reordered.content);
  assert.equal(app.state.notes[0].body_revision, 'reordered');
  await operations.addItem('a', 'C', () => {})();
  assert.equal(app.state.notes[0].body_revision, 'appended');
  await operations.toggleTodayStep('a', 0, () => {}, () => {});
  assert.deepEqual(calls, [['toggle', 'a', 0, 'A', true, 'first'], ['get', 'a'], ['append', 'a', 'C'], ['toggle', 'a', 0, 'B', true, 'appended']]);
  assert.equal(app.state.notes[0].content, done.content);
  assert.equal(app.state.notes[0].body_revision, 'done');
});

test('checkbox and quick add use the toggle and append endpoints, not a whole-list PUT', async () => {
  const calls = [];
  const app = owner([{ id: 'a', body_revision: 'r1', content: '- [ ] A', items: [{ text: 'A', done: false }] }]);
  const api = {
    patchNote: async (...args) => { calls.push(['patch', ...args]); },
    toggleTask: async (...args) => { calls.push(['toggle', ...args]); },
    appendTask: async (...args) => { calls.push(['append', ...args]); },
  };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError() {} } });
  operations.toggleItem('a', 0, () => {}, () => {});
  operations.addItem('a', 'B', () => {})();
  await operations.toggleTodayStep('a', 1, () => {}, () => {});
  await drain();
  assert.deepEqual(calls, [['toggle', 'a', 0, 'A', true, 'r1'], ['append', 'a', 'B'], ['toggle', 'a', 1, 'B', true, 'r1']], 'a tick names the task it was made on, the state asked for and the body revision');
  assert.equal(app.state.notes[0].content, '- [x] A\n- [x] B');
});

test('a card tick sends the state the box shows, and redraws when the server copy differs', async () => {
  // Another device already ticked A; this card still shows it open.
  const app = owner([{ id: 'a', body_revision: 'r1', content: '- [ ] A\n- [ ] B', items: [{ text: 'A', done: false }, { text: 'B', done: false }] }]);
  const calls = [];
  let renders = 0;
  let server = { id: 'a', body_revision: 'r2', content: '- [x] A\n- [ ] B', items: [{ index: 0, text: 'A', done: true }, { index: 1, text: 'B', done: false }] };
  const api = { toggleTask: async (...args) => { calls.push(args); return { ok: true, note: server }; } };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() { renders += 1; }, showError() {} } });
  operations.toggleItem('a', 0, () => {}, () => {});
  await drain();
  assert.deepEqual(calls, [['a', 0, 'A', true, 'r1']], 'a tick asks for done, not for a flip');
  assert.equal(renders, 0, 'the server agrees with the card: nothing to redraw');
  // Unticking B while someone else unticked A: the card must show A open again.
  server = { id: 'a', body_revision: 'r3', content: '- [ ] A\n- [x] B', items: [{ index: 0, text: 'A', done: false }, { index: 1, text: 'B', done: true }] };
  operations.toggleItem('a', 1, () => {}, () => {});
  await drain();
  assert.deepEqual(calls[1], ['a', 1, 'B', true, 'r2'], 'the second tick sends the revision the first one\'s answer left');
  assert.equal(renders, 1, 'the card was changed in place, so a different server copy is drawn');
  assert.equal(app.state.notes[0].content, '- [ ] A\n- [x] B');
  assert.equal(app.state.notes[0].items[0].done, false);
});

test('a card write tells the note window once it has landed, and only then', async () => {
  const app = owner([{ id: 'a', content: '- [ ] A', items: [{ text: 'A', done: false }] }]);
  const followed = [];
  let fail = false;
  const answer = async () => { if (fail) throw new Error('offline'); };
  const api = { patchNote: answer, toggleTask: answer, appendTask: answer };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError() {}, followed: id => followed.push(id) } });
  operations.toggleItem('a', 0, () => {}, () => {});
  assert.deepEqual(followed, [], 'not before the server answers');
  await drain();
  operations.addItem('a', 'B', () => {})();
  await drain();
  assert.deepEqual(followed, ['a', 'a']);
  fail = true;
  operations.toggleItem('a', 0, () => {}, () => {});
  operations.addItem('a', 'C', () => {})();
  await drain();
  assert.deepEqual(followed, ['a', 'a'], 'a failed write is not followed');
});

test('a card write waits for the note window\'s pending save, then takes the server copy', async () => {
  const app = owner([{ id: 'a', content: 'Typed\n\n- [ ] A', items: [{ text: 'A', done: false }] }]);
  const order = [];
  let release;
  const windowSave = new Promise(resolve => { release = resolve; });
  const api = {
    toggleTask: async () => { order.push('toggle'); return { ok: true, note: { id: 'a', content: 'Typed more\n\n- [x] A', items: [{ index: 0, text: 'A', done: true, indent: 0 }] } }; },
  };
  const effects = { render() {}, showError() {}, beforeWrite: id => { order.push(`settle ${id}`); return windowSave.then(() => order.push('window saved')); }, followed: () => order.push('followed') };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects });
  operations.toggleItem('a', 0, () => {}, () => {});
  assert.equal(app.state.notes[0].items[0].done, true, 'the card ticks at once');
  await drain();
  assert.deepEqual(order, ['settle a'], 'no toggle while the window is still saving');
  release();
  await drain();
  assert.deepEqual(order, ['settle a', 'window saved', 'toggle', 'followed']);
  assert.equal(app.state.notes[0].content, 'Typed more\n\n- [x] A', 'the list holds the server copy, window text included');
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
  const app = owner([{ id: 'a', content: 'x', updated_at: 'u1', body_revision: 'v1' }]);
  const versions = createNoteVersions({ app });
  const api = versions.track({
    patchNote: async id => ({ id, updated_at: 'u2', body_revision: 'v2' }),
    toggleTask: async id => ({ ok: true, note: { id, updated_at: 'u3', body_revision: 'v3' } }),
    appendTask: async id => ({ ok: true, note: { id, updated_at: 'u4', body_revision: 'v4' } }),
    saveNote: async () => ({ id: 'b', updated_at: 'b1' }),
    listNotes: () => 'untouched',
  });
  await api.patchNote('a', {});
  assert.equal(app.state.notes[0].updated_at, 'u2');
  assert.equal(app.state.notes[0].body_revision, 'v2', 'both tokens move on together');
  await api.toggleTask('a', 0);
  await api.appendTask('a', 'x');
  await api.saveNote({});
  assert.deepEqual(['v2', 'v3', 'v4', 'u2', 'u3', 'u4'].map(v => versions.isLocal('a', v)), [true, true, true, true, true, true], 'either token names the version');
  assert.equal(versions.isLocal('a', 'v9'), false);
  assert.equal(versions.isLocal('a', undefined), false);
  assert.equal(versions.isLocal('b', 'b1'), true);
  assert.equal(api.listNotes(), 'untouched');
});

test('a tracked write whose body differs from the list copy does not move that copy\'s tokens', async () => {
  const app = owner([{ id: 'a', content: 'shown', updated_at: 'u1', body_revision: 'v1' }]);
  const versions = createNoteVersions({ app });
  const api = versions.track({ toggleTask: async id => ({ ok: true, note: { id, content: 'newer elsewhere', updated_at: 'u2', body_revision: 'v2' } }) });
  await api.toggleTask('a', 0);
  assert.deepEqual([app.state.notes[0].updated_at, app.state.notes[0].body_revision], ['u1', 'v1'], 'the caller merges the body and its tokens together');
  assert.equal(versions.isLocal('a', 'v2'), true);
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

const conflict = note => Object.assign(new Error('Note changed elsewhere'), { status: 409, body: { detail: { message: 'Note changed elsewhere', note } } });

test('a card edit or removal sends the version it was made on', async () => {
  const calls = [];
  const app = owner([{ id: 'a', content: '- [ ] A\n- [ ] B', updated_at: 'v1', items: [{ text: 'A', done: false }, { text: 'B', done: false }] }]);
  const api = { patchNote: async (id, body) => { calls.push(body); return { id, updated_at: 'v2' }; } };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError() {} } });
  operations.editItem(app.state.notes[0], 1, 'B edited');
  await drain();
  assert.deepEqual(calls, [{ items: [{ text: 'A', done: false }, { text: 'B edited', done: false }], base_updated_at: 'v1' }]);
});

test('a card edit over a newer copy is redone on it, keeping the other person\'s tick', async () => {
  const calls = [], errors = [];
  const app = owner([{ id: 'a', content: '- [ ] A\n- [ ] B', updated_at: 'v1', items: [{ text: 'A', done: false }, { text: 'B', done: false }] }]);
  // Bob ticked A and added C since this card was drawn.
  const server = { id: 'a', content: '- [x] A\n- [ ] B\n- [ ] C', updated_at: 'v2', items: [{ index: 0, text: 'A', done: true }, { index: 1, text: 'B', done: false }, { index: 2, text: 'C', done: false }] };
  const api = { patchNote: async (id, body) => { calls.push(body); if (body.base_updated_at === 'v1') throw conflict(server); return { id, updated_at: 'v3' }; } };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError: e => errors.push(e) } });
  operations.editItem(app.state.notes[0], 1, 'B edited');
  await drain();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].base_updated_at, 'v2');
  assert.deepEqual(calls[1].items.map(it => [it.text, it.done]), [['A', true], ['B edited', false], ['C', false]]);
  assert.equal(app.state.notes[0].content, '- [x] A\n- [ ] B edited\n- [ ] C');
  assert.deepEqual(errors, []);
});

test('a card removal of an item that is gone elsewhere keeps the server copy', async () => {
  const calls = [], errors = [];
  const app = owner([{ id: 'a', content: '- [ ] A\n- [ ] B', updated_at: 'v1', items: [{ text: 'A', done: false }, { text: 'B', done: false }] }]);
  const server = { id: 'a', content: '- [ ] A\n- [ ] B2', updated_at: 'v2', items: [{ index: 0, text: 'A', done: false }, { index: 1, text: 'B2', done: false }] };
  const api = { patchNote: async (id, body) => { calls.push(body); throw conflict(server); } };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError: e => errors.push(e) } });
  operations.removeItem('a', 1);
  await drain();
  assert.equal(calls.length, 1, 'no second write when the item can not be found again');
  assert.equal(app.state.notes[0].content, '- [ ] A\n- [ ] B2');
  assert.deepEqual(errors, ['This checklist changed elsewhere. It has been refreshed.']);
});

test('a card tick on a task that changed elsewhere shows the server copy', async () => {
  const errors = [];
  const app = owner([{ id: 'a', content: '- [ ] A\n- [ ] B', updated_at: 'v1', items: [{ text: 'A', done: false }, { text: 'B', done: false }] }]);
  const server = { id: 'a', content: '- [ ] A\n- [ ] Renamed', updated_at: 'v2', items: [{ index: 0, text: 'A', done: false }, { index: 1, text: 'Renamed', done: false }] };
  const api = { toggleTask: async () => { throw conflict(server); } };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError: e => errors.push(e) } });
  operations.toggleItem('a', 1, () => {}, () => {});
  await drain();
  assert.equal(app.state.notes[0].content, '- [ ] A\n- [ ] Renamed');
  assert.equal(app.state.notes[0].items[1].done, false);
  assert.deepEqual(errors, ['This checklist changed elsewhere. It has been refreshed.']);
});

test('a card action on a task or note that is gone does nothing, and an unchanged edit only redraws', async () => {
  const calls = [];
  let renders = 0;
  const app = owner([{ id: 'a', content: '- [ ] A', updated_at: 'v1', items: [{ text: 'A', done: false }] }]);
  const record = name => async (...args) => { calls.push([name, ...args]); return {}; };
  const operations = createChecklistOperations({ app, api: { patchNote: record('patch'), toggleTask: record('toggle'), appendTask: record('append') }, uid: () => 'new', effects: { render() { renders += 1; }, showError() {} } });
  operations.removeItem('a', 5);
  operations.removeItem('missing', 0);
  operations.toggleItem('a', 3, () => assert.fail('no change'), () => {});
  await operations.toggleTodayStep('missing', 0, () => assert.fail('no change'), () => {});
  assert.equal(operations.addItem('missing', 'B', () => assert.fail('nothing added')), undefined);
  operations.editItem(app.state.notes[0], 0, 'A');
  await drain();
  assert.deepEqual(calls, []);
  assert.equal(renders, 1, 'an edit to the same text only redraws');
  // Clearing an item's text removes it.
  operations.editItem(app.state.notes[0], 0, '');
  await drain();
  assert.deepEqual(calls, [['patch', 'a', { items: [], base_updated_at: 'v1' }]]);
});

test('a quick add to a note without tasks yet starts its checklist, and the server copy is taken', async () => {
  const followed = [];
  const app = owner([{ id: 'a', content: 'Just prose', updated_at: 'v1', items: null }]);
  const api = { appendTask: async (id, text) => ({ ok: true, note: { id, content: `Just prose\n\n- [ ] ${text}`, items: [{ index: 0, text, done: false }] } }) };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError() {}, followed: id => followed.push(id) } });
  const persist = operations.addItem('a', 'First task', () => {});
  assert.deepEqual(app.state.notes[0].items.map(it => it.text), ['First task']);
  await persist();
  assert.equal(app.state.notes[0].content, 'Just prose\n\n- [ ] First task');
  assert.deepEqual(followed, ['a']);
});

test('a card edit that fails without a newer copy puts the card back and says so', async () => {
  const errors = [];
  const app = owner([{ id: 'a', content: '- [ ] A\n- [ ] B', updated_at: 'v1', items: [{ text: 'A', done: false }, { text: 'B', done: false }] }]);
  const operations = createChecklistOperations({ app, api: { patchNote: async () => { throw Object.assign(new Error('boom'), { status: 500 }); } }, uid: () => 'new', effects: { render() {}, showError: e => errors.push(e) } });
  operations.editItem(app.state.notes[0], 0, 'A edited');
  await drain();
  assert.equal(app.state.notes[0].content, '- [ ] A\n- [ ] B');
  assert.deepEqual(errors, ['Failed to update item']);
});

test('the redo of a card edit that is refused again keeps the newest server copy', async () => {
  const errors = [];
  const app = owner([{ id: 'a', content: '- [ ] A', updated_at: 'v1', items: [{ text: 'A', done: false }] }]);
  const v2 = { id: 'a', content: '- [ ] A\n- [ ] B', updated_at: 'v2', items: [{ index: 0, text: 'A', done: false }, { index: 1, text: 'B', done: false }] };
  const v3 = { id: 'a', content: '- [x] A\n- [ ] B', updated_at: 'v3', items: [{ index: 0, text: 'A', done: true }, { index: 1, text: 'B', done: false }] };
  const api = { patchNote: async (id, body) => { throw conflict(body.base_updated_at === 'v1' ? v2 : v3); } };
  const operations = createChecklistOperations({ app, api, uid: () => 'new', effects: { render() {}, showError: e => errors.push(e) } });
  operations.editItem(app.state.notes[0], 0, 'A edited');
  await drain(); await drain();
  assert.equal(app.state.notes[0].content, v3.content);
  assert.equal(app.state.notes[0].updated_at, 'v3');
  assert.deepEqual(errors, ['Failed to update item']);
});

test('a Today step tick that fails reverts it, and a refused one shows the server copy', async () => {
  const offlineApp = owner([{ id: 'a', content: '- [ ] A', items: [{ text: 'A', done: false }] }]);
  const offline = createChecklistOperations({ app: offlineApp, api: { toggleTask: async () => { throw new Error('offline'); } }, uid: () => 'new', effects: { render() {}, showError() {} } });
  await offline.toggleTodayStep('a', 0, () => {}, () => assert.fail('not completed'));
  assert.equal(offlineApp.state.notes[0].content, '- [ ] A');
  assert.equal(offlineApp.state.notes[0].items[0].done, false);

  const errors = [];
  const server = { id: 'a', content: '- [ ] Renamed', updated_at: 'v2', items: [{ index: 0, text: 'Renamed', done: false }] };
  const refusedApp = owner([{ id: 'a', content: '- [ ] A', updated_at: 'v1', items: [{ text: 'A', done: false }] }]);
  const refused = createChecklistOperations({ app: refusedApp, api: { toggleTask: async () => { throw conflict(server); } }, uid: () => 'new', effects: { render() {}, showError: e => errors.push(e) } });
  await refused.toggleTodayStep('a', 0, () => {}, () => {});
  assert.equal(refusedApp.state.notes[0].content, '- [ ] Renamed');
  assert.deepEqual(errors, ['This checklist changed elsewhere. It has been refreshed.']);

  let completed = 0;
  const okApp = owner([{ id: 'a', content: '- [ ] A', items: [{ text: 'A', done: false }] }]);
  const ok = createChecklistOperations({ app: okApp, api: { toggleTask: async () => ({}) }, uid: () => 'new', effects: { render() {}, showError() {} } });
  await ok.toggleTodayStep('a', 0, () => {}, () => { completed += 1; });
  assert.equal(completed, 1, 'the last open step completes the goal');
});

test('bulk actions with nothing selected do nothing, and a declined delete deletes nothing', async () => {
  const calls = [];
  const app = owner([{ id: 'a', content: '' }]);
  const api = { patchNote: async id => calls.push(['patch', id]), deleteNote: async id => calls.push(['delete', id]) };
  const make = confirm => createSelectionOperations({
    app, api, sendContext: () => calls.push('dispatch'), exitSelection: () => calls.push('exit'), fetchNotes: async () => calls.push('fetch'),
    effects: { render() {}, showToast: message => calls.push(message), ...(confirm ? { styledConfirm: confirm } : {}) },
  });
  const operations = make(null);
  operations.attachSelected();
  await operations.archiveSelected();
  await operations.deleteSelected();
  assert.deepEqual(calls, [], 'empty selection');

  app.selectId('a');
  const prompts = [];
  await make(async (message, options) => { prompts.push([message, options.confirmText, options.danger]); return false; }).deleteSelected();
  assert.deepEqual(prompts, [['Delete 1 note?', 'Delete', true]]);
  assert.deepEqual(calls, [], 'declined');
  // Without a confirm port (the legacy facade) the delete goes straight through.
  await operations.deleteSelected();
  assert.deepEqual(calls, [['delete', 'a'], 'exit', 'fetch', 'Deleted 1']);
});

function deletionHarness(notes, { deleteFails = false, leaveFails = false, answer = true, styled = true } = {}) {
  // The server's list: what a re-read brings back, whatever the view removed meanwhile.
  const server = () => notes.map(note => ({ ...note }));
  const app = owner(server()), events = [];
  const ask = message => { events.push(`ask:${message}`); return answer; };
  const operations = createDeletion({ app, state: app.state,
    _deleteNoteApi: async id => { events.push(`delete:${id}`); if (deleteFails) throw new Error('Server said no'); },
    _leaveNoteApi: async id => { events.push(`leave:${id}`); if (leaveFails) throw new Error('offline'); },
    _fetchNotes: async () => { events.push('fetch'); app.replaceNotes(server()); },
    _isPastReminder: n => n.due_date === 'past',
    _renderNotes: () => events.push('render'),
    effects: { ...(styled ? { styledConfirm: async message => ask(message) } : { confirm: ask }), showToast: message => events.push(`toast:${message}`), showError: message => events.push(`error:${message}`) },
  });
  return { app, events, operations };
}

test('a collaborator leaves a note after confirming; a failed leave puts the card back', async () => {
  const theirs = { id: 's', is_owner: false, can_edit: true };
  const declined = deletionHarness([theirs], { answer: false });
  assert.equal(await declined.operations.leaveNote('s'), false);
  assert.deepEqual(declined.events, ['ask:Leave this note? It will no longer be shared with you.']);

  const left = deletionHarness([theirs]);
  assert.equal(await left.operations.leaveNote('s'), true);
  assert.equal(left.app.state.notes.length, 0);
  assert.deepEqual(left.events.slice(1), ['render', 'leave:s', 'toast:Left the note']);

  const failed = deletionHarness([theirs], { leaveFails: true, styled: false });
  assert.equal(await failed.operations.leaveNote('s'), false);
  assert.deepEqual(failed.events.slice(1), ['render', 'leave:s', 'error:Failed to leave the note', 'fetch', 'render']);
  assert.equal(failed.app.state.notes[0].id, 's', 'the list is read again, card included');
});

test('a card delete asks first, and reports the server error when it fails', async () => {
  const declined = deletionHarness([{ id: 'a' }], { answer: false, styled: false });
  await declined.operations._deleteNote('a');
  assert.deepEqual(declined.events, ['ask:Delete this note?']);

  const done = deletionHarness([{ id: 'a' }]);
  await done.operations._deleteNote('a');
  assert.deepEqual(done.events, ['ask:Delete this note?', 'delete:a', 'fetch', 'render', 'toast:Deleted']);

  const failed = deletionHarness([{ id: 'a' }], { deleteFails: true });
  await failed.operations._deleteNote('a');
  assert.deepEqual(failed.events, ['ask:Delete this note?', 'delete:a', 'error:Server said no']);
  assert.equal(await deletionHarness([{ id: 'a' }], { styled: false }).operations.confirmLeave(), true, 'the plain confirm when there is no styled one');
});

test('Clear past reminders deletes only my past reminders, after one question, and counts them', async () => {
  const nothing = deletionHarness([{ id: 'future', due_date: 'later' }, { id: 'shared', due_date: 'past', is_owner: false }, { id: 'archived', due_date: 'past', archived: true }]);
  await nothing.operations._clearPastReminders();
  assert.deepEqual(nothing.events, ['toast:No past reminders to clear'], 'shared and archived reminders are not mine to clear');

  const two = deletionHarness([{ id: 'p1', due_date: 'past' }, { id: 'p2', due_date: 'past' }, { id: 'future', due_date: 'later' }], { deleteFails: true, styled: false });
  await two.operations._clearPastReminders();
  assert.deepEqual(two.events, ['ask:Delete 2 past reminders?', 'delete:p1', 'delete:p2', 'fetch', 'render', 'toast:Cleared 2 past reminders']);

  const declined = deletionHarness([{ id: 'p1', due_date: 'past' }], { answer: false });
  await declined.operations._clearPastReminders();
  assert.deepEqual(declined.events, ['ask:Delete 1 past reminder?']);
});

// A delete or leave that fails because the network is down re-reads the list
// to put the card back. That re-read fails too; it used to replace the list
// with nothing, so every card disappeared until a later read succeeded. The
// card goes back in its place and a failed re-read keeps the list, like the
// 20 s re-read does.
test('a delete or leave that fails offline keeps every card on screen', async () => {
  for (const action of ['delete', 'leave']) {
    const app = owner([{ id: 'a', title: 'Mine' }, { id: 's', title: 'Theirs', is_owner: false, can_edit: true }, { id: 'b', title: 'Other' }]);
    const offline = async () => { throw new TypeError('Failed to fetch'); };
    const workflows = createNotesWorkflows({ app, api: { listNotes: offline, patchNote: offline, deleteNote: offline, saveNote: offline }, effects: { render() {}, showError() {}, archived() {} } });
    const deletion = createDeletion({ app, state: app.state,
      _deleteNoteApi: workflows._deleteNoteApi, _leaveNoteApi: offline, _fetchNotes: workflows._fetchNotes,
      _isPastReminder: () => false, _renderNotes: () => {}, effects: { showToast() {}, showError() {} },
    });
    const original = console.error;
    console.error = () => {};
    try {
      if (action === 'delete') await deletion.deleteNow('a'); else await deletion.leaveNow('s');
      await drain();
    } finally { console.error = original; }
    assert.deepEqual(app.state.notes.map(n => n.id), ['a', 's', 'b'], `${action} failed offline: the list should stay as it was, in the same order`);
  }
});

test('archive, unarchive and colour: optimistic, put back with a message when the server refuses', async () => {
  const refuse = async () => { throw new Error('403'); };
  const errors = [], toasts = [], archived = [], undos = [];
  const app = owner([{ id: 'a', color: null }, { id: 'b' }, { id: 'c' }]);
  const operations = createCardOperations({ app, api: { patchNote: refuse }, pushUndo: entry => undos.push(entry), undoArchive: () => {},
    effects: { render() {}, showError: e => errors.push(e), showToast: t => toasts.push(t), archived: undo => archived.push(undo) } });
  const persist = operations.stageArchive('b');
  assert.deepEqual(app.state.notes.map(n => n.id), ['a', 'c'], 'gone from the grid at once');
  assert.equal(undos[0].label, 'archive');
  persist(); await drain();
  assert.deepEqual(app.state.notes.map(n => n.id), ['a', 'b', 'c'], 'back in its place');
  operations.unarchive('c'); await drain();
  assert.deepEqual(app.state.notes.map(n => n.id), ['a', 'b', 'c']);
  await operations.updateColor('a', 'red');
  assert.equal(app.state.notes[0].color, null, 'the colour is only shown once saved');
  assert.deepEqual(errors, ['Failed to archive', 'Failed to unarchive', 'Failed to update color']);
  assert.equal(operations.stageArchive('missing'), null);
  operations.unarchive('missing');
  operations.togglePin('missing');
  operations.deleteOptimistically('missing');
  assert.deepEqual(archived, []);
});

test('accepted card writes: archive offers its undo, unarchive and delete say so, colour is kept, a phone reorder numbers the cards', async () => {
  const calls = [], toasts = [], archived = [];
  const app = owner([{ id: 'a' }, { id: 'b' }, { id: 'c', pinned: true, sort_order: -2 }]);
  const api = { patchNote: async (id, patch) => { calls.push([id, patch]); return {}; }, deleteNote: async id => { calls.push(['delete', id]); }, reorder: async ids => { calls.push(['reorder', ids]); } };
  const operations = createCardOperations({ app, api, pushUndo() {}, undoArchive: () => 'undone',
    effects: { render() {}, showError: e => assert.fail(e), showToast: t => toasts.push(t), archived: undo => archived.push(undo()) } });
  operations.stageArchive('a')(); await drain();
  assert.deepEqual(archived, ['undone']);
  operations.unarchive('b'); await drain();
  await operations.updateColor('c', '');
  assert.equal(app.state.notes[0].color, '');
  operations.togglePin('c'); await drain();
  assert.deepEqual(calls.at(-1), ['c', { pinned: false }], 'unpinning sends no order');
  app.replaceNotes([{ id: 'x' }, { id: 'y' }]);
  operations.deleteOptimistically('x', true); await drain();
  await operations.commitMobileOrder(['y']);
  assert.equal(app.state.notes[0].sort_order, 0);
  assert.deepEqual(toasts, ['Unarchived', 'Deleted']);
  assert.deepEqual(calls.find(c => c[0] === 'c' && 'color' in c[1]), ['c', { color: null }], 'no colour is sent as null');

  // A phone reorder the server refuses keeps the order on screen and only logs it.
  const warn = console.warn; const warned = [];
  console.warn = (...args) => warned.push(args[0]);
  try {
    const failing = createCardOperations({ app, api: { reorder: async () => { throw new Error('offline'); } }, effects: {} });
    await failing.commitMobileOrder(['y']);
  } finally { console.warn = warn; }
  assert.deepEqual(warned, ['reorder failed']);
});
