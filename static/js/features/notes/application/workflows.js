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
  let notesRequest = 0;

  async function resolveLinkedNote(id) {
    const requested = String(id ?? '').trim();
    if (!requested) return null;
    const response = await api.getNote(requested);
    if (response.ok) {
      const note = await response.json();
      if (!note || note.id !== requested || typeof note.archived !== 'boolean') {
        throw new Error('Invalid note response');
      }
      return note;
    }
    if (response.status !== 404) throw new Error('Could not read note');
    // Chat/tool links also use short IDs. Resolve them against both owned
    // collections, and never guess between colliding prefixes.
    if (requested.length > 8) return null;
    const responses = await Promise.all([api.listNotes(false), api.listNotes(true)]);
    const matches = new Map();
    for (const result of responses) {
      if (!result.ok) throw new Error('Could not read notes');
      const body = await result.json();
      const notes = Array.isArray(body) ? body : body?.notes;
      if (!Array.isArray(notes)) throw new Error('Invalid notes response');
      for (const note of notes) {
        if (typeof note?.id === 'string' && note.id.startsWith(requested)) {
          if (typeof note.archived !== 'boolean') throw new Error('Invalid note response');
          matches.set(note.id, note);
        }
      }
    }
    return matches.size === 1 ? matches.values().next().value : null;
  }
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
    const request = ++notesRequest;
    const archived = state.showingArchived;
    const current = () => request === notesRequest && archived === state.showingArchived;
    app.setLoading(true);
    try {
      const res = await api.listNotes(archived);
      if (!current()) return;
      if (!res.ok) { app.replaceNotes([]); return; }
      const data = await res.json();
      if (current()) app.replaceNotes(data.notes || data || []);
    } catch (e) {
      console.error('Failed to fetch notes:', e);
      if (current()) app.replaceNotes([]);
    } finally {
      if (request === notesRequest) app.setLoading(false);
    }
  }

  function _saveNote(note) { return api.saveNote(note); }

  function _deleteNoteApi(id) { return api.deleteNote(id); }

  return { _pushUndo, _popAndRunUndo, _undoArchive, _archiveNoteById, _fetchNotes, _saveNote, _deleteNoteApi, _patchNote, resolveLinkedNote, undoCount: () => _undoStack.length };
}
