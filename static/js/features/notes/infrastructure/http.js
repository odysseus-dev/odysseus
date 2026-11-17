/** Notes HTTP port. Response interpretation stays with the owning workflow. */
export function createNotesApi({ origin, request }) {
  // Startup asks for the active list twice a few seconds apart (rail badge,
  // then reminders). Within the first 15 s of page life a just-fetched active
  // list is reused briefly (writes made outside this port are not seen, hence
  // the startup-only limit); every
  // write below drops it so no caller ever sees pre-edit data.
  const RECENT_MS = 5000;
  const STARTUP_MS = 15000;
  let recent = null;
  const dropRecent = () => { recent = null; };
  const json = (method, body) => ({ method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  async function saveNote(note) {
    dropRecent();
    const response = await request(note.id ? `${origin}/api/notes/${note.id}` : `${origin}/api/notes`, json(note.id ? 'PUT' : 'POST', note));
    if (!response.ok) throw new Error('Failed to save note');
    return await response.json();
  }
  async function patchNote(id, patch) {
    dropRecent();
    const response = await request(`${origin}/api/notes/${id}`, json('PUT', patch));
    if (!response.ok) throw new Error('Failed to update note');
    return await response.json();
  }
  async function deleteNote(id) {
    dropRecent();
    const response = await request(`${origin}/api/notes/${id}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
  }
  return {
    saveNote, patchNote, deleteNote,
    listNotes: async archived => {
      if (archived) return request(`${origin}/api/notes?archived=true`, { credentials: 'same-origin' });
      if (recent && Date.now() - recent.at < RECENT_MS && performance.now() < STARTUP_MS) return recent.response.clone();
      const response = await request(`${origin}/api/notes`, { credentials: 'same-origin' });
      recent = response.ok ? { at: Date.now(), response: response.clone() } : null;
      return response;
    },
    reorder: ids => (dropRecent(), request(`${origin}/api/notes/reorder`, json('POST', { ids }))),
    upload: body => request(`${origin}/api/upload`, { method: 'POST', body, credentials: 'same-origin' }),
    uploadUrl: id => `${origin}/api/upload/${id}`,
    fireReminder: body => request('/api/notes/fire-reminder', json('POST', body)),
    defaultChat: () => request(`${origin}/api/default-chat`, { credentials: 'same-origin' }),
    createSession: body => request(`${origin}/api/session`, { method: 'POST', body, credentials: 'same-origin' }),
    startChat: body => request(`${origin}/api/chat_stream`, { method: 'POST', body, credentials: 'same-origin' }),
  };
}
