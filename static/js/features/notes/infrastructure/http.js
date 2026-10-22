/** Notes HTTP port. Response interpretation stays with the owning workflow. */
export function createNotesApi({ origin, request }) {
  const json = (method, body) => ({ method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  async function saveNote(note) {
    const response = await request(note.id ? `${origin}/api/notes/${note.id}` : `${origin}/api/notes`, json(note.id ? 'PUT' : 'POST', note));
    if (!response.ok) throw new Error('Failed to save note');
    return await response.json();
  }
  async function patchNote(id, patch) {
    const response = await request(`${origin}/api/notes/${id}`, json('PUT', patch));
    if (!response.ok) throw new Error('Failed to update note');
    return await response.json();
  }
  async function deleteNote(id) {
    const response = await request(`${origin}/api/notes/${id}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
  }
  return {
    saveNote, patchNote, deleteNote,
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
