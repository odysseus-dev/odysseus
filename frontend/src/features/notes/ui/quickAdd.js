/** Quick-add expansion, draft restoration and opening inline editors. */
export function createQuickAdd({
  _applyDraftToNote,
  _applyMasonry,
  _buildForm,
  app,
  openPanel,
  state,
  uiModule
}) {
  function _renderQuickAdd(body) {
    const wrap = document.createElement('div');
    wrap.className = 'notes-quick-add';
    // 2-pill Note/Todo toggle mirrors the full form's type-seg (minus Draw :
    // drawing happens in the expanded form). The pill that's active steers
    // both the placeholder and the type the form opens in.
    wrap.innerHTML = `
    <div class="notes-quick-type-seg is-todo" role="group" aria-label="New item type">
      <button type="button" class="notes-quick-type-pill" data-type="note" aria-label="Note" aria-pressed="false" title="Note">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="14" y2="18"/></svg>
      </button>
      <button type="button" class="notes-quick-type-pill active" data-type="todo" aria-label="To-do" aria-pressed="true" title="To-do">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      </button>
    </div>
    <input type="text" class="notes-quick-input" placeholder="Add a to-do…" />
    <button class="notes-quick-icon" data-action="photo" title="Attach photo">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    </button>
  `;
    body.appendChild(wrap);

    const input = wrap.querySelector('.notes-quick-input');
    const seg = wrap.querySelector('.notes-quick-type-seg');
    let currentType = 'todo';
    const setType = (t) => {
      if (t !== 'note' && t !== 'todo') return;
      currentType = t;
      seg.classList.toggle('is-todo', t === 'todo');
      seg.classList.toggle('is-note', t === 'note');
      seg.querySelectorAll('.notes-quick-type-pill').forEach(p => {
        const on = p.dataset.type === t;
        p.classList.toggle('active', on);
        p.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      input.placeholder = t === 'note' ? 'Add a note…' : 'Add a to-do…';
    };
    seg.querySelectorAll('.notes-quick-type-pill').forEach(p => {
      p.addEventListener('click', (e) => {
        e.stopPropagation();
        setType(p.dataset.type);
      });
    });
    // Click input or type → expand to full form
    const expandToForm = (initialType = 'note', initialText = '') => {
      app.setEditingId('__new__');
      const form = _buildForm({ note_type: initialType });
      form.classList.add('note-form-new');
      if (initialText) {
        const titleEl = form.querySelector('.note-form-title');
        if (titleEl) titleEl.value = initialText;
      }
      const mobileGrid = body.closest('.notes-pane')?.classList.contains('notes-view-grid')
        && window.matchMedia('(max-width: 768px)').matches;
      if (mobileGrid) {
        form.style.gridColumn = '1 / -1';
        form.style.gridRowEnd = 'span 64';
      }
      wrap.replaceWith(form);
      _applyMasonry(body);
      requestAnimationFrame(() => _applyMasonry(body));
      const titleEl = form.querySelector('.note-form-title');
      if (titleEl) {
        titleEl.focus();
        // Move caret to end
        titleEl.setSelectionRange(titleEl.value.length, titleEl.value.length);
      }
    };
    // Expand only on real intent: a click directly on the input, or actual
    // typing. Focus alone : including focus stolen from a missed nearby
    // click : no longer creates an empty form.
    input.addEventListener('click', () => expandToForm(currentType, input.value));
    input.addEventListener('input', () => expandToForm(currentType, input.value));
    wrap.querySelector('[data-action="photo"]').addEventListener('click', (e) => {
      e.stopPropagation();
      expandToForm(currentType);
      // Trigger photo input on the new form
      setTimeout(() => document.querySelector('.note-form-photo-btn')?.click(), 50);
    });
  }

  function _createNote(type = 'todo') {
    const body = document.querySelector('#notes-pane .notes-pane-body');
    if (!body || state.editingId === '__new__') return;
    app.setEditingId('__new__');
    // Restore an unsaved new-note draft if one survived a prior close/loss.
    const { note: _n, restored } = _applyDraftToNote({ note_type: type }, '__new__');
    const form = _buildForm(_n);
    form.classList.add('note-form-new');
    body.prepend(form);
    const initialField = type === 'note'
      ? form.querySelector('.note-form-content')
      : form.querySelector('.note-cl-text, .note-form-title');
    initialField?.focus();
    if (restored) uiModule.showToast('Restored unsaved note');
  }

  function newNote() {
    if (!state.open) openPanel();
    requestAnimationFrame(() => _createNote('note'));
  }

  function _editNote(id) {
    const note = state.notes.find(n => n.id === id);
    if (!note) return;
    app.setEditingId(id);
    const card = document.querySelector(`.note-card[data-note-id="${id}"]`);
    if (!card) return;
    // Restore an unsaved draft (from a prior connection loss / close) over
    // the saved note so the user picks up where they left off.
    const { note: _n, restored } = _applyDraftToNote(note, id);
    const form = _buildForm(_n);
    card.replaceWith(form);
    if (restored) uiModule.showToast('Restored unsaved changes');
    // Pinned notes live in the first masonry column : the edit form has
    // column-span:all, which can leave the form rendered above the fold or
    // visually buried under neighboring pinned cards. Bring it into view
    // (and onto a higher stacking context) so editing a pinned note always
    // pops to the top.
    form.style.position = 'relative';
    form.style.zIndex = '5';
    // Grid view: the form keeps the CSS default `grid-row-end: span 16` (64px)
    // after replaceWith, which is much shorter than the actual form. Recompute
    // masonry so the form gets the correct row span and cards below stop
    // overlapping it. ResizeObserver inside _applyMasonry keeps it in sync as
    // the user types / adds checklist items.
    const _body = form.closest('.notes-pane-body');
    if (_body) {
      _applyMasonry(_body);
      requestAnimationFrame(() => _applyMasonry(_body));
    }
    requestAnimationFrame(() => {
      try { form.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      catch { form.scrollIntoView(); }
    });
    // Pick the most useful field to focus. On phones especially, the user
    // taps Edit to type : landing in the title when there's already a title
    // (and likely a body to extend) loses momentum. Prefer the body textarea
    // for plain notes, the first checklist item for todos, fall back to title.
    const _focusBest = () => {
      if (note.note_type === 'note' || !note.note_type) {
        const ta = form.querySelector('.note-form-content');
        if (ta) { ta.focus(); try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch {} return; }
      }
      if (note.note_type === 'todo' || note.note_type === 'goal' || note.note_type === 'checklist') {
        // Last non-empty checklist row, or the first row if all empty.
        const rows = form.querySelectorAll('.note-cl-row .note-cl-text');
        let target = null;
        for (const inp of rows) { if ((inp.value || '').trim()) target = inp; }
        target = target || rows[0];
        if (target) { target.focus(); try { target.setSelectionRange(target.value.length, target.value.length); } catch {} return; }
      }
      const titleEl = form.querySelector('.note-form-title');
      if (titleEl) titleEl.focus();
    };
    _focusBest();
  }

  return { _renderQuickAdd, _createNote, newNote, _editNote };
}
