/** Serialized autosave and synchronous local draft backup. DOM collection is a supplied port. */
export function createNotesDrafts({
  app,
  api,
  persistence,
  scheduler,
  effects
}) {
  const state = app.state;
  const { _draftKey, _loadDraft, _clearDraft, _keepDraftForRecovery } = persistence;
  function bindDraft({ collect, id, view }) {
    const key = _draftKey(id);
    const writer = crypto.getRandomValues(new Uint32Array(4)).join('-');
    let t = null;
    let pending = Promise.resolve();
    let stopped = false;
    let warned = false;
    let sequence = 0;
    let latest = null;
    function warnBackup() {
      if (!warned) effects.showError('Draft backup unavailable. Keep this note open until saved.');
      warned = true;
    }
    const save = () => {
      const d = { ...collect(), _draftWriter: writer };
      const capture = { sequence: ++sequence, data: d };
      // An empty edit can be intentional: don't restore stale server text over it.
      try {
        const previous = _loadDraft(id);
        if (previous && previous._draftWriter !== writer && previous.body_revision !== d.body_revision) {
          _keepDraftForRecovery(id, previous);
        }
        persistence.setItem(key, JSON.stringify(d));
        latest = capture;
      } catch { warnBackup(); }
      return capture;
    };
    function acknowledge(submitted, revision, savedRevision) {
      if (view.bodyRevision !== revision) return;
      view.bodyRevision = savedRevision;
      try {
        // A different form or tab may have replaced the draft during the save.
        if (!latest || persistence.getItem(key) !== JSON.stringify(latest.data)) return;
        if (latest === submitted) {
          _clearDraft(id);
          latest = null;
        } else if (latest.sequence > submitted.sequence && latest.data.body_revision === revision) {
          const rebased = { ...latest.data, body_revision: savedRevision };
          persistence.setItem(key, JSON.stringify(rebased));
          latest.data = rebased;
        }
      } catch { warnBackup(); }
    }
    const persist = (capture) => {
      const d = capture.data;
      if (!id || id === '__new__' || d.note_type === 'draw') return pending;
      const { _ts, _draftWriter, ...payload } = d;
      payload.label = [...new Set(payload.label.split(/\s+/).map(s => s.replace(/^#+/, '')).filter(Boolean))].join(' ') || null;
      pending = pending.then(async () => {
        const revision = view.bodyRevision;
        payload.body_revision = revision;
        try {
          const saved = await api.patchNote(id, payload);
          acknowledge(capture, revision, saved.body_revision);
          view.failed = false;
          const note = state.notes.find(n => n.id === id);
          if (note && note.body_revision === revision) app.mergeNote(note, saved);
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
    return { flush, stop, schedule: sched, acknowledge };
  }
  return { bindDraft };
}
