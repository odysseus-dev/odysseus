/** Checklist item workflows. Supplied feedback callbacks update the invoking view. */
export function createChecklistOperations({
  app,
  api,
  effects,
  uid
}) {
    const state = app.state;
    function itemNote(id, index) {
      const note = state.notes.find(n => n.id === id);
      return note && Array.isArray(note.items) && note.items[index] ? note : null;
    }
    function removeItem(id, index) {
      const note = itemNote(id, index);
      if (!note) return;
      const removed = note.items[index];
      app.mergeNote(note, { items: note.items.filter((_, i) => i !== index) });
      effects.render();
      api.patchNote(id, { items: note.items }).catch(() => {
        app.spliceItems(note, index, 0, removed); effects.render(); effects.showError('Failed to remove item');
      });
    }
    function editItem(note, index, text) {
      const oldText = (note.items[index].text || '').trim();
      if (text === oldText) { effects.render(); return; }
      const oldItem = note.items[index];
      if (!text) app.spliceItems(note, index, 1);
      else app.updateItem(note, index, { text });
      api.patchNote(note.id, { items: note.items }).catch(() => {
        if (!text) app.spliceItems(note, index, 0, oldItem);
        else app.updateItem(note, index, { text: oldText });
        effects.render(); effects.showError('Failed to update item');
      });
      effects.render();
    }
    function addItem(id, text, added) {
      const note = state.notes.find(n => n.id === id);
      if (!note) return;
      const items = Array.isArray(note.items) ? [...note.items] : [];
      items.push({ id: uid(), text, done: false });
      app.mergeNote(note, { items });
      added(); effects.render();
      return () => api.patchNote(id, { items }).catch(() => {
        app.mergeNote(note, { items: items.slice(0, -1) }); effects.render(); effects.showError('Failed to add item');
      });
    }
    function toggleItem(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      const wasDone = note.items.length > 0 && note.items.every(it => it.done);
      app.updateItem(note, index, { done: !note.items[index].done });
      changed(note.items[index].done);
      if (!wasDone && note.items.length > 0 && note.items.every(it => it.done)) completed();
      api.patchNote(id, { items: note.items }).catch(() => {
        app.updateItem(note, index, { done: !note.items[index].done }); changed(note.items[index].done);
      });
    }
    async function toggleTodayStep(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      app.updateItem(note, index, { done: !note.items[index].done }); changed();
      try {
        await api.patchNote(id, { items: note.items }); effects.render();
        if (note.items.every(it => it.done)) completed();
      } catch { app.updateItem(note, index, { done: !note.items[index].done }); }
    }
    return { removeItem, editItem, addItem, toggleItem, toggleTodayStep };
}
