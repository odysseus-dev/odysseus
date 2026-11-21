/** One open note's edit session: dirty flag, debounced autosave, stale-write conflicts (by `body_revision`) and the local draft backup. The view supplies collect() and reacts through effects. */
import { itemsToMarkdown } from '../domain/markdownTasks.js';

const AUTOSAVE_DELAY_MS = 600;
// A new note with nothing but empty checkboxes in it is still empty.
const isBlank = text => !String(text || '').replace(/^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]*$/gm, '').trim();

export function createNoteSession({
  api,
  persistence,
  scheduler,
  clock,
  note = null,
  collect,
  meta = {},
  firstWrite = () => ({}),
  delay = AUTOSAVE_DELAY_MS,
  effects = {},
}) {
  let id = note?.id || null;
  // The body revision this session last saw. Every content save and every
  // checkbox flip sends it; the server answers 409 when the body moved on.
  let base = note?.body_revision || null;
  // This window's drafts, told apart from another tab's on the same note.
  const writer = `${clock.now()}-${Math.random().toString(36).slice(2)}`;
  let saved = { title: note?.title || '', content: note?.content || '' };
  const pendingMeta = { ...meta };
  let dirty = false;
  let timer = null;
  let pending = Promise.resolve();
  let inFlight = 0;
  let conflict = null;
  let wrote = false;
  const key = () => persistence._draftKey(id, writer);

  // Every write goes through one queue, so a colour change can never land
  // between a content save and its response.
  const enqueue = work => {
    inFlight++;
    const run = pending.then(work).finally(() => { inFlight--; });
    pending = run.catch(() => {});
    return run;
  };

  function readDraft() {
    try {
      const own = persistence.getItem(key());
      if (own) return JSON.parse(own);
      return JSON.parse(persistence.getItem(persistence._draftKey(id || '__new__')) || 'null');
    } catch { return null; }
  }

  function draftText(draft) {
    if (!draft || typeof draft !== 'object') return null;
    const content = typeof draft.content === 'string' ? draft.content : (Array.isArray(draft.items) ? itemsToMarkdown(draft.items) : null);
    if (content === null) return null;
    return { title: typeof draft.title === 'string' ? draft.title : saved.title, content };
  }

  function clearSavedBackup(draftKey, record) {
    try {
      if (persistence.getItem(draftKey) === record) persistence.removeItem(draftKey);
    } catch { effects.backupUnavailable?.(); }
  }

  // The draft remembers the body revision it was typed against. A draft left
  // by another tab is kept for recovery rather than overwritten.
  function backup(draft = collect()) {
    const record = JSON.stringify({ _ts: clock.now(), _draftWriter: writer, title: draft.title, content: draft.content, body_revision: base });
    try {
      const previous = readDraft();
      if (previous && typeof previous === 'object' && previous._draftWriter !== writer) {
        const prev = draftText(previous);
        if (prev && (prev.title !== saved.title || prev.content !== saved.content)) {
          persistence._keepDraftForRecovery?.(id || '__new__', previous);
        }
      }
      persistence.setItem(key(), record);
    } catch { effects.backupUnavailable?.(); }
    return record;
  }

  function adopt(serverNote, fallback) {
    if (!serverNote) return;
    id = serverNote.id || id;
    base = serverNote.body_revision ?? base;
    saved = { title: serverNote.title ?? fallback?.title ?? saved.title, content: serverNote.content ?? fallback?.content ?? saved.content };
  }

  async function save() {
    if (!dirty || conflict) return;
    const draft = collect();
    if (draft.title === saved.title && draft.content === saved.content) {
      dirty = false;
      try {
        if (key() !== persistence._draftKey(id || '__new__')) persistence.removeItem(key());
        if (readDraft()?._draftWriter === writer) persistence._clearDraft(id || '__new__', writer);
      } catch {}
      return;
    }
    if (!id && !draft.title.trim() && isBlank(draft.content)) { dirty = false; return; }
    const record = backup(draft);
    const draftKey = key();
    // The first write of a session also carries what the old Save button
    // sent once, such as the sort order that lifts an edited note to the top.
    const extras = wrote ? {} : firstWrite();
    dirty = false;
    try {
      if (!id) {
        const created = await api.saveNote({ ...pendingMeta, ...extras, title: draft.title, content: draft.content });
        wrote = true;
        adopt(created, draft);
        clearSavedBackup(draftKey, record);
        effects.created?.(created);
        return;
      }
      // The window holds the whole document: replace_content writes it as
      // typed (a deleted or moved task stays deleted or moved).
      const body = { ...extras, title: draft.title, content: draft.content, replace_content: true };
      if (base) body.body_revision = base;
      const result = await api.patchNote(id, body);
      wrote = true;
      adopt(result, draft);
      clearSavedBackup(draftKey, record);
      effects.saved?.(result);
    } catch (error) {
      if (error?.status === 409) {
        let server = null;
        try { server = await api.getNote(id); } catch { /* offline: Keep mine reads it again */ }
        conflict = { local: draft, server };
        effects.conflict?.(conflict);
        return;
      }
      dirty = true;
      effects.failed?.(error);
    }
  }

  function flush() {
    scheduler.clearTimeout(timer);
    timer = null;
    if (!dirty || conflict) return pending;
    return enqueue(save);
  }

  /** A real edit happened: back it up now, save it after the debounce. */
  function markDirty() {
    dirty = true;
    backup();
    if (conflict) return;
    scheduler.clearTimeout(timer);
    timer = scheduler.setTimeout(() => { timer = null; enqueue(save); }, delay);
  }

  /** Colour, tags, reminder, archive: never conflict-checked. A new note keeps them for its first save. */
  function patchMeta(patch) {
    if (!id) { Object.assign(pendingMeta, patch); return Promise.resolve(null); }
    return enqueue(async () => {
      const result = await api.patchNote(id, patch);
      // Only move the base on when the content is still what this session
      // last saved; otherwise someone else's edit would be silently accepted.
      if (result && result.content === saved.content && (result.title ?? saved.title) === saved.title) base = result.body_revision ?? base;
      return result;
    });
  }

  /** Checkbox flips go through the toggle endpoint, after any pending text is saved. */
  function toggleTask(resolveIndex) {
    return enqueue(async () => {
      scheduler.clearTimeout(timer);
      timer = null;
      if (dirty && !conflict) await save();
      // A failed save leaves the edit dirty; the view then flips the box as a
      // content edit instead, so the click is not lost.
      if (dirty || conflict || !id) return null;
      const index = resolveIndex();
      if (index == null || Number.isNaN(index)) return null;
      try {
        const result = await api.toggleTask(id, index, base);
        adopt(result?.note);
        return result;
      } catch (error) {
        if (error?.status === 409) {
          let server = null;
          try { server = await api.getNote(id); } catch { /* offline: Keep mine reads it again */ }
          const draft = collect();
          conflict = { local: draft, server };
          effects.conflict?.(conflict);
          return null;
        }
        throw error;
      }
    });
  }

  function resolveConflict(choice) {
    if (!conflict) return pending;
    const activeConflict = conflict;
    const server = activeConflict.server;
    if (choice === 'reload') {
      return enqueue(async () => {
        if (conflict !== activeConflict) return;
        let current = server;
        if (!current) {
          try { current = await api.getNote(id); }
          catch (error) { effects.failed?.(error); return; }
        }
        if (!current || conflict !== activeConflict) return;
        adopt(current);
        dirty = false;
        conflict = null;
        try {
          if (key() !== persistence._draftKey(id || '__new__')) persistence.removeItem(key());
          if (readDraft()?._draftWriter === writer) persistence._clearDraft(id || '__new__', writer);
        } catch {}
        effects.reloaded?.(current);
      });
    }
    conflict = null;
    // Keep mine: write the local text over the newer server copy.
    dirty = true;
    return enqueue(async () => {
      let current = server;
      if (!current) { try { current = await api.getNote(id); } catch { current = null; } }
      if (current) saved = { title: current.title ?? '', content: current.content ?? '' };
      base = current?.body_revision ?? base;
      return save();
    });
  }

  /**
   * An earlier unsaved draft of this note on this device, if it differs from
   * what was loaded. `stale` when the note changed after the draft was typed:
   * restoring it would write over that change, so the view offers it to copy
   * from instead. `recovered` are drafts another tab left behind.
   */
  function storedDraft() {
    const raw = readDraft();
    let draft = draftText(raw);
    if (draft && draft.content === saved.content && draft.title === saved.title) {
      try {
        if (key() !== persistence._draftKey(id || '__new__')) persistence.removeItem(key());
        if (!id || readDraft()?._draftWriter === writer) persistence._clearDraft(id || '__new__', writer);
        if (!raw?._draftWriter) persistence._clearDraft(id || '__new__');
      } catch {}
      draft = null;
    }
    const targetId = id || '__new__';
    const recovered = (persistence._loadDraftRecovery?.(targetId) || [])
      .filter(entry => entry._draftWriter !== writer)
      .map(draftText)
      .filter(Boolean);
    if (!draft && !recovered.length) return null;
    const stale = !!draft && !!id && raw.body_revision !== undefined && raw.body_revision !== base;
    const foreign = !!(raw && raw._draftWriter && raw._draftWriter !== writer);
    return {
      ...(draft || { title: saved.title, content: null }),
      stale,
      recovered,
      ...(foreign ? { foreign: true } : {}),
    };
  }

  /** Stop without saving: the note is being deleted. */
  function abandon() {
    scheduler.clearTimeout(timer);
    timer = null;
    dirty = false;
    conflict = null;
    try {
      if (key() !== persistence._draftKey(id || '__new__')) persistence.removeItem(key());
      if (id || readDraft()?._draftWriter === writer) persistence._clearDraft(id || '__new__', writer);
    } catch {}
  }

  return {
    markDirty,
    abandon,
    /** A same-tab write elsewhere (a card checkbox) moved the note on. */
    rebase: revision => { base = revision || base; },
    /** Take a newer server copy while nothing local is pending. */
    replace: serverNote => { if (!dirty && inFlight === 0) adopt(serverNote); },
    flush,
    patchMeta,
    toggleTask,
    resolveConflict,
    storedDraft,
    dismissDraft: () => {},
    discardDraft: (options = {}) => {
      try {
        if (key() !== persistence._draftKey(id || '__new__')) persistence.removeItem(key());
        const raw = readDraft();
        const isForeign = !!(raw?._draftWriter && raw._draftWriter !== writer);
        if (!isForeign || options?.includeForeign) {
          if (id || raw?._draftWriter === writer || !raw?._draftWriter) {
            persistence._clearDraft(id || '__new__', writer);
          }
          if (!raw?._draftWriter || options?.includeForeign) {
            persistence._clearDraft(id || '__new__');
          }
        }
        if (!id || id === '__new__') {
          if (options?.includeForeign) {
            persistence._clearDraftRecovery?.('__new__');
          } else {
            persistence._clearDraftRecovery?.('__new__', writer);
          }
        } else {
          if (!isForeign || options?.includeForeign) {
            persistence._clearDraftRecovery?.(id);
          }
        }
      } catch {}
    },
    close: () => flush(),
    get id() { return id; },
    get base() { return base; },
    get saved() { return { ...saved }; },
    get dirty() { return dirty; },
    get saving() { return inFlight > 0; },
    get conflict() { return conflict; },
    get meta() { return { ...pendingMeta }; },
  };
}
