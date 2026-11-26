/**
 * One open note's edit session: dirty flag, debounced autosave, stale-write conflicts and the local draft backup. The view supplies collect() and reacts through effects.
 * A write names the version it was made on twice: `body_revision` (the body) and `base_updated_at` (the whole note). The server answers 409 when either moved on.
 */
import { itemsToMarkdown, parseTaskLines, toggleTask as toggleTaskLine } from '../domain/markdownTasks.js';
import { joinSegments, splitMarkdownBlocks } from '../domain/markdownSerializer.js';

export const AUTOSAVE_DELAY_MS = 600;
// The first edit is backed up at once; the edits that follow within this
// window are backed up together, so a keystroke in a long note does not
// serialize the whole document every time.
export const DRAFT_BACKUP_INTERVAL_MS = 300;
// A new note with nothing but empty checkboxes in it is still empty.
const isBlank = text => !String(text || '').replace(/^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]*$/gm, '').trim();
// The text a window shows for a stored note it has not edited: the editor
// reads CRLF as LF and drops blank lines at either end and runs of them
// between blocks. Only for comparing; nothing is saved from it.
const asShown = text => joinSegments(splitMarkdownBlocks(text).map((block, group) => ({ group, source: block.source })));

/** The task indices whose box differs between two versions of a note, when that is all that differs; null otherwise. */
export function taskFlips(before, after) {
  const was = parseTaskLines(before);
  const now = parseTaskLines(after);
  if (was.length !== now.length) return null;
  let content = before || '';
  const flips = [];
  now.forEach((item, index) => {
    if (item.done === was[index].done) return;
    flips.push(index);
    content = toggleTaskLine(content, index).content;
  });
  return content === (after || '') ? flips : null;
}

export function createNoteSession({
  api,
  persistence,
  scheduler,
  clock,
  note = null,
  collect,
  meta = {},
  firstWrite = () => ({}),
  // A note shared with this account as view only: nothing is saved.
  readOnly = () => false,
  delay = AUTOSAVE_DELAY_MS,
  effects = {},
}) {
  let id = note?.id || null;
  // The version this session last saw: `base` is the note's updated_at,
  // `revision` its body_revision. Every content save sends both, a checkbox
  // flip sends the revision.
  let base = note?.updated_at || null;
  let revision = note?.body_revision || null;
  // This window's drafts, told apart from another tab's on the same note.
  const writer = `${clock.now()}-${Math.random().toString(36).slice(2)}`;
  let saved = { title: note?.title || '', content: note?.content || '' };
  // `saved` is the title and text of version `base`. Not so after Restore
  // (the draft's older base) or Keep mine (the newer copy's base).
  let savedIsBase = true;
  const pendingMeta = { ...meta };
  let dirty = false;
  let timer = null;
  let backupTimer = null;
  let backupPending = false;
  let pending = Promise.resolve();
  let inFlight = 0;
  let conflict = null;
  // The server answered 404: the note was deleted, its owner's account was
  // removed, or it is no longer shared with this account. Nothing more is
  // written, and this window's draft goes too: no note will ever offer it back.
  let gone = false;
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

  // collect() may return `unsaved`: the note with an edit the editor could
  // not write (it kept the block's stored text in `content`). That edit is
  // what the draft holds, and it stays on this device until it is gone.
  let holding = false;
  function hold(draft) {
    const now = draft.unsaved != null;
    if (now !== holding) { holding = now; effects.unsaved?.(now); }
    return now;
  }

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

  // Only this window's draft: one another tab left on the same note stays.
  function clearOwnDraft() {
    try {
      if (key() !== persistence._draftKey(id || '__new__')) persistence.removeItem(key());
      if (readDraft()?._draftWriter === writer) persistence._clearDraft(id || '__new__', writer);
    } catch {}
  }

  // The draft remembers the version it was typed against. A draft left by
  // another tab is kept for recovery rather than overwritten.
  function backup(draft = collect()) {
    const record = JSON.stringify({ _ts: clock.now(), _draftWriter: writer, title: draft.title, content: draft.unsaved ?? draft.content, body_revision: revision, base_updated_at: base });
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

  function flushBackup() {
    scheduler.clearTimeout(backupTimer);
    backupTimer = null;
    if (!backupPending) return;
    backupPending = false;
    if (dirty && !gone && !readOnly()) backup();
  }

  function scheduleBackup() {
    if (backupTimer !== null) { backupPending = true; return; }
    backup();
    backupTimer = scheduler.setTimeout(flushBackup, DRAFT_BACKUP_INTERVAL_MS);
  }

  function markGone() {
    if (gone) return;
    gone = true;
    dirty = false;
    scheduler.clearTimeout(timer);
    timer = null;
    scheduler.clearTimeout(backupTimer);
    backupTimer = null;
    backupPending = false;
    clearOwnDraft();
    effects.gone?.();
  }

  function adopt(serverNote, fallback) {
    if (!serverNote) return;
    id = serverNote.id || id;
    base = serverNote.updated_at ?? base;
    revision = serverNote.body_revision ?? revision;
    savedIsBase = true;
    saved = { title: serverNote.title ?? fallback?.title ?? saved.title, content: serverNote.content ?? fallback?.content ?? saved.content };
  }

  // The newer copy a 409 carries, or the note read again when it carries none.
  async function serverCopy(error) {
    const carried = error?.body?.detail?.note;
    if (carried && typeof carried === 'object') return carried;
    try { return await api.getNote(id); } catch { return null; /* offline: Keep mine reads it again */ }
  }

  // The note now has an id, but a held edit, or typing done while the
  // create request was out, was backed up under the new-note key. Left
  // there, the next new note would offer it back and Restore would create
  // the note a second time; it moves to this note, on the version just made.
  function settleNewDraft(draftKey, record, held) {
    let left = null;
    try { left = persistence.getItem(draftKey); persistence.removeItem(draftKey); } catch { effects.backupUnavailable?.(); return; }
    if (left == null || (left === record && !held)) return;
    let moved = null;
    try { moved = JSON.parse(left); } catch { return; }
    if (!moved || typeof moved !== 'object') return;
    try { persistence.setItem(key(), JSON.stringify({ ...moved, body_revision: revision, base_updated_at: base })); }
    catch { effects.backupUnavailable?.(); }
  }

  async function save(rebased = false) {
    if (readOnly()) { dirty = false; return; }
    if (!dirty || conflict || gone) return;
    const draft = collect();
    const held = hold(draft);
    if (draft.title === saved.title && draft.content === saved.content) {
      dirty = false;
      if (held) backup(draft);
      else clearOwnDraft();
      return;
    }
    if (!id && !draft.title.trim() && isBlank(draft.content)) { dirty = false; return; }
    backupPending = false;
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
        settleNewDraft(draftKey, record, held);
        effects.created?.(created);
        return;
      }
      // The window holds the whole document: replace_content writes it as
      // typed (a deleted or moved task stays deleted or moved).
      const body = { ...extras, title: draft.title, content: draft.content, replace_content: true };
      if (revision) body.body_revision = revision;
      if (base) body.base_updated_at = base;
      const result = await api.patchNote(id, body);
      wrote = true;
      adopt(result, draft);
      if (!held) clearSavedBackup(draftKey, record);
      effects.saved?.(result);
    } catch (error) {
      if (error?.status === 409) {
        const server = await serverCopy(error);
        // Only the colour, tag, pin or another field outside the text moved
        // on: the body this edit started from is still the stored one, so
        // writing it on the newer version overwrites nothing. Once.
        const bodyUnchanged = server && (server.body_revision && revision
          ? server.body_revision === revision
          : server.title === saved.title && server.content === saved.content);
        if (!rebased && savedIsBase && bodyUnchanged && server.updated_at && server.updated_at !== base) {
          base = server.updated_at;
          revision = server.body_revision ?? revision;
          dirty = true;
          return save(true);
        }
        conflict = { local: draft, server };
        effects.conflict?.(conflict);
        return;
      }
      if (error?.status === 404 && id) { markGone(); return; }
      // The window never sends an owner-only field for a collaborator, so a
      // 403 on a text save means the note is now view only for this account.
      // Retrying cannot help; the draft stays on this device.
      if (error?.status === 403 && id) { effects.viewOnly?.(error); return; }
      dirty = true;
      effects.failed?.(error);
    }
  }

  function flush() {
    flushBackup();
    scheduler.clearTimeout(timer);
    timer = null;
    if (!dirty || conflict || gone) return pending;
    return enqueue(save);
  }

  /** A real edit happened: back it up, save it after the debounce. */
  function markDirty() {
    if (readOnly() || gone) return;
    dirty = true;
    scheduleBackup();
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
      if (result && result.content === saved.content && (result.title ?? saved.title) === saved.title) {
        base = result.updated_at ?? base;
        revision = result.body_revision ?? revision;
      }
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
      if (dirty || conflict || gone || !id) return null;
      const index = resolveIndex();
      if (index == null || Number.isNaN(index)) return null;
      // The text of the task the box shows, so a tick can't land on another
      // task if lines were added or removed elsewhere since the last save.
      const task = parseTaskLines(saved.content)[index];
      const text = task?.text;
      // The state the box is going to, so a tick made elsewhere since the
      // last save is kept instead of undone.
      const done = task ? !task.done : undefined;
      // What the server should hold once this tick lands, if nothing else
      // was written since the last save.
      const before = { title: saved.title, content: saved.content };
      let result;
      try { result = await api.toggleTask(id, index, text, done, revision); }
      catch (error) {
        if (error?.status === 404) markGone();
        if (error?.status === 409) {
          // The note changed elsewhere: Reload or Keep mine, as for a save.
          // The base stays where it was, so nothing typed meanwhile is
          // written over the newer copy unseen.
          const server = await serverCopy(error);
          conflict = { local: collect(), server };
          effects.conflict?.(conflict);
          return null;
        }
        throw error;
      }
      // Text typed while the tick was out was typed on the copy the window
      // shows. If the answer holds more than this tick (a collaborator's
      // edit not received yet), the window never showed it: moving the base
      // there would let the next save write over it unseen. The base stays,
      // so that save meets the newer copy and asks Keep mine or Reload.
      if (dirty && !tickOnly(before, index, result?.note)) return result;
      adopt(result?.note);
      return result;
    });
  }

  function tickOnly(before, index, note) {
    if (!note || (note.title ?? before.title) !== before.title) return false;
    try { return note.content === toggleTaskLine(before.content, index).content; }
    catch { return false; }
  }

  function resolveConflict(choice) {
    if (!conflict) return pending;
    const activeConflict = conflict;
    const server = activeConflict.server;
    if (choice === 'reload') {
      // The conflict, and the draft, stay until a server copy is in hand.
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
        clearOwnDraft();
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
      base = current?.updated_at ?? base;
      revision = current?.body_revision ?? revision;
      savedIsBase = false;
      return save();
    });
  }

  /**
   * An earlier unsaved draft of this note on this device, if it differs from
   * what was loaded. `stale` when the note's body changed after the draft was
   * typed: restoring it would write over that change, so the view offers it
   * to copy from instead. `recovered` are drafts another tab left behind,
   * `foreign` marks a draft another tab is still writing.
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
    const stale = !!draft && !!id && raw.body_revision != null && raw.body_revision !== revision;
    const foreign = !!(raw && raw._draftWriter && raw._draftWriter !== writer);
    return {
      ...(draft || { title: saved.title, content: null }),
      stale,
      recovered,
      ...(foreign ? { foreign: true } : {}),
    };
  }

  /**
   * The person chose Restore: the draft's text is saved against the version
   * it was typed on, not the one just loaded, so a newer copy written since
   * meets the conflict choice instead of being overwritten. Call it before
   * the restored text is put in the window, whose edit backs up the draft
   * again. A draft with no recorded version keeps the loaded one.
   */
  function restoreDraft() {
    const draft = readDraft();
    if (typeof draft?.base_updated_at === 'string' && draft.base_updated_at) { base = draft.base_updated_at; savedIsBase = false; }
    if (typeof draft?.body_revision === 'string' && draft.body_revision) { revision = draft.body_revision; savedIsBase = false; }
  }

  /** Stop without saving: the note is being deleted. */
  function abandon() {
    scheduler.clearTimeout(timer);
    timer = null;
    scheduler.clearTimeout(backupTimer);
    backupTimer = null;
    backupPending = false;
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
    /**
     * A same-tab write elsewhere (a card tick, a card item edit) landed while
     * the window is open. `current` is what the window shows now. Returns
     * 'base' when that write left the text as saved, 'take' when the window
     * has nothing unsaved and adopts it (the view then redraws), and 'keep'
     * when it has: the base stays, so the next save meets the conflict choice
     * instead of writing the old text over the newer version.
     */
    follow(serverNote, current) {
      if (!serverNote?.updated_at && !serverNote?.body_revision) return 'keep';
      const title = serverNote.title ?? saved.title;
      const content = serverNote.content ?? saved.content;
      if (content === saved.content && title === saved.title) {
        base = serverNote.updated_at ?? base;
        revision = serverNote.body_revision ?? revision;
        return 'base';
      }
      if (dirty || inFlight > 0 || conflict || gone || holding || current?.title !== saved.title || typeof current?.content !== 'string' || asShown(current.content) !== asShown(saved.content)) return 'keep';
      adopt(serverNote);
      return 'take';
    },
    /** Take a newer server copy while nothing local is pending or in flight. */
    replace: serverNote => { if (!dirty && inFlight === 0) adopt(serverNote); },
    flush,
    /** The page is going away: write a pending draft backup now. */
    flushBackup,
    patchMeta,
    toggleTask,
    resolveConflict,
    storedDraft,
    restoreDraft,
    /** Hide the offer for now; the draft stays for the next open. */
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
        } else if (!isForeign || options?.includeForeign) {
          persistence._clearDraftRecovery?.(id);
        }
      } catch {}
    },
    close: () => flush(),
    get id() { return id; },
    get base() { return base; },
    get revision() { return revision; },
    get saved() { return { ...saved }; },
    get dirty() { return dirty; },
    get saving() { return inFlight > 0; },
    get conflict() { return conflict; },
    get gone() { return gone; },
    get meta() { return { ...pendingMeta }; },
  };
}
