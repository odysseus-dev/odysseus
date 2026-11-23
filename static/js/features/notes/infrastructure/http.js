/** Notes HTTP port. Response interpretation stays with the owning workflow. */
export function createNotesApi({ origin, request }) {
  const json = (method, body) => ({ method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  async function readNoteResponse(response, message) {
    if (!response.ok) throw Object.assign(new Error(message), { status: response.status });
    return await response.json();
  }
  async function getNote(id) {
    const response = await request(`${origin}/api/notes/${id}`, { credentials: 'same-origin' });
    return readNoteResponse(response, 'Failed to reload note');
  }
  async function saveNote(note) {
    const response = await request(note.id ? `${origin}/api/notes/${note.id}` : `${origin}/api/notes`, json(note.id ? 'PUT' : 'POST', note));
    return readNoteResponse(response, 'Failed to save note');
  }
  async function patchNote(id, patch) {
    const response = await request(`${origin}/api/notes/${id}`, json('PUT', patch));
    return readNoteResponse(response, 'Failed to update note');
  }
  async function toggleTask(id, index, body_revision) {
    const response = await request(`${origin}/api/notes/${id}/items/${index}/toggle`, json('POST', { body_revision }));
    return readNoteResponse(response, 'Failed to toggle item');
  }
  async function appendTask(id, text, indent = 0) {
    const response = await request(`${origin}/api/notes/${id}/items`, json('POST', { text, indent }));
    return readNoteResponse(response, 'Failed to add item');
  }
  async function deleteNote(id) {
    const response = await request(`${origin}/api/notes/${id}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
  }
  return {
    saveNote, getNote, patchNote, deleteNote, toggleTask, appendTask,
    listNotes: archived => request(`${origin}/api/notes${archived ? '?archived=true' : ''}`, { credentials: 'same-origin' }),
    reorder: ids => request(`${origin}/api/notes/reorder`, json('POST', { ids })),
    upload: body => request(`${origin}/api/upload`, { method: 'POST', body, credentials: 'same-origin' }),
    uploadUrl: id => `${origin}/api/upload/${id}`,
    fireReminder: body => request('/api/notes/fire-reminder', json('POST', body)),
    defaultChat: () => request(`${origin}/api/default-chat`, { credentials: 'same-origin' }),
    createSession: body => request(`${origin}/api/session`, { method: 'POST', body, credentials: 'same-origin' }),
    startChat: body => request(`${origin}/api/chat_stream`, { method: 'POST', body, credentials: 'same-origin' }),
  };
}
