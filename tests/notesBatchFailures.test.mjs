import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createSelectionOperations } from '../frontend/src/features/notes/application/selection.js';
import { createDeletion } from '../frontend/src/features/notes/application/deletion.js';

function fixture(fail = [], confirm = true) {
  const app = createNotesState();
  app.replaceNotes(['a', 'b', 'c'].map(id => ({ id })));
  app.setSelectMode(true);
  for (const id of ['a', 'b', 'c']) app.selectId(id);
  const calls = [], errors = [], toasts = [];
  const mutate = async id => {
    calls.push(id);
    if (fail.includes(id)) throw Error('Rejected request');
  };
  const exit = () => { app.setSelectMode(false); app.clearSelection(); };
  const effects = { render() {}, styledConfirm: async () => confirm,
    showToast: text => toasts.push(text), showError: text => errors.push(text) };
  const selection = createSelectionOperations({ app,
    api: { patchNote: mutate, deleteNote: mutate }, effects, sendContext() {},
    exitSelection: exit, fetchNotes: async () => {} });
  const deletion = createDeletion({ app, state: app.state, _deleteNoteApi: mutate,
    _fetchNotes: async () => {}, _renderNotes() {}, _isPastReminder: () => true, effects });
  return { app, calls, errors, toasts, selection, deletion };
}

for (const [action, verb, infinitive] of [['archiveSelected', 'Archived', 'archive'], ['deleteSelected', 'Deleted', 'delete']]) {
  test(`${action} reports partial success and retains failed IDs for retry`, async () => {
    const f = fixture(['b']);
    await f.selection[action]();
    assert.deepEqual(f.calls, ['a', 'b', 'c']);
    assert.deepEqual([...f.app.state.selectedIds], ['b']);
    assert.equal(f.app.state.selectMode, true);
    assert.deepEqual(f.toasts, []);
    assert.deepEqual(f.errors, [`${verb} 2; failed to ${infinitive} 1 note`]);
  });
  test(`${action} reports total rejection without claiming success`, async () => {
    const f = fixture(['a', 'b', 'c']);
    await f.selection[action]();
    assert.deepEqual([...f.app.state.selectedIds], ['a', 'b', 'c']);
    assert.deepEqual(f.toasts, []);
    assert.deepEqual(f.errors, [`Failed to ${infinitive} 3 notes`]);
  });
  test(`${action} keeps the existing all-success exit behavior`, async () => {
    const f = fixture();
    await f.selection[action]();
    assert.equal(f.app.state.selectedIds.size, 0);
    assert.equal(f.app.state.selectMode, false);
    assert.deepEqual(f.toasts, [verb + ' 3']);
    assert.deepEqual(f.errors, []);
  });
  test(`${action} does not clear a selection made while requests are pending`, async () => {
    const f = fixture();
    f.app.insertNoteAt(3, { id: 'later' });
    let finish;
    const waiting = new Promise(resolve => { finish = resolve; });
    const operation = createSelectionOperations({ app: f.app,
      api: { patchNote: () => waiting, deleteNote: () => waiting },
      effects: { render() {}, styledConfirm: async () => true, showToast() {}, showError() {} },
      exitSelection: () => { f.app.clearSelection(); f.app.setSelectMode(false); },
      fetchNotes: async () => {}, sendContext() {} });
    const pending = operation[action]();
    f.app.selectId('later');
    finish(); await pending;
    assert.deepEqual([...f.app.state.selectedIds], ['later']);
    assert.equal(f.app.state.selectMode, true);
  });
  test(`${action} is a no-op with no selected IDs`, async () => {
    const f = fixture(); f.app.clearSelection();
    await f.selection[action]();
    assert.deepEqual(f.calls, []); assert.deepEqual(f.toasts, []); assert.deepEqual(f.errors, []);
  });
}

test('declining bulk deletion sends no requests and retains selection', async () => {
  const f = fixture([], false);
  await f.selection.deleteSelected();
  assert.deepEqual(f.calls, []);
  assert.deepEqual([...f.app.state.selectedIds], ['a', 'b', 'c']);
});

for (const fail of [['b'], ['a', 'b', 'c'], []]) {
  test(`clear past reminders reports actual successes with ${fail.length} rejections`, async () => {
    const f = fixture(fail);
    await f.deletion._clearPastReminders();
    assert.deepEqual(f.calls, ['a', 'b', 'c']);
    assert.deepEqual(f.toasts, fail.length ? [] : ['Cleared 3 past reminders']);
    assert.deepEqual(f.errors, fail.length ? [`${fail.length === 1 ? 'Cleared 2 past reminders; failed' : 'Failed'} to delete ${fail.length} past reminder${fail.length === 1 ? '' : 's'}`] : []);
  });
}

test('declining reminder clearing sends no requests', async () => {
  const f = fixture([], false);
  await f.deletion._clearPastReminders();
  assert.deepEqual(f.calls, []); assert.deepEqual(f.toasts, []);
});
