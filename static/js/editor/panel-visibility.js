/**
 * Side-panel visibility.
 *
 * Photoshop-style View > Panels toggles for every docked side panel (plus the
 * pinned Layers strip and the Tool Options bar). Visibility is a per-user,
 * document-independent preference persisted in localStorage so it survives
 * reloads and applies to every document.
 *
 * The dock itself has no visibility API, so we apply visibility by tagging the
 * panel wrapper, its tab strip item, and (when a whole group is hidden) the
 * group element with `data-panel-hidden="1"`; CSS hides tagged nodes.
 */
export const PANELS = Object.freeze([
  { id: 'swatches', label: 'Swatches', group: 'Color' },
  { id: 'gradients', label: 'Gradients', group: 'Color' },
  { id: 'patterns', label: 'Patterns', group: 'Color' },
  { id: 'properties', label: 'Properties', group: 'Adjustments' },
  { id: 'adjustments', label: 'Adjustments', group: 'Adjustments' },
  { id: 'histogram', label: 'Histogram', group: 'Adjustments' },
  { id: 'info', label: 'Info', group: 'Adjustments' },
  { id: 'channels', label: 'Channels', group: 'Panels' },
  { id: 'paths', label: 'Paths', group: 'Panels' },
  { id: 'character', label: 'Character', group: 'Panels' },
  { id: 'navigator', label: 'Navigator', group: 'Panels' },
  { id: 'history', label: 'History', group: 'History' },
  { id: 'actions', label: 'Actions', group: 'History' },
  { id: 'layers', label: 'Layers', group: 'Pinned' },
  { id: 'options', label: 'Tool Options', group: 'Pinned' },
]);

export const PANEL_IDS = PANELS.map(panel => panel.id);
const STORAGE_KEY = 'ge-panel-visibility';
const DOCK_GROUPS = ['Color', 'Adjustments', 'Panels', 'History'];
const subscribers = new Set();

let hidden = null;

function readStored() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_) {
    return {};
  }
}

function ensureLoaded() {
  if (hidden) return;
  const stored = readStored();
  hidden = new Set(PANEL_IDS.filter(id => stored[id] === false));
}

function persist() {
  const map = {};
  for (const id of PANEL_IDS) map[id] = !hidden.has(id);
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(map)); } catch (_) {}
}

export function isVisible(id) {
  ensureLoaded();
  return !hidden.has(id);
}

export function list() {
  ensureLoaded();
  return PANELS.map(panel => ({ ...panel, visible: !hidden.has(panel.id) }));
}

function notify() {
  for (const fn of subscribers) { try { fn(); } catch (_) {} }
}

function markHidden(el, hide) {
  if (!el) return;
  if (hide) el.setAttribute('data-panel-hidden', '1');
  else el.removeAttribute('data-panel-hidden');
}

export function apply() {
  ensureLoaded();
  for (const id of PANEL_IDS) {
    const hide = hidden.has(id);
    document.querySelectorAll(`.ge-dock-panel[data-panel="${id}"]`).forEach(el => markHidden(el, hide));
    document.querySelectorAll(`.ge-dock-tab-item[data-panel="${id}"]`).forEach(el => markHidden(el, hide));
    document.querySelectorAll(`.ge-dock-select option[value="${id}"]`).forEach(el => markHidden(el, hide));
  }
  // Collapse a whole group (and the divider that precedes it) when every one
  // of its panels is hidden, so the rail reflows without an empty gap.
  for (const group of DOCK_GROUPS) {
    const members = PANELS.filter(panel => panel.group === group).map(panel => panel.id);
    const allHidden = members.every(id => hidden.has(id));
    document.querySelectorAll(`.ge-dock-group[data-group="${group}"]`).forEach(el => markHidden(el, allHidden));
  }
  document.querySelectorAll('.ge-dock-divider').forEach(div => {
    const next = div.nextElementSibling;
    markHidden(div, !!(next && next.matches('.ge-dock-group[data-panel-hidden="1"]')));
  });
  // Pinned panels live outside the dock.
  markHidden(document.querySelector('.ge-layers'), hidden.has('layers'));
  markHidden(document.querySelector('.ge-controls'), hidden.has('options'));
  // If every dock group is hidden, collapse the dock entirely so the rail
  // doesn't leave an empty column of blank space; hide the whole rail once
  // the pinned panels are hidden too (the canvas takes the freed width).
  const anyDockVisible = DOCK_GROUPS.some(group =>
    PANELS.some(panel => panel.group === group && !hidden.has(panel.id)));
  document.querySelectorAll('.ge-dock').forEach(el => markHidden(el, !anyDockVisible));
  const railEmpty = !anyDockVisible && hidden.has('layers') && hidden.has('options');
  document.querySelectorAll('.ge-right-panel').forEach(el => {
    markHidden(el, railEmpty);
    if (!anyDockVisible) el.setAttribute('data-panel-dock-hidden', '1');
    else el.removeAttribute('data-panel-dock-hidden');
  });
  // Never leave a group showing a hidden panel: activate the first visible tab.
  for (const group of DOCK_GROUPS) {
    const members = PANELS.filter(panel => panel.group === group).map(panel => panel.id);
    if (members.every(id => hidden.has(id))) continue;
    const groupEl = document.querySelector(`.ge-dock-group[data-group="${group}"]`);
    if (!groupEl) continue;
    const activeTab = groupEl.querySelector('.ge-dock-tab-item.active');
    const activeId = activeTab?.dataset.panel;
    if (activeId && hidden.has(activeId)) {
      const firstVisible = members.find(id => !hidden.has(id));
      groupEl.querySelector(`.ge-dock-tab-item[data-panel="${firstVisible}"] .ge-dock-tab`)?.click();
    }
  }
}

export function setVisible(id, visible) {
  ensureLoaded();
  if (!PANEL_IDS.includes(id)) return false;
  const on = visible !== false;
  if (on) hidden.delete(id);
  else hidden.add(id);
  persist();
  apply();
  notify();
  return on;
}

export function toggle(id) {
  return setVisible(id, !isVisible(id));
}

export function showAll() {
  ensureLoaded();
  hidden.clear();
  persist();
  apply();
  notify();
}

export function hideAll() {
  ensureLoaded();
  hidden = new Set(PANEL_IDS);
  persist();
  apply();
  notify();
}

export function reset() {
  return showAll();
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

// Test seam: drop the in-memory cache so the next read re-loads localStorage.
export function _resetCache() {
  hidden = null;
}
