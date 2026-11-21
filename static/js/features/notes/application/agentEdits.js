/** Agent edits to a note a run was started from (core/note_agent_scope.py): the note on screen follows each edit, the user is told once, and Undo puts the earlier note back. */
import { isOwnNote } from '../domain/noteRules.js';

/**
 * Ports: `api` ({ getNote, agentUndo }), `app`, `state`, `noteWindow`
 * ({ settle, followServer }), and `effects` ({ renderNote, showToast,
 * showError, undoIcon, seeChanges(id, sessionId) }).
 */
export function createAgentEdits({ api, app, state, noteWindow, effects }) {
  const announced = new Set();

  /** Tell the user once per edit, with Undo, while the edit is still the note's latest change. */
  function announce(note) {
    const edit = note?.agent_edit;
    if (!edit?.undoable || !isOwnNote(note)) return false;
    const key = `${note.id}@${edit.at}`;
    if (announced.has(key)) return false;
    announced.add(key);
    effects.showToast('Agent edited this note', {
      duration: 10000, action: 'Undo', actionIcon: effects.undoIcon, onAction: () => undo(note.id),
      // The history, on this edit's row.
      ...(effects.seeChanges ? { secondaryAction: 'See what changed', onSecondaryAction: () => effects.seeChanges(note.id, edit.session_id || null) } : {}),
    });
    return true;
  }

  /** The server's copy of a note: into the list, its card, and the open window. */
  function take(note) {
    const existing = note && state.notes.find(n => n.id === note.id);
    if (!existing) return;
    app.mergeNote(existing, note);
    effects.renderNote(note.id);
    noteWindow.followServer(note);
  }

  /** A run reported an edit to its note. */
  async function edited(id) {
    let note;
    try { note = await api.getNote(id); } catch { return; }
    take(note);
    announce(note);
  }

  /** A list re-read: edits made from the chat (a follow-up turn) show up here. `before` maps note ids to the agent edit they had. */
  function noticeInList(notes, before) {
    for (const note of notes || []) {
      if (before.has(note.id) && before.get(note.id) !== (note.agent_edit?.at || null)) announce(note);
    }
  }

  const agentEditsOf = notes => new Map((notes || []).map(n => [n.id, n.agent_edit?.at || null]));

  async function undo(id) {
    // Anything typed in the open note is saved first; Undo then sees it and
    // leaves the note alone.
    try { await noteWindow.settle(id); } catch {}
    try {
      take(await api.agentUndo(id));
      effects.showToast('Agent edit undone');
      return true;
    } catch (error) {
      const current = error?.body?.detail?.note;
      if (current) take(current);
      if (error?.status === 409) effects.showError('The note changed after the agent edit, so nothing was undone.');
      else if (error?.status !== 404) effects.showError('Could not undo the agent edit');
      return false;
    }
  }

  return { edited, undo, announce, noticeInList, agentEditsOf };
}
