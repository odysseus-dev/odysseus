/**
 * Checklist item workflows. Supplied feedback callbacks update the invoking view.
 * A note open in the note window is written to from a card too: `effects.beforeWrite(id)`
 * lets the window finish its pending save first, so the two writes never race, and
 * `effects.followed(id)` runs once the card's write has landed, so the window takes it.
 * Every write names the version or the task it was made against: a tick sends the
 * task's text and the note's `body_revision`, an edit or removal sends `body_revision`
 * and `base_updated_at`. Writes to one note go out one at a time, each with the version
 * the previous one left. When the server says the note changed (409), the card takes
 * the server copy instead of overwriting it.
 */
import { applyItemsToContent, appendTaskLine, toggleTask as toggleTaskLine } from '../domain/markdownTasks.js';
import { sizeRefusal } from '../domain/noteRules.js';

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
    // A failed write puts the card back only while it still shows what that
    // write drew: a newer copy that arrived meanwhile is never undone.
    const stillShows = (note, content) => state.notes.includes(note) && note.content === content;
    // One write per note at a time, so the next one sends the version the
    // last one left instead of meeting its own 409.
    const queues = new Map();
    function queued(id, call) {
      const previous = queues.get(id) || Promise.resolve();
      const run = previous.then(call);
      const tail = run.catch(() => {});
      queues.set(id, tail);
      tail.then(() => { if (queues.get(id) === tail) queues.delete(id); });
      return run;
    }
    // Straight away unless the window has a save to finish, so requests keep their order.
    const write = (id, call) => queued(id, () => {
      const pending = effects.beforeWrite?.(id);
      return pending ? Promise.resolve(pending).catch(() => {}).then(call) : call();
    });
    // The tokens a server copy carries, for every merge into state.
    const tokens = server => {
      const out = {};
      if (server?.body_revision !== undefined) out.body_revision = server.body_revision;
      if (server?.updated_at !== undefined) out.updated_at = server.updated_at;
      return out;
    };
    /**
     * The server's copy after a toggle or an append: it also carries whatever the window saved just before.
     * The card was changed in place; when the server's copy differs from it, redraw, since the
     * next poll compares against state and would not see anything new.
     */
    function landed(note, result) {
      if (result?.note && typeof result.note.content === 'string') {
        const shown = note.content;
        app.mergeNote(note, { content: result.note.content, items: result.note.items, ...tokens(result.note) });
        if (result.note.content !== shown) effects.render();
      }
      effects.followed?.(note.id);
    }
    function setItems(note, items) {
      app.mergeNote(note, { items, content: applyItemsToContent(note.content, items) });
    }
    function toggledContent(content, index) {
      try { return toggleTaskLine(content || '', index).content; } catch { return content; }
    }
    /** The newer note behind a 409 (carried, or read again when it is not), or null for any other failure. */
    async function newerCopy(error, id) {
      if (error?.status !== 409) return null;
      const carried = error.body?.detail?.note;
      if (carried && typeof carried === 'object') return carried;
      try { return await api.getNote(id); } catch { return null; }
    }
    function takeServerCopy(note, server) {
      app.mergeNote(note, { content: server.content, items: server.items, title: server.title, ...tokens(server) });
    }
    const sameText = (a, b) => String(a || '').trim().split(/\s+/).join(' ') === String(b || '').trim().split(/\s+/).join(' ');
    // A whole-checklist PUT names both versions the card was drawn from.
    const versionOf = source => {
      const out = {};
      if (source?.body_revision) out.body_revision = source.body_revision;
      if (source?.updated_at) out.base_updated_at = source.updated_at;
      return out;
    };
    /**
     * Rewrite the checklist with `change(items, index)` against the version the
     * card showed. If someone else changed the note first, redo the change once
     * on the server copy, locating the item again by its text; if it is gone or
     * ambiguous there, keep the server copy and report it.
     */
    function rewriteItems(note, index, change, failure) {
      // What a failure puts back, and what the card must still show for that.
      let fallback = snapshot(note);
      const oldText = note.items[index].text;
      const send = (items, version) => api.patchNote(note.id, { items, ...version });
      setItems(note, change(note.items, index));
      let drawn = note.content;
      effects.render();
      // The version is read when the request goes out: an earlier card write
      // to this note has landed by then and moved it on.
      return write(note.id, () => send(note.items, versionOf(note))).then(saved => {
        if (saved && typeof saved === 'object') app.mergeNote(note, tokens(saved));
        effects.followed?.(note.id);
      }, async error => {
        const server = await newerCopy(error, note.id);
        if (!server) throw error;
        takeServerCopy(note, server);
        fallback = snapshot(note);
        const items = Array.isArray(server.items) ? server.items : [];
        const matches = items.map((it, i) => (sameText(it.text, oldText) ? i : -1)).filter(i => i >= 0);
        if (matches.length !== 1) { effects.render(); effects.showError('This checklist changed elsewhere. It has been refreshed.'); return; }
        const retried = change(items, matches[0]);
        setItems(note, retried);
        drawn = note.content;
        effects.render();
        const saved = await send(retried, versionOf(server));
        if (saved && typeof saved === 'object') app.mergeNote(note, tokens(saved));
        effects.followed?.(note.id);
      }).catch(async error => {
        const server = await newerCopy(error, note.id);
        if (server) takeServerCopy(note, server);
        else if (stillShows(note, drawn)) app.mergeNote(note, fallback);
        effects.render(); effects.showError(sizeRefusal(error) || failure);
      });
    }
    function removeItem(id, index) {
      const note = itemNote(id, index);
      if (!note) return;
      return rewriteItems(note, index, (items, at) => items.filter((_, i) => i !== at), 'Failed to remove item');
    }
    function editItem(note, index, text) {
      const oldText = (note.items[index].text || '').trim();
      if (text === oldText) { effects.render(); return; }
      return rewriteItems(note, index, (items, at) => (text ? items.map((it, i) => (i === at ? { ...it, text } : it)) : items.filter((_, i) => i !== at)), 'Failed to update item');
    }
    function addItem(id, text, added) {
      const note = state.notes.find(n => n.id === id);
      if (!note) return;
      const before = snapshot(note);
      const items = Array.isArray(note.items) ? [...note.items] : [];
      items.push({ id: uid(), text, done: false });
      app.mergeNote(note, { items, content: appendTaskLine(note.content, text).content });
      const drawn = note.content;
      added(); effects.render();
      // Server-side append: no whole-checklist PUT that could overwrite a
      // concurrent change to the same note.
      return () => write(id, () => api.appendTask(id, text)).then(result => landed(note, result), error => {
        if (stillShows(note, drawn)) app.mergeNote(note, before);
        effects.render(); effects.showError(sizeRefusal(error) || 'Failed to add item');
      });
    }
    function toggleItem(id, index, changed, completed) {
      const note = itemNote(id, index);
      if (!note) return;
      const wasDone = note.items.length > 0 && note.items.every(it => it.done);
      const before = note.content;
      app.updateItem(note, index, { done: !note.items[index].done });
      app.mergeNote(note, { content: toggledContent(before, index) });
      const drawn = note.content;
      changed(note.items[index].done);
      if (!wasDone && note.items.length > 0 && note.items.every(it => it.done)) completed();
      // The card is updated in place, with no redraw: tell the note window,
      // which would otherwise save over this version and get a 409.
      const text = note.items[index].text;
      const done = !!note.items[index].done;
      return write(id, () => api.toggleTask(id, index, text, done, note.body_revision)).then(result => landed(note, result), async error => {
        const server = await newerCopy(error, id);
        if (server) {
          // The task moved or went away elsewhere: show the list as it is now.
          takeServerCopy(note, server);
          effects.render(); effects.showError('This checklist changed elsewhere. It has been refreshed.');
          return;
        }
        // A newer copy merged while the tick was out stays as it is.
        if (!stillShows(note, drawn)) { effects.render(); return; }
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
      const drawn = note.content;
      const text = note.items[index].text;
      const done = !!note.items[index].done;
      try {
        const result = await write(id, () => api.toggleTask(id, index, text, done, note.body_revision));
        if (result?.note && typeof result.note.content === 'string') {
          app.mergeNote(note, { content: result.note.content, items: result.note.items, ...tokens(result.note) });
        }
        effects.render();
        if (note.items.every(it => it.done)) completed();
      } catch (error) {
        const server = await newerCopy(error, id);
        if (server) {
          takeServerCopy(note, server);
          effects.render(); effects.showError('This checklist changed elsewhere. It has been refreshed.');
          return;
        }
        if (!stillShows(note, drawn)) { effects.render(); return; }
        app.mergeNote(note, { content: before });
        app.updateItem(note, index, { done: !note.items[index].done });
      }
    }
    return { removeItem, editItem, addItem, toggleItem, toggleTodayStep };
}
