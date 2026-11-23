/** Checklist actions reconcile with the canonical note returned by the server. */
export function createChecklistOperations({ app, api, effects }) {
  const state = app.state;
  const pending = new Set();
  const findNote = id => state.notes.find(note => note.id === id);

  function observedNote(id, revision) {
    const note = findNote(id);
    if (note?.body_revision === revision) return note;
    effects.showError('Note changed. Review the checklist and try again.');
    effects.render();
  }

  async function write(note, request, onSaved, errorMessage) {
    if (!note || pending.has(note.id)) return;
    const id = note.id;
    const revision = note.body_revision;
    const mergeBody = saved => {
      const current = findNote(id);
      if (current !== note || current.body_revision !== revision) return false;
      const { content, items, note_type, image_url, body_revision } = saved;
      app.mergeNote(current, { content, items, note_type, image_url, body_revision });
      return true;
    };
    pending.add(id);
    try {
      const saved = await request(revision);
      if (mergeBody(saved)) onSaved(saved);
    } catch (err) {
      if (err.status === 409) {
        try { mergeBody(await api.getNote(id)); } catch { /* Keep the last known state if offline. */ }
      }
      effects.showError(err.status === 409 ? 'Note changed. Review the checklist and try again.' : errorMessage);
    } finally {
      pending.delete(id);
      effects.render();
    }
  }

  function removeItem(id, index, revision) {
    const note = observedNote(id, revision);
    if (!note?.items?.[index]) return;
    const items = note.items.filter((_, i) => i !== index);
    return write(note, body_revision => api.patchNote(id, { items, body_revision }), () => {}, 'Failed to remove item');
  }

  function editItem(note, index, text) {
    const item = note.items?.[index];
    if (!item || text === (item.text || '').trim()) { effects.render(); return; }
    const items = text
      ? note.items.map((it, i) => (i === index ? { ...it, text } : it))
      : note.items.filter((_, i) => i !== index);
    return write(note, body_revision => api.patchNote(note.id, { items, body_revision }), () => {}, 'Failed to update item');
  }

  function addItem(id, text, added) {
    return write(findNote(id), async () => (await api.appendTask(id, text)).note, added, 'Failed to add item');
  }

  function toggleItem(id, index, changed, completed, revision) {
    const note = observedNote(id, revision);
    if (!note?.items?.[index]) return;
    const wasDone = note.items.every(item => item.done);
    return write(note, async revision => (await api.toggleTask(id, index, revision)).note, saved => {
      changed(saved.items[index].done);
      if (!wasDone && saved.items.every(item => item.done)) completed();
    }, 'Failed to toggle item');
  }

  return { removeItem, editItem, addItem, toggleItem, toggleTodayStep: toggleItem };
}
