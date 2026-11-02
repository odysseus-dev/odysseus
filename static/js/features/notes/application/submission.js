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
  function submitNote(payload, note, isEdit) {
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
      // Optimistic update : update local state first, render, then save in background
      app.setEditingId(null);
      const draftId = isEdit ? note.id : '__new__';
      const submittedDraft = JSON.stringify(_loadDraft(draftId));
      if (isEdit) {
        const idx = state.notes.findIndex(n => n.id === note.id);
        if (idx >= 0) app.replaceNoteAt(idx, { ...state.notes[idx], ...payload });
      } else {
        app.prependNote({ ...payload, id: 'tmp_' + uid(), created_at: new Date(clock.now()).toISOString(), updated_at: new Date(clock.now()).toISOString() });
      }
      effects.render();
      // Background save
      api.saveNote(payload).then(saved => {
        if (JSON.stringify(_loadDraft(draftId)) === submittedDraft) _clearDraft(draftId);
        if (!isEdit && saved && saved.id) {
          // Replace temp ID with real one from server. AND re-render : the
          // existing card's `data-note-id="tmp_xxx"` is stale after Object.assign
          // bumps the in-memory id, so all subsequent clicks (edit, done, copy,
          // archive, delete) silently fail to find the note in `_notes`.
          const tmp = state.notes.find(n => n.id.startsWith('tmp_'));
          if (tmp) app.mergeNote(tmp, saved);
          effects.render();
        }
      }).catch(err => {
        effects.showError('Save failed: ' + err.message);
        fetchNotes().then(() => effects.render());
      });

  }
  return { submitNote };
}
