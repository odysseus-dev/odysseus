/**
 * The note window in the browser history, as `#note-<id>`.
 *
 * Opening the window adds a history entry, so Back closes it (the app stays)
 * and Forward opens it again. Closing it by hand takes that entry back off.
 * A page loaded on `#note-<id>` opens that note, over an entry without it,
 * so Back from there stays in the app too. A note that is gone or not
 * readable opens nothing, and its hash goes.
 *
 * Only an entry this page pushed is ever removed with history.back(): an
 * entry from an earlier page load or another site is left where it is.
 *
 * The entry also keeps the chat the note is open over, in history.state
 * (CHAT_KEY, kept up to date by sessions.js while the note is open). A reload
 * keeps history.state, a new tab has none: a reload comes back to that chat
 * with the note on top, a `#note-<id>` link in a new tab opens over the chat
 * a plain load gives.
 */
const NOTE_HASH_RE = /^#note-(.+)$/;
const NOTE_ID_RE = /^[A-Za-z0-9_-]{1,128}$/;
const MARK = 'odysseusNoteWindow';
// Same key in static/js/sessions.js.
export const CHAT_KEY = 'odysseusChatId';
const CHAT_HASH_RE = /^#([A-Za-z0-9_-]{1,128})$/;
const ENTITY_HASH_RE = /^#(?:document|note|image|email|event|task|skill|research)-/;
// history.back() is asynchronous: an open that comes before its popstate
// waits for it, or this long when it never comes.
const BACK_TIMEOUT_MS = 1000;

/** The note id in a `#note-<id>` hash, or null. */
export function noteIdFromHash(hash) {
  const match = NOTE_HASH_RE.exec(String(hash || ''));
  if (!match) return null;
  let id = '';
  try { id = decodeURIComponent(match[1]); } catch { return null; }
  // A note id, nothing that could reach another path once in a URL.
  return NOTE_ID_RE.test(id) ? id : null;
}

export const noteHash = id => `#note-${encodeURIComponent(id)}`;

/** The chat id in a plain `#<session-id>` hash, or null. */
export function chatIdFromHash(hash) {
  const value = String(hash || '');
  if (ENTITY_HASH_RE.test(value)) return null;
  const match = CHAT_HASH_RE.exec(value);
  return match ? match[1] : null;
}

export function createWindowRoute({
  win = globalThis.window,
  // Open a note the page may not have loaded yet. Resolves truthy when its window is open.
  openNote,
  // Close the window without touching the history (Back already moved it).
  closeWindow,
  isOpen,
  isOpenOn,
  setTimer = (fn, ms) => setTimeout(fn, ms),
}) {
  const token = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let backPending = false;
  let queued = [];

  const loc = () => win.location;
  const base = () => `${loc().pathname}${loc().search}`;
  const ours = () => win.history.state?.[MARK] === token;
  const marked = () => ({ ...(win.history.state || {}), [MARK]: token });
  // The chat under the note: the hash it is opened over, else the one this entry recorded.
  const chat = () => {
    const shown = chatIdFromHash(loc().hash);
    if (shown) return shown;
    const kept = win.history.state?.[CHAT_KEY];
    return typeof kept === 'string' && chatIdFromHash(`#${kept}`) ? kept : null;
  };
  // This entry without the note: back on the chat's own hash.
  const withoutNote = () => {
    const id = chat();
    return `${base()}${id ? `#${id}` : ''}`;
  };

  function whenSettled(fn) {
    if (backPending) queued.push(fn);
    else fn();
  }

  function settle() {
    backPending = false;
    const run = queued;
    queued = [];
    run.forEach(fn => fn());
  }

  /** The window opened on `id` (null: a note not saved yet). */
  function opened(id) {
    whenSettled(() => {
      const target = id ? noteHash(id) : null;
      // Already there: Forward, or a hash typed in the address bar.
      if (target && loc().hash === target) return;
      const url = target ? `${base()}${target}` : loc().href;
      // Our entry is current (one note replaced by another): reuse it.
      if (ours()) win.history.replaceState(marked(), '', url);
      else win.history.pushState({ [MARK]: token, [CHAT_KEY]: chat() }, '', url);
    });
  }

  /** A new note got its id from its first save, with its window still open. */
  function created(id) {
    whenSettled(() => {
      if (ours() && id) win.history.replaceState(marked(), '', `${base()}${noteHash(id)}`);
    });
  }

  /** Closed by hand (close button, Escape, delete...): its entry goes. */
  function closed() {
    if (backPending) return;
    if (ours()) {
      backPending = true;
      win.history.back();
      setTimer(() => { if (backPending) settle(); }, BACK_TIMEOUT_MS);
      return;
    }
    // Not pushed here (the page was loaded on it): drop the hash, keep the entry.
    if (noteIdFromHash(loc().hash)) win.history.replaceState(win.history.state, '', withoutNote());
  }

  async function route(id) {
    let shown = false;
    try { shown = !!(await openNote(id)); } catch { shown = false; }
    // Unknown or no longer readable: the hash goes, nothing opens.
    if (!shown && noteIdFromHash(loc().hash) === id) win.history.replaceState(win.history.state, '', withoutNote());
    return shown;
  }

  function onPopState() {
    if (backPending) { settle(); return; }
    const id = noteIdFromHash(loc().hash);
    if (id && isOpenOn(id)) return;
    if (isOpen()) closeWindow();
    if (id) route(id);
  }

  /**
   * At page load: a `#note-<id>` URL opens that note. Its hash moves to an
   * entry of its own, pushed when the window opens, so Back from there stays
   * in the app. After a reload, the entry under it is the chat's again.
   */
  function start() {
    win.addEventListener('popstate', onPopState);
    const id = noteIdFromHash(loc().hash);
    if (!id) return Promise.resolve(false);
    win.history.replaceState(win.history.state, '', withoutNote());
    return route(id);
  }

  return { opened, created, closed, start, onPopState };
}
