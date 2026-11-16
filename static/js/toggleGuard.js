// static/js/toggleGuard.js
// Shared guard for agent-originated tool toggles.
//
// The backend refuses to let the agent enable shell access; this module is the
// client-side half of that contract. It is deliberately DOM-free so it can be
// tested under Node without a browser shim.

const TOGGLE_ALIASES = {
  shell: 'bash',
  terminal: 'bash',
};

export const USER_ONLY_ENABLE_TOGGLES = new Set(['bash']);

export function canonicalToggleName(name) {
  const normalized = String(name || '').trim().toLowerCase();
  return TOGGLE_ALIASES[normalized] || normalized;
}

export function isToggleOn(value) {
  if (value === true || value === 1) return true;
  if (value === false || value === 0 || value == null) return false;
  const text = String(value).trim().toLowerCase();
  return text === 'true' || text === '1' || text === 'on' ||
    text === 'yes' || text === 'enable' || text === 'enabled';
}

export function isBlockedAgentToggle(uiData) {
  const data = uiData || {};
  return isToggleOn(data.state) &&
    USER_ONLY_ENABLE_TOGGLES.has(canonicalToggleName(data.toggle_name));
}
