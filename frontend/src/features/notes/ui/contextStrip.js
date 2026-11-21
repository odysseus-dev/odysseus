/**
 * The notes in the current chat's context. Nothing shows when the chat
 * carries no note. The notes selected for the chat are cards above the
 * composer, each with an x that takes it out of the selection. The note a
 * chat was started from is a card at the top of the conversation, before its
 * first message: it is in context on every turn and stays. Read-only: a card
 * opens its note.
 */
import { contextNoteIds, contextNoteRecord, noteExcerpt } from '../domain/contextNotes.js';

const STRIP_ID = 'notes-context-strip';
const HISTORY_ID = 'chat-history';
// Saves from the note window come in bursts while typing.
const CHANGE_DELAY_MS = 300;
const REMOVE_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

/**
 * Open a note in the note window, over the chat. The window reads the note
 * from this page's Notes list, which may not have it (a note it never loaded).
 * With the panel closed and nothing loading, the note is added to that list;
 * otherwise the panel loads the note and opens it.
 */
export function openNoteOverChat(id, note, { state, app, openWindow, openInPanel }) {
  if (openWindow(id)) return 'window';
  if (note && !state.open && !state.showingArchived && !state.loading) {
    app.prependNote({ ...note });
    if (openWindow(id)) return 'window';
  }
  openInPanel(id);
  return 'panel';
}

export function createContextStrip({
  api,
  esc,
  renderMarkdown,
  colorClass = () => '',
  // This page's copy of a note when it is the newest (the panel's list), else null.
  localNote = () => null,
  // The chat's own note: the sessions list's note_scope_id for the current chat.
  scopeNoteId = () => null,
  openNote,
  removeFromContext,
  scheduler,
  doc = globalThis.document,
  win = globalThis.window,
  Observer = globalThis.MutationObserver,
}) {
  let strip = null;
  let history = null;
  let header = null;
  let selected = [];
  const notes = new Map();
  const missing = new Set();
  let seq = 0;
  let changeTimer = null;

  const entries = () => contextNoteIds(scopeNoteId(), selected);

  function hide() {
    if (!strip) return;
    strip.hidden = true;
    strip.innerHTML = '';
  }

  function cardHtml({ id, removable }) {
    const note = notes.get(id);
    const title = String(note.title || '').trim();
    const excerpt = noteExcerpt(note);
    const body = excerpt ? renderMarkdown(excerpt) : '';
    const label = title || 'Untitled note';
    return `<div class="note-card notes-context-card${colorClass(note)}" role="button" tabindex="0" data-context-note-id="${esc(id)}" aria-label="Open note: ${esc(label)}">`
      + `<div class="note-card-header"><div class="note-card-title${title ? '' : ' empty'}">${esc(title)}</div>`
      + (removable ? `<button type="button" class="notes-context-remove" data-context-remove="${esc(id)}" title="Remove from this chat's context" aria-label="Remove ${esc(label)} from this chat's context">${REMOVE_ICON}</button>` : '')
      + '</div>'
      + (body ? `<div class="note-content-preview note-md-preview notes-context-excerpt">${body}</div>` : '')
      + '</div>';
  }

  // The chat's note at the top of the conversation. Loading a chat empties
  // the history, so the card is put back first whenever it is taken out,
  // as long as it is still this chat's note.
  function placeHeader() {
    if (!header || !history) return;
    if (header.dataset.chatNoteId !== String(scopeNoteId() || '')) { header.remove(); header = null; return; }
    if (history.firstChild !== header) history.insertBefore(header, history.firstChild);
  }

  function renderHeader(entry) {
    if (!history) return;
    if (!entry) { header?.remove(); header = null; return; }
    if (!header) {
      header = doc.createElement('div');
      header.className = 'notes-chat-note';
      header.setAttribute('role', 'note');
      header.setAttribute('aria-label', "This chat's note");
      header.addEventListener('click', event => activate(event.target));
      header.addEventListener('keydown', onKey);
    }
    header.dataset.chatNoteId = entry.id;
    header.innerHTML = `<div class="notes-chat-note-label">This chat's note</div>${cardHtml(entry)}`;
    placeHeader();
  }

  /** The cards for the notes in context that are loaded. */
  function render() {
    if (!strip) return;
    const shown = entries().filter(entry => notes.has(entry.id));
    renderHeader(shown.find(entry => !entry.removable) || null);
    const selectedCards = shown.filter(entry => entry.removable);
    if (!selectedCards.length) { hide(); return; }
    strip.innerHTML = selectedCards.map(cardHtml).join('');
    strip.hidden = false;
  }

  /** Load the notes in context this strip has not got yet, then draw. A note that cannot be read (deleted, not mine) is left out. */
  async function sync({ reload = false } = {}) {
    if (!strip) return;
    const mine = ++seq;
    const wanted = entries().map(entry => entry.id);
    const toLoad = wanted.filter(id => reload || (!notes.has(id) && !missing.has(id)));
    for (const id of toLoad) {
      const local = localNote(id);
      if (local) { notes.set(id, { ...local }); missing.delete(id); }
    }
    const remote = toLoad.filter(id => !localNote(id));
    if (remote.length) {
      const loaded = await Promise.all(remote.map(id => api.getNote(id).then(note => [id, note], () => [id, null])));
      if (mine !== seq) return;
      for (const [id, note] of loaded) {
        if (note) { notes.set(id, note); missing.delete(id); }
        else { notes.delete(id); missing.add(id); }
      }
    }
    render();
  }

  /** chat.js says which notes are selected for the current chat. */
  function setSelected(ids) {
    selected = Array.isArray(ids) ? ids.map(String) : [];
    sync();
  }

  /** A note changed on this page (window save, pin, archive): redraw the cards from the newest copy. */
  function changed() {
    if (!strip) return;
    scheduler.clearTimeout(changeTimer);
    changeTimer = scheduler.setTimeout(() => { changeTimer = null; sync({ reload: true }); }, CHANGE_DELAY_MS);
  }

  /** What the message about to be sent carries: the notes on screen, as a sent message records them. */
  function current() {
    if (!strip) return [];
    return contextNoteRecord(entries().filter(entry => notes.has(entry.id)).map(entry => notes.get(entry.id)));
  }

  function activate(target) {
    const remove = target.closest('[data-context-remove]');
    if (remove) { removeFromContext(remove.dataset.contextRemove); return; }
    const card = target.closest('[data-context-note-id]');
    if (card) openNote(card.dataset.contextNoteId, notes.get(card.dataset.contextNoteId) || null);
  }

  function onKey(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (!event.target.matches?.('[data-context-note-id]')) return;
    event.preventDefault();
    activate(event.target);
  }

  function start() {
    if (strip) return;
    strip = doc.getElementById(STRIP_ID);
    if (!strip) return;
    history = doc.getElementById(HISTORY_ID);
    if (history && Observer) new Observer(placeHeader).observe(history, { childList: true });
    strip.addEventListener('click', event => activate(event.target));
    strip.addEventListener('keydown', onKey);
    win.addEventListener('odysseus:notes-context-changed', event => setSelected(event.detail?.noteIds));
    // The chat's own note comes from the sessions list: a new chat, or the
    // list arriving after the chat was opened.
    win.addEventListener('odysseus:session-changed', () => sync());
    win.addEventListener('odysseus:sessions-loaded', () => sync());
    sync();
  }

  return { start, sync, render, changed, current, setSelected, isShowing: () => !!strip && !strip.hidden, hasChatNote: () => !!header };
}
