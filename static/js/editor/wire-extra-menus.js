/**
 * Extra topbar dropdown menus — Layer, Type, Window.
 *
 * These are thin delegators: they never own editor state. Each item
 * re-uses the existing control that already implements the behaviour,
 * so there is a single source of truth.
 *
 *   Layer menu items click the corresponding layer-panel header button
 *   (#ge-add-layer, #ge-merge-down, #ge-merge-all, #ge-flatten,
 *   #ge-select-all-layers, #ge-group-selected, #ge-layer-fill).
 *
 *   Type menu "Add Text Layer" selects the text tool.
 *
 *   Window menu reveals a dock tab (Tool Options / Layers) or clicks
 *   the existing History / Shortcuts topbar buttons. Its rows toggle a
 *   panel's visibility and show a dot when it is currently shown; the
 *   composer supplies `syncWindowChecks` (dot state) and `togglePanel`
 *   (the toggle) so this file owns no state. The menu stays open while
 *   toggling.
 *
 * @param {{
 *   closeOtherTopbarMenus: (keepId: string) => void,
 *   registerDocClickAway:  (handler: (e: Event) => void) => void,
 *   dock:                  { show: (id: string) => void } | null,
 *   combineShapes:         (op: string) => void,
 *   syncWindowChecks?:     (menu: HTMLElement | null) => void,
 *   togglePanel?:          (id: string) => void,
 * }} deps
 */
import { wireSubmenuToggles } from './submenu-toggles.js';

const MENU_ITEM_SELECTOR = '[data-layer-action],[data-type-action],[data-window-action],[data-file-action],[data-edit-action],[data-help-action],[data-image-action],[data-selection-action],[data-filter-action],[data-view-action],[data-3d-action],[data-plugins-action]';

// Window-menu rows that map to a dock/side panel (everything else is a dialog
// or a workspace preset handled separately).
const WINDOW_PANEL_ACTIONS = ['options', 'layers', 'properties', 'character', 'adjustments', 'swatches', 'gradients', 'channels', 'paths', 'patterns', 'histogram', 'info', 'navigator', 'actions'];


function wireMenuKeyboard(container) {
  const focusableItems = (menu) => [...menu.querySelectorAll('.dropdown-item-compact')]
    .filter(el => MENU_ITEM_SELECTOR.split(',').some(sel => el.matches(sel)) && !el.disabled);
  const focusItem = (menu, index) => {
    const items = focusableItems(menu);
    if (!items.length) return;
    const i = Math.max(0, Math.min(items.length - 1, index));
    items[i].focus();
  };
  container.addEventListener('keydown', (event) => {
    const menu = event.target.closest?.('.ge-topbar .dropdown');
    const btn = event.target.closest?.('.ge-topbar-left button[aria-haspopup="true"]');
    if (menu) {
      const items = focusableItems(menu);
      if (!items.length) return;
      const current = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        focusItem(menu, current < 0 ? 0 : current + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        focusItem(menu, current < 0 ? items.length - 1 : current - 1);
      } else if (event.key === 'Home') {
        event.preventDefault();
        focusItem(menu, 0);
      } else if (event.key === 'End') {
        event.preventDefault();
        focusItem(menu, items.length - 1);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        menu.hidden = true;
        menu.parentElement?.querySelector('button')?.focus();
      } else if (event.key === 'Enter' || event.key === ' ') {
        if (document.activeElement?.classList.contains('dropdown-item-compact')) {
          event.preventDefault();
          document.activeElement.click();
        }
      }
      return;
    }
    if (btn && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault();
      btn.click();
      const openMenu = document.getElementById(btn.getAttribute('aria-controls'))
        || btn.parentElement?.querySelector('.dropdown');
      if (openMenu) {
        requestAnimationFrame(() => focusItem(openMenu, event.key === 'ArrowUp' ? -1 : 0));
      }
    }
  });
}

export function wireExtraMenus({ closeOtherTopbarMenus, registerDocClickAway, dock, combineShapes, workspace, uiModule, exportLayeredPsd, syncWindowChecks, togglePanel }) {
  const click = (id) => document.getElementById(id)?.click();
  // A menu row may synthesise a click on an external control (Window/Edit >
  // History clicks #ge-history-btn). That click bubbles to the document and
  // would trip our own click-away handler, closing the menu mid-toggle — so
  // ignore outside clicks for the duration of the row handler.
  let suppressAway = false;
  const topbar = document.querySelector('.ge-topbar-left');
  if (topbar) wireMenuKeyboard(topbar);

  function bind(btnId, menuId, handler, onOpen, keepOpen) {
    const btn = document.getElementById(btnId);
    const menu = document.getElementById(menuId);
    if (!btn || !menu) return;
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      const willOpen = menu.hidden;
      if (willOpen) { closeOtherTopbarMenus(menuId); onOpen?.(); }
      menu.hidden = !menu.hidden;
    });
    menu.addEventListener('click', (event) => {
      const item = event.target.closest('[data-layer-action],[data-type-action],[data-window-action],[data-file-action],[data-edit-action],[data-help-action],[data-3d-action],[data-plugins-action]');
      if (!item || item.disabled) return;
      if (!keepOpen) menu.hidden = true;
      suppressAway = true;
      try { handler(item); } finally { suppressAway = false; }
      // Toggle menus stay open so several panels can be flipped in a row; the
      // freshly-toggled state is re-read so the dot indicator stays truthful.
      if (keepOpen) onOpen?.();
    });
    registerDocClickAway((event) => {
      if (suppressAway) return;
      if (!menu.hidden && !menu.contains(event.target) && event.target !== btn) menu.hidden = true;
    });
    // Support an optional nested flyout (e.g. Window > Workspace). No-op when
    // the menu has no `.ge-menu-parent` rows.
    wireSubmenuToggles(menu);
  }

  bind('ge-layer-menu-btn', 'ge-layer-menu', (item) => {
    const action = item.dataset.layerAction;
    if (action === 'new') click('ge-add-layer');
    else if (action === 'new-fill') { click('ge-add-layer'); click('ge-layer-fill'); }
    else if (action === 'duplicate') window.geEditorControl?.invoke('layers.duplicate', {});
    else if (action === 'delete') window.geEditorControl?.invoke('layers.delete', {});
    else if (action === 'rasterize') window.geEditorControl?.invoke('layers.rasterize', {});
    else if (action === 'arrange-front') window.geEditorControl?.invoke('layers.reorder', { direction: 'top' });
    else if (action === 'arrange-forward') window.geEditorControl?.invoke('layers.reorder', { direction: 'up' });
    else if (action === 'arrange-backward') window.geEditorControl?.invoke('layers.reorder', { direction: 'down' });
    else if (action === 'arrange-back') window.geEditorControl?.invoke('layers.reorder', { direction: 'bottom' });
    else if (action === 'merge-down') click('ge-merge-down');
    else if (action === 'merge-layers') window.geEditorControl?.invoke('layers.mergeLayers', {});
    else if (action === 'merge-all') click('ge-merge-all');
    else if (action === 'merge-visible') click('ge-merge-all');
    else if (action === 'flatten') window.geEditorControl?.invoke('layers.flatten', {});
    else if (action === 'select-all') click('ge-select-all-layers');
    else if (action === 'vector-mask-reveal') window.geEditorControl?.invoke('layers.addMaskFromSelection', {});
    else if (action === 'vector-mask-hide') window.geEditorControl?.invoke('layers.addMaskFromSelection', { invert: true });
    else if (action === 'group') click('ge-group-selected');
    else if (action === 'combine-unite') combineShapes?.('unite');
    else if (action === 'combine-subtract') combineShapes?.('subtract');
    else if (action === 'combine-intersect') combineShapes?.('intersect');
    else if (action === 'combine-exclude') combineShapes?.('exclude');
    else if (action === 'fill') click('ge-layer-fill');
  });

  bind('ge-type-menu-btn', 'ge-type-menu', (item) => {
    const action = item.dataset.typeAction;
    if (action === 'add') document.querySelector('[data-tool="text"]')?.click();
    else if (action === 'text-on-path') window.geEditorControl?.invoke('text.attachToPath', {});
    else if (action === 'text-on-circle') window.geEditorControl?.invoke('text.onCircle', {});
    else if (action === 'text-off-path') window.geEditorControl?.invoke('text.detachFromPath', {});
    else if (['character', 'paragraph', 'warp'].includes(action)) dock?.show('character');
  });

  // Window menu: every row is a real toggle, so it stays open and its dot
  // indicator is refreshed after each click (see `bind` keepOpen).
  bind('ge-window-menu-btn', 'ge-window-menu', (item) => {
    const action = item.dataset.windowAction;
    if (action === 'history') click('ge-history-btn');
    else if (action === 'shortcuts') click('ge-shortcuts-btn');
    else if (action.startsWith('workspace-')) {
      const name = action.slice('workspace-'.length);
      try { localStorage.setItem('ge-active-workspace', name); } catch {}
      workspace?.(name);
    } else if (WINDOW_PANEL_ACTIONS.includes(action)) togglePanel?.(action);
  }, () => syncWindowChecks?.(document.getElementById('ge-window-menu')), true);

  bind('ge-file-menu-btn', 'ge-file-menu', (item) => {
    const action = item.dataset.fileAction;
    if (action === 'import') click('ge-import-topbar');
    else if (action === 'save') click('ge-save');
    else if (action === 'save-copy') click('ge-export-gallery');
    else if (action === 'export') click('ge-download');
    else if (action === 'export-psd') exportLayeredPsd?.();
    else if (action === 'save-project') click('ge-save-project');
    else if (action === 'load-project') click('ge-load-project');
  });

  bind('ge-edit-menu-btn', 'ge-edit-menu', (item) => {
    const action = item.dataset.editAction;
    if (action === 'undo') click('ge-undo');
    else if (action === 'redo') click('ge-redo');
    else if (action === 'history') click('ge-history-btn');
    else if (action === 'copy-merged') window.geEditorControl?.invoke('edit.copyMerged', {});
    else if (action === 'rotate-90') window.geEditorControl?.invoke('layer.rotate', { degrees: 90 });
    else if (action === 'rotate-180') window.geEditorControl?.invoke('layer.rotate', { degrees: 180 });
    else if (action === 'flip-h') window.geEditorControl?.invoke('layer.flip', { axis: 'h' });
    else if (action === 'flip-v') window.geEditorControl?.invoke('layer.flip', { axis: 'v' });
    else if (action === 'content-aware-move') {
      const raw = window.prompt('Content-Aware Move — shift the selection by (x,y) in pixels, e.g. "40,-20"', '40,0');
      if (raw != null) {
        const parts = String(raw).split(/[\s,]+/).map(Number).filter(Number.isFinite);
        if (parts.length >= 2) {
          window.geEditorControl?.invoke('edit.contentAwareMove', { dx: Math.round(parts[0]), dy: Math.round(parts[1]) });
        }
      }
    }
    else if (action === 'stroke') document.querySelector('[data-filter-action="effect-stroke"]')?.click();
    else if (action === 'define-brush') click('ge-brush-preset-save');
  });

  bind('ge-help-menu-btn', 'ge-help-menu', (item) => {
    const action = item.dataset.helpAction;
    if (action === 'shortcuts') click('ge-shortcuts-btn');
    else if (action === 'about') uiModule?.showToast?.('Ajax image editor — PhotoCraft parity');
  });

  bind('ge-3d-menu-btn', 'ge-3d-menu', (item) => {
    if (item.getAttribute('data-3d-action')) {
      uiModule?.showToast?.('3D features are not available in this build');
    }
  });

  bind('ge-plugins-menu-btn', 'ge-plugins-menu', (item) => {
    const action = item.getAttribute('data-plugins-action');
    if (action === 'browse') uiModule?.showToast?.('Plugin browser is not available in this build');
    else if (action === 'manage') uiModule?.showToast?.('No plugins installed');
    else if (action === 'script') uiModule?.showToast?.('Editor scripting is not available in this build');
  });
}
