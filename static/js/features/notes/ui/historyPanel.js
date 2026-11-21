/** The note window's History sheet: what changed the note, newest first under a "Now" row and grouped by day, and for the chosen change what it did (added lines green, removed red), plus, on demand, what undoing it and everything after it would do to the note as it is now. It takes the body's place like the sketch sheet, on desktop and on a phone alike. */
import { changeAuthor, changeDiff, changeSentence, foldDiff, groupByDay, noteDiff, relativeTime, restoreDiff } from '../domain/historyText.js';

const NOW = 'now';

/**
 * Fill `panel` with the history of `noteId`. Ports: `history` ({ list,
 * read, restore }), `currentNote()` (the note as it is now), `esc`, `stamp`
 * (a server timestamp in full, for tooltips), `openChat(sessionId)`, and
 * `onClose()` (back to the note). `focus`: { sessionId } selects that chat's
 * newest agent edit instead of the newest change. Returns { reload, ready }.
 */
export function mountHistoryPanel(panel, { noteId, me, history, currentNote, esc, stamp, openChat, onClose, focus = null }) {
  let versions = [];
  let selected = null;
  let shown = null;
  let busy = false;
  let undoShown = false;
  let wanted = focus;

  const head = '<div class="note-window-history-head"><span class="note-reminder-menu-title">History</span>'
    + '<button type="button" class="memory-toolbar-btn" data-act="back">Back to note</button></div>';

  function paint(inner) {
    panel.innerHTML = head + inner;
    panel.querySelector('[data-act="back"]').addEventListener('click', () => onClose());
  }

  function status(message, retry = false) {
    paint(`<div class="note-share-status">${esc(message)}</div>${retry ? '<button type="button" class="memory-toolbar-btn" data-act="retry">Try again</button>' : ''}`);
    panel.querySelector('[data-act="retry"]')?.addEventListener('click', () => reload());
  }

  const row = (id, main, sub, title = '') => `<button type="button" class="note-reminder-menu-item note-window-history-row${id === selected ? ' active' : ''}" data-vid="${esc(String(id))}" aria-pressed="${id === selected}">`
    + `<span>${esc(main)}</span><span class="note-reminder-menu-sub"${title ? ` title="${esc(title)}"` : ''}>${esc(sub)}</span></button>`;

  function listHtml() {
    const now = new Date();
    const current = currentNote();
    let html = row(NOW, 'Now', current?.updated_at ? `Edited ${relativeTime(current.updated_at, now)}` : 'The note as it is', current?.updated_at ? stamp(current.updated_at) : '');
    for (const group of groupByDay(versions, now)) {
      html += `<div class="note-reminder-menu-title note-window-history-day">${esc(group.label)}</div>`;
      html += group.rows.map(v => row(v.id, changeSentence(v, me), relativeTime(v.created_at, now), stamp(v.created_at))).join('');
    }
    return `<div class="note-window-history-list" role="list">${html}</div>`;
  }

  function diffHtml(diff) {
    const fields = diff.fields.map(f => `<div class="note-window-history-field"><span class="note-window-history-field-name">${esc(f.name)}</span> `
      + `<span class="note-window-history-del">${esc(f.from)}</span> <span class="note-window-history-add">${esc(f.to)}</span></div>`).join('');
    const lines = foldDiff(diff.lines).map(line => {
      if (line.op === 'gap') return `<div class="note-window-history-gap">${line.count === 1 ? '1 unchanged line' : `${line.count} unchanged lines`}</div>`;
      const cls = line.op === 'add' ? ' note-window-history-add' : line.op === 'del' ? ' note-window-history-del' : '';
      const mark = line.op === 'add' ? '+ ' : line.op === 'del' ? '- ' : '  ';
      return `<div class="note-window-history-line${cls}"><span class="note-window-history-mark" aria-hidden="true">${mark}</span>${esc(line.text) || '&nbsp;'}</div>`;
    }).join('');
    return `${fields}${diff.lines.some(l => l.op !== 'same') ? `<div class="note-window-history-diff">${lines}</div>` : ''}`;
  }

  function detailHtml() {
    if (selected === NOW) {
      return '<div class="note-window-history-preview note-share-status">This is the note as it is now. Pick a change below to see what it did.</div>';
    }
    if (!shown || shown.id !== selected) return selected != null ? '<div class="note-share-status">Loading…</div>' : '';
    const version = versions.find(v => v.id === shown.id) || shown;
    const { who, chat, chatLink } = changeAuthor(version, me);
    const by = chatLink
      ? `${esc(who)} in <button type="button" class="note-window-meta-undo" data-act="chat" title="Open this chat">${esc(chat)}</button>`
      : esc(chat ? `${who} in ${chat}` : who);
    const when = `<span title="${esc(stamp(version.created_at))}">${esc(relativeTime(version.created_at, new Date()))}</span>`;
    const now = currentNote();
    const change = changeDiff(shown);
    const undo = restoreDiff(now, shown);
    // Changes made after this one: undoing goes back over them too, so it is not just this diff reversed.
    const later = !!shown.after && !noteDiff(shown.after, now).same;
    const body = change.same
      ? '<div class="note-share-status">This change left the text and the fields as they were.</div>'
      : `<div class="note-window-history-caption">What this change did:</div>${diffHtml(change)}`;
    const undoLabel = later ? 'Undo this change and everything after it' : 'Undo this change';
    const action = undo.same || shown.is_current
      ? '<button type="button" class="memory-toolbar-btn" data-act="restore" disabled>Same as now</button>'
      : `<button type="button" class="memory-toolbar-btn active" data-act="restore" title="Put the note back as it was before this change"${busy ? ' disabled' : ''}>${undoLabel}</button>`;
    const undoPreview = later && !undo.same
      ? `<div class="note-window-sketch-actions"><button type="button" class="memory-toolbar-btn" data-act="undo-preview" aria-expanded="${undoShown}">${undoShown ? 'Hide what undoing does' : 'Show what undoing does'}</button></div>`
        + (undoShown ? `<div class="note-window-history-preview" data-part="undo"><div class="note-window-history-caption">Undoing this change and everything after it would do this to the note as it is now:</div>${diffHtml(undo)}</div>` : '')
      : '';
    return '<div class="note-window-history-preview">'
      + `<div class="note-window-history-by">${by} · ${when}</div>${body}</div>`
      + undoPreview
      + `<div class="note-window-sketch-actions">${action}</div>`;
  }

  function render() {
    if (!versions.length) { status('No changes kept yet. One is kept before every agent edit, every restore, and your own edits from time to time.'); return; }
    paint(listHtml() + detailHtml());
    panel.querySelectorAll('[data-vid]').forEach(button => button.addEventListener('click', () => select(button.dataset.vid === NOW ? NOW : Number(button.dataset.vid))));
    panel.querySelector('[data-act="chat"]')?.addEventListener('click', () => openChat(shown.session_id));
    panel.querySelector('[data-act="undo-preview"]')?.addEventListener('click', () => { undoShown = !undoShown; render(); });
    panel.querySelector('[data-act="restore"]:not([disabled])')?.addEventListener('click', () => restore(shown));
  }

  async function select(id) {
    selected = id;
    undoShown = false;
    shown = null;
    render();
    if (id === NOW) return;
    try {
      const version = await history.read(noteId, id);
      if (selected !== id) return;
      shown = version;
    } catch {
      if (selected !== id) return;
      selected = null;
      status('Could not load this change.', true);
      return;
    }
    render();
  }

  async function restore(version) {
    if (busy) return;
    busy = true;
    render();
    const ok = await history.restore(noteId, version.id, { at: version.created_at });
    busy = false;
    if (ok) { onClose(); return; }
    await reload();
  }

  /** The row to start on: the asked-for chat's newest agent edit, else the newest change. */
  function initial() {
    if (versions.some(v => v.id === selected)) return selected;
    const sid = wanted?.sessionId;
    wanted = null;
    const fromChat = sid ? versions.find(v => v.source === 'agent' && v.session_id === sid) : null;
    return (fromChat || versions[0])?.id;
  }

  async function reload() {
    status('Loading…');
    try {
      versions = await history.list(noteId);
    } catch {
      status('Could not load the history.', true);
      return;
    }
    // The chosen version is read again (whether it is still "Same as now" may have changed).
    const next = initial();
    selected = null;
    shown = null;
    render();
    if (next != null) await select(next);
  }

  return { reload, ready: reload() };
}
