/** Checklist item workflows. Supplied feedback callbacks update the invoking view. */
import { applyItemsToContent, appendTaskLine, toggleTask as toggleTaskLine } from '../domain/markdownTasks.js';

export function createChecklistOperations({
  app,
  api,
  effects,
  uid
}) {
    const state = app.state;
    // Items are task lines inside `content`. Every optimistic change updates
    // both, using the same rules the server applies, so the card (which renders
    // content) and the item indices stay in step until the next fetch.
    function itemNote(id, index) {
      const note = state.notes.find(n => n.id === id);
      return note && Array.isArray(note.items) && note.items[index] ? note : null;
    }
    const snapshot = note => ({ content: note.content, items: note.items });
    function setItems(note, items) {
      app.mergeNote(note, { items, content: applyItemsToContent(note.content, items) });
    }
    function toggledContent(content, index) {
      try { return toggleTaskLine(content || '', index).content; } catch { return content; }
    }
    function removeItem(id, index) {
      const note = itemNote(id, index);
      if (!note) return;
      const before = snapshot(note);
      setItems(note, note.items.filter((_, i) => i !== index));
      effects.render();
      api.patchNote(id, { items: note.items }).catch(() => {
        app.mergeNote(note, before); effects.render(); effects.showError('Failed to remove item');
      });
    }
    function editItem(note, index, text) {
      const oldText = (note.items[index].text || '').trim();
      if (text === oldText) { effects.render(); return; }
      const before = snapshot(note);
      setItems(note, text ? note.items.map((it, i) => (i === index ? { ...it, text } : it)) : note.items.filter((_, i) => i !== index));
      api.patchNote(note.id, { items: note.items }).catch(() => {
        app.mergeNote(note, before);
        effects.render(); effects.showError('Failed to update item');
      });
      effects.render();
    }
    function addItem(id, text, added) {
      const note = state.notes.find(n => n.id === id);
      if (!note) return;
      const before = snapshot(note);
      const items = Array.isArray(note.items) ? [...note.items] : [];
      items.push({ id: uid(), text, done: false });
      app.mergeNote(note, { items, content: appendTaskLine(note.content, text).content });
      added(); effects.render();
      // Server-side append: no whole-checklist PUT that could overwrite a
      // concurrent change to the same note.
      return () => api.appendTask(id, text).catch(() => {
        app.mergeNote(note, before); effects.render(); effects.showError('Failed to add item');
      });
    }
    function toggleItem(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      const wasDone = note.items.length > 0 && note.items.every(it => it.done);
      const before = note.content;
      app.updateItem(note, index, { done: !note.items[index].done });
      app.mergeNote(note, { content: toggledContent(before, index) });
      changed(note.items[index].done);
      if (!wasDone && note.items.length > 0 && note.items.every(it => it.done)) completed();
      api.toggleTask(id, index).catch(() => {
        app.mergeNote(note, { content: before });
        app.updateItem(note, index, { done: !note.items[index].done }); changed(note.items[index].done);
      });
    }
    async function toggleTodayStep(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      const before = note.content;
      app.updateItem(note, index, { done: !note.items[index].done });
      app.mergeNote(note, { content: toggledContent(before, index) }); changed();
      try {
        await api.toggleTask(id, index); effects.render();
        if (note.items.every(it => it.done)) completed();
      } catch {
        app.mergeNote(note, { content: before });
        app.updateItem(note, index, { done: !note.items[index].done });
      }
    }
    return { removeItem, editItem, addItem, toggleItem, toggleTodayStep };
}
