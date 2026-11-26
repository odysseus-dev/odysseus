/** Notes HTTP port. Response interpretation stays with the owning workflow. */
export function createNotesApi({ origin, request }) {
  const json = (method, body) => ({ method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  // Keep the status and body: a 409 carries the newer copy of the note
  // (`detail.note`), and a 404 means the note is gone for this account.
  async function failure(response, message) {
    const error = new Error(message);
    error.status = response.status;
    try { error.body = await response.json(); } catch { error.body = null; }
    return error;
  }
  async function readNoteResponse(response, message) {
    if (!response.ok) throw await failure(response, message);
    return await response.json();
  }
  async function saveNote(note) {
    const response = await request(note.id ? `${origin}/api/notes/${note.id}` : `${origin}/api/notes`, json(note.id ? 'PUT' : 'POST', note));
    return readNoteResponse(response, 'Failed to save note');
  }
  async function patchNote(id, patch) {
    const response = await request(`${origin}/api/notes/${id}`, json('PUT', patch));
    return readNoteResponse(response, 'Failed to update note');
  }
  // `text` is the task as this view shows it, so a line added or removed
  // above it elsewhere can't redirect the tick (the server answers 409 when
  // the task is gone). `done` is the state the user asked for: the server sets
  // it rather than flipping, so a tick from a view that is behind can't undo
  // the same tick made elsewhere. `body_revision` is the body the tick was
  // made on: 409 when it moved on.
  async function toggleTask(id, index, text, done, body_revision) {
    const body = {};
    if (text != null) body.text = text;
    if (typeof done === 'boolean') body.done = done;
    if (body_revision) body.body_revision = body_revision;
    const init = Object.keys(body).length ? json('POST', body) : { method: 'POST', credentials: 'same-origin' };
    const response = await request(`${origin}/api/notes/${id}/items/${index}/toggle`, init);
    return readNoteResponse(response, 'Failed to toggle item');
  }
  async function appendTask(id, text, indent = 0) {
    const response = await request(`${origin}/api/notes/${id}/items`, json('POST', { text, indent }));
    return readNoteResponse(response, 'Failed to add item');
  }
  async function getNote(id) {
    const response = await request(`${origin}/api/notes/${id}`, { credentials: 'same-origin' });
    return readNoteResponse(response, 'Failed to load note');
  }
  // Puts back the note as it was before its last agent edit. 409 (with the
  // note as it is) when anything changed it since.
  async function agentUndo(id) {
    const response = await request(`${origin}/api/notes/${id}/agent-undo`, { method: 'POST', credentials: 'same-origin' });
    if (!response.ok) throw await failure(response, 'Failed to undo the agent edit');
    return await response.json();
  }
  // Earlier versions of a note (owner only): the list has no full text.
  async function listVersions(id) {
    const response = await request(`${origin}/api/notes/${id}/versions`, { credentials: 'same-origin' });
    if (!response.ok) throw await failure(response, 'Failed to load the history');
    return await response.json();
  }
  async function getVersion(id, versionId) {
    const response = await request(`${origin}/api/notes/${id}/versions/${versionId}`, { credentials: 'same-origin' });
    if (!response.ok) throw await failure(response, 'Failed to load the version');
    return await response.json();
  }
  // 409 (with the note as it is) when the note is no longer `base`.
  async function restoreVersion(id, versionId, base) {
    const response = await request(`${origin}/api/notes/${id}/versions/${versionId}/restore`, json('POST', { base_updated_at: base ?? null }));
    if (!response.ok) throw await failure(response, 'Failed to restore the version');
    return await response.json();
  }
  async function deleteNote(id) {
    const response = await request(`${origin}/api/notes/${id}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
  }
  // `directory` is false for everyone but an admin: no account list, the
  // owner types the exact username instead.
  async function shareTargets() {
    const response = await request(`${origin}/api/notes/share-targets`, { credentials: 'same-origin' });
    if (!response.ok) throw await failure(response, 'Failed to load users');
    const body = await response.json();
    return { users: body.users || [], directory: body.directory !== false };
  }
  async function listShares(id) {
    const response = await request(`${origin}/api/notes/${id}/shares`, { credentials: 'same-origin' });
    if (!response.ok) throw await failure(response, 'Failed to load sharing');
    return (await response.json()).shared_with || [];
  }
  async function share(id, users, permission) {
    const response = await request(`${origin}/api/notes/${id}/share`, json('POST', { users, permission }));
    if (!response.ok) throw await failure(response, 'Failed to share note');
    return (await response.json()).shared_with || [];
  }
  async function unshare(id, username) {
    const response = await request(`${origin}/api/notes/${id}/share/${encodeURIComponent(username)}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!response.ok) throw await failure(response, 'Failed to update sharing');
    return await response.json();
  }
  return {
    saveNote, patchNote, deleteNote, toggleTask, appendTask, getNote, agentUndo,
    listVersions, getVersion, restoreVersion,
    shareTargets, listShares, share, unshare,
    // `etag`: the list last seen; an unchanged list then answers 304 with no body.
    listNotes: (archived, { etag = null } = {}) => request(`${origin}/api/notes${archived ? '?archived=true' : ''}`, etag ? { credentials: 'same-origin', headers: { 'If-None-Match': etag } } : { credentials: 'same-origin' }),
    reorder: ids => request(`${origin}/api/notes/reorder`, json('POST', { ids })),
    upload: body => request(`${origin}/api/upload`, { method: 'POST', body, credentials: 'same-origin' }),
    uploadUrl: id => `${origin}/api/upload/${id}`,
    fireReminder: body => request('/api/notes/fire-reminder', json('POST', body)),
    defaultChat: () => request(`${origin}/api/default-chat`, { credentials: 'same-origin' }),
    createSession: body => request(`${origin}/api/session`, { method: 'POST', body, credentials: 'same-origin' }),
    startChat: body => request(`${origin}/api/chat_stream`, { method: 'POST', body, credentials: 'same-origin' }),
  };
}
