/**
 * Note versions this tab wrote itself. The note window follows those (a card checkbox ticked behind it) instead of calling them a conflict.
 * A version is named by its `body_revision` or its `updated_at`; either one finds it.
 */
export function createNoteVersions({ app }) {
  const state = app.state;
  const local = new Set();
  function record(note) {
    if (!note?.id || (!note.body_revision && !note.updated_at)) return;
    if (note.body_revision) local.add(`${note.id}@${note.body_revision}`);
    if (note.updated_at) local.add(`${note.id}@${note.updated_at}`);
    const existing = state.notes.find(n => n.id === note.id);
    // The list's copy moves on to the version written, but only when it holds
    // the body that version has: tokens newer than the body they sit on would
    // let the next write from the list pass over a change it never showed.
    if (!existing || (typeof note.content === 'string' && note.content !== existing.content)) return;
    const tokens = {};
    if (note.updated_at && existing.updated_at !== note.updated_at) tokens.updated_at = note.updated_at;
    if (note.body_revision && existing.body_revision !== note.body_revision) tokens.body_revision = note.body_revision;
    if (Object.keys(tokens).length) app.mergeNote(existing, tokens);
  }
  const after = (call, pick) => async (...args) => { const result = await call(...args); record(pick(result)); return result; };
  function track(api) {
    return {
      ...api,
      saveNote: after(api.saveNote, r => r),
      patchNote: after(api.patchNote, r => r),
      toggleTask: after(api.toggleTask, r => r?.note),
      appendTask: after(api.appendTask, r => r?.note),
    };
  }
  /** `version`: a body_revision or an updated_at. */
  return { track, record, isLocal: (id, version) => !!version && local.has(`${id}@${version}`) };
}
