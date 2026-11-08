/** Notes CRUD, optimistic archive and in-memory undo workflows. */
export function createNotesWorkflows({
  app,
  api,
  effects
}) {
  const state = app.state;
  const _undoStack = [];
  const _renderNotes = effects.render;
  const _patchNote = api.patchNote;
  const uiModule = effects;
  function _pushUndo(entry) {
    _undoStack.push(entry);
    if (_undoStack.length > 20) _undoStack.shift();
  }

  function _popAndRunUndo() {
    const entry = _undoStack.pop();
    if (entry) entry.run();
    return !!entry;
  }

  function _undoArchive(note, prevIdx) {
    // Re-insert at original position and clear archived flag on the server.
    const safeIdx = Math.min(Math.max(prevIdx, 0), state.notes.length);
    app.insertNoteAt(safeIdx, { ...note, archived: false });
    _renderNotes();
    _patchNote(note.id, { archived: false }).catch(() => {
      // Roll back local insertion if the server refuses
      const i = state.notes.findIndex(n => n.id === note.id);
      if (i >= 0) app.removeNoteAt(i);
      _renderNotes();
      uiModule.showError('Undo failed');
    });
  }

  function _archiveNoteById(id, { card = null, celebrate = false } = {}) {
    if (!id) return false;
    const idx = state.notes.findIndex(n => n.id === id);
    if (idx < 0) return false;
    const note = state.notes[idx];
    if (celebrate && card && note) effects.celebrateArchive(note, card);
    const removed = app.removeNoteAt(idx);
    app.setEditingId(null);
    _renderNotes();
    const undo = () => _undoArchive(removed, idx);
    _pushUndo({ label: 'archive', run: undo });
    _patchNote(id, { archived: true }).then(() => {
      effects.archived(undo);
    }).catch(() => {
      app.insertNoteAt(idx, removed);
      _renderNotes();
      uiModule.showError('Failed to archive');
    });
    return true;
  }

  async function _fetchNotes() {
    app.setLoading(true);
    try {
      const res = await api.listNotes(state.showingArchived);
      if (!res.ok) { app.replaceNotes([]); return; }
      const data = await res.json();
      app.replaceNotes(data.notes || data || []);
    } catch (e) {
      console.error('Failed to fetch notes:', e);
      app.replaceNotes([]);
    } finally {
      app.setLoading(false);
    }
  }

  function _saveNote(note) { return api.saveNote(note); }

  function _deleteNoteApi(id) { return api.deleteNote(id); }

  return { _pushUndo, _popAndRunUndo, _undoArchive, _archiveNoteById, _fetchNotes, _saveNote, _deleteNoteApi, _patchNote, undoCount: () => _undoStack.length };
}
