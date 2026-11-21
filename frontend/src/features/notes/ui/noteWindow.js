/** The note window: the one place a note is read and edited, on desktop and mobile. One at a time; opening another note flushes this one and rebuilds. */
import { toolbarHtml } from './richBody.js';
import { parseTags } from './noteTags.js';

const NOTE_WINDOW_ID = 'note-window-modal';
const NOTE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
const BELL_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
const ARCHIVE_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>';
const UNARCHIVE_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14l-5-5 5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>';
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
const SERVER_FIELDS = ['title', 'content', 'items', 'note_type', 'image_url', 'body_revision', 'updated_at', 'created_at', 'color', 'label', 'due_date', 'repeat', 'sort_order', 'archived', 'pinned'];
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
  createSession,
  wireReminderPicker,
  drawing,
  colors,
  esc,
  attrEsc,
  isMobile,
  isLocalVersion,
  effects,
}) {
  const { COLORS, COLOR_HEX, _dotBg, _dotIsActive, _isBgImage, _pickCustomBgImage } = colors;
  let current = null;

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
        <button type="button" class="note-form-remind-btn note-window-icon-btn note-window-remind" title="Remind me" aria-label="Remind me">${BELL_ICON}</button>
        <input type="hidden" class="note-form-due" />
        <input type="hidden" class="note-form-repeat" />
        <button type="button" class="modal-close" title="Close" aria-label="Close note"></button>
      </div>
      <div class="note-form-reminder-tags note-window-reminder"></div>
      <div class="note-window-banner hidden" role="alert"></div>
      <div class="doc-md-toolbar note-window-toolbar" data-mode="rich" role="toolbar" aria-label="Formatting">${toolbarHtml()}</div>
      <div class="note-window-hint hidden">Some parts of this note can only be edited as markdown. <button type="button" data-act="source">Open Markdown source</button></div>
      <div class="modal-body note-window-scroll">
        <div class="note-window-body md-body" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Note" spellcheck="true" data-placeholder="Take a note…"></div>
        <textarea class="note-window-source hidden" spellcheck="false" aria-label="Markdown source"></textarea>
        <div class="note-window-sketch hidden"></div>
        <div class="note-window-meta"></div>
      </div>
      <div class="note-window-footer">
        <div class="note-window-colors">${COLORS.map(c => `<span class="note-color-dot" data-color="${c.value}" title="${c.name || 'default'}" role="button" tabindex="0" aria-label="Color ${c.name || 'default'}"></span>`).join('')}</div>
        <div class="note-window-tags"></div>
        <span class="note-window-spacer"></span>
        <button type="button" class="note-window-icon-btn note-window-act" data-act="archive" title="Archive" aria-label="Archive">${ARCHIVE_ICON}</button>
        <button type="button" class="note-window-icon-btn note-window-act note-window-act-danger" data-act="delete" title="Delete" aria-label="Delete">${TRASH_ICON}</button>
      </div>`;
    modal.appendChild(content);
    const q = selector => content.querySelector(selector);
    return {
      modal, content,
      title: q('.note-window-title'), remind: q('.note-window-remind'), due: q('.note-form-due'), repeat: q('.note-form-repeat'),
      close: q('.modal-close'), banner: q('.note-window-banner'), toolbar: q('.note-window-toolbar'), hint: q('.note-window-hint'),
      body: q('.note-window-body'), source: q('.note-window-source'), sketch: q('.note-window-sketch'), meta: q('.note-window-meta'),
      colors: q('.note-window-colors'), tags: q('.note-window-tags'), archive: q('[data-act="archive"]'), remove: q('[data-act="delete"]'),
    };
  }

  function showColor(ctx, color) {
    ctx.color = color || '';
    ctx.els.content.style.setProperty('--note-ring', ring(ctx.color));
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
    ctx.els.meta.textContent = parts.join(' · ');
    const saved = !!ctx.session.id;
    ctx.els.archive.classList.toggle('hidden', !saved);
    const archived = !!note?.archived;
    ctx.els.archive.title = archived ? 'Unarchive' : 'Archive';
    ctx.els.archive.setAttribute('aria-label', ctx.els.archive.title);
    ctx.els.archive.innerHTML = archived ? UNARCHIVE_ICON : ARCHIVE_ICON;
    ctx.els.remove.title = saved ? 'Delete' : 'Discard';
    ctx.els.remove.setAttribute('aria-label', ctx.els.remove.title);
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

  function mergeIntoState(ctx, note) {
    const existing = stateNote(note?.id);
    if (!existing) return;
    app.mergeNote(existing, serverFields(note));
    effects.render();
  }

  function setMeta(ctx, patch) {
    const existing = stateNote(ctx.session.id);
    const before = existing ? Object.fromEntries(Object.keys(patch).map(key => [key, existing[key]])) : null;
    ctx.metaWrites = (ctx.metaWrites || 0) + 1;
    if (existing) { app.mergeNote(existing, patch); effects.render(); }
    return ctx.session.patchMeta(patch).then(result => {
      if (result && existing) { app.mergeNote(existing, { updated_at: result.updated_at, body_revision: ctx.session.base }); showMeta(ctx); }
      return result;
    }).catch(() => {
      if (existing && before) { app.mergeNote(existing, before); effects.render(); }
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
    if (!li || current !== ctx) return;
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
      if (!ctx.session.dirty && ctx.editor.getMarkdown() !== ctx.session.saved.content) ctx.editor.setContent(ctx.session.saved.content);
      const items = result.items || result.note?.items || [];
      if (done && items.length && items.every(item => item.done)) effects.celebrate(target.getBoundingClientRect());
    } catch {
      if (target) ctx.editor.flipTask(target);
      effects.showError('Failed to update checklist item');
    }
  }

  function openSketch(insert) {
    const ctx = current;
    if (!ctx) return;
    const panel = ctx.els.sketch;
    panel.innerHTML = `${drawing._buildDrawHtml()}<div class="note-window-sketch-actions"><button type="button" class="memory-toolbar-btn" data-act="cancel">Cancel</button><button type="button" class="memory-toolbar-btn active" data-act="insert">Insert sketch</button></div>`;
    panel.classList.remove('hidden');
    drawing._wireCanvas(panel, null);
    const done = () => { panel.classList.add('hidden'); panel.replaceChildren(); };
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
      body: els.body, toolbar: els.toolbar, source: els.source, hint: els.hint,
      onEdit: () => ctx.session.markDirty(),
      onToggle: li => toggle(ctx, li),
      onEscape: () => close(),
      onRenderError: () => banner(ctx, 'This note cannot be shown formatted, so it is open in Markdown source.'),
    });
    ctx.editor.setContent(note ? note.content || '' : content);
    ctx.caretWatch = watchCaret(els);
    ctx.session = createSession({
      note,
      collect: () => ({ title: els.title.value, content: ctx.editor.getMarkdown() }),
      effects: {
        saved: saved => { mergeIntoState(ctx, saved); showMeta(ctx); relabel(ctx); ctx.failed = false; },
        created: created => {
          if (!stateNote(created.id)) app.prependNote(created);
          els.modal.dataset.noteId = created.id;
          showMeta(ctx);
          relabel(ctx);
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
        failed: () => { if (!ctx.failed) effects.showError('Note not synced. Your edits are kept on this device.'); ctx.failed = true; },
        backupUnavailable: () => { if (!ctx.warned) effects.showError('Draft backup unavailable. Keep this note open until it is saved.'); ctx.warned = true; },
      },
    });
    if (note) els.modal.dataset.noteId = note.id;
    showColor(ctx, ctx.color);
    showMeta(ctx);

    const draft = ctx.session.storedDraft();
    if (draft && !typed && draft.content !== null && !draft.stale && !draft.recovered.length) {
      if (draft.foreign) {
        banner(ctx, 'This note has unsaved changes from another session on this device.', [
          { label: 'Restore', run: () => { banner(ctx, null); els.title.value = draft.title; ctx.editor.setContent(draft.content); ctx.session.markDirty(); } },
          { label: 'Dismiss', run: () => { ctx.session.dismissDraft?.(); banner(ctx, null); } },
          { label: 'Discard draft', run: () => { banner(ctx, null); ctx.session.discardDraft({ includeForeign: true }); } },
        ]);
      } else {
        banner(ctx, note ? 'This note has unsaved changes from earlier on this device.' : 'You have an unsaved note from earlier on this device.', [
          { label: 'Restore', run: () => { banner(ctx, null); els.title.value = draft.title; ctx.editor.setContent(draft.content); ctx.session.markDirty(); } },
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
    els.archive.addEventListener('click', () => archive(ctx));
    els.remove.addEventListener('click', () => remove(ctx));
    // Key presses inside the window are the window's, not the panel's shortcuts.
    els.content.addEventListener('keydown', event => event.stopPropagation());

    current = ctx;
    if (focus === 'body') ctx.editor.focusEnd();
    else if (focus === 'title') { els.title.focus(); els.title.setSelectionRange(els.title.value.length, els.title.value.length); }
    return ctx;
  }

  function teardown(ctx) {
    ctx.caretWatch?.disconnect();
    ctx.editor.destroy();
    ctx.tags.destroy();
    document.querySelectorAll('.note-reminder-menu').forEach(dismissOrRemove);
    ctx.els.modal.remove();
    if (current === ctx) current = null;
    try { Modals.unregister(NOTE_WINDOW_ID); } catch {}
  }

  /** Close the window. Pending edits are flushed; nothing is written if nothing changed. */
  function close() {
    const ctx = current;
    if (!ctx) return Promise.resolve();
    teardown(ctx);
    return ctx.session.close().then(() => { effects.render(); restoreFocus(ctx); });
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

  /** Open a saved note. Returns false when it is not loaded. */
  function open(id, { focus } = {}) {
    const note = stateNote(id) || (id ? state.notes.find(n => n.id.startsWith(String(id).slice(0, 8))) : null);
    if (!note) return false;
    if (current && current.session.id === note.id) {
      if (Modals.isMinimized(NOTE_WINDOW_ID)) Modals.restore(NOTE_WINDOW_ID);
      sync();
      return true;
    }
    const opener = document.activeElement;
    close();
    mount(note, { focus: focus === undefined ? (isMobile() ? null : 'body') : focus, opener });
    return true;
  }

  /** A note that does not exist yet: it is created by its first non-empty save. */
  function openNew({ text = '' } = {}) {
    const opener = document.activeElement;
    close();
    const value = String(text || '');
    const typed = !!value.trim();
    return mount(null, { title: value, focus: typed ? 'title' : 'body', typed, opener });
  }

  /** After the list re-renders: follow same-tab changes to the open note, and remote ones while nothing is pending. */
  function sync() {
    const ctx = current;
    const note = ctx && stateNote(ctx.session.id);
    if (!note) return;
    if (ctx.color !== (note.color || '')) showColor(ctx, note.color);
    syncMeta(ctx, note);
    if (!note.body_revision || note.body_revision === ctx.session.base) return;
    if (ctx.session.dirty || ctx.session.saving || ctx.session.conflict) {
      return;
    }
    const currentMarkdown = ctx.editor.getMarkdown();
    const currentTitle = ctx.els.title.value;
    if (currentMarkdown !== ctx.session.saved.content || currentTitle !== ctx.session.saved.title) {
      return;
    }
    const active = document.activeElement;
    const isInteracting = active && (active === ctx.els.title || active === ctx.els.body || active === ctx.els.source || ctx.els.modal?.contains?.(active));
    if (isInteracting) {
      ctx.pendingSync = true;
      return;
    }
    ctx.pendingSync = false;
    ctx.session.replace(note);
    ctx.els.title.value = note.title || '';
    ctx.editor.setContent(note.content || '');
    showMeta(ctx);
  }

  return {
    open,
    openNew,
    close,
    /** A swipe down on the phone sheet hid the window (ui.js, `modal-dismissed`): close it as the close button does. */
    dismissed: id => (id === NOTE_WINDOW_ID && current ? close() : undefined),
    sync,
    openSketch,
    flush: () => current?.session.flush(),
    isOpen: () => !!current,
    /** Open and on screen (not minimised to its dock chip). */
    isShowing: () => !!current && !Modals.isMinimized(NOTE_WINDOW_ID),
    currentId: () => current?.session.id || null,
  };
}
