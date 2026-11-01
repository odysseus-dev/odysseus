/** Serialized autosave and synchronous local draft backup. DOM collection is a supplied port. */
export function createNotesDrafts({
  app,
  api,
  persistence,
  scheduler,
  effects
}) {
  const state = app.state;
  const { _draftKey, _clearDraft } = persistence;
  function bindDraft({ collect, id, view }) {
    let t = null;
    let pending = Promise.resolve();
    let stopped = false;
    let warned = false;
    const save = () => {
      const d = collect();
      // An empty edit can be intentional: don't restore stale server text over it.
      try { persistence.setItem(_draftKey(id), JSON.stringify(d)); }
      catch {
        if (!warned) effects.showError('Draft backup unavailable. Keep this note open until saved.');
        warned = true;
      }
      return d;
    };
    const persist = (d) => {
      if (!id || id === '__new__' || d.note_type === 'draw') return pending;
      const { _ts, ...payload } = d;
      payload.label = [...new Set(payload.label.split(/\s+/).map(s => s.replace(/^#+/, '')).filter(Boolean))].join(' ') || null;
      pending = pending.then(async () => {
        try {
          await api.patchNote(id, payload);
          view.failed = false;
          const note = state.notes.find(n => n.id === id);
          if (note) app.mergeNote(note, payload);
          if (persistence.getItem(_draftKey(id)) === JSON.stringify(d)) _clearDraft(id);
        } catch {
          if (!view.failed) effects.showError('Note not synced. Your edits are kept on this device; retry Save.');
          view.failed = true;
        }
      });
      return pending;
    };
    const flush = () => { scheduler.clearTimeout(t); return save(); };
    const stop = () => { stopped = true; scheduler.clearTimeout(t); return pending; };
    const sched = () => {
      const d = save();
      if (stopped) return;
      scheduler.clearTimeout(t);
      t = scheduler.setTimeout(() => persist(d), 500);
    };
    return { flush, stop, schedule: sched };
  }
  return { bindDraft };
}
