/** Selected-note attachment and batch outcomes, including failed-item retry selection. */
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
    async function mutateSelected(ids, mutate, verb, infinitive) {
      const results = await Promise.allSettled(ids.map(mutate));
      let succeeded = 0;
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          app.deselectId(ids[index]);
          succeeded++;
        }
      });
      await fetchNotes();
      if (!state.selectedIds.size) exitSelection();
      effects.render();
      const failed = ids.length - succeeded;
      if (failed) effects.showError(`${succeeded ? `${verb} ${succeeded}; failed` : 'Failed'} to ${infinitive} ${failed} note${failed === 1 ? '' : 's'}`);
      else effects.showToast(`${verb} ${succeeded}`);
    }
    async function archiveSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      await mutateSelected(ids, id => api.patchNote(id, { archived: true }), 'Archived', 'archive');
    }
    async function deleteSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      if (effects.styledConfirm) {
        const ok = await effects.styledConfirm(`Delete ${ids.length} note${ids.length === 1 ? '' : 's'}?`, { confirmText: 'Delete', danger: true });
        if (!ok) return;
      }
      await mutateSelected(ids, id => api.deleteNote(id), 'Deleted', 'delete');
    }
    return { attachSelected, archiveSelected, deleteSelected };
}
