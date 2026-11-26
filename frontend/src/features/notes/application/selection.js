/** Selected-note attachment retains synchronous acceptance; bulk requests retain partial-failure behavior. */
export function createSelectionOperations({
  app,
  api,
  effects,
  sendContext,
  exitSelection,
  fetchNotes
}) {
    const state = app.state;
    function attachSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      const detail = { noteIds: ids, accepted: false };
      sendContext(detail);
      if (!detail.accepted) {
        effects.showToast('Choose a chat or model before attaching notes');
        return;
      }
      exitSelection();
    }
    /** Run `request` for each id; the toast counts what the server accepted, and says how many it refused. */
    async function each(ids, request, done, verb) {
      const results = await Promise.all(ids.map(id => request(id).then(() => true, () => false)));
      const ok = results.filter(Boolean).length;
      const failed = ids.length - ok;
      exitSelection(); await fetchNotes(); effects.render();
      effects.showToast(failed ? `${done} ${ok}; ${failed} could not be ${verb}` : `${done} ${ok}`);
    }
    async function archiveSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      await each(ids, id => api.patchNote(id, { archived: true }), 'Archived', 'archived');
    }
    async function deleteSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      if (effects.styledConfirm) {
        const ok = await effects.styledConfirm(`Delete ${ids.length} note${ids.length === 1 ? '' : 's'}?`, { confirmText: 'Delete', danger: true });
        if (!ok) return;
      }
      await each(ids, id => api.deleteNote(id), 'Deleted', 'deleted');
    }
    return { attachSelected, archiveSelected, deleteSelected };
}
