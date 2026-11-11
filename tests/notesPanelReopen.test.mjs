import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createPanel } from '../frontend/src/features/notes/ui/panel.js';

function fixture(t, open) {
  const app = createNotesState();
  app.replaceNotes([{ id: 'first', title: 'Keep this note' }, { id: 'second' }]);
  app.setOpen(open);
  app.setEditingId('first');
  app.setSearchQuery('query');
  let focused = 0;
  const previous = { document: globalThis.document, window: globalThis.window };
  // Mount boundary: owner transitions run, actual DOM mounting is tested in Chrome.
  globalThis.document = { getElementById: () => null };
  globalThis.window = {};
  t.after(() => { globalThis.document = previous.document; globalThis.window = previous.window; });
  const panel = createPanel({ app, state: app.state,
    _bringNotesToFront: () => focused++, _clearViewedReminderGlows() {},
    persistence: { setItem() {} }, REMINDER_DISMISSED_AT_KEY: 'test-dismissed' });
  return { app, panel, focused: () => focused };
}

test('fresh Notes mount clears selection left by a prior panel', t => {
  const { app, panel } = fixture(t, false);
  app.setSelectMode(true); app.selectId('first');
  panel.openPanel();
  assert.equal(app.state.open, true);
  assert.equal(app.state.selectMode, false);
  assert.deepEqual([...app.state.selectedIds], []);
  assert.equal(app.state.editingId, null);
  assert.equal(app.state.searchQuery, '');
  assert.deepEqual(app.state.notes.map(n => n.id), ['first', 'second']);
});

test('focusing an already-open Notes panel keeps its selection and editor', t => {
  const { app, panel, focused } = fixture(t, true);
  app.setSelectMode(true); app.selectId('first');
  panel.openPanel();
  assert.equal(focused(), 1);
  assert.equal(app.state.selectMode, true);
  assert.deepEqual([...app.state.selectedIds], ['first']);
  assert.equal(app.state.editingId, 'first');
  assert.equal(app.state.searchQuery, 'query');
});

test('a fresh Notes panel with no selection keeps its notes and exits editing', t => {
  const { app, panel } = fixture(t, false);
  panel.openPanel();
  assert.equal(app.state.selectMode, false);
  assert.equal(app.state.selectedIds.size, 0);
  assert.equal(app.state.editingId, null);
  assert.equal(app.state.notes[0].title, 'Keep this note');
});
