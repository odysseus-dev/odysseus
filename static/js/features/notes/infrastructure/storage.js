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

function _draftKey(id) { return _DRAFT_PREFIX + (id || '__new__'); }

function _loadDraft(id) {
  try { return JSON.parse(localStorage.getItem(_draftKey(id)) || 'null'); } catch { return null; }
}

function _clearDraft(id) { try { localStorage.removeItem(_draftKey(id)); } catch {} }

return { _loadPendingHighlights, _loadGlowedReminders, _saveGlowedReminders, _loadActiveHighlights, _saveActiveHighlights, _savePendingHighlights, _loadFiredReminders, _saveFiredReminders, _draftKey, _loadDraft, _clearDraft,
  getItem: key => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
  removeItem: key => localStorage.removeItem(key),
};
}
