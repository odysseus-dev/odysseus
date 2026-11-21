/** Collect editor drafts, restore them and wire the autosave workflow. */
import { itemsToMarkdown } from '../domain/markdownTasks.js';

export function createDraftEditor({
  bindDraft,
  _collectItems,
  _loadDraft,
  _loadDraftRecovery
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
    if (form._bodyRevision) d.body_revision = form._bodyRevision;
    if (type === 'note') {
      d.content = form.querySelector('.note-form-content')?.value || '';
      if (form.dataset.replaceContent === 'true') d.replace_content = true;
    }
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
      get bodyRevision() { return form._bodyRevision; },
      set bodyRevision(value) { form._bodyRevision = value; },
    };
    const draft = bindDraft({ collect: () => _collectFormDraft(form), id, view });
    form._flushDraft = draft.flush;
    form._stopDraftAutosave = draft.stop;
    form._acknowledgeDraftSave = draft.acknowledge;
    const currentDraft = _loadDraft(id);
    const recoverable = _loadDraftRecovery(id);
    if (currentDraft && currentDraft.body_revision !== view.bodyRevision) recoverable.push(currentDraft);
    if (recoverable.length) {
      const recovery = document.createElement('details');
      recovery.className = 'note-form-draft-recovery';
      const summary = document.createElement('summary');
      summary.textContent = 'Recover unsaved draft';
      const help = document.createElement('p');
      help.textContent = 'This note changed after the draft was written. Copy the edits you want to keep into the current note.';
      recovery.append(summary, help);
      for (const entry of recoverable) {
        const text = document.createElement('textarea');
        text.readOnly = true;
        text.setAttribute('aria-label', 'Unsaved draft');
        text.value = [
          entry.title, entry.content, itemsToMarkdown(entry.items),
          entry.label && `Tags ${entry.label}`,
          entry.due_date && `Reminder ${entry.due_date}`,
        ].filter(Boolean).join('\n\n');
        recovery.appendChild(text);
      }
      recovery.addEventListener('keydown', event => event.stopPropagation());
      form.appendChild(recovery);
    }
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
    if (note?.body_revision !== d.body_revision) return { note, restored: false };
    const merged = { ...(note || {}) };
    ['note_type', 'color', 'title', 'label', 'due_date', 'repeat', 'content', 'items', 'body_revision'].forEach(k => {
      if (d[k] !== undefined) merged[k] = d[k];
    });
    if (d.content !== undefined) merged.replace_content = d.replace_content === true;
    return { note: merged, restored: true };
  }

  return { _collectFormDraft, _isDraftEmpty, _wireDraftAutosave, _commitOpenInPlaceEditor, _applyDraftToNote };
}
