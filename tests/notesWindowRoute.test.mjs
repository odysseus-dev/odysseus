import test from 'node:test';
import assert from 'node:assert/strict';
import { createWindowRoute, noteIdFromHash, noteHash, chatIdFromHash, CHAT_KEY } from '../frontend/src/features/notes/ui/windowRoute.js';

// A same-document session history: entries, an index, popstate on traversal
// (asynchronous, as in a browser), none on pushState / replaceState.
function fakeWindow(url = 'http://app.test/') {
  const listeners = [];
  const entries = [{ url: new URL(url), state: null }];
  let index = 0;
  const pending = [];
  const at = () => entries[index];
  const win = {
    location: {
      get pathname() { return at().url.pathname; },
      get search() { return at().url.search; },
      get hash() { return at().url.hash; },
      get href() { return at().url.href; },
    },
    history: {
      get state() { return at().state; },
      get length() { return entries.length; },
      pushState(state, _title, next) {
        entries.splice(index + 1);
        entries.push({ url: new URL(next, at().url), state: structuredClone(state) });
        index += 1;
      },
      replaceState(state, _title, next) {
        entries[index] = { url: new URL(next ?? at().url.href, at().url), state: structuredClone(state) };
      },
      back() { pending.push(-1); },
      forward() { pending.push(1); },
    },
    addEventListener(type, fn) { if (type === 'popstate') listeners.push(fn); },
    /** Run queued traversals, as the browser's task queue would. */
    flush() {
      while (pending.length) {
        const delta = pending.shift();
        const next = index + delta;
        if (next < 0 || next >= entries.length) continue;
        index = next;
        listeners.forEach(fn => fn({ state: at().state }));
      }
    },
    entries: () => entries.map(e => e.url.pathname + e.url.search + e.url.hash),
    index: () => index,
  };
  return win;
}

// The note window, as far as the route sees it.
function fakeNotes(win, readable = ['n1', 'n2']) {
  let open = null;
  let route;
  const notes = {
    opened: [],
    closedByHistory: 0,
    async openNote(id) {
      if (!readable.includes(id)) return false;
      notes.show(id);
      return true;
    },
    /** What noteWindow.open does: close the current one keeping its entry, mount, report. */
    show(id) { open = id; notes.opened.push(id); route.opened(id); },
    showNew() { open = 'new'; route.opened(null); },
    /** Closed by hand: the close button, Escape, delete. */
    closeByHand() { open = null; route.closed(); },
    current: () => open,
  };
  route = createWindowRoute({
    win,
    openNote: id => notes.openNote(id),
    closeWindow: () => { open = null; notes.closedByHistory += 1; },
    isOpen: () => open !== null,
    isOpenOn: id => open === id,
    setTimer: () => {},
  });
  notes.route = route;
  return notes;
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('a note hash names a note id, and nothing else', () => {
  assert.equal(noteIdFromHash('#note-3f2a-9c'), '3f2a-9c');
  assert.equal(noteIdFromHash(noteHash('a_b-1')), 'a_b-1');
  for (const hash of ['', '#', '#note-', '#abc', '#document-x', '#note-..%2F..%2Fadmin', '#note-a%2Fb', '#note-%E0%A4%A', '#open=notes&note=x']) {
    assert.equal(noteIdFromHash(hash), null, hash);
  }
});

test('opening a note adds an entry; Back closes the window and stays in the app; Forward opens it again', async () => {
  const win = fakeWindow('http://app.test/#chat-1');
  const notes = fakeNotes(win);
  notes.route.start();
  notes.show('n1');
  assert.deepEqual(win.entries(), ['/#chat-1', '/#note-n1']);

  win.history.back(); win.flush(); await tick();
  assert.equal(notes.current(), null);
  assert.equal(notes.closedByHistory, 1);
  assert.equal(win.location.hash, '#chat-1', 'the chat hash is back');

  win.history.forward(); win.flush(); await tick();
  assert.equal(notes.current(), 'n1');
  assert.deepEqual(win.entries(), ['/#chat-1', '/#note-n1'], 'Forward adds nothing');
});

test('closing by hand takes the entry off, so Back then leaves the note behind', () => {
  const win = fakeWindow('http://app.test/#chat-1');
  const notes = fakeNotes(win);
  notes.route.start();
  notes.show('n1');
  notes.closeByHand();
  win.flush();
  assert.equal(win.index(), 0);
  assert.equal(win.location.hash, '#chat-1');
  assert.equal(notes.closedByHistory, 0, 'the popstate of that back() closes nothing');
});

test('one note replacing another reuses the entry: Back closes, it does not reopen the first', async () => {
  const win = fakeWindow('http://app.test/');
  const notes = fakeNotes(win);
  notes.route.start();
  notes.show('n1');
  notes.show('n2');
  assert.deepEqual(win.entries(), ['/', '/#note-n2']);
  win.history.back(); win.flush(); await tick();
  assert.equal(notes.current(), null);
});

test('an open right after a close by hand waits for that back() before adding its entry', () => {
  const win = fakeWindow('http://app.test/');
  const notes = fakeNotes(win);
  notes.route.start();
  notes.show('n1');
  notes.closeByHand();
  notes.show('n2');
  win.flush();
  assert.deepEqual(win.entries(), ['/', '/#note-n2']);
  assert.equal(win.index(), 1);
});

test('a new note gets an entry at once, and its hash when the first save gives it an id', async () => {
  const win = fakeWindow('http://app.test/#chat-1');
  const notes = fakeNotes(win);
  notes.route.start();
  notes.showNew();
  assert.deepEqual(win.entries(), ['/#chat-1', '/#chat-1']);
  notes.route.created('n9');
  assert.deepEqual(win.entries(), ['/#chat-1', '/#note-n9']);
  win.history.back(); win.flush(); await tick();
  assert.equal(notes.current(), null);
});

test('a page loaded on #note-<id> opens the note over an entry without it', async () => {
  const win = fakeWindow('http://app.test/?x=1#note-n1');
  const notes = fakeNotes(win);
  assert.equal(await notes.route.start(), true);
  assert.equal(notes.current(), 'n1');
  assert.deepEqual(win.entries(), ['/?x=1', '/?x=1#note-n1']);
  win.history.back(); win.flush(); await tick();
  assert.equal(notes.current(), null);
  assert.equal(win.index(), 0, 'still in the app');
});

test('a page loaded on a note that is gone or not readable opens nothing and drops the hash', async () => {
  const win = fakeWindow('http://app.test/#note-other');
  const notes = fakeNotes(win);
  assert.equal(await notes.route.start(), false);
  assert.equal(notes.current(), null);
  assert.deepEqual(win.entries(), ['/']);
});

test('Forward to a note deleted since opens nothing, and its hash goes', async () => {
  const win = fakeWindow('http://app.test/');
  const readable = ['n1'];
  const notes = fakeNotes(win, readable);
  notes.route.start();
  notes.show('n1');
  win.history.back(); win.flush(); await tick();
  readable.length = 0;
  win.history.forward(); win.flush(); await tick();
  assert.equal(notes.current(), null);
  assert.equal(win.location.hash, '');
});

test('a chat hash names a chat, an entity hash does not', () => {
  assert.equal(chatIdFromHash('#3f2a-b1'), '3f2a-b1');
  assert.equal(chatIdFromHash('#note-n1'), null);
  assert.equal(chatIdFromHash('#document-d1'), null);
  assert.equal(chatIdFromHash('#open=notes&note=n1'), null);
  assert.equal(chatIdFromHash(''), null);
  assert.equal(chatIdFromHash('#a/b'), null);
});

// A reload: same entry, same history.state, a new page (a new route, a new token).
function reloaded(win) {
  const again = fakeWindow(win.location.href);
  again.history.replaceState(win.history.state, '', win.location.href);
  return again;
}

test('the entry keeps the chat it was opened over, and a reload comes back to it with the note on top', async () => {
  const win = fakeWindow('http://app.test/#chat-1');
  const notes = fakeNotes(win);
  notes.route.start();
  notes.show('n1');
  assert.equal(win.history.state[CHAT_KEY], 'chat-1');
  const after = reloaded(win);
  const again = fakeNotes(after);
  assert.equal(await again.route.start(), true);
  assert.equal(again.current(), 'n1');
  assert.deepEqual(after.entries(), ['/#chat-1', '/#note-n1']);
  assert.equal(after.history.state[CHAT_KEY], 'chat-1');
  after.history.back(); after.flush(); await tick();
  assert.equal(again.current(), null);
  assert.equal(after.location.hash, '#chat-1', 'Back stays on that chat');
});

test('one note replacing another keeps the chat under them', () => {
  const win = fakeWindow('http://app.test/#chat-1');
  const notes = fakeNotes(win);
  notes.show('n1');
  notes.show('n2');
  assert.deepEqual(win.entries(), ['/#chat-1', '/#note-n2']);
  assert.equal(win.history.state[CHAT_KEY], 'chat-1');
});

test('a note opened over a chat that has no id yet records none', async () => {
  const win = fakeWindow('http://app.test/');
  const notes = fakeNotes(win);
  notes.show('n1');
  assert.equal(win.history.state[CHAT_KEY], null);
  const after = reloaded(win);
  await fakeNotes(after).route.start();
  assert.deepEqual(after.entries(), ['/', '/#note-n1']);
});

test('after a reload, a note gone since drops only its own hash: the chat stays', async () => {
  const win = fakeWindow('http://app.test/#chat-1');
  fakeNotes(win).show('n1');
  const after = reloaded(win);
  assert.equal(await fakeNotes(after, []).route.start(), false);
  assert.deepEqual(after.entries(), ['/#chat-1']);
});

test('closing by hand a note the page was loaded on puts the chat hash back', () => {
  const win = fakeWindow('http://app.test/#note-n1');
  win.history.replaceState({ odysseusNoteWindow: 'earlier-page', [CHAT_KEY]: 'chat-1' }, '', '/#note-n1');
  const notes = fakeNotes(win);
  notes.show('n1');
  notes.closeByHand();
  win.flush();
  assert.equal(win.location.hash, '#chat-1');
});

test('an entry this page did not push is never removed with back()', () => {
  // A reload on #note-n1 keeps the history state of the page before it.
  const win = fakeWindow('http://app.test/#note-n1');
  win.history.replaceState({ odysseusNoteWindow: 'earlier-page' }, '', '/#note-n1');
  const notes = fakeNotes(win);
  notes.show('n1');
  assert.equal(win.history.length, 1, 'already on its hash: nothing pushed');
  notes.closeByHand();
  win.flush();
  assert.equal(win.index(), 0);
  assert.equal(win.location.hash, '', 'its hash goes, the entry stays');
});
