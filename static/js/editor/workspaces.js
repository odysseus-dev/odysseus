/**
 * Workspace presets ("Window > Workspace").
 *
 * Each preset sets up the editor windows: which dock panels are shown, the
 * active dock tab and the right-panel width. Selecting one is a one-click
 * window layout switch.
 */

import { PANEL_IDS } from './panel-visibility.js';

export const WORKSPACES = Object.freeze({
  essentials: Object.freeze({ label: 'Essentials', tab: 'layers', width: 300, panels: Object.freeze(['layers', 'options', 'swatches', 'gradients', 'adjustments', 'history', 'info']) }),
  photography: Object.freeze({ label: 'Photography', tab: 'histogram', width: 320, panels: Object.freeze(['layers', 'options', 'histogram', 'info', 'navigator', 'adjustments']) }),
  painting: Object.freeze({ label: 'Painting', tab: 'swatches', width: 300, panels: Object.freeze(['layers', 'options', 'swatches', 'gradients', 'patterns', 'character']) }),
  graphic: Object.freeze({ label: 'Graphic & Web', tab: 'options', width: 320, panels: Object.freeze(['layers', 'options', 'character', 'properties', 'adjustments', 'paths', 'info', 'history']) }),
});

export function applyWorkspace(name, { dock = null, rightPanel = null, setPanelVisible = null, persistKey = 'ge-right-panel-width' } = {}) {
  const workspace = WORKSPACES[name];
  if (!workspace) return false;
  if (dock && typeof dock.show === 'function') dock.show(workspace.tab);
  if (rightPanel && Number.isFinite(workspace.width)) {
    rightPanel.style.width = `${workspace.width}px`;
    try {
      localStorage.setItem(persistKey, String(workspace.width));
    } catch (error) {
      /* storage unavailable — ignore */
    }
  }
  // Show exactly this preset's panels and hide the rest, so switching a
  // workspace visibly rearranges the window.
  if (typeof setPanelVisible === 'function' && Array.isArray(workspace.panels)) {
    const visible = new Set(workspace.panels);
    for (const id of PANEL_IDS) setPanelVisible(id, visible.has(id));
  }
  return true;
}
