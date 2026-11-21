import test from 'node:test';
import assert from 'node:assert/strict';
import { contextNoteIds, contextNoteRecord, noteExcerpt } from '../frontend/src/features/notes/domain/contextNotes.js';
import { createContextStrip, openNoteOverChat } from '../frontend/src/features/notes/ui/contextStrip.js';

const note = (id, extra = {}) => ({ id, title: id, content: '', ...extra });

test('the chat\'s own note comes first and cannot be removed; selected notes follow, once each', () => {
  assert.deepEqual(contextNoteIds('scope', ['a', 'scope', 'b', 'a']), [
    { id: 'scope', removable: false }, { id: 'a', removable: true }, { id: 'b', removable: true },
  ]);
  assert.deepEqual(contextNoteIds(null, []), []);
  assert.deepEqual(contextNoteIds(null, ['a']), [{ id: 'a', removable: true }]);
});

test('a text note shows its first three lines, without blank lines, code or images', () => {
  const content = 'Line one\n\n![photo](/api/upload/abc)\n```\nsecret code\n```\nLine **two** ![x](/a.png)\nLine three\nLine four';
  assert.equal(noteExcerpt(note('t', { content })), 'Line one\nLine **two**\nLine three');
  assert.equal(noteExcerpt(note('e')), '');
});

test('a checklist shows its first open items', () => {
  const content = '- [x] done one\n- [ ] milk\n- [ ] eggs\n  - [ ] free range\n- [ ] bread';
  assert.equal(noteExcerpt(note('c', { note_type: 'todo', content })), '- [ ] milk\n- [ ] eggs\n- [ ] free range');
  assert.equal(noteExcerpt(note('d', { note_type: 'checklist', content: '- [x] a\n- [x] b' })), 'All done');
  assert.equal(noteExcerpt(note('i', { note_type: 'todo', content: 'Shopping\n- [ ] milk' })), '- [ ] milk');
  assert.equal(noteExcerpt(note('p', { content: '- [x] a\n- [ ] b' })), '- [ ] b');
  assert.equal(noteExcerpt(note('q', { content: 'Intro\n- [ ] b' })), 'Intro\n- [ ] b');
});

test('a sent message records id and title only', () => {
  assert.deepEqual(contextNoteRecord([note('a', { title: 'Trip', content: 'x', owner: 'me' }), null, { title: 'no id' }]), [{ id: 'a', title: 'Trip' }]);
});

function harness({ server = { n1: note('n1', { title: 'Trip', content: 'hello' }), n2: note('n2', { title: 'Milk' }) }, scope = null, local = {} } = {}) {
  const listeners = {};
  const winListeners = {};
  const strip = { hidden: true, innerHTML: '', addEventListener: (type, fn) => { listeners[type] = fn; } };
  // The chat history: a list of children; clearing it is what loading a chat does.
  const history = {
    children: [],
    get firstChild() { return this.children[0] || null; },
    insertBefore(el, ref) { this.children = this.children.filter(c => c !== el); const at = ref ? this.children.indexOf(ref) : this.children.length; this.children.splice(at < 0 ? this.children.length : at, 0, el); },
  };
  const doc = {
    getElementById: id => ({ 'notes-context-strip': strip, 'chat-history': history })[id] || null,
    createElement: () => {
      const el = { dataset: {}, innerHTML: '', listeners: {}, setAttribute() {}, addEventListener(type, fn) { el.listeners[type] = fn; }, remove() { history.children = history.children.filter(c => c !== el); } };
      return el;
    },
  };
  let observed = null;
  class Observer { constructor(fn) { observed = fn; } observe() {} }
  const win = { addEventListener: (type, fn) => { winListeners[type] = fn; } };
  const calls = { get: [], opened: [], removed: [] };
  const pending = [];
  const timers = [];
  const state = { scope, hold: false };
  const api = {
    getNote: id => {
      calls.get.push(id);
      const reply = () => (server[id] ? Promise.resolve({ ...server[id] }) : Promise.reject(new Error('404')));
      if (state.hold) return new Promise((resolve, reject) => pending.push(() => reply().then(resolve, reject)));
      return reply();
    },
  };
  const ui = createContextStrip({
    api,
    esc: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
    renderMarkdown: src => `<md>${src}</md>`,
    localNote: id => local[id] || null,
    scopeNoteId: () => state.scope,
    openNote: (id, n) => calls.opened.push([id, n?.title]),
    removeFromContext: id => calls.removed.push(id),
    scheduler: { setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout: () => { timers.length = 0; } },
    doc,
    win,
    Observer,
  });
  const settle = () => new Promise(resolve => setTimeout(resolve, 0));
  return {
    ui, strip, calls, server, state, pending, settle, local, history,
    header: () => history.children.find(c => c.dataset.chatNoteId) || null,
    clearHistory: msgs => { history.children = msgs || []; observed?.(); },
    emit: (type, detail) => winListeners[type]({ detail }),
    flushTimers: () => timers.splice(0).forEach(fn => fn()),
    click: target => listeners.click({ target }),
    key: (key, target) => { let prevented = false; listeners.keydown({ key, target, preventDefault: () => { prevented = true; } }); return prevented; },
  };
}

const cardIds = html => [...html.matchAll(/data-context-note-id="([^"]+)"/g)].map(m => m[1]);
const removable = html => [...html.matchAll(/data-context-remove="([^"]+)"/g)].map(m => m[1]);
const target = attrs => ({
  closest: sel => {
    if (sel === '[data-context-remove]') return attrs.remove ? { dataset: { contextRemove: attrs.remove } } : null;
    if (sel === '[data-context-note-id]') return attrs.id ? { dataset: { contextNoteId: attrs.id } } : null;
    return null;
  },
  matches: sel => sel === '[data-context-note-id]' && !!attrs.id && !attrs.remove,
});

test('by default no note is on screen and nothing is read', async () => {
  const h = harness();
  h.ui.start();
  await h.settle();
  assert.equal(h.strip.hidden, true);
  assert.equal(h.strip.innerHTML, '');
  assert.deepEqual(h.calls.get, []);
  assert.deepEqual(h.ui.current(), []);
});

test('selected notes are cards with an x above the composer; the chat\'s own note is a card at the top of the conversation', async () => {
  const h = harness({ scope: 'n2' });
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1'] });
  await h.settle();
  assert.equal(h.strip.hidden, false);
  assert.deepEqual(cardIds(h.strip.innerHTML), ['n1']);
  assert.deepEqual(removable(h.strip.innerHTML), ['n1']);
  assert.equal(h.history.firstChild, h.header());
  assert.deepEqual(cardIds(h.header().innerHTML), ['n2']);
  assert.deepEqual(removable(h.header().innerHTML), []);
  assert.match(h.header().innerHTML, /This chat's note/);
  // The message about to be sent carries both, the chat's note first.
  assert.match(h.strip.innerHTML, /<md>hello<\/md>/);
  assert.deepEqual(h.ui.current(), [{ id: 'n2', title: 'Milk' }, { id: 'n1', title: 'Trip' }]);
  // Not a .note-card[data-note-id]: reminder glows and the panel's handlers look those up page-wide.
  assert.doesNotMatch(h.strip.innerHTML, /data-note-id=/);
});

test('a chat started from a note shows that note at the top, and nothing above the composer', async () => {
  const h = harness({ scope: 'n1' });
  h.ui.start();
  await h.settle();
  assert.equal(h.strip.hidden, true);
  assert.deepEqual(cardIds(h.header().innerHTML), ['n1']);
  assert.deepEqual(h.ui.current(), [{ id: 'n1', title: 'Trip' }]);
  h.header().listeners.click({ target: target({ id: 'n1' }) });
  assert.deepEqual(h.calls.opened, [['n1', 'Trip']]);
});

test('loading the chat\'s messages keeps its note first', async () => {
  const msg = { dataset: {} };
  const h = harness({ scope: 'n1' });
  h.ui.start();
  await h.settle();
  h.clearHistory([msg]);
  assert.equal(h.history.firstChild, h.header());
  assert.equal(h.history.children[1], msg);
  // Another chat's history loads before its session is known: the old note is not put back.
  h.state.scope = null;
  h.clearHistory([msg]);
  assert.equal(h.header(), null);
  assert.deepEqual(h.history.children, [msg]);
});

test('the sessions list arriving later brings the chat\'s note in', async () => {
  const h = harness();
  h.ui.start();
  await h.settle();
  assert.equal(h.strip.hidden, true);
  h.state.scope = 'n1';
  h.emit('odysseus:sessions-loaded', {});
  await h.settle();
  assert.deepEqual(cardIds(h.header().innerHTML), ['n1']);
});

test('switching to a chat with nothing in context clears the strip', async () => {
  const h = harness({ scope: 'n1' });
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n2'] });
  await h.settle();
  h.state.scope = null;
  h.emit('odysseus:notes-context-changed', { noteIds: [] });
  await h.settle();
  assert.equal(h.strip.hidden, true);
  assert.equal(h.strip.innerHTML, '');
  assert.equal(h.header(), null);
  assert.deepEqual(h.history.children, []);
  assert.deepEqual(h.ui.current(), []);
});

test('the x asks to take the note out of the context; a click or Enter opens it', async () => {
  const h = harness();
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1'] });
  await h.settle();
  h.click(target({ id: 'n1', remove: 'n1' }));
  assert.deepEqual(h.calls.removed, ['n1']);
  assert.deepEqual(h.calls.opened, []);
  h.click(target({ id: 'n1' }));
  assert.deepEqual(h.calls.opened, [['n1', 'Trip']]);
  assert.equal(h.key('Enter', target({ id: 'n1' })), true);
  assert.equal(h.key('a', target({ id: 'n1' })), false);
  assert.equal(h.calls.opened.length, 2);
});

test('a note that cannot be read is left out, and not asked for again', async () => {
  const h = harness();
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['gone', 'n1'] });
  await h.settle();
  assert.deepEqual(cardIds(h.strip.innerHTML), ['n1']);
  h.emit('odysseus:notes-context-changed', { noteIds: ['gone', 'n1'] });
  await h.settle();
  assert.equal(h.calls.get.filter(id => id === 'gone').length, 1);
});

test('a note already loaded is not read again; the panel\'s copy is used when it has one', async () => {
  const h = harness({ local: { n2: note('n2', { title: 'Milk (local)' }) } });
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1', 'n2'] });
  await h.settle();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1', 'n2'] });
  await h.settle();
  assert.deepEqual(h.calls.get, ['n1']);
  assert.match(h.strip.innerHTML, /Milk \(local\)/);
});

test('an edit on this page shows on its card', async () => {
  const h = harness();
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1'] });
  await h.settle();
  h.server.n1 = note('n1', { title: 'Renamed', content: 'hello' });
  h.ui.changed();
  h.flushTimers();
  await h.settle();
  assert.match(h.strip.innerHTML, /Renamed/);
});

test('a change while no note is in context reads nothing', async () => {
  const h = harness();
  h.ui.start();
  await h.settle();
  h.ui.changed();
  h.flushTimers();
  await h.settle();
  assert.deepEqual(h.calls.get, []);
});

test('a slower, older read never overwrites a newer selection', async () => {
  const h = harness();
  h.ui.start();
  h.state.hold = true;
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1'] });
  h.state.hold = false;
  h.emit('odysseus:notes-context-changed', { noteIds: [] });
  await h.settle();
  h.pending.forEach(release => release());
  await h.settle();
  await h.settle();
  assert.equal(h.strip.hidden, true);
});

test('an older read of a note never overwrites a newer one', async () => {
  const h = harness();
  h.ui.start();
  h.state.hold = true;
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1'] });
  h.state.hold = false;
  h.server.n1 = note('n1', { title: 'Newer' });
  await h.ui.sync({ reload: true });
  assert.match(h.strip.innerHTML, /Newer/);
  h.server.n1 = note('n1', { title: 'Older' });
  h.pending.forEach(release => release());
  await h.settle();
  await h.settle();
  assert.match(h.strip.innerHTML, /Newer/);
  assert.doesNotMatch(h.strip.innerHTML, /Older/);
});

test('titles are escaped', async () => {
  const h = harness({ server: { n1: note('n1', { title: '<img src=x onerror=alert(1)>' }) } });
  h.ui.start();
  h.emit('odysseus:notes-context-changed', { noteIds: ['n1'] });
  await h.settle();
  assert.doesNotMatch(h.strip.innerHTML, /<img/);
  assert.match(h.strip.innerHTML, /&lt;img/);
});

test('a note opens in the window over the chat, added to the page\'s list when it is missing', () => {
  const make = ({ has = false, open = false, archived = false, loading = false } = {}) => {
    let list = has ? [note('n1')] : [];
    const log = [];
    const deps = {
      state: { open, showingArchived: archived, loading },
      app: { prependNote: n => { log.push(['add', n.id]); list = [n, ...list]; } },
      openWindow: id => { const ok = list.some(n => n.id === id); log.push(['window', id, ok]); return ok; },
      openInPanel: id => log.push(['panel', id]),
    };
    return { deps, log };
  };
  let h = make({ has: true });
  assert.equal(openNoteOverChat('n1', note('n1'), h.deps), 'window');
  assert.deepEqual(h.log, [['window', 'n1', true]]);
  h = make();
  assert.equal(openNoteOverChat('n1', note('n1'), h.deps), 'window');
  assert.deepEqual(h.log, [['window', 'n1', false], ['add', 'n1'], ['window', 'n1', true]]);
  h = make();
  assert.equal(openNoteOverChat('n1', null, h.deps), 'panel');
  for (const flags of [{ open: true }, { archived: true }, { loading: true }]) {
    h = make(flags);
    assert.equal(openNoteOverChat('n1', note('n1'), h.deps), 'panel');
    assert.deepEqual(h.log, [['window', 'n1', false], ['panel', 'n1']]);
  }
});
