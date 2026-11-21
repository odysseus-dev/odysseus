/** The note window: the one place a note is read and edited, on desktop and mobile. One at a time; opening another note flushes this one and rebuilds. */
import { bindTap, toolbarHtml } from './richBody.js';
import { parseTags } from './noteTags.js';
import { isOwnNote, canEditNote, isSharedNote, canRunNoteAgent, showsTaskAgent, sizeRefusal } from '../domain/noteRules.js';
import { AGENT_ICON, AGENT_CHAT_ICON, TASK_AGENT_CLASS, decorateTaskRows, taskRowText } from './noteAgent.js';
import { mountHistoryPanel } from './historyPanel.js';
import { taskFlips } from '../application/noteSession.js';

const NOTE_WINDOW_ID = 'note-window-modal';
const NOTE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
const BELL_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
const ARCHIVE_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
const UNARCHIVE_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14l-5-5 5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>';
const SHARE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>';
const LEAVE_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';
const HISTORY_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><polyline points="3 3 3 8 8 8"/><polyline points="12 7 12 12 15 14"/></svg>';
const TRASH_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

// Server timestamps are naive UTC.
const asDate = iso => new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`);
function stamp(iso) {
  if (!iso) return '';
  const date = asDate(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}
export function chipLabel(title) {
  const label = `Note: ${(title || '').trim() || 'Untitled'}`;
  return label.length > 28 ? `${label.slice(0, 27)}…` : label;
}
const SERVER_FIELDS = ['title', 'content', 'items', 'note_type', 'image_url', 'body_revision', 'updated_at', 'created_at', 'color', 'label', 'due_date', 'repeat', 'sort_order', 'archived', 'pinned', 'agent_edit', 'version_count',
  'is_owner', 'shared_by', 'my_permission', 'can_edit', 'is_shared', 'shared_with'];
const serverFields = note => Object.fromEntries(SERVER_FIELDS.filter(key => note && key in note).map(key => [key, note[key]]));

export function createNoteWindow({
  app,
  state,
  Modals,
  makeWindowDraggable,
  bindMenuDismiss,
  dismissOrRemove,
  richBody,
  noteTags,
  sharePopover,
  agentUi,
  agentActions,
  createSession,
  wireReminderPicker,
  drawing,
  colors,
  esc,
  attrEsc,
  isMobile,
  isLocalVersion,
  effects,
  // The window's entry in the browser history (windowRoute.js).
  navigation = { opened() {}, created() {}, closed() {} },
  // Put the window above every other tool window, the Notes panel included.
  raise = () => {},
}) {
  const { COLORS, COLOR_HEX, _dotBg, _dotIsActive, _isBgImage, _pickCustomBgImage } = colors;
  let current = null;
  // Set while a close must leave the history alone: Back did it, or another note replaces this one.
  let keepEntry = false;

  const ring = color => (!color || _isBgImage(color) ? 'transparent' : (COLOR_HEX[color] || 'transparent'));
  const stateNote = id => (id ? state.notes.find(n => n.id === id) : null);

  function build() {
    const modal = document.createElement('div');
    modal.className = 'modal note-window';
    modal.id = NOTE_WINDOW_ID;
    // Space over the window must not minimise it: a view-only body never has
    // focus, so ui.js would otherwise treat the window as idle.
    modal.dataset.spaceMinimize = 'off';
    // The window keeps its title and caret on screen with the keyboard up
    // (watchCaret, and 15-notes.css keyboard-open): ui.js must not scroll it too.
    modal.dataset.keyboardScroll = 'off';
    const content = document.createElement('div');
    content.className = 'modal-content note-window-content';
    content.innerHTML = `
      <div class="modal-header note-window-header">
        <input type="text" class="note-window-title" placeholder="Title" aria-label="Note title" autocomplete="off" />
        <button type="button" class="note-window-icon-btn note-window-share hidden" title="Share" aria-label="Share">${SHARE_ICON}<span class="note-window-share-count"></span></button>
        <button type="button" class="note-form-remind-btn note-window-icon-btn note-window-remind" title="Remind me" aria-label="Remind me">${BELL_ICON}</button>
        <input type="hidden" class="note-form-due" />
        <input type="hidden" class="note-form-repeat" />
        <button type="button" class="modal-close" title="Close" aria-label="Close note"></button>
      </div>
      <div class="note-form-reminder-tags note-window-reminder"></div>
      <div class="note-window-banner hidden" role="alert"></div>
      <div class="doc-md-toolbar note-window-toolbar" data-mode="rich" role="toolbar" aria-label="Formatting">${toolbarHtml()}</div>
      <div class="modal-body note-window-scroll">
        <div class="note-window-body md-body" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Note" spellcheck="true" data-placeholder="Take a note…"></div>
        <textarea class="note-window-source hidden" spellcheck="false" aria-label="Markdown source"></textarea>
        <div class="note-window-meta"></div>
      </div>
      <div class="note-window-sketch hidden" role="dialog" aria-label="Sketch"></div>
      <div class="note-window-history hidden" role="region" aria-label="Note history"></div>
      <div class="note-window-footer">
        <div class="note-window-colors">${COLORS.map(c => `<span class="note-color-dot" data-color="${c.value}" title="${c.name || 'default'}" role="button" tabindex="0" aria-label="Color ${c.name || 'default'}"></span>`).join('')}</div>
        <div class="note-window-tags"></div>
        <span class="note-window-spacer"></span>
        <button type="button" class="note-window-icon-btn note-window-act note-window-agent-chat hidden" data-act="agent-chat" title="Open agent chat" aria-label="Open agent chat">${AGENT_CHAT_ICON}<span>Agent chat</span></button>
        <button type="button" class="note-window-icon-btn note-window-act hidden" data-act="ask" title="Ask about this note" aria-label="Ask about this note">${AGENT_ICON}</button>
        <button type="button" class="note-window-icon-btn note-window-act hidden" data-act="history" title="History" aria-label="History">${HISTORY_ICON}</button>
        <button type="button" class="note-window-icon-btn note-window-act" data-act="archive" title="Archive" aria-label="Archive">${ARCHIVE_ICON}</button>
        <button type="button" class="note-window-icon-btn note-window-act note-window-act-danger" data-act="delete" title="Delete" aria-label="Delete">${TRASH_ICON}</button>
        <button type="button" class="note-window-icon-btn note-window-act note-window-act-danger note-window-leave hidden" data-act="leave" title="Leave note" aria-label="Leave note">${LEAVE_ICON}</button>
      </div>`;
    modal.appendChild(content);
    const q = selector => content.querySelector(selector);
    return {
      modal, content,
      title: q('.note-window-title'), remind: q('.note-window-remind'), share: q('.note-window-share'), shareCount: q('.note-window-share-count'), due: q('.note-form-due'), repeat: q('.note-form-repeat'),
      close: q('.modal-close'), banner: q('.note-window-banner'), toolbar: q('.note-window-toolbar'),
      body: q('.note-window-body'), source: q('.note-window-source'), sketch: q('.note-window-sketch'), meta: q('.note-window-meta'),
      colors: q('.note-window-colors'), tags: q('.note-window-tags'), archive: q('[data-act="archive"]'), remove: q('[data-act="delete"]'), leave: q('[data-act="leave"]'),
      ask: q('[data-act="ask"]'), agentChat: q('[data-act="agent-chat"]'),
      history: q('[data-act="history"]'), historyPanel: q('.note-window-history'),
    };
  }

  function showColor(ctx, color) {
    ctx.color = color || '';
    // The colour ring is the owner's; a collaborator's window has none.
    const own = isOwnNote(stateNote(ctx.session?.id) || ctx.note);
    ctx.els.content.style.setProperty('--note-ring', own ? ring(ctx.color) : 'transparent');
    ctx.els.colors.querySelectorAll('.note-color-dot').forEach(dot => {
      dot.classList.toggle('active', _dotIsActive(dot.dataset.color, ctx.color));
      dot.style.background = _dotBg(dot.dataset.color, ctx.color);
    });
  }

  function showMeta(ctx) {
    const note = stateNote(ctx.session.id);
    const parts = [];
    if (note?.created_at) parts.push(`Created ${stamp(note.created_at)}`);
    if (note?.updated_at) parts.push(`Edited ${stamp(note.updated_at)}`);
    // The last agent edit, while nothing else has changed the note since.
    // Ownership comes from the note itself: on open this runs before
    // applyAccess sets ctx.own.
    const agentEdit = isOwnNote(note) && note?.agent_edit?.undoable ? note.agent_edit : null;
    if (agentEdit) parts.push(`Agent edited ${stamp(agentEdit.at)}`);
    ctx.els.meta.textContent = parts.join(' · ');
    if (agentEdit) {
      const undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'note-window-meta-undo';
      undo.textContent = 'Undo';
      undo.title = 'Undo the agent edit';
      undo.addEventListener('click', () => effects.undoAgentEdit?.(note.id));
      ctx.els.meta.append(' · ', undo);
    }
    // The history, named where the eye already is (the footer icon stays).
    const count = ctx.session.id && isOwnNote(note) ? Number(note?.version_count) || 0 : 0;
    ctx.versionCount = note?.version_count;
    if (count) {
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'note-window-meta-undo note-window-meta-history';
      link.textContent = `History (${count})`;
      link.title = 'See what changed this note';
      link.addEventListener('click', () => (ctx.historyOpen ? closeHistory(ctx) : openHistory(ctx)));
      ctx.els.meta.append(parts.length ? ' · ' : '', link);
    }
    const saved = !!ctx.session.id;
    ctx.els.archive.classList.toggle('hidden', !saved);
    const archived = !!note?.archived;
    ctx.els.archive.title = archived ? 'Unarchive' : 'Archive';
    ctx.els.archive.setAttribute('aria-label', ctx.els.archive.title);
    ctx.els.archive.innerHTML = archived ? UNARCHIVE_ICON : ARCHIVE_ICON;
    ctx.els.remove.title = saved ? 'Delete' : 'Discard';
    ctx.els.remove.setAttribute('aria-label', ctx.els.remove.title);
  }

  /** What this account may do here. View only: nothing is editable and nothing saves. Edit: content, title and tasks; colour, tags, reminder, archive and delete stay with the owner, who alone sees Share. A collaborator gets Leave instead of Delete. */
  function applyAccess(ctx) {
    const note = stateNote(ctx.session?.id) || ctx.note;
    const own = isOwnNote(note);
    const editable = canEditNote(note);
    const { els } = ctx;
    const wasEditable = ctx.editable;
    ctx.own = own;
    ctx.editable = editable;
    els.content.classList.toggle('note-window-readonly', !editable);
    els.content.classList.toggle('note-window-collab', !own);
    // Re-setting contenteditable, even to the same value, makes the browser
    // re-evaluate the whole body: on a long note that cost 200 ms per call.
    const editableAttr = editable ? 'true' : 'false';
    if (els.body.getAttribute('contenteditable') !== editableAttr) els.body.setAttribute('contenteditable', editableAttr);
    els.title.readOnly = !editable;
    els.source.readOnly = !editable;
    if (!editable && ctx.editor?.mode === 'source') ctx.editor.setMode('rich');
    els.remind.disabled = !own;
    els.leave.classList.toggle('hidden', own || !ctx.session?.id);
    const count = Array.isArray(note?.shared_with) ? note.shared_with.length : 0;
    els.share.classList.toggle('hidden', !(own && state.me && ctx.session?.id));
    els.share.classList.toggle('has-date', count > 0);
    els.shareCount.textContent = count ? String(count) : '';
    els.share.title = count ? `Shared with ${count}` : 'Share';
    els.share.setAttribute('aria-label', els.share.title);
    if (!editable) banner(ctx, `View only, shared by ${note?.shared_by || 'its owner'}`);
    else if (wasEditable === false) banner(ctx, null);
    showAgent(ctx, note);
  }

  /** Owner only, on a saved note: Ask, "Agent chat" once a run is linked, and the per-task buttons (outside select mode). */
  const agentNote = ctx => {
    const note = stateNote(ctx.session?.id);
    return note && canRunNoteAgent(note) ? note : null;
  };
  function showAgent(ctx, note = stateNote(ctx.session?.id)) {
    const runnable = !!ctx.session?.id && canRunNoteAgent(note);
    ctx.els.ask.classList.toggle('hidden', !runnable);
    ctx.els.agentChat.classList.toggle('hidden', !(runnable && note.agent_session_id));
    // The history is the owner's, of a saved note.
    const history = !!ctx.session?.id && !!note && isOwnNote(note);
    ctx.els.history.classList.toggle('hidden', !history);
    if (!history && ctx.historyOpen) closeHistory(ctx);
    ctx.editor?.decorate();
  }

  /** History takes the body's place, like the sketch sheet. Pending typing is saved first, so the list starts from the note as shown. `focus`: { sessionId } starts on that chat's latest agent edit. */
  async function openHistory(ctx, focus = null) {
    const id = ctx.session?.id;
    if (!id || ctx.historyOpen || !isOwnNote(stateNote(id))) return;
    ctx.historyOpen = true;
    try { await ctx.session.flush(); } catch {}
    if (current !== ctx || !ctx.historyOpen) return;
    ctx.els.historyPanel.classList.remove('hidden');
    ctx.els.content.classList.add('note-window-browsing');
    mountHistoryPanel(ctx.els.historyPanel, {
      noteId: id,
      me: state.me,
      history: effects.history,
      currentNote: () => stateNote(id),
      esc,
      stamp,
      openChat: sid => agentActions.openChat(sid),
      onClose: () => closeHistory(ctx),
      focus,
    }).ready.then(() => ctx.els.historyPanel.querySelector('button')?.focus({ preventScroll: true }));
  }

  function closeHistory(ctx) {
    if (!ctx.historyOpen) return;
    ctx.historyOpen = false;
    const panel = ctx.els.historyPanel;
    const hadFocus = panel.contains(document.activeElement);
    panel.classList.add('hidden');
    panel.replaceChildren();
    ctx.els.content.classList.remove('note-window-browsing');
    if (hadFocus && !ctx.els.history.classList.contains('hidden')) ctx.els.history.focus({ preventScroll: true });
  }

  function relabel(ctx) {
    const label = chipLabel(ctx.els.title.value);
    if (label === ctx.label || Modals.isMinimized(NOTE_WINDOW_ID)) return;
    ctx.label = label;
    register(ctx);
  }

  function register(ctx) {
    Modals.register(NOTE_WINDOW_ID, { restoreFn: () => { sync(); }, closeFn: () => { if (current === ctx) close(); }, label: ctx.label, icon: NOTE_ICON });
  }

  function banner(ctx, message, actions = []) {
    const el = ctx.els.banner;
    if (!message) { el.classList.add('hidden'); el.replaceChildren(); return; }
    el.innerHTML = `<span>${esc(message)}</span>${actions.map((a, i) => `<button type="button" class="memory-toolbar-btn" data-i="${i}">${esc(a.label)}</button>`).join('')}`;
    el.classList.remove('hidden');
    el.querySelectorAll('[data-i]').forEach(button => button.addEventListener('click', () => actions[Number(button.dataset.i)].run()));
  }

  // A save changes one note: redraw its card, not the grid.
  const renderNote = id => (effects.renderNote ? effects.renderNote(id) : effects.render());

  function mergeIntoState(ctx, note) {
    const existing = stateNote(note?.id);
    if (!existing) return;
    app.mergeNote(existing, serverFields(note));
    renderNote(existing.id);
  }

  function setMeta(ctx, patch) {
    const existing = stateNote(ctx.session.id);
    const before = existing ? Object.fromEntries(Object.keys(patch).map(key => [key, existing[key]])) : null;
    ctx.metaWrites = (ctx.metaWrites || 0) + 1;
    if (existing) { app.mergeNote(existing, patch); renderNote(existing.id); }
    return ctx.session.patchMeta(patch).then(result => {
      if (result && existing) { app.mergeNote(existing, { updated_at: ctx.session.base, body_revision: ctx.session.revision }); showMeta(ctx); }
      return result;
    }).catch(() => {
      if (existing && before) { app.mergeNote(existing, before); renderNote(existing.id); }
      effects.showError('Failed to save note');
    }).finally(() => { ctx.metaWrites -= 1; });
  }

  /** Tags and the reminder follow the list's copy of the note, so the next tag or reminder edit starts from it rather than from what the window first showed. Skipped while one of this window's own writes is in flight or a reminder menu is open. */
  function syncMeta(ctx, note) {
    if (ctx.metaWrites || document.querySelector('.note-reminder-picker')) return;
    ctx.tags.set(note.label || '');
    const due = note.due_date || '';
    const repeat = note.repeat || 'none';
    if (ctx.els.due.value === due && ctx.els.repeat.value === repeat) return;
    ctx.els.due.value = due;
    ctx.els.repeat.value = repeat;
    ctx.els.remind.classList.toggle('has-date', !!due);
    ctx.reminder?.refresh();
  }

  async function toggle(ctx, li) {
    if (!li || current !== ctx || !ctx.editable) return;
    if (!ctx.session.id) { ctx.editor.flipTask(li); ctx.session.markDirty(); return; }
    const position = ctx.editor.taskItems().indexOf(li);
    let target = null;
    let done = false;
    try {
      const result = await ctx.session.toggleTask(() => {
        if (ctx.session.dirty) return null;
        // The text was just saved; render the saved copy so every box carries
        // the index the server will flip.
        if (ctx.editor.edited) ctx.editor.setContent(ctx.session.saved.content);
        target = ctx.editor.taskItems()[position] || null;
        if (!target || target.dataset.taskIndex == null) return null;
        done = ctx.editor.flipTask(target);
        return Number(target.dataset.taskIndex);
      });
      if (!result) {
        if (ctx.session.conflict) {
          if (target) ctx.editor.flipTask(target);
          return;
        }
        if (!target) { ctx.editor.flipTask(li); ctx.session.markDirty(); }
        return;
      }
      mergeIntoState(ctx, result.note);
      showMeta(ctx);
      // A held edit (see `unsaved`) is only in the window and the draft, and
      // text typed while the tick was out is not saved yet: a redraw would drop
      // either. The window already shows the tick; the next save sends both.
      if (!ctx.holding && !ctx.session.dirty && ctx.editor.getMarkdown() !== ctx.session.saved.content) ctx.editor.setContent(ctx.session.saved.content);
      const items = result.items || result.note?.items || [];
      if (done && items.length && items.every(item => item.done)) effects.celebrate(target.getBoundingClientRect());
    } catch {
      // A 409 does not land here: the session holds it as a conflict (the
      // tick is taken back above and the window offers Reload or Keep mine).
      if (target) ctx.editor.flipTask(target);
      if (!ctx.session.gone) effects.showError('Failed to update checklist item');
    }
  }

  function openSketch(insert) {
    const ctx = current;
    if (!ctx) return;
    const panel = ctx.els.sketch;
    panel.innerHTML = `${drawing._buildDrawHtml()}<div class="note-window-sketch-actions"><button type="button" class="memory-toolbar-btn" data-act="cancel">Cancel</button><button type="button" class="memory-toolbar-btn active" data-act="insert">Insert sketch</button></div>`;
    // The sketch takes the body's place, like a sheet, until it is inserted or cancelled.
    panel.classList.remove('hidden');
    ctx.els.content.classList.add('note-window-sketching');
    drawing._wireCanvas(panel, null);
    const done = () => { panel.classList.add('hidden'); panel.replaceChildren(); ctx.els.content.classList.remove('note-window-sketching'); };
    panel.querySelector('[data-act="cancel"]').addEventListener('click', done);
    panel.querySelector('[data-act="insert"]').addEventListener('click', async event => {
      const button = event.currentTarget;
      if (button.disabled) return;
      button.disabled = true;
      const upload = await drawing._uploadCanvasAsPng(panel.querySelector('.note-form-canvas'));
      button.disabled = false;
      if (!upload?.url) { effects.showError('Failed to save sketch'); return; }
      done();
      insert(effects.uploadPath(upload.url));
    });
  }

  // A card's title is its keyboard handle (see cardInteractions).
  const cardHandle = id => (id ? document.querySelector(`#notes-pane .note-card[data-note-id="${CSS.escape(id)}"] .note-card-title`) : null);

  /** Focus goes back where the window was opened from, or to that note's card, unless the user already moved it somewhere else. A phone never gets a text field focused for it (that opens the keyboard). */
  function restoreFocus(ctx, fallbackId = null) {
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected) return;
    const target = [ctx.opener, cardHandle(ctx.openerCard), cardHandle(ctx.session?.id), cardHandle(fallbackId)]
      .find(el => el?.isConnected && !el.closest(`#${NOTE_WINDOW_ID}`));
    if (!target || (isMobile() && target.matches('input, textarea, [contenteditable="true"]'))) return;
    target.focus({ preventScroll: true });
  }

  // When the keyboard comes up on a phone the sheet shrinks to what is still
  // visible (15-notes.css, keyboard-open), which can leave the caret under the
  // keyboard. Scroll the note's own box to bring it back, never the page.
  function watchCaret(els) {
    const scroll = els.body.parentElement;
    if (typeof ResizeObserver !== 'function') return null;
    const observer = new ResizeObserver(() => {
      if (!document.documentElement.classList.contains('keyboard-open') || document.activeElement !== els.body) return;
      const selection = getSelection();
      if (!selection.rangeCount || !els.body.contains(selection.focusNode)) return;
      const range = selection.getRangeAt(0).cloneRange();
      range.collapse(false);
      const node = selection.focusNode.nodeType === Node.ELEMENT_NODE ? selection.focusNode : selection.focusNode.parentElement;
      // A collapsed range at the end of a text node can measure as all zeros: use its line then.
      const rect = range.getClientRects()[0];
      const caret = rect && (rect.top || rect.bottom) ? rect : node.getBoundingClientRect();
      const box = scroll.getBoundingClientRect();
      const margin = 8;
      if (caret.bottom > box.bottom - margin) scroll.scrollTop += caret.bottom - box.bottom + margin;
      else if (caret.top < box.top + margin) scroll.scrollTop -= box.top + margin - caret.top;
    });
    observer.observe(scroll);
    return observer;
  }

  /** Returns null, with an error shown, when the window cannot be built: nothing half built stays on screen. */
  function mount(note, options = {}) {
    const els = build();
    document.body.appendChild(els.modal);
    const ctx = { els, note, label: chipLabel(note?.title || options.title || ''), color: note?.color || '' };
    try {
      return wire(ctx, options);
    } catch (error) {
      console.error('notes: the note window could not be opened', error);
      try { ctx.editor?.destroy(); } catch {}
      try { ctx.tags?.destroy(); } catch {}
      els.modal.remove();
      if (current === ctx) current = null;
      try { Modals.unregister(NOTE_WINDOW_ID); } catch {}
      effects.showError('This note could not be opened.');
      return null;
    }
  }

  function wire(ctx, { content = '', title = '', focus = null, typed = false, opener = null } = {}) {
    const { els, note } = ctx;
    ctx.opener = opener && opener !== document.body ? opener : null;
    ctx.openerCard = ctx.opener?.closest?.('.note-card')?.dataset.noteId || null;
    els.title.value = note?.title || title;
    els.due.value = note?.due_date || '';
    els.repeat.value = note?.repeat || 'none';
    els.remind.classList.toggle('has-date', !!note?.due_date);
    ctx.editor = richBody.attach({
      body: els.body, toolbar: els.toolbar, source: els.source,
      onEdit: () => ctx.session.markDirty(),
      onToggle: li => toggle(ctx, li),
      onEscape: () => close(),
      onFlush: () => ctx.session.flush(),
      decorate: body => decorateTaskRows(body, !!ctx.session && showsTaskAgent(agentNote(ctx), { selectMode: state.selectMode })),
      onRenderError: () => banner(ctx, 'This note cannot be shown formatted, so it is open in Markdown source.'),
      mediaNote: () => {
        const id = ctx.session?.id || note?.id;
        return id && isSharedNote(stateNote(id) || note) ? id : null;
      },
    });
    ctx.editor.setContent(note ? note.content || '' : content);
    ctx.caretWatch = watchCaret(els);
    ctx.session = createSession({
      note,
      collect: () => ({ title: els.title.value, ...ctx.editor.read() }),
      readOnly: () => ctx.editable === false,
      effects: {
        // Tasks typed since the last render get their agent button now.
        saved: saved => { mergeIntoState(ctx, saved); showMeta(ctx); relabel(ctx); synced(ctx); ctx.editor.decorate(); },
        created: created => {
          if (!stateNote(created.id)) app.prependNote(created);
          els.modal.dataset.noteId = created.id;
          if (current === ctx) navigation.created(created.id);
          showMeta(ctx);
          showAgent(ctx);
          relabel(ctx);
          synced(ctx);
          effects.render();
        },
        conflict: () => banner(ctx, 'This note changed elsewhere.', [
          { label: 'Reload', run: () => ctx.session.resolveConflict('reload') },
          { label: 'Keep mine', run: () => { banner(ctx, null); ctx.session.resolveConflict('keep'); } },
        ]),
        reloaded: server => {
          banner(ctx, null);
          if (!server) return;
          els.title.value = server.title || '';
          ctx.editor.setContent(server.content || '');
          mergeIntoState(ctx, server);
          showMeta(ctx);
        },
        // An edit the editor could not write: the rest of the note saves, this
        // part is kept on this device and can be fixed in the source.
        unsaved: held => {
          if (!held) { if (ctx.holding) banner(ctx, null); ctx.holding = false; return; }
          ctx.holding = true;
          banner(ctx, 'Part of this edit is not saved: the editor cannot write it. It is kept on this device.', [
            { label: 'Markdown source', run: () => { banner(ctx, null); ctx.editor.setMode('source'); } },
          ]);
        },
        // Too long to save: retrying can't help, so the window says why and
        // keeps saying it until an edit brings the note back under the limit.
        failed: error => {
          const tooLong = sizeRefusal(error);
          if (tooLong) { ctx.tooLong = true; banner(ctx, `${tooLong} Shorten it to save. Your edits are kept on this device.`); return; }
          if (!ctx.failed) effects.showError('Note not synced. Your edits are kept on this device.');
          ctx.failed = true;
        },
        // The owner made the note view only for me while it was open: the
        // window turns read-only and says so, instead of a save that keeps failing.
        viewOnly: () => {
          const viewOnly = { can_edit: false, my_permission: 'view' };
          const existing = stateNote(ctx.session.id);
          if (existing) app.mergeNote(existing, viewOnly);
          if (ctx.note) ctx.note = { ...ctx.note, ...viewOnly };
          if (current === ctx) applyAccess(ctx);
          renderNote(ctx.session.id);
        },
        // Deleted, its owner's account removed, or no longer shared with me.
        // The card goes now; the window stays so the text can still be copied.
        gone: () => {
          const index = state.notes.findIndex(n => n.id === ctx.session.id);
          if (index >= 0) app.removeNoteAt(index);
          // Render either way: the reminder start-up read can replace the
          // list without redrawing it, leaving the card on screen.
          effects.render();
          banner(ctx, 'This note no longer exists or is no longer shared with you. Changes here are not saved.', [
            { label: 'Close', run: () => close() },
          ]);
        },
        backupUnavailable: () => { if (!ctx.warned) effects.showError('Draft backup unavailable. Keep this note open until it is saved.'); ctx.warned = true; },
      },
    });
    if (note) els.modal.dataset.noteId = note.id;
    showColor(ctx, ctx.color);
    showMeta(ctx);
    applyAccess(ctx);

    const draft = ctx.editable ? ctx.session.storedDraft() : null;
    // Restore saves the draft against the version it was typed on
    // (restoreDraft), so a newer copy meets the conflict choice.
    const restore = () => { banner(ctx, null); ctx.session.restoreDraft(); els.title.value = draft.title; ctx.editor.setContent(draft.content); ctx.session.markDirty(); };
    if (draft && !typed && draft.content !== null && !draft.stale && !draft.recovered.length) {
      if (draft.foreign) {
        banner(ctx, 'This note has unsaved changes from another session on this device.', [
          { label: 'Restore', run: restore },
          { label: 'Dismiss', run: () => { ctx.session.dismissDraft?.(); banner(ctx, null); } },
          { label: 'Discard draft', run: () => { banner(ctx, null); ctx.session.discardDraft({ includeForeign: true }); } },
        ]);
      } else {
        banner(ctx, note ? 'This note has unsaved changes from earlier on this device.' : 'You have an unsaved note from earlier on this device.', [
          { label: 'Restore', run: restore },
          { label: 'Discard', run: () => { banner(ctx, null); ctx.session.discardDraft(); } },
        ]);
      }
    } else if (draft && !typed) {
      // The note changed after the draft was typed (or another tab left one):
      // restoring would write over that change, so the draft is offered to
      // copy from instead, and kept until it is discarded.
      const texts = [...(draft.content !== null ? [draft] : []), ...draft.recovered]
        .map(entry => [entry.title, entry.content].filter(Boolean).join('\n\n'));
      if (draft.foreign || draft.recovered.length) {
        banner(ctx, 'This note changed after an unsaved draft on this device was written. Copy what you want to keep into the note.', [
          { label: 'Copy draft', run: () => { navigator.clipboard?.writeText(texts.join('\n\n')).then(() => effects.showToast?.('Draft copied'), () => effects.showError('Copy failed')); } },
          { label: 'Dismiss', run: () => { ctx.session.dismissDraft?.(); banner(ctx, null); } },
          { label: 'Discard drafts', run: () => { banner(ctx, null); ctx.session.discardDraft({ includeForeign: true }); } },
        ]);
      } else {
        banner(ctx, 'This note changed after your unsaved draft on this device was written. Copy what you want to keep into the note.', [
          { label: 'Copy draft', run: () => { navigator.clipboard?.writeText(texts.join('\n\n')).then(() => effects.showToast?.('Draft copied'), () => effects.showError('Copy failed')); } },
          { label: 'Discard', run: () => { banner(ctx, null); ctx.session.discardDraft(); } },
        ]);
      }
    } else if (!note && typed) {
      ctx.session.markDirty();
    }

    register(ctx);
    Modals.injectMinimizeButton(els.modal, NOTE_WINDOW_ID);
    makeWindowDraggable(els.modal, { content: els.content, header: els.content.querySelector('.modal-header') });

    els.close.addEventListener('click', () => close());
    els.title.addEventListener('mousedown', event => event.stopPropagation());
    els.title.addEventListener('input', () => ctx.session.markDirty());
    els.title.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.key === 'Enter') { event.preventDefault(); ctx.editor.focusEnd(); }
      else if (event.key === 'Escape') { event.preventDefault(); close(); }
    });
    // Blur flushes; it never writes on its own (the session saves only after a real edit).
    els.body.addEventListener('blur', () => { ctx.session.flush().then(() => sync()); });
    els.source.addEventListener('blur', () => { ctx.session.flush().then(() => sync()); });
    els.title.addEventListener('blur', () => { ctx.session.flush().then(() => sync()); });
    els.modal.addEventListener('focusout', () => {
      setTimeout(() => {
        if (current === ctx && !els.modal?.contains?.(document.activeElement)) {
          sync();
        }
      }, 0);
    });

    ctx.reminder = wireReminderPicker(els.content, {
      onChange: () => {
        const previous = stateNote(ctx.session.id)?.due_date || '';
        const due = els.due.value || '';
        els.remind.classList.toggle('has-date', !!due);
        setMeta(ctx, { due_date: due, repeat: els.repeat.value || 'none' });
        if (ctx.session.id && previous !== due) effects.reminderChanged(ctx.session.id);
      },
    });
    const pickColor = async dot => {
      let color = dot.dataset.color;
      if (color === 'custom') {
        const url = await _pickCustomBgImage();
        if (!url) return;
        color = `bg:${url}`;
      }
      showColor(ctx, color);
      setMeta(ctx, { color });
    };
    els.colors.addEventListener('click', event => { const dot = event.target.closest('.note-color-dot'); if (dot) pickColor(dot); });
    els.colors.addEventListener('keydown', event => {
      const dot = event.target.closest('.note-color-dot');
      if (dot && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); pickColor(dot); }
    });
    ctx.tags = noteTags.mount(els.tags, {
      label: note?.label || '',
      knownTags: () => state.notes.filter(n => n.id !== ctx.session.id).flatMap(n => parseTags(n.label)),
      onChange: label => setMeta(ctx, { label }),
      onEscape: () => close(),
    });
    els.share.addEventListener('click', () => {
      const id = ctx.session.id;
      if (!id || !ctx.own) return;
      sharePopover.open(els.share, id, {
        onChange: roster => {
          const existing = stateNote(id);
          if (existing) { app.mergeNote(existing, { shared_with: roster, is_shared: roster.length > 0 }); effects.render(); }
          if (current === ctx) applyAccess(ctx);
        },
        onError: error => effects.showError(error?.body?.detail || 'Failed to update sharing'),
      });
    });
    // A task's agent button reads the row's text as shown, at click time, and
    // asks about that item first: a run costs money, so one stray tap never
    // starts one. The run reads the saved note, so pending typing is saved first.
    const askRowAgent = button => {
      const id = ctx.session.id;
      const text = taskRowText(button.closest('li.md-task'));
      if (!id || !text || !agentNote(ctx)) return;
      agentUi.openAsk(button, id, {
        item: text,
        draft: () => ({ title: els.title.value }),
        before: () => Promise.resolve(ctx.session.flush()).catch(() => {}),
      });
    };
    bindTap(els.body, `.${TASK_AGENT_CLASS}`, askRowAgent);
    els.body.addEventListener('click', event => {
      const button = event.target.closest(`.${TASK_AGENT_CLASS}`);
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      askRowAgent(button);
    });
    els.ask.addEventListener('click', () => {
      const id = ctx.session.id;
      if (!id || !agentNote(ctx)) return;
      agentUi.openAsk(els.ask, id, {
        draft: () => ({ title: els.title.value, content: ctx.editor.getMarkdown() }),
        before: () => Promise.resolve(ctx.session.flush()).catch(() => {}),
      });
    });
    els.agentChat.addEventListener('click', () => { if (ctx.session.id) agentActions.openAgentChat(ctx.session.id); });
    els.history.addEventListener('click', () => (ctx.historyOpen ? closeHistory(ctx) : openHistory(ctx)));
    els.historyPanel.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeHistory(ctx); } });
    els.archive.addEventListener('click', () => archive(ctx));
    els.remove.addEventListener('click', () => remove(ctx));
    els.leave.addEventListener('click', () => leave(ctx));
    // Key presses inside the window are the window's, not the panel's shortcuts.
    els.content.addEventListener('keydown', event => event.stopPropagation());

    current = ctx;
    if (focus === 'body') ctx.editor.focusEnd();
    else if (focus === 'title') { els.title.focus(); els.title.setSelectionRange(els.title.value.length, els.title.value.length); }
    return ctx;
  }

  function teardown(ctx) {
    ctx.caretWatch?.disconnect();
    sharePopover.close();
    agentUi.closeAsk();
    ctx.editor.destroy();
    ctx.tags.destroy();
    document.querySelectorAll('.note-reminder-menu').forEach(dismissOrRemove);
    ctx.els.modal.remove();
    if (current === ctx) current = null;
    try { Modals.unregister(NOTE_WINDOW_ID); } catch {}
    if (!keepEntry) navigation.closed();
  }

  // A save that failed (offline) says "Note not synced"; the one that lands after it says so too.
  function synced(ctx) {
    if (ctx.failed) effects.showToast('Note synced');
    ctx.failed = false;
    if (ctx.tooLong) { ctx.tooLong = false; banner(ctx, null); }
  }

  // Windows closed while their last save had failed. Their session is kept
  // until the connection comes back and the save goes through, meets a
  // conflict (the draft stays on this device and Restore offers it), or the
  // note is opened again (its new window offers the draft).
  const unsent = new Set();
  const settled = ctx => !ctx.session.dirty || !!ctx.session.conflict || ctx.session.gone;

  /** The connection is back: send what failed to save, in the open window and in the closed ones. */
  function retry() {
    for (const ctx of unsent) {
      Promise.resolve(ctx.session.flush()).catch(() => {}).then(() => { if (settled(ctx)) unsent.delete(ctx); });
    }
    return current?.session.flush();
  }

  /** Close the window. Pending edits are flushed; nothing is written if nothing changed. */
  function close() {
    const ctx = current;
    if (!ctx) return Promise.resolve();
    teardown(ctx);
    return ctx.session.close().then(() => {
      if (!settled(ctx)) unsent.add(ctx);
      effects.render();
      restoreFocus(ctx);
    });
  }

  /** Close without leaving history: Back or another note's window. */
  function closeKeepingEntry() {
    keepEntry = true;
    try { return close(); } finally { keepEntry = false; }
  }

  async function archive(ctx) {
    const id = ctx.session.id;
    if (!id) return;
    const archived = !!stateNote(id)?.archived;
    await close();
    if (archived) effects.unarchive(id);
    else effects.archive(id);
  }

  /** The card after `id` in the list (or before it, at the end): where focus goes once that note's card is gone. */
  function neighbourId(id) {
    const cards = Array.from(document.querySelectorAll('#notes-pane .note-card[data-note-id]'));
    const at = cards.findIndex(card => card.dataset.noteId === id);
    return at < 0 ? null : (cards[at + 1] || cards[at - 1])?.dataset.noteId || null;
  }

  async function remove(ctx) {
    const id = ctx.session.id;
    if (id && !(await effects.confirmDelete())) return;
    const next = neighbourId(id);
    ctx.session.abandon();
    teardown(ctx);
    if (id) effects.deleteNote(id);
    restoreFocus(ctx, next);
  }

  /** A collaborator leaves: nothing pending is saved, the window closes and the note leaves the list. */
  async function leave(ctx) {
    const id = ctx.session.id;
    if (!id || ctx.own || !(await effects.confirmLeave())) return;
    const next = neighbourId(id);
    ctx.session.abandon();
    teardown(ctx);
    effects.leaveNote(id);
    restoreFocus(ctx, next);
  }

  /** Open a saved note. Returns false when it is not loaded. */
  function open(id, { focus } = {}) {
    const note = stateNote(id) || (id ? state.notes.find(n => n.id.startsWith(String(id).slice(0, 8))) : null);
    if (!note) return false;
    if (current && current.session.id === note.id) {
      // Already open, maybe under the panel (a page loaded on #note-<id>
      // opens it before the panel): opening it again brings it back on top.
      if (Modals.isMinimized(NOTE_WINDOW_ID)) Modals.restore(NOTE_WINDOW_ID);
      else raise(current.els.modal);
      sync();
      return true;
    }
    const opener = document.activeElement;
    closeKeepingEntry();
    for (const ctx of unsent) if (ctx.session.id === note.id) unsent.delete(ctx);
    if (mount(note, { focus: focus === undefined ? (isMobile() ? null : 'body') : focus, opener })) navigation.opened(note.id);
    else navigation.closed();
    return true;
  }

  /** A note that does not exist yet: it is created by its first non-empty save. */
  /** `pickImage`: start by choosing an image, as the toolbar's Insert image does. Call it from the click that asked for it, so the file picker may open. */
  function openNew({ text = '', pickImage = false } = {}) {
    const opener = document.activeElement;
    closeKeepingEntry();
    const value = String(text || '');
    const typed = !!value.trim();
    const ctx = mount(null, { title: value, focus: typed ? 'title' : 'body', typed, opener });
    if (ctx) navigation.opened(null);
    else navigation.closed();
    if (pickImage) ctx?.editor.pickImage();
    return ctx;
  }

  /** Something typed here that the server does not have yet: pending, held back, in conflict, or a title or body that differs from the saved one. Focus alone is not unsaved. */
  function hasUnsaved(ctx) {
    const saved = ctx.session.saved;
    return ctx.session.dirty || !!ctx.session.conflict || ctx.holding || ctx.els.title.value !== saved.title
      || (ctx.editor.edited && ctx.editor.getMarkdown() !== saved.content);
  }

  /** An agent edit to this note, or its undo, just landed. Shown at once unless the user has typed something not yet saved, which is never overwritten: their save then meets the newer version and gets the usual conflict choice. */
  function followServer(note) {
    const ctx = current;
    if (!ctx || !note || ctx.session.id !== note.id) return;
    if (hasUnsaved(ctx)) { showMeta(ctx); return; }
    ctx.session.replace(note);
    ctx.els.title.value = note.title || '';
    ctx.editor.setContent(note.content || '');
    showMeta(ctx);
  }

  /**
   * A card wrote to the note while the window is in use. With nothing unsaved
   * here the window takes that version, a tick in place so the caret stays;
   * with something unsaved it keeps its base, and the next save gets the
   * conflict choice rather than writing the old text over the card's change.
   */
  function followLocal(ctx, note) {
    const before = ctx.session.saved.content;
    const outcome = ctx.session.follow(note, { title: ctx.els.title.value, content: ctx.editor.getMarkdown() });
    if (outcome !== 'take') return;
    const { title, content } = ctx.session.saved;
    if (!flipInPlace(ctx, before, content)) ctx.editor.setContent(content);
    ctx.els.title.value = title;
    showMeta(ctx);
  }

  function flipInPlace(ctx, before, after) {
    const flips = ctx.editor.mode === 'rich' ? taskFlips(before, after) : null;
    if (!flips) return false;
    const rows = new Map(ctx.editor.taskItems().map(li => [Number(li.dataset.taskIndex), li]));
    if (!flips.every(index => rows.has(index))) return false;
    flips.forEach(index => ctx.editor.flipTask(rows.get(index)));
    return ctx.editor.getMarkdown() === after;
  }

  /** After the list re-renders: follow same-tab changes to the open note, and remote ones while nothing is unsaved. An idle window takes a collaborator's change even with the caret in it. */
  function sync() {
    const ctx = current;
    const note = ctx && stateNote(ctx.session.id);
    if (!note) return;
    applyAccess(ctx);
    if (ctx.color !== (note.color || '')) showColor(ctx, note.color);
    if (ctx.own) syncMeta(ctx, note);
    if (ctx.versionCount !== note.version_count) showMeta(ctx);
    // body_revision names the body; a note from a server without one falls back to updated_at.
    if (note.body_revision && note.body_revision === ctx.session.revision) {
      // Same body, other fields moved on (a tag, a colour): only the base follows.
      if (note.updated_at && note.updated_at !== ctx.session.base && !ctx.session.saving) ctx.session.follow(note, { title: ctx.els.title.value, content: ctx.editor.getMarkdown() });
      return;
    }
    if (!note.body_revision && (!note.updated_at || note.updated_at === ctx.session.base)) return;
    // A save in flight: its answer moves the window on, and the list's next
    // render brings the rest.
    if (ctx.session.saving) return;
    const local = isLocalVersion(note.id, note.body_revision) || isLocalVersion(note.id, note.updated_at);
    if (hasUnsaved(ctx)) {
      if (local) followLocal(ctx, note);
      return;
    }
    // Focus on the title or on a toolbar control, a menu or a dialog in the
    // window: redrawing would pull the field or the selection away from it.
    // Taken when focus leaves (blur, focusout) or the window comes back.
    // With the caret in the body, the window takes it and keeps the caret.
    const active = document.activeElement;
    const inBody = !!active && (ctx.els.body.contains(active) || ctx.els.source.contains(active));
    if (active && !inBody && (active === ctx.els.title || ctx.els.modal?.contains?.(active))) {
      ctx.pendingSync = true;
      return;
    }
    ctx.pendingSync = false;
    const focused = !!active && ctx.els.content.contains(active);
    if (focused && local) { followLocal(ctx, note); return; }
    ctx.session.replace(note);
    ctx.els.title.value = note.title || '';
    // The caret stays where it was, not at the start of the note.
    const caret = focused ? ctx.editor.saveCaret() : null;
    ctx.editor.setContent(note.content || '');
    ctx.editor.restoreCaret(caret);
    showMeta(ctx);
  }

  /** Open a saved note on its History, at `focus` ({ sessionId }: that chat's latest agent edit). Returns false when the note is not loaded or not the user's. */
  async function showHistory(id, focus = null) {
    if (!open(id, { focus: null })) return false;
    const ctx = current;
    if (!ctx || !isOwnNote(stateNote(ctx.session.id))) return false;
    if (ctx.historyOpen) closeHistory(ctx);
    await openHistory(ctx, focus);
    return true;
  }

  return {
    open,
    openNew,
    showHistory,
    close,
    /** A swipe down on the phone sheet hid the window (ui.js, `modal-dismissed`): close it as the close button does. */
    dismissed: id => (id === NOTE_WINDOW_ID && current ? close() : undefined),
    /** Back left the window's entry: close it, the history is already right. */
    closeFromHistory: closeKeepingEntry,
    sync,
    followServer,
    openSketch,
    flush: () => current?.session.flush(),
    retry,
    /** The page is being hidden or unloaded: keep the open note's draft. */
    flushDraft: () => current?.session.flushBackup(),
    /** Before a card writes to the open note: its pending save, if any, goes first. */
    settle: id => (current && current.session.id === id ? current.session.flush() : undefined),
    isOpenOn: id => !!current && current.session.id === id,
    isOpen: () => !!current,
    /** Open and on screen (not minimised to its dock chip). */
    isShowing: () => !!current && !Modals.isMinimized(NOTE_WINDOW_ID),
    /** The open note holds typing the server does not have yet. */
    hasUnsaved: () => !!current && hasUnsaved(current),
    currentId: () => current?.session.id || null,
  };
}
