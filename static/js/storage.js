// static/js/storage.js
// Centralized localStorage access with key constants and JSON parse safety

// ── Key constants ──
export const KEYS = {
  THEME: 'odysseus-theme',
  TOGGLES: 'odysseus-toggles',
  SIDEBAR_COLLAPSED: 'sidebar-collapsed',
  SIDEBAR_WIDTH: 'sidebar-width',
  SIDEBAR_SIDE: 'sidebar-side',
  CURRENT_SESSION: 'currentSessionId',
  COMPARE_SAVE: 'compare-save-results',
  COMPARE_CHAT: 'compare-continue-chat',
  COMPARE_BLIND: 'compare-blind',
  COMPARE_RANDOM: 'compare-randomize',
  MODELS_EXPANDED: 'odysseus-model-expanded',
  MODEL_ENDPOINTS: 'odysseus-model-endpoints',
  MODEL_SELECTED: 'odysseus-selected-model',
  SORT_ORDER: 'odysseus-sessions-sort',
  CHAT_SEARCH_SCOPE: 'odysseus-search-scope',
  INCOGNITO: 'odysseus-incognito',
  RAG_ACTIVE: 'odysseus-rag-active',
  MCP_ACTIVE: 'odysseus-mcp-active',
  SECTION_ORDER: 'sidebar-section-order',
  ADMIN_LAST_TAB: 'admin-last-tab',
  DENSITY: 'odysseus-density',
  UI_SCALE: 'odysseus-ui-scale',
  WORKSPACE: 'odysseus-workspace',
  SESSION_MODES: 'odysseus-session-modes'
};

/**
 * Safely get and parse a JSON value from localStorage.
 * Returns fallback on any error.
 */
export function getJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback !== undefined ? fallback : null;
    return JSON.parse(raw);
  } catch (e) {
    console.warn('[Storage] Failed to parse key "' + key + '":', e.message);
    return fallback !== undefined ? fallback : null;
  }
}

/**
 * Set a JSON-serialized value in localStorage.
 */
export function setJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    console.warn('[Storage] Failed to set key "' + key + '":', e.message);
  }
}

/**
 * Get a raw string value from localStorage.
 */
export function get(key, fallback) {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : (fallback !== undefined ? fallback : null);
  } catch (e) {
    return fallback !== undefined ? fallback : null;
  }
}

/**
 * Set a raw string value in localStorage.
 */
export function set(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn('[Storage] Failed to set key "' + key + '":', e.message);
  }
}

/**
 * Remove a key from localStorage.
 */
export function remove(key) {
  try {
    localStorage.removeItem(key);
  } catch (e) {
    // Ignore removal errors
  }
}

// ── Toggle state helpers ──

export function loadToggleState() {
  return getJSON(KEYS.TOGGLES, {});
}

export function saveToggleState(state) {
  setJSON(KEYS.TOGGLES, state);
}

export function getToggle(name, fallback) {
  const state = loadToggleState();
  return state[name] !== undefined ? state[name] : (fallback !== undefined ? fallback : false);
}

export function setToggle(name, value) {
  const state = loadToggleState();
  state[name] = value;
  saveToggleState(state);
}

// ── Per-session Agent/Chat mode ──
// The global 'mode' toggle above is a single localStorage key shared by
// every open tab and conversation. Flipping it anywhere (another tab, a
// tool-error auto-fallback, an agent-driven set_mode event) silently
// changed which mode an unrelated, already-open conversation would send
// next — a real conversation with tools actually attached could be
// followed by a message sent with zero tools attached, with no visible
// cause. Once a conversation has an explicit mode recorded here, that
// value wins over the global default for that conversation specifically.

export function getSessionMode(sessionId, fallback) {
  const globalDefault = getToggle('mode', fallback !== undefined ? fallback : 'chat');
  if (!sessionId) return globalDefault;
  const perSession = getJSON(KEYS.SESSION_MODES, {});
  return perSession[sessionId] !== undefined ? perSession[sessionId] : globalDefault;
}

export function setSessionMode(sessionId, mode) {
  if (!sessionId) { setToggle('mode', mode); return; }
  const perSession = getJSON(KEYS.SESSION_MODES, {});
  perSession[sessionId] = mode;
  setJSON(KEYS.SESSION_MODES, perSession);
}

const Storage = {
  KEYS,
  getJSON,
  setJSON,
  get,
  set,
  remove,
  loadToggleState,
  saveToggleState,
  getToggle,
  setToggle,
  getSessionMode,
  setSessionMode
};

export default Storage;
