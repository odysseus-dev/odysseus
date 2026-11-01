/** View menu wiring for rulers, guides, grid, snapping preferences, and panel visibility. */
import { state } from './state.js';
import * as panelVisibility from './panel-visibility.js';

export function wireViewMenu({ closeOtherTopbarMenus, registerDocClickAway, precisionGuides, composite, schedulePersist }) {
  const button = document.getElementById('ge-view-menu-btn');
  const menu = document.getElementById('ge-view-menu');
  if (!button || !menu) return;

  const sync = () => {
    const values = { rulers: state.rulersVisible, grid: state.gridVisible, snap: state.snapEnabled, 'snap-grid': state.snapToGrid };
    for (const [action, enabled] of Object.entries(values)) {
      const item = menu.querySelector(`[data-view-action="${action}"]`);
      item?.classList.toggle('active', !!enabled);
      item?.setAttribute('aria-checked', enabled ? 'true' : 'false');
    }
    const size = menu.querySelector('#ge-grid-size');
    if (size && document.activeElement !== size) size.value = String(state.gridSize || 16);
    const clear = menu.querySelector('[data-view-action="clear-guides"]');
    if (clear) clear.disabled = (state.guides?.vertical?.length || 0) + (state.guides?.horizontal?.length || 0) === 0;
    for (const panel of panelVisibility.list()) {
      const item = menu.querySelector(`[data-view-action="panel-${panel.id}"]`);
      item?.classList.toggle('active', !!panel.visible);
      item?.setAttribute('aria-checked', panel.visible ? 'true' : 'false');
    }
  };

  button.addEventListener('click', event => {
    event.stopPropagation();
    const opening = menu.hidden;
    if (opening) closeOtherTopbarMenus('ge-view-menu');
    menu.hidden = !opening;
    if (opening) sync();
  });
  menu.addEventListener('click', event => {
    const item = event.target.closest('[data-view-action]');
    if (!item || item.disabled) return;
    const action = item.dataset.viewAction;
    if (action === 'screen-mode') { document.getElementById('ge-maximize-toggle')?.click(); return; }
    if (action === 'panel-show-all') { panelVisibility.showAll(); sync(); return; }
    if (action === 'panel-hide-all') { panelVisibility.hideAll(); sync(); return; }
    if (action === 'panel-reset') { panelVisibility.reset(); sync(); return; }
    if (action.startsWith('panel-')) { panelVisibility.toggle(action.slice('panel-'.length)); sync(); return; }
    if (action === 'rulers') state.rulersVisible = !state.rulersVisible;
    else if (action === 'grid') state.gridVisible = !state.gridVisible;
    else if (action === 'snap') state.snapEnabled = !state.snapEnabled;
    else if (action === 'snap-grid') state.snapToGrid = !state.snapToGrid;
    else if (action === 'new-h-guide') precisionGuides.addGuide?.('horizontal', Math.round((state.imgHeight || 0) / 2));
    else if (action === 'new-v-guide') precisionGuides.addGuide?.('vertical', Math.round((state.imgWidth || 0) / 2));
    else if (action === 'clear-guides') precisionGuides.clearGuides();
    if (action !== 'clear-guides') {
      precisionGuides.syncVisibility();
      schedulePersist?.();
    }
    sync();
  });
  menu.querySelector('#ge-grid-size')?.addEventListener('input', event => {
    event.stopPropagation();
    state.gridSize = Math.max(2, Math.min(1000, Math.round(Number(event.target.value) || 16)));
    schedulePersist?.();
    composite();
  });
  registerDocClickAway(event => {
    if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) menu.hidden = true;
  });
  sync();
}
