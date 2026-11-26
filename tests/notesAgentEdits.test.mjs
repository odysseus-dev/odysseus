import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createAgentEdits } from '../frontend/src/features/notes/application/agentEdits.js';

// A note whose bytes a re-serialization would change: CRLF, double spaces,
// trailing blanks, an escaped star.
const BEFORE = { title: '  Trip\t', content: 'Pack  light\r\n\r\n- [ ] cabin  \n2 \\* 3' };
const AGENT = { title: 'Trip', content: 'Pack light, bring boots\n\n- [x] cabin' };
const edit = (at, undoable = true) => ({ at, session_id: 's1', undoable });

function harness({ server = {}, undoError = null, getError = null } = {}) {
  const app = createNotesState();
  app.replaceNotes([{ id: 'n1', ...BEFORE, updated_at: 't0', agent_edit: null }, { id: 'n2', title: 'Other', content: 'x', updated_at: 't0' }]);
  const calls = [];
  const effects = { toasts: [], errors: [], rendered: [] };
  const api = {
    getNote: async id => { calls.push(['get', id]); if (getError) throw getError; return { id, ...AGENT, updated_at: 't1', agent_edit: edit('a1'), ...server }; },
    agentUndo: async id => { calls.push(['undo', id]); if (undoError) throw undoError; return { id, ...BEFORE, updated_at: 't2', agent_edit: null }; },
  };
  const followed = [];
  const noteWindow = {
    settle: async id => { calls.push(['settle', id]); },
    followServer: note => followed.push({ ...note }),
  };
  const agentEdits = createAgentEdits({
    api, app, state: app.state, noteWindow,
    effects: {
      renderNote: id => effects.rendered.push(id),
      showToast: (message, options) => effects.toasts.push({ message, options }),
      showError: message => effects.errors.push(message),
      undoIcon: '<svg/>',
    },
  });
  return { app, agentEdits, calls, effects, followed };
}

test('an agent edit lands in the list, its card and the open window, with one Undo toast', async () => {
  const { app, agentEdits, effects, followed } = harness();
  await agentEdits.edited('n1');
  const note = app.state.notes[0];
  assert.equal(note.content, AGENT.content);
  assert.equal(note.updated_at, 't1');
  assert.deepEqual(effects.rendered, ['n1']);
  assert.equal(followed.length, 1);
  assert.equal(followed[0].content, AGENT.content, 'the open window is handed the new version');
  assert.equal(effects.toasts.length, 1);
  assert.equal(effects.toasts[0].message, 'Agent edited this note');
  assert.equal(effects.toasts[0].options.action, 'Undo');
  // The same edit reported again (a later list read, a repeated event) is not announced twice.
  await agentEdits.edited('n1');
  assert.equal(effects.toasts.length, 1);
});

test('Undo from the toast saves pending typing first, then restores the exact bytes', async () => {
  const { app, agentEdits, calls, effects, followed } = harness();
  await agentEdits.edited('n1');
  await effects.toasts[0].options.onAction();
  assert.deepEqual(calls.slice(1), [['settle', 'n1'], ['undo', 'n1']]);
  const note = app.state.notes[0];
  assert.equal(note.title, BEFORE.title);
  assert.equal(note.content, BEFORE.content);
  assert.equal(note.agent_edit, null);
  assert.equal(followed.at(-1).content, BEFORE.content, 'the window shows the restored note');
  assert.equal(effects.toasts.at(-1).message, 'Agent edit undone');
  assert.deepEqual(effects.errors, []);
});

test('Undo after a later change restores nothing and shows the note as it is', async () => {
  const later = { id: 'n1', title: 'Trip', content: 'typed after the agent', updated_at: 't3', agent_edit: edit('a1', false) };
  const error = Object.assign(new Error('409'), { status: 409, body: { detail: { message: 'changed', note: later } } });
  const { app, agentEdits, effects } = harness({ undoError: error });
  assert.equal(await agentEdits.undo('n1'), false);
  assert.equal(app.state.notes[0].content, 'typed after the agent');
  assert.deepEqual(effects.errors, ['The note changed after the agent edit, so nothing was undone.']);
});

test('no toast for an edit that is no longer undoable, or for a note that is not mine', async () => {
  const stale = harness({ server: { agent_edit: edit('a1', false) } });
  await stale.agentEdits.edited('n1');
  assert.equal(stale.effects.toasts.length, 0);
  assert.equal(stale.app.state.notes[0].content, AGENT.content, 'the edit still shows');

  const theirs = harness({ server: { is_owner: false, can_edit: true } });
  await theirs.agentEdits.edited('n1');
  assert.equal(theirs.effects.toasts.length, 0);
});

test('a list re-read announces edits made from the chat, but not ones already on screen', () => {
  const { agentEdits, effects } = harness();
  const before = agentEdits.agentEditsOf([{ id: 'n1', agent_edit: null }, { id: 'n2', agent_edit: edit('old') }]);
  agentEdits.noticeInList([
    { id: 'n1', ...AGENT, agent_edit: edit('a9') },
    { id: 'n2', title: 'Other', content: 'x', agent_edit: edit('old') },
    { id: 'n3', title: 'New to this list', content: 'y', agent_edit: edit('a3') },
  ], before);
  assert.deepEqual(effects.toasts.map(t => t.message), ['Agent edited this note']);
});

test('a failed read after an edit changes nothing', async () => {
  const { app, agentEdits, effects, followed } = harness({ getError: new Error('offline') });
  await agentEdits.edited('n1');
  assert.equal(app.state.notes[0].content, BEFORE.content);
  assert.equal(followed.length, 0);
  assert.equal(effects.toasts.length, 0);
});

test('Undo that fails says so, except when the note is gone; a failed save first does not stop it', async () => {
  const offline = harness({ undoError: new Error('offline') });
  assert.equal(await offline.agentEdits.undo('n1'), false);
  assert.deepEqual(offline.effects.errors, ['Could not undo the agent edit']);
  assert.equal(offline.app.state.notes[0].content, BEFORE.content, 'nothing taken from a failed answer');

  // Deleted or unshared meanwhile: the 404 path of the window says that; no second message here.
  const gone = harness({ undoError: Object.assign(new Error('gone'), { status: 404 }) });
  assert.equal(await gone.agentEdits.undo('n1'), false);
  assert.deepEqual(gone.effects.errors, []);

  const { app, agentEdits, calls, effects } = harness();
  const failingSave = createAgentEdits({
    api: { agentUndo: async id => { calls.push(['undo', id]); return { id, ...BEFORE, updated_at: 't2', agent_edit: null }; } },
    app, state: app.state,
    noteWindow: { settle: async () => { throw new Error('save failed'); }, followServer() {} },
    effects: { renderNote() {}, showToast: message => effects.toasts.push({ message }), showError: message => effects.errors.push(message) },
  });
  assert.equal(await failingSave.undo('n1'), true);
  assert.deepEqual(calls, [['undo', 'n1']]);
  assert.deepEqual(effects.toasts.map(t => t.message), ['Agent edit undone']);
  assert.equal(agentEdits.announce(null), false);
});

test('the toast offers See what changed, on the chat of the edit, when the history can open', async () => {
  const { app } = harness();
  const seen = [];
  const toasts = [];
  const agentEdits = createAgentEdits({
    api: { getNote: async id => ({ id, ...AGENT, updated_at: 't1', agent_edit: { at: 'a9', undoable: true } }) },
    app, state: app.state, noteWindow: { followServer() {} },
    effects: { renderNote() {}, showToast: (message, options) => toasts.push(options), showError() {}, seeChanges: (id, sid) => seen.push([id, sid]) },
  });
  await agentEdits.edited('n1');
  assert.equal(toasts[0].secondaryAction, 'See what changed');
  toasts[0].onSecondaryAction();
  assert.deepEqual(seen, [['n1', null]], 'an edit without a chat id opens on the latest edit');
});

test('an edit to a note no longer in the list is announced but changes no card', async () => {
  const { agentEdits, effects, followed } = harness({ server: { id: 'elsewhere' } });
  await agentEdits.edited('elsewhere');
  assert.deepEqual(effects.rendered, []);
  assert.equal(followed.length, 0);
  assert.equal(effects.toasts.length, 1);
  assert.deepEqual([...agentEdits.agentEditsOf(null)], []);
  agentEdits.noticeInList(null, new Map());
});
