/**
 * Fugassa session snapshot (UI only — not game DB).
 */
const LS_KEY = 'titan-fugassa-session';

const DEFAULT = {
  version: 1,
  mode: 'menu',
  menuScreen: 'home',
  wizardStep: 0,
  activeSaveId: null,
  lastTool: null,
  titanSidebarCollapsed: true,
  play: {
    hudSplits: { top: 40, chat: 280, right: 220, party: 90 },
    rightSidebarMode: 'explore',
    chatScrollTop: 0,
  },
};

export function loadSession() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { ...DEFAULT, play: { ...DEFAULT.play, hudSplits: { ...DEFAULT.play.hudSplits } } };
    const parsed = JSON.parse(raw);
    return {
      ...DEFAULT,
      ...parsed,
      play: { ...DEFAULT.play, ...(parsed.play || {}), hudSplits: { ...DEFAULT.play.hudSplits, ...(parsed.play?.hudSplits || {}) } },
    };
  } catch {
    return { ...DEFAULT, play: { ...DEFAULT.play, hudSplits: { ...DEFAULT.play.hudSplits } } };
  }
}

export function saveSession(partial) {
  const next = { ...loadSession(), ...partial, lastActiveAt: new Date().toISOString() };
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(next));
  } catch (_) { /* quota */ }
  return next;
}

export function clearSession() {
  try {
    localStorage.removeItem(LS_KEY);
  } catch (_) { /* ignore */ }
}
