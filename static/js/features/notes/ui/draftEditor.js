/** Collect editor drafts, restore them and wire the autosave workflow. */
export function createDraftEditor({
  bindDraft,
  _collectItems,
  _loadDraft
}) {
  function _collectFormDraft(form) {
    if (!form) return null;
    const type = form.querySelector('.note-form-type-pill.active')?.dataset.type || form.dataset.noteType || 'note';
    const d = {
      _ts: Date.now(),
      note_type: type,
      color: form.dataset.noteColor || '',
      title: form.querySelector('.note-form-title')?.value || '',
      label: form.querySelector('.note-form-label')?.value || '',
      due_date: form.querySelector('.note-form-due')?.value || null,
      repeat: form.querySelector('.note-form-repeat')?.value || 'none',
    };
    if (type === 'note') d.content = form.querySelector('.note-form-content')?.value || '';
    else if (type === 'goal') { d.content = form.querySelector('.note-form-goal-desc')?.value || ''; d.items = _collectItems(form); }
    else d.items = _collectItems(form);
    return d;
  }

  function _isDraftEmpty(d) {
    if (!d) return true;
    if ((d.title || '').trim()) return false;
    if ((d.content || '').trim()) return false;
    if (Array.isArray(d.items) && d.items.some(it => (it.text || '').trim())) return false;
    return true;
  }

  function _wireDraftAutosave(form, id) {
    const view = {
      get failed() { return form._autosaveFailed; },
      set failed(value) { form._autosaveFailed = value; },
    };
    const draft = bindDraft({ collect: () => _collectFormDraft(form), id, view });
    form._flushDraft = draft.flush;
    form._stopDraftAutosave = draft.stop;
    form.addEventListener('input', draft.schedule);
    form.addEventListener('change', draft.schedule);
    form.addEventListener('click', event => {
      if (event.target.closest('.note-cl-dot, .note-cl-rm')) draft.schedule();
    });
  }

  function _commitOpenInPlaceEditor() {
    const form = document.querySelector('#notes-pane .note-form');
    if (!form) return;
    const d = _collectFormDraft(form);
    if (_isDraftEmpty(d)) { form.querySelector('.note-form-cancel')?.click(); return; }
    form.querySelector('.note-form-save')?.click();
  }

  function _applyDraftToNote(note, id) {
    const d = _loadDraft(id);
    if (!d) return { note, restored: false };
    const merged = { ...(note || {}) };
    ['note_type', 'color', 'title', 'label', 'due_date', 'repeat', 'content', 'items'].forEach(k => {
      if (d[k] !== undefined) merged[k] = d[k];
    });
    return { note: merged, restored: true };
  }

  return { _collectFormDraft, _isDraftEmpty, _wireDraftAutosave, _commitOpenInPlaceEditor, _applyDraftToNote };
}
