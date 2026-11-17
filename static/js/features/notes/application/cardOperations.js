/** Optimistic card mutations and rollback. Animation completion is a UI-supplied boundary. */
export function createCardOperations({
  app,
  api,
  effects,
  pushUndo,
  undoArchive
}) {
    const state = app.state;
    function togglePin(id) {
      const note = state.notes.find(n => n.id === id);
      if (!note) return;
      const prevPinned = note.pinned, prevSortOrder = note.sort_order;
      app.mergeNote(note, { pinned: !prevPinned });
      const patch = { pinned: note.pinned };
      if (note.pinned) {
        const minPinned = state.notes.filter(n => n.pinned && n.id !== id).reduce((m, n) => Math.min(m, n.sort_order || 0), 0);
        app.mergeNote(note, { sort_order: minPinned - 1 });
        patch.sort_order = note.sort_order;
      }
      effects.render();
      api.patchNote(id, patch).catch(() => {
        app.mergeNote(note, { pinned: prevPinned, sort_order: prevSortOrder });
        effects.render(); effects.showError('Failed to pin');
      });
    }
    async function updateColor(id, color) {
      try {
        await api.patchNote(id, { color: color || null });
        const note = state.notes.find(n => n.id === id);
        if (note) app.mergeNote(note, { color });
      } catch { effects.showError('Failed to update color'); }
    }
    function stageArchive(id) {
      const index = state.notes.findIndex(n => n.id === id);
      if (index < 0) return null;
      const removed = app.removeNoteAt(index);
      const undo = () => undoArchive(removed, index);
      pushUndo({ label: 'archive', run: undo });
      return () => {
        effects.render();
        api.patchNote(id, { archived: true }).then(() => effects.archived(undo)).catch(() => {
          app.insertNoteAt(index, removed); effects.render(); effects.showError('Failed to archive');
        });
      };
    }
    function unarchive(id) {
      const index = state.notes.findIndex(n => n.id === id);
      if (index < 0) return;
      const removed = app.removeNoteAt(index);
      effects.render();
      api.patchNote(id, { archived: false }).then(() => effects.showToast('Unarchived')).catch(() => {
        app.insertNoteAt(index, removed); effects.render(); effects.showError('Failed to unarchive');
      });
    }
    function deleteOptimistically(id, toast = false) {
      const index = state.notes.findIndex(n => n.id === id);
      if (index < 0) return;
      const removed = app.removeNoteAt(index);
      effects.render();
      const deletion = api.deleteNote(id);
      const completion = toast ? deletion.then(() => effects.showToast('Deleted')) : deletion;
      completion.catch(() => {
        app.insertNoteAt(index, removed); effects.render(); effects.showError('Failed to delete');
      });
    }
    // Desktop deliberately persists without updating local sort_order, as before.
    function persistOrder(ids) { return api.reorder(ids); }
    async function commitMobileOrder(ids) {
      try {
        await api.reorder(ids);
        ids.forEach((id, index) => {
          const note = state.notes.find(note => note.id === id);
          if (note) app.mergeNote(note, { sort_order: index });
        });
      } catch (error) { console.warn('reorder failed', error); }
    }
    return { togglePin, updateColor, stageArchive, unarchive, deleteOptimistically, persistOrder, commitMobileOrder };
}
