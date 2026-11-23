/** Browser persistence and unchanged Notes key/serialization contracts. */
export const REMINDER_FIRED_KEY = 'odysseus-notes-reminder-fired';
export const REMINDER_GLOWED_KEY = 'odysseus-notes-reminder-glowed';
export const REMINDER_PENDING_HIGHLIGHT_KEY = 'odysseus-notes-reminder-pending-highlight';
export const REMINDER_ACTIVE_HIGHLIGHT_KEY = 'odysseus-notes-reminder-active-highlight';
export const REMINDER_DISMISSED_AT_KEY = 'odysseus-notes-reminder-dismissed-at';
export const NOTES_FIRST_OPEN_HINT_KEY = 'odysseus-notes-first-open-hint-v1';
export const NOTES_LABELS_COLLAPSED_KEY = 'odysseus-notes-labels-collapsed';
export const _DRAFT_PREFIX = 'odysseus-note-draft-';

export function createNotesPersistence({ storage: localStorage }) {
function _loadPendingHighlights() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_PENDING_HIGHLIGHT_KEY) || '[]')); }
  catch { return new Set(); }
}

function _loadGlowedReminders() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_GLOWED_KEY) || '[]')); }
  catch { return new Set(); }
}

function _saveGlowedReminders(set) {
  try { localStorage.setItem(REMINDER_GLOWED_KEY, JSON.stringify([...set])); } catch {}
}

function _loadActiveHighlights() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_ACTIVE_HIGHLIGHT_KEY) || '[]')); }
  catch { return new Set(); }
}

function _saveActiveHighlights(set) {
  try { localStorage.setItem(REMINDER_ACTIVE_HIGHLIGHT_KEY, JSON.stringify([...set])); } catch {}
}

function _savePendingHighlights(set) {
  try { localStorage.setItem(REMINDER_PENDING_HIGHLIGHT_KEY, JSON.stringify([...set])); }
  catch {}
}

function _loadFiredReminders() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_FIRED_KEY) || '[]')); }
  catch { return new Set(); }
}

function _saveFiredReminders(set) {
  try { localStorage.setItem(REMINDER_FIRED_KEY, JSON.stringify([...set])); }
  catch {}
}

function _draftKey(id, writer) {
  if (!id || id === '__new__') return _DRAFT_PREFIX + (writer ? `__new__-${writer}` : '__new__');
  return _DRAFT_PREFIX + id;
}

function _loadDraft(id, writer) {
  if (id && id !== '__new__') {
    try { return JSON.parse(localStorage.getItem(_draftKey(id)) || 'null'); } catch { return null; }
  }
  if (writer) {
    try {
      const own = localStorage.getItem(_draftKey('__new__', writer));
      if (own) return JSON.parse(own);
    } catch {}
  }
  try {
    const standard = localStorage.getItem(_draftKey(null));
    if (standard) return JSON.parse(standard);
  } catch {}
  try {
    if (typeof localStorage.length === 'number' && typeof localStorage.key === 'function') {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(_DRAFT_PREFIX + '__new__-')) {
          const val = localStorage.getItem(k);
          if (val) return JSON.parse(val);
        }
      }
    }
  } catch {}
  return null;
}

function _clearDraft(id, writer) {
  try {
    if (!id || id === '__new__') {
      if (writer) {
        localStorage.removeItem(_draftKey('__new__', writer));
        return;
      }
      localStorage.removeItem(_draftKey(null));
      return;
    }
    if (writer) {
      const raw = localStorage.getItem(_draftKey(id));
      if (raw) {
        try {
          const parsed = JSON.parse(raw);
          if (parsed && parsed._draftWriter && parsed._draftWriter !== writer) {
            return;
          }
        } catch {}
      }
    }
    localStorage.removeItem(_draftKey(id));
  } catch {}
}

function _draftRecoveryKey(id) { return 'odysseus-note-recovery-' + (id && id !== '__new__' ? id : '__new__'); }

function _loadDraftRecovery(id) {
  const targetId = id && id !== '__new__' ? id : '__new__';
  const list = [];
  try {
    const drafts = JSON.parse(localStorage.getItem(_draftRecoveryKey(targetId)) || '[]');
    if (Array.isArray(drafts)) list.push(...drafts);
  } catch {}
  if (!id || id === '__new__') {
    try {
      if (typeof localStorage.length === 'number' && typeof localStorage.key === 'function') {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.startsWith(_DRAFT_PREFIX + '__new__-')) {
            const val = localStorage.getItem(k);
            if (val) {
              try {
                const parsed = JSON.parse(val);
                if (parsed && !list.some(e => JSON.stringify(e) === JSON.stringify(parsed))) {
                  list.push(parsed);
                }
              } catch {}
            }
          }
        }
      }
    } catch {}
  }
  return list;
}

function _clearDraftRecovery(id, writer) {
  try {
    const targetId = id && id !== '__new__' ? id : '__new__';
    const key = _draftRecoveryKey(targetId);
    if (!writer) {
      localStorage.removeItem(key);
      return;
    }
    const raw = localStorage.getItem(key);
    if (!raw) return;
    const drafts = JSON.parse(raw);
    if (!Array.isArray(drafts)) {
      localStorage.removeItem(key);
      return;
    }
    const remaining = drafts.filter(entry => entry && (!entry._draftWriter || entry._draftWriter !== writer));
    if (remaining.length === 0) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, JSON.stringify(remaining));
    }
  } catch {}
}

function _keepDraftForRecovery(id, draft) {
  if (!draft || typeof draft !== 'object') return;
  const targetId = id && id !== '__new__' ? id : '__new__';
  const drafts = _loadDraftRecovery(targetId);
  const stored = JSON.stringify(draft);
  if (drafts.some(entry => JSON.stringify(entry) === stored)) return;
  const filtered = draft._draftWriter
    ? drafts.filter(entry => entry._draftWriter !== draft._draftWriter)
    : drafts;
  try {
    localStorage.setItem(_draftRecoveryKey(targetId), JSON.stringify([...filtered, draft]));
  } catch {}
}

return { _loadPendingHighlights, _loadGlowedReminders, _saveGlowedReminders, _loadActiveHighlights, _saveActiveHighlights, _savePendingHighlights, _loadFiredReminders, _saveFiredReminders, _draftKey, _loadDraft, _clearDraft,
  _loadDraftRecovery, _keepDraftForRecovery, _clearDraftRecovery,
  getItem: key => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
  removeItem: key => localStorage.removeItem(key),
};
}
