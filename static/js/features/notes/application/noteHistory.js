/** A note's earlier versions (core/note_versions.py): list them, read one, put one back. Owner only; a restore keeps the note it replaces as a version, and the toast after it offers Undo, which puts that one back. */
import { shortTime } from '../domain/historyText.js';

/**
 * Ports: `api` ({ listVersions, getVersion, restoreVersion }), `app`,
 * `state`, `noteWindow` ({ settle, followServer }), `clock` ({ now }), and
 * `effects` ({ renderNote, showToast, showError, undoIcon }).
 */
export function createNoteHistory({ api, app, state, noteWindow, clock, effects }) {
  const stateNote = id => state.notes.find(n => n.id === id) || null;

  /** The server's copy of a note: into the list, its card, and the open window. */
  function take(note) {
    const existing = note && stateNote(note.id);
    if (!existing) return;
    app.mergeNote(existing, note);
    effects.renderNote(note.id);
    noteWindow.followServer(note);
  }

  const list = async id => (await api.listVersions(id)).versions || [];
  const read = (id, versionId) => api.getVersion(id, versionId);

  /**
   * Put version `versionId` back. Anything typed in the open note is saved
   * first, so it is the version the restore keeps. `at`: when that version
   * was kept, for the toast. `undoing`: this restore undoes another one, so
   * its toast offers no further Undo.
   */
  async function restore(id, versionId, { at = null, undoing = false } = {}) {
    try { await noteWindow.settle(id); } catch {}
    try {
      const { undo_version_id: undoId = null, ...note } = await api.restoreVersion(id, versionId, stateNote(id)?.updated_at ?? null);
      take(note);
      if (undoing) effects.showToast('Restore undone');
      else {
        const when = at ? shortTime(at, new Date(clock.now())) : '';
        const message = when ? `Restored the version from ${when}` : 'Version restored';
        effects.showToast(message, undoId != null
          ? { duration: 10000, action: 'Undo', actionIcon: effects.undoIcon, onAction: () => restore(id, undoId, { undoing: true }) }
          : undefined);
      }
      return true;
    } catch (error) {
      const detail = error?.body?.detail;
      if (detail?.note) take(detail.note);
      if (error?.status === 409 && detail?.note) effects.showError(undoing ? 'The note changed meanwhile, so the restore was not undone.' : 'The note changed meanwhile, so nothing was restored. Try again.');
      else if (error?.status === 409) effects.showError('An image in this version has been deleted, so it cannot be restored.');
      else effects.showError(undoing ? 'Could not undo the restore' : 'Could not restore this version');
      return false;
    }
  }

  return { list, read, restore };
}
