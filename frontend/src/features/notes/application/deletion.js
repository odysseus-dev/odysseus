/** Confirmed deletion and past-reminder clearing workflows. */
export function createDeletion({
  app,
  _deleteNoteApi,
  _fetchNotes,
  _isPastReminder,
  _renderNotes,
  effects,
  state
}) {
  async function _deleteNote(id) {
    const ok = effects.styledConfirm
      ? await effects.styledConfirm('Delete this note?', { confirmText: 'Delete', danger: true })
      : effects.confirm('Delete this note?');
    if (!ok) return;
    try { await _deleteNoteApi(id); await _fetchNotes(); _renderNotes(); effects.showToast('Deleted'); }
    catch (err) { effects.showError(err.message); }
  }

  async function _clearPastReminders() {
    const targets = state.notes.filter(n => !n.archived && _isPastReminder(n));
    if (!targets.length) {
      effects.showToast?.('No past reminders to clear');
      return;
    }
    const ok = effects.styledConfirm
      ? await effects.styledConfirm(`Delete ${targets.length} past reminder${targets.length === 1 ? '' : 's'}?`, { confirmText: 'Delete', danger: true })
      : effects.confirm(`Delete ${targets.length} past reminder${targets.length === 1 ? '' : 's'}?`);
    if (!ok) return;
    const results = await Promise.allSettled(targets.map(n => _deleteNoteApi(n.id)));
    const succeeded = results.filter(result => result.status === 'fulfilled').length;
    await _fetchNotes();
    _renderNotes();
    const failed = targets.length - succeeded;
    const summary = `Cleared ${succeeded} past reminder${succeeded === 1 ? '' : 's'}`;
    if (failed) effects.showError(`${succeeded ? `${summary}; failed` : 'Failed'} to delete ${failed} past reminder${failed === 1 ? '' : 's'}`);
    else effects.showToast?.(summary);
  }

  async function deleteFromEditor(id) {
    const ok = effects.styledConfirm
      ? await effects.styledConfirm('Delete this note?', { confirmText: 'Delete', danger: true })
      : effects.confirm('Delete this note?');
    if (!ok) return;
    const index = state.notes.findIndex(note => note.id === id);
    if (index >= 0) app.removeNoteAt(index);
    app.setEditingId(null);
    _renderNotes();
    _deleteNoteApi(id).then(() => effects.showToast('Deleted')).catch(() => {
      effects.showError('Failed to delete');
      _fetchNotes().then(() => _renderNotes());
    });
  }
  return { _deleteNote, _clearPastReminders, deleteFromEditor };
}
