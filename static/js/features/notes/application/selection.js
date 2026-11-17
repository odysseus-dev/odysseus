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
    async function archiveSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      await Promise.all(ids.map(id => api.patchNote(id, { archived: true }).catch(() => {})));
      exitSelection(); await fetchNotes(); effects.render(); effects.showToast(`Archived ${ids.length}`);
    }
    async function deleteSelected() {
      const ids = [...state.selectedIds];
      if (!ids.length) return;
      if (effects.styledConfirm) {
        const ok = await effects.styledConfirm(`Delete ${ids.length} note${ids.length === 1 ? '' : 's'}?`, { confirmText: 'Delete', danger: true });
        if (!ok) return;
      }
      await Promise.all(ids.map(id => api.deleteNote(id).catch(() => {})));
      exitSelection(); await fetchNotes(); effects.render(); effects.showToast(`Deleted ${ids.length}`);
    }
    return { attachSelected, archiveSelected, deleteSelected };
}
