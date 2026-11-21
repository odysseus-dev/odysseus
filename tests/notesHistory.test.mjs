import test from 'node:test';
import assert from 'node:assert/strict';
import { createNotesState } from '../frontend/src/features/notes/application/state.js';
import { createNoteHistory } from '../frontend/src/features/notes/application/noteHistory.js';
import { changeDiff, changeSentence, dayLabel, foldDiff, groupByDay, lineDiff, relativeTime, restoreDiff, shortTime } from '../frontend/src/features/notes/domain/historyText.js';

const NOW = { title: 'Trip', content: 'typed just now', updated_at: 't5' };
const OLD = { title: 'Trip', content: 'Pack  light\r\n- [ ] cabin', updated_at: 't6' };
// The history's clock, so a toast's "when" does not follow the wall clock.
const CLOCK_AT = new Date(2026, 10, 12, 12, 0, 0);

function harness({ restoreError = null, undoId = 12 } = {}) {
  const app = createNotesState();
  app.replaceNotes([{ id: 'n1', ...NOW }, { id: 'n2', title: 'Other', content: 'x', updated_at: 't0' }]);
  const calls = [];
  const effects = { toasts: [], errors: [], rendered: [] };
  const api = {
    listVersions: async id => { calls.push(['list', id]); return { versions: [{ id: 7, source: 'agent' }, { id: 3, source: 'user' }] }; },
    getVersion: async (id, vid) => { calls.push(['get', id, vid]); return { id: vid, content: OLD.content }; },
    restoreVersion: async (id, vid, base) => {
      calls.push(['restore', id, vid, base]);
      if (restoreError) throw restoreError;
      // The restore's own version undoes it; restoring that one brings back what was typed.
      return vid === undoId ? { id, ...NOW, updated_at: 't7', undo_version_id: 13 } : { id, ...OLD, undo_version_id: undoId };
    },
  };
  const followed = [];
  const noteWindow = {
    // The open window saves what was typed: the note's version moves on.
    settle: async id => { calls.push(['settle', id]); app.mergeNote(app.state.notes[0], { updated_at: 't5b' }); },
    followServer: note => followed.push({ ...note }),
  };
  const history = createNoteHistory({
    api, app, state: app.state, noteWindow, clock: { now: () => CLOCK_AT.getTime() },
    effects: {
      renderNote: id => effects.rendered.push(id),
      showToast: (message, options) => effects.toasts.push(options ? { message, ...options } : message),
      showError: message => effects.errors.push(message),
    },
  });
  return { app, history, calls, effects, followed };
}

test('the list and one version come from the owner routes', async () => {
  const { history, calls } = harness();
  assert.deepEqual((await history.list('n1')).map(v => v.id), [7, 3]);
  assert.equal((await history.read('n1', 3)).content, OLD.content);
  assert.deepEqual(calls, [['list', 'n1'], ['get', 'n1', 3]]);
});

test('Restore saves pending typing first, then restores onto that version and shows it everywhere', async () => {
  const { app, history, calls, effects, followed } = harness();
  assert.equal(await history.restore('n1', 3), true);
  assert.deepEqual(calls, [['settle', 'n1'], ['restore', 'n1', 3, 't5b']]);
  assert.equal(app.state.notes[0].content, OLD.content, 'the exact bytes in the list');
  assert.deepEqual(effects.rendered, ['n1']);
  assert.equal(followed.at(-1).content, OLD.content, 'the open window shows the restored note');
  assert.equal(effects.toasts.length, 1);
  assert.equal(effects.toasts[0].message, 'Version restored', 'no time given: no time said');
  assert.equal(effects.toasts[0].action, 'Undo');
  assert.equal('undo_version_id' in app.state.notes[0], false, 'the undo id is not a note field');
  assert.deepEqual(effects.errors, []);
});

test('the restore toast names the version and its Undo restores what the restore replaced', async () => {
  const { app, history, calls, effects } = harness();
  const at = new Date(CLOCK_AT.getTime() - 24 * 60 * 60 * 1000).toISOString();
  assert.equal(await history.restore('n1', 3, { at }), true);
  const toast = effects.toasts[0];
  assert.equal(toast.message, `Restored the version from ${shortTime(at, CLOCK_AT)}`);
  assert.match(toast.message, /from yesterday /, 'said against the injected clock, not the wall clock');
  toast.onAction();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(calls.at(-1), ['restore', 'n1', 12, 't5b'], 'onto the note as it is (the harness settle stamps t5b)');
  assert.equal(app.state.notes[0].content, NOW.content, 'back to the note before the restore');
  assert.deepEqual(effects.toasts.at(-1), 'Restore undone', 'undoing a restore offers no further Undo');
});

test('a restore that kept nothing offers no Undo', async () => {
  const { history, effects } = harness({ undoId: null });
  assert.equal(await history.restore('n1', 3), true);
  assert.deepEqual(effects.toasts, ['Version restored']);
});

test('a note that changed meanwhile restores nothing and shows the note as it is', async () => {
  const later = { id: 'n1', title: 'Trip', content: 'from another tab', updated_at: 't9' };
  const error = Object.assign(new Error('409'), { status: 409, body: { detail: { message: 'Note changed elsewhere', note: later } } });
  const { app, history, effects } = harness({ restoreError: error });
  assert.equal(await history.restore('n1', 3), false);
  assert.equal(app.state.notes[0].content, 'from another tab');
  assert.deepEqual(effects.errors, ['The note changed meanwhile, so nothing was restored. Try again.']);
  assert.deepEqual(effects.toasts, []);
});

test('a version whose image was deleted says so', async () => {
  const error = Object.assign(new Error('409'), { status: 409, body: { detail: 'Referenced upload is no longer available: abc.png' } });
  const { app, history, effects } = harness({ restoreError: error });
  assert.equal(await history.restore('n1', 3), false);
  assert.equal(app.state.notes[0].content, NOW.content);
  assert.deepEqual(effects.errors, ['An image in this version has been deleted, so it cannot be restored.']);
});

const at = (base, minutes) => new Date(base.getTime() - minutes * 60 * 1000).toISOString();
const NOON = new Date(2026, 10, 12, 12, 0, 0);
const EN = 'en-GB';

test('times read as a person says them', () => {
  assert.equal(relativeTime(at(NOON, 0.2), NOON, EN), 'just now');
  assert.equal(relativeTime(at(NOON, 2), NOON, EN), '2 min ago');
  assert.equal(relativeTime(at(NOON, 70), NOON, EN), '1 h ago');
  assert.equal(relativeTime(at(NOON, 180), NOON, EN), '09:00');
  const yesterday = new Date(2026, 10, 11, 18, 4).toISOString();
  assert.equal(relativeTime(yesterday, NOON, EN), 'yesterday 18:04');
  assert.match(shortTime(new Date(2026, 10, 9, 8, 30).toISOString(), NOON, EN), /^Mon 08:30$/);
  assert.equal(shortTime(new Date(2026, 9, 2, 8, 30).toISOString(), NOON, EN), '2 Oct 08:30');
  assert.equal(shortTime(new Date(2025, 9, 2, 8, 30).toISOString(), NOON, EN), '2 Oct 2025 08:30');
  // Server timestamps are naive UTC.
  assert.equal(relativeTime(new Date(NOON.getTime() - 5 * 60000).toISOString().replace('Z', ''), NOON, EN), '5 min ago');
});

test('rows are grouped by day, newest first', () => {
  const rows = [{ id: 3, created_at: at(NOON, 5) }, { id: 2, created_at: at(NOON, 10) }, { id: 1, created_at: new Date(2026, 10, 11, 18, 4).toISOString() }];
  assert.deepEqual(groupByDay(rows, NOON, EN).map(g => [g.label, g.rows.map(r => r.id)]), [['Today', [3, 2]], ['Yesterday', [1]]]);
  assert.equal(dayLabel(new Date(2026, 10, 9, 8).toISOString(), NOON, EN), 'Monday 9 November');
});

test('each row says who changed what, and where', () => {
  const agent = { source: 'agent', session_id: 's1', chat_title: 'Agent: Trip', chat_exists: true, summary: 'ticked \u201cbook the cabin\u201d' };
  assert.equal(changeSentence(agent, 'alice'), 'Agent in Agent: Trip ticked \u201cbook the cabin\u201d');
  assert.equal(changeSentence({ ...agent, chat_title: '' }, 'alice'), 'Agent in a chat ticked \u201cbook the cabin\u201d', 'an unnamed chat');
  assert.equal(changeSentence({ ...agent, chat_title: null, chat_exists: false }, 'alice'), 'Agent in a deleted chat ticked \u201cbook the cabin\u201d');
  assert.equal(changeSentence({ source: 'user', author: 'alice', summary: 'renamed the note' }, 'alice'), 'You renamed the note');
  assert.equal(changeSentence({ source: 'user', author: null, summary: 'added 1 line' }, null), 'You added 1 line', 'single-user mode has no names');
  assert.equal(changeSentence({ source: 'user', author: 'bob', summary: 'edited the text' }, 'alice'), 'bob edited the text', 'a collaborator is named');
  assert.equal(changeSentence({ source: 'restore', author: 'alice', summary: 'removed 1 line' }, 'alice'), 'Restored a version: removed 1 line');
});

test('the line diff keeps order and marks what goes and what comes back', () => {
  assert.deepEqual(lineDiff('a\nb\nc', 'a\nB\nc\nd'), [
    { op: 'same', text: 'a' }, { op: 'del', text: 'b' }, { op: 'add', text: 'B' }, { op: 'same', text: 'c' }, { op: 'add', text: 'd' }]);
  assert.deepEqual(lineDiff('', 'x'), [{ op: 'add', text: 'x' }]);
  assert.deepEqual(lineDiff('x\r\ny', 'x\ny'), [{ op: 'same', text: 'x' }, { op: 'same', text: 'y' }]);
  const big = Array.from({ length: 2000 }, (_, i) => `line ${i}`).join('\n');
  assert.equal(lineDiff(big, big.replace('line 1000', 'changed')).filter(l => l.op !== 'same').length, 2);
});

test('the diff against now covers the title and fields, and says when there is nothing to undo', () => {
  const now = { title: 'Cabin trip', content: 'Pack light\n- [x] book the cabin\nBring a rain jacket', color: 'red', pinned: false };
  const then = { title: 'Trip', content: 'Pack light\n- [ ] book the cabin', color: null, pinned: false };
  const diff = restoreDiff(now, then);
  assert.deepEqual(diff.fields, [{ name: 'Title', from: 'Cabin trip', to: 'Trip' }, { name: 'Colour', from: 'red', to: 'none' }]);
  assert.deepEqual(diff.lines.filter(l => l.op !== 'same'), [
    { op: 'del', text: '- [x] book the cabin' }, { op: 'del', text: 'Bring a rain jacket' }, { op: 'add', text: '- [ ] book the cabin' }]);
  assert.equal(diff.same, false);
  assert.equal(restoreDiff(now, { ...now }).same, true);
});

test('a change shows what it did: its added lines are added, its removed lines removed', () => {
  // "You added 2 lines": the version is the note before, `after` the note it left.
  const added = changeDiff({ title: 'Test', content: 'Hello', after: { title: 'Test', content: 'Hello\n\nI love learning' } });
  assert.deepEqual(added.lines.filter(l => l.op !== 'same'), [{ op: 'add', text: '' }, { op: 'add', text: 'I love learning' }]);
  assert.equal(added.lines.filter(l => l.op === 'add').length, 2, 'as many added lines as the summary says');
  // "removed 1 line": the line it took out is the removed one.
  const removed = changeDiff({ title: 'Test', content: 'Hello\nBye', after: { title: 'Test', content: 'Hello' } });
  assert.deepEqual(removed.lines.filter(l => l.op !== 'same'), [{ op: 'del', text: 'Bye' }]);
  // A rename reads old -> new.
  assert.deepEqual(changeDiff({ title: 'Trip', content: 'x', after: { title: 'Cabin trip', content: 'x' } }).fields,
    [{ name: 'Title', from: 'Trip', to: 'Cabin trip' }]);
  // The undo of the newest change is the same diff the other way round.
  const before = { title: 'Test', content: 'Hello', after: { title: 'Test', content: 'Hello\nmore' } };
  assert.deepEqual(restoreDiff(before.after, before).lines.filter(l => l.op !== 'same'), [{ op: 'del', text: 'more' }]);
  assert.equal(changeDiff({ content: 'same', after: { content: 'same' } }).same, true);
});

test('long unchanged stretches fold away', () => {
  const lines = [...'abcdefgh'].map(text => ({ op: 'same', text }));
  lines.splice(4, 0, { op: 'add', text: 'new' });
  assert.deepEqual(foldDiff(lines, 1).map(l => l.op === 'gap' ? `gap${l.count}` : l.text), ['gap3', 'd', 'new', 'e', 'gap3']);
});

test('a restore that fails for another reason, or an Undo of a restore that fails, says which', async () => {
  const offline = harness({ restoreError: new Error('offline') });
  assert.equal(await offline.history.restore('n1', 3), false);
  assert.deepEqual(offline.effects.errors, ['Could not restore this version']);
  assert.equal(offline.app.state.notes[0].content, NOW.content);

  const undoOffline = harness({ restoreError: new Error('offline') });
  assert.equal(await undoOffline.history.restore('n1', 12, { undoing: true }), false);
  assert.deepEqual(undoOffline.effects.errors, ['Could not undo the restore']);

  const changed = { status: 409, body: { detail: { note: { id: 'n1', title: 'Trip', content: 'typed on the phone', updated_at: 't9' } } } };
  const undoChanged = harness({ restoreError: changed });
  assert.equal(await undoChanged.history.restore('n1', 12, { undoing: true }), false);
  assert.deepEqual(undoChanged.effects.errors, ['The note changed meanwhile, so the restore was not undone.']);
  assert.equal(undoChanged.app.state.notes[0].content, 'typed on the phone', 'the note as it is now is shown');
});

test('the restore goes ahead when saving pending typing fails, and an empty answer lists no versions', async () => {
  const { app, history, effects } = harness();
  const calls = [];
  const failingSettle = createNoteHistory({
    api: {
      listVersions: async () => ({}),
      restoreVersion: async (id, vid, base) => { calls.push([id, vid, base]); return { id, ...OLD }; },
    },
    app, state: app.state,
    noteWindow: { settle: async () => { throw new Error('save failed'); }, followServer() {} },
    effects: { renderNote() {}, showToast: message => effects.toasts.push(message), showError: message => effects.errors.push(message) },
  });
  assert.deepEqual(await failingSettle.list('n1'), []);
  assert.equal(await failingSettle.restore('n1', 3), true);
  assert.deepEqual(calls, [['n1', 3, 't5']], 'the base is the version on screen');
  assert.deepEqual(effects.toasts, ['Version restored'], 'no undo id: no Undo offered');
  // A note no longer in the list: nothing to update, and the base is unknown.
  assert.equal(await failingSettle.restore('gone', 3), true);
  assert.deepEqual(calls.at(-1), ['gone', 3, null]);
});

test('a rewrite too big to compare line by line shows the old middle removed, then the new one added, ends kept', () => {
  // 600 x 600 differing lines is 360k cells, over the 250k the diff will spend.
  const old = Array.from({ length: 600 }, (_, i) => `old ${i}`);
  const fresh = Array.from({ length: 600 }, (_, i) => `new ${i}`);
  const diff = lineDiff(['head', ...old, 'tail'].join('\n'), ['head', ...fresh, 'tail'].join('\n'));
  assert.equal(diff.length, 1202);
  assert.deepEqual(diff[0], { op: 'same', text: 'head' });
  assert.deepEqual(diff.at(-1), { op: 'same', text: 'tail' });
  assert.ok(diff.slice(1, 601).every(line => line.op === 'del' && line.text.startsWith('old ')));
  assert.ok(diff.slice(601, 1201).every(line => line.op === 'add' && line.text.startsWith('new ')));
  // Just under the cap the real comparison runs: one shared line in the middle is found.
  const under = lineDiff([...old.slice(0, 400), 'shared', ...old.slice(400, 499)].join('\n'), [...fresh.slice(0, 400), 'shared', ...fresh.slice(400, 499)].join('\n'));
  assert.equal(under.filter(line => line.op === 'same').map(line => line.text).join(), 'shared');
});
