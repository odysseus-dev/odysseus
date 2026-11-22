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
    await Promise.all(targets.map(n => _deleteNoteApi(n.id).catch(() => {})));
    await _fetchNotes();
    _renderNotes();
    effects.showToast?.(`Cleared ${targets.length} past reminder${targets.length === 1 ? '' : 's'}`);
  }

  function confirmDelete() {
    return Promise.resolve(effects.styledConfirm
      ? effects.styledConfirm('Delete this note?', { confirmText: 'Delete', danger: true })
      : effects.confirm('Delete this note?'));
  }

  /** Delete without asking again: the note window already confirmed. */
  // A delete the server did not take puts the card back where it
  // was, then re-reads the list in case it did happen. If that read fails
  // too (offline), the list on screen stays as it is.
  function putBack(index, removed) {
    if (removed && !state.notes.some(note => note.id === removed.id)) app.insertNoteAt(Math.min(index, state.notes.length), removed);
    return _fetchNotes({ keepOnFailure: true });
  }

  function deleteNow(id) {
    const index = state.notes.findIndex(note => note.id === id);
    const removed = index >= 0 ? app.removeNoteAt(index) : null;
    _renderNotes();
    return _deleteNoteApi(id).then(() => effects.showToast('Deleted')).catch(() => {
      effects.showError('Failed to delete');
      putBack(index, removed).then(() => _renderNotes());
    });
  }
  return { _deleteNote, _clearPastReminders, confirmDelete, deleteNow };
}
