/**
 * Topbar wiring — undo/redo/history, Save dropdown, zoom buttons,
 * Save/Export/Download/Project, Edge popup, and the cross-dropdown
 * coordination (close-others + global outside-click).
 *
 *   #ge-undo / #ge-redo / #ge-history-btn
 *   #ge-save-menu-btn + #ge-save-menu  (Save / Save as / Download /
 *                                       Save project / Load project)
 *   #ge-zoom-out / #ge-zoom-in / #ge-zoom-fit / #ge-zoom-100
 *   #ge-export-gallery / #ge-download
 *   #ge-save-project / #ge-load-project
 *   #ge-edge-menu-btn + #ge-edge-menu (Width input + Feather / Delete
 *                                      action buttons)
 *
 * Dropdown coordination: every menu hides any sibling menu when it
 * opens (closeOtherTopbarMenus), and a global outside-click handler
 * closes every open menu if the user clicks anywhere outside.
 *
 * @param {{
 *   undo:                 () => void,
 *   redo:                 () => void,
 *   toggleHistoryPanel:   () => void,
 *   fitZoom:              () => void,
 *   applyZoom:            () => void,
 *   exportToGallery:      () => void,
 *   downloadPNG:          () => void,
 *   saveProject:          () => void,
 *   loadProjectPrompt:    () => void,
 *   activeLayer:          () => object | null,
 *   saveState:            (label?: string) => void,
 *   applyEdgeFeather:     (layer: object, width: number, hardDelete: boolean) => void,
 *   composite:            () => void,
 *   registerDocClickAway: (handler: (e: Event) => void) => void,
 *   uiModule:             object,
 * }} deps
 */
import { state } from './state.js';

const TOPBAR_MENU_IDS = ['ge-image-menu', 'ge-filter-menu', 'ge-resize-menu', 'ge-save-menu'];
const TOPBAR_TRIGGER_IDS = ['ge-image-menu-btn', 'ge-filter-menu-btn', 'ge-resize-menu-btn', 'ge-save-menu-btn'];

function topbarMenuBounds(pad = 8) {
  const viewport = {
    left: pad,
    top: pad,
    right: Math.max(pad, (window.innerWidth || 0) - pad),
    bottom: Math.max(pad, (window.innerHeight || 0) - pad),
  };
  const surface = state.container?.closest?.('.gallery-modal-content, .modal-content')
    || state.container?.closest?.('.gallery-editor')
    || state.container;
  const rect = surface?.getBoundingClientRect?.();
  if (!rect || rect.width < 120 || rect.height < 120) return viewport;
  const bounds = {
    left: Math.max(viewport.left, rect.left + pad),
    top: Math.max(viewport.top, rect.top + pad),
    right: Math.min(viewport.right, rect.right - pad),
    bottom: Math.min(viewport.bottom, rect.bottom - pad),
  };
  if (bounds.right - bounds.left < 140 || bounds.bottom - bounds.top < 120) return viewport;
  return bounds;
}

/**
 * Close every topbar dropdown except an optional "keep open" one.
 * Exported so the Image / Filter / Resize menus (wired elsewhere)
 * can call it from their own open handlers.
 */
export function closeOtherTopbarMenus(keepId) {
  for (const id of TOPBAR_MENU_IDS) {
    if (id === keepId) continue;
    const m = document.getElementById(id);
    if (m && !m.hidden) m.hidden = true;
  }
}

export function wireTopbar(deps) {
  const {
    undo, redo, toggleHistoryPanel,
    fitZoom, applyZoom,
    exportToGallery, downloadPNG, saveProject, loadProjectPrompt,
    activeLayer, saveState, applyEdgeFeather, composite,
    registerDocClickAway, uiModule,
  } = deps;

  // Undo / Redo / History.
  document.getElementById('ge-undo')?.addEventListener('click', undo);
  document.getElementById('ge-redo')?.addEventListener('click', redo);
  document.getElementById('ge-history-btn')?.addEventListener('click', toggleHistoryPanel);

  // Save dropdown — "Save ▾" toggles a small menu (Save / Save-as /
  // Download / Save project / Load project). Inner items keep their
  // original IDs so the standalone handlers below wire to them
  // unchanged.
  {
    const saveBtn = document.getElementById('ge-save-menu-btn');
    const saveMenu = document.getElementById('ge-save-menu');
    if (saveBtn && saveMenu) {
      const saveTopbar = saveBtn.closest('.ge-topbar');
      // Reparent the menu to <body>. Without this, the menu inherits
      // the gallery modal's containing block (the modal applies a
      // `transform: scale(...)` for its enter animation — and any
      // non-`none` transform on an ancestor makes that ancestor the
      // containing block for `position: fixed` descendants, even after
      // the animation lands on identity). The JS math below assumes
      // viewport-relative coords, so without the reparent the menu
      // ends up "way off" the button on desktop.
      if (saveMenu.parentNode !== document.body) {
        document.body.appendChild(saveMenu);
      }
      const setSaveMenuOpen = (open) => {
        saveMenu.hidden = !open;
        saveTopbar?.classList.toggle('ge-topbar-menu-open', !!open);
      };
      const positionSaveMenu = () => {
        const r = saveBtn.getBoundingClientRect();
        const bounds = topbarMenuBounds(8);
        const width = Math.min(saveMenu.offsetWidth || 180, Math.max(140, bounds.right - bounds.left));
        const left = Math.max(bounds.left, Math.min(Math.max(bounds.left, bounds.right - width), r.right - width));
        const top = Math.max(bounds.top, r.bottom + 2);
        saveMenu.style.top = `${Math.round(top)}px`;
        saveMenu.style.left = `${Math.round(left)}px`;
        saveMenu.style.right = 'auto';
        saveMenu.style.maxWidth = `${Math.round(bounds.right - left)}px`;
        saveMenu.style.maxHeight = `${Math.round(Math.max(96, Math.min(420, bounds.bottom - top)))}px`;
        saveMenu.style.overflowY = 'auto';
      };
      saveBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = saveMenu.hidden;
        setSaveMenuOpen(willOpen);
        if (willOpen) positionSaveMenu();
      });
      saveMenu.addEventListener('click', () => { setSaveMenuOpen(false); });
      window.addEventListener('resize', () => { if (!saveMenu.hidden) positionSaveMenu(); });
      registerDocClickAway((e) => {
        if (!saveMenu.hidden && !saveMenu.contains(e.target) && e.target !== saveBtn) {
          setSaveMenuOpen(false);
        }
      });
    }
  }

  // Zoom buttons.
  document.getElementById('ge-zoom-fit')?.addEventListener('click', fitZoom);
  document.getElementById('ge-zoom-100')?.addEventListener('click', () => { state.zoom = 1; applyZoom(); });
  document.getElementById('ge-zoom-in')?.addEventListener('click', () => { state.zoom = Math.min(5, state.zoom * 1.25); applyZoom(); });
  document.getElementById('ge-zoom-out')?.addEventListener('click', () => { state.zoom = Math.max(0.1, state.zoom / 1.25); applyZoom(); });

  // Export / Download / Project Save / Project Load.
  document.getElementById('ge-export-gallery')?.addEventListener('click', exportToGallery);
  document.getElementById('ge-download')?.addEventListener('click', downloadPNG);
  document.getElementById('ge-save-project')?.addEventListener('click', saveProject);
  document.getElementById('ge-load-project')?.addEventListener('click', loadProjectPrompt);

  // Global outside-click — closes EVERY editor dropdown when the
  // user clicks anywhere that isn't a menu or trigger button. Each
  // menu has its own click-away handler too; this is a defence-in-
  // depth net for cross-menu clicks / mobile touches that miss the
  // individual handlers.
  document.addEventListener('pointerdown', (e) => {
    for (const id of TOPBAR_MENU_IDS.concat(TOPBAR_TRIGGER_IDS)) {
      const el = document.getElementById(id);
      if (el && el.contains(e.target)) return;
    }
    for (const id of TOPBAR_MENU_IDS) {
      const m = document.getElementById(id);
      if (m && !m.hidden) m.hidden = true;
    }
  });

  // Edge popup — Width input + Feather / Delete action buttons.
  function applyEdgeAction(hardDelete) {
    const layer = activeLayer();
    if (!layer || layer.locked) { uiModule.showToast('Select an unlocked layer'); return; }
    const widthInput = document.getElementById('ge-edge-width');
    const width = parseInt(widthInput?.value || '8');
    if (isNaN(width) || width < 1) { uiModule.showToast('Invalid width'); return; }
    saveState();
    applyEdgeFeather(layer, width, hardDelete);
    composite();
    uiModule.showToast(hardDelete ? `Edges deleted ${width}px` : `Edges feathered ${width}px`);
  }
  {
    const btn = document.getElementById('ge-edge-menu-btn');
    const menu = document.getElementById('ge-edge-menu');
    if (btn && menu) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = menu.hidden;
        if (willOpen) closeOtherTopbarMenus('ge-edge-menu');
        menu.hidden = !menu.hidden;
        if (!menu.hidden) {
          // Autofocus the width input so users can type immediately.
          setTimeout(() => document.getElementById('ge-edge-width')?.select(), 0);
        }
      });
      document.getElementById('ge-edge-feather')?.addEventListener('click', () => {
        menu.hidden = true;
        applyEdgeAction(false);
      });
      document.getElementById('ge-edge-delete')?.addEventListener('click', () => {
        menu.hidden = true;
        applyEdgeAction(true);
      });
      document.getElementById('ge-edge-width')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          menu.hidden = true;
          applyEdgeAction(false);
        }
      });
      registerDocClickAway((e) => {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) menu.hidden = true;
      });
    }
  }
}
