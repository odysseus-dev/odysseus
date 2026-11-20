/** Editor submission ordering, optimistic state, reminder rearming and save recovery. */
export function createNotesSubmission({
  app,
  api,
  persistence,
  clock,
  uid,
  effects,
  fetchNotes
}) {
  const state = app.state;
  const { _loadFiredReminders, _saveFiredReminders, _loadGlowedReminders, _saveGlowedReminders, _loadDraft, _clearDraft } = persistence;
  function submitNote(payload, note, isEdit, onSaved) {
      // Reset fired reminder if due_date changed (so re-arm works), and also
      // clear the entry-glow seen flag so the new firing glows again on the
      // next time the user opens the panel.
      if (isEdit && note.due_date !== payload.due_date) {
        const fired = _loadFiredReminders();
        fired.delete(note.id);
        _saveFiredReminders(fired);
        const glowed = _loadGlowedReminders();
        glowed.delete(note.id);
        _saveGlowedReminders(glowed);
        effects.clearGlow(note.id);
      }
      // Edited notes move to the top of their section (under pinned). Compute
      // sort_order = (min unpinned sort_order) - 1 so the saved note sorts above
      // siblings; the pin block keeps its own ordering above this.
      if (!payload.pinned) {
        // Both edits AND newly-created notes anchor above the rest of the
        // unpinned section. Without this, freshly created notes sit at the
        // bottom because manually-reordered siblings already carry negative
        // sort_order values.
        const minUnpinned = state.notes
          .filter(n => !n.pinned && (!isEdit || n.id !== note.id))
          .reduce((m, n) => Math.min(m, n.sort_order || 0), 0);
        payload.sort_order = minUnpinned - 1;
      }
      const draftId = isEdit ? note.id : '__new__';
      const submittedDraft = JSON.stringify(_loadDraft(draftId));
      const temporaryId = isEdit ? null : 'tmp_' + uid();
      if (!isEdit) {
        app.setEditingId(null);
        app.prependNote({ ...payload, id: temporaryId, created_at: new Date(clock.now()).toISOString(), updated_at: new Date(clock.now()).toISOString() });
        effects.render();
      }
      return api.saveNote(payload).then(saved => {
        if (!onSaved && JSON.stringify(_loadDraft(draftId)) === submittedDraft) _clearDraft(draftId);
        const current = state.notes.find(n => n.id === (isEdit ? note.id : temporaryId));
        if (current && (!isEdit || current.body_revision === payload.body_revision)) app.mergeNote(current, saved);
        const formIsOpen = onSaved ? onSaved(saved) : true;
        if (isEdit && formIsOpen && state.editingId === note.id) app.setEditingId(null);
        if (formIsOpen || !state.editingId) effects.render();
      }).catch(async err => {
        if (isEdit && err.status === 409) {
          effects.showError('Note changed elsewhere. Copy your draft and reopen the note to see the latest version.');
          try {
            const saved = await api.getNote(note.id);
            const current = state.notes.find(n => n.id === note.id);
            if (current && current.body_revision === payload.body_revision) app.mergeNote(current, saved);
          } catch { /* The draft stays in the form and local storage. */ }
          return;
        }
        effects.showError('Save failed: ' + err.message);
        if (!isEdit) fetchNotes().then(() => effects.render());
      });

  }
  return { submitNote };
}
