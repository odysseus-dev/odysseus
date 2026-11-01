/**
 * Tool-aware canvas context menu (Photoshop-style right-click).
 *
 * A right-click on the image opens a menu whose items depend on the
 * active tool: paint tools get brush size/presets, selection tools get
 * select/feather/expand actions, the crop tool gets Apply/Cancel, the
 * transform tool gets commit/cancel, and so on. Every action re-uses the
 * existing control ops (window.geEditorControl.invoke) or clicks the
 * existing topbar menu items, so there is a single source of truth.
 *
 * Wire it with wireCanvasContextMenu(canvasArea) from canvas-events.js.
 */
import { state } from './state.js';

const PAINT = ['brush', 'eraser', 'clone', 'heal', 'dodge', 'burn', 'smudge', 'inpaint', 'rembg', 'sharpen'];
const SELECT = ['marquee', 'lasso', 'wand', 'quickselect', 'objectselect', 'pen', 'path', 'sam'];
const TRANSFORM = ['move', 'transform'];

let rootMenu = null;
let subMenu = null;
let cleanups = [];

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const hasSelection = () => !!(state.wandMask || (state.lassoPoints && state.lassoPoints.length >= 3));

function invoke(op, params) {
  try { return window.geEditorControl?.invoke(op, params || {}); } catch (err) { console.warn('[ge-canvas-menu]', op, err); }
}
function clickEl(selector) { document.querySelector(selector)?.click(); }
function clickMenu(selector) { document.querySelector(selector)?.click(); }
function dispatchEscape() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
}
function stepBrushSize(factor) {
  const slider = document.querySelector('.ge-size-slider');
  if (!slider) return;
  const next = Math.max(1, Math.min(800, Math.round((state.brushSize || 1) * factor)));
  slider.value = String(Math.round(Math.log(next) / Math.log(800) * 1000));
  slider.dispatchEvent(new Event('input', { bubbles: true }));
}
function applyPresetId(id) {
  const sel = document.getElementById('ge-brush-preset');
  if (!sel) return;
  sel.value = id;
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}
function presetItems() {
  const sel = document.getElementById('ge-brush-preset');
  if (!sel) return [];
  return [...sel.options].map((opt) => ({
    label: opt.textContent,
    checked: opt.value === sel.value,
    onSelect: () => applyPresetId(opt.value),
  }));
}
function promptNumber(message, fallback, run) {
  const raw = window.prompt(message, String(fallback));
  if (raw == null) return;
  const value = Number(raw);
  if (Number.isFinite(value)) run(value);
}

/** Build the menu item descriptors for a tool. Exported for tests. */
export function buildCanvasMenuItems(tool) {
  const items = [];
  const push = (...x) => items.push(...x);
  const sep = () => items.push({ type: 'sep' });
  const sel = hasSelection();

  if (PAINT.includes(tool)) {
    push(
      { label: 'Increase Brush Size', hint: ']', onSelect: () => stepBrushSize(1.25) },
      { label: 'Decrease Brush Size', hint: '[', onSelect: () => stepBrushSize(0.8) },
      { label: 'Brush Preset', submenu: () => presetItems() },
      {
        label: 'Pixel Brush',
        checked: !!state.brushPixel,
        onSelect: () => applyPresetId(state.brushPixel ? 'hard-round' : 'pixel-1px'),
      },
    );
    sep();
  } else if (SELECT.includes(tool)) {
    push(
      { label: 'Select All', hint: 'Ctrl+A', onSelect: () => invoke('selection.selectAll') },
      { label: 'Deselect', hint: 'Ctrl+D', disabled: !sel, onSelect: () => invoke('selection.deselect') },
      { label: 'Inverse Selection', disabled: !sel, onSelect: () => invoke('selection.invert') },
      { label: 'Transform Selection', disabled: !sel, onSelect: () => clickMenu('[data-selection-action="transform"]') },
      { label: 'Refine Edge…', disabled: !sel, onSelect: () => clickMenu('[data-selection-action="refine"]') },
    );
    sep();
    push(
      { label: 'Feather…', disabled: !sel, onSelect: () => promptNumber('Feather radius (px)', 8, (v) => invoke('selection.feather', { radius: v })) },
      { label: 'Expand', disabled: !sel, onSelect: () => invoke('selection.expand', { amount: 4 }) },
      { label: 'Contract', disabled: !sel, onSelect: () => invoke('selection.contract', { amount: 4 }) },
    );
    if (sel) {
      push(
        { label: 'Fill Selection', onSelect: () => invoke('selection.fill', { color: state.color }) },
        { label: 'Delete Selection', hint: 'Del', onSelect: () => invoke('selection.delete') },
      );
    }
    sep();
  } else if (tool === 'crop' || state.cropRect) {
    const cropping = !!(state.cropRect || state.cropping || state.cropMoving);
    push(
      { label: 'Apply Crop', disabled: !cropping, onSelect: () => clickEl('.ge-crop-apply-btn') },
      { label: 'Cancel Crop', hint: 'Esc', disabled: !cropping, onSelect: () => dispatchEscape() },
    );
    sep();
  } else if (TRANSFORM.includes(tool) || state.transformActive) {
    push(
      { label: 'Commit Transform', hint: 'Enter', disabled: !state.transformActive, onSelect: () => invoke('transform.apply') },
      { label: 'Cancel Transform', hint: 'Esc', disabled: !state.transformActive, onSelect: () => invoke('transform.cancel') },
      { label: 'Free Transform', hint: 'Ctrl+T', disabled: state.transformActive, onSelect: () => invoke('transform.start') },
    );
    sep();
  } else if (tool === 'text') {
    push(
      { label: 'Add Text Layer', onSelect: () => invoke('text.add', { text: 'Text' }) },
      { label: 'Rasterize Text', onSelect: () => invoke('layers.rasterize') },
    );
    sep();
  } else if (tool === 'eyedropper') {
    push(
      { label: 'Copy Color Hex', onSelect: () => { try { navigator.clipboard?.writeText(state.color || ''); } catch (_) {} } },
      { label: 'Swap Foreground / Background', onSelect: () => clickEl('#ge-swap-colors') },
    );
    sep();
  } else if (tool === 'hand') {
    push(
      { label: 'Zoom In', hint: 'Ctrl++', onSelect: () => clickEl('#ge-zoom-in') },
      { label: 'Zoom Out', hint: 'Ctrl+-', onSelect: () => clickEl('#ge-zoom-out') },
      { label: 'Fit on Screen', hint: 'Ctrl+0', onSelect: () => clickEl('#ge-zoom-100') },
    );
    sep();
  } else if (tool === 'gradient' || tool === 'shape') {
    push(
      { label: 'Fill Selection with Foreground', disabled: !sel, onSelect: () => invoke('selection.fill', { color: state.color }) },
    );
    sep();
  }

  if (!SELECT.includes(tool) && sel) {
    push(
      { label: 'Deselect', hint: 'Ctrl+D', onSelect: () => invoke('selection.deselect') },
      { label: 'Inverse Selection', onSelect: () => invoke('selection.invert') },
      { label: 'Fill Selection', onSelect: () => invoke('selection.fill', { color: state.color }) },
      { label: 'Delete Selection', hint: 'Del', onSelect: () => invoke('selection.delete') },
    );
    sep();
  }

  push(
    { label: 'Cut', hint: 'Ctrl+X', disabled: !sel, onSelect: () => invoke('selection.copy', { cut: true }) },
    { label: 'Copy', hint: 'Ctrl+C', disabled: !sel, onSelect: () => invoke('selection.copy') },
    { label: 'Paste', hint: 'Ctrl+V', onSelect: () => invoke('selection.paste') },
    sep(),
    { label: 'Select All', hint: 'Ctrl+A', onSelect: () => invoke('selection.selectAll') },
    { label: 'Deselect', hint: 'Ctrl+D', disabled: !sel, onSelect: () => invoke('selection.deselect') },
    sep(),
    { label: 'Rotate 90° CW', onSelect: () => invoke('layer.rotate', { degrees: 90 }) },
    { label: 'Rotate 180°', onSelect: () => invoke('layer.rotate', { degrees: 180 }) },
    { label: 'Flip Horizontal', onSelect: () => invoke('layer.flip', { axis: 'h' }) },
    { label: 'Flip Vertical', onSelect: () => invoke('layer.flip', { axis: 'v' }) },
    sep(),
    { label: 'Undo', hint: 'Ctrl+Z', onSelect: () => invoke('history.undo') },
    { label: 'Redo', hint: 'Ctrl+Shift+Z', onSelect: () => invoke('history.redo') },
  );
  return items;
}

function buildItemEl(item, close) {
  if (item.type === 'sep') {
    const hr = document.createElement('div');
    hr.className = 'ge-canvas-menu-sep';
    return hr;
  }
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ge-canvas-menu-item';
  btn.setAttribute('role', 'menuitem');
  btn.disabled = !!item.disabled;
  if (item.checked) btn.classList.add('is-checked');
  const lead = document.createElement('span');
  lead.className = 'ge-canvas-menu-lead';
  lead.textContent = item.checked ? '✓' : '';
  const label = document.createElement('span');
  label.className = 'ge-canvas-menu-label';
  label.textContent = item.label;
  btn.append(lead, label);
  if (item.submenu) {
    btn.classList.add('has-submenu');
    const caret = document.createElement('span');
    caret.className = 'ge-canvas-menu-caret';
    caret.textContent = '›';
    btn.appendChild(caret);
    btn.addEventListener('pointerenter', () => openSubmenu(btn, item.submenu));
  } else if (item.hint) {
    const hint = document.createElement('span');
    hint.className = 'ge-canvas-menu-hint';
    hint.textContent = item.hint;
    btn.appendChild(hint);
  }
  btn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (item.disabled) return;
    if (item.submenu) return;
    try { item.onSelect?.(); } finally { close(); }
  });
  btn.addEventListener('pointerenter', () => { closeSubmenu(); });
  return btn;
}

function buildMenuEl(items, close) {
  const menu = document.createElement('div');
  menu.className = 'ge-canvas-menu';
  menu.setAttribute('role', 'menu');
  for (const item of items) menu.appendChild(buildItemEl(item, close));
  return menu;
}

function placeMenu(menu, x, y) {
  menu.style.left = '0px';
  menu.style.top = '0px';
  document.body.appendChild(menu);
  const rect = menu.getBoundingClientRect();
  const left = clamp(x, 4, Math.max(4, window.innerWidth - rect.width - 4));
  const top = clamp(y, 4, Math.max(4, window.innerHeight - rect.height - 4));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function closeSubmenu() {
  subMenu?.remove();
  subMenu = null;
}

function openSubmenu(anchor, spec) {
  closeSubmenu();
  const items = typeof spec === 'function' ? spec() : spec;
  if (!items || !items.length) return;
  subMenu = buildMenuEl(items, closeAll);
  subMenu.classList.add('ge-canvas-menu-sub');
  document.body.appendChild(subMenu);
  const a = anchor.getBoundingClientRect();
  const r = subMenu.getBoundingClientRect();
  let left = a.right - 4;
  if (left + r.width > window.innerWidth - 4) left = a.left - r.width + 4;
  const top = clamp(a.top - 4, 4, Math.max(4, window.innerHeight - r.height - 4));
  subMenu.style.left = `${left}px`;
  subMenu.style.top = `${top}px`;
}

function moveFocus(menu, delta) {
  const focusables = [...menu.querySelectorAll('.ge-canvas-menu-item:not([disabled])')];
  if (!focusables.length) return;
  const current = focusables.indexOf(document.activeElement);
  const next = clamp((current < 0 ? (delta > 0 ? -1 : 0) : current) + delta, 0, focusables.length - 1);
  focusables[next]?.focus();
}

export function closeCanvasContextMenu() {
  for (const fn of cleanups) { try { fn(); } catch (_) {} }
  cleanups = [];
  closeSubmenu();
  rootMenu?.remove();
  rootMenu = null;
}

/** Open the tool-aware menu at a pointer event (or {clientX, clientY}). */
export function openCanvasContextMenu(event) {
  if (!state.editorOpen || !event) return;
  closeCanvasContextMenu();
  const menu = buildMenuEl(buildCanvasMenuItems(state.tool), closeCanvasContextMenu);
  placeMenu(menu, event.clientX, event.clientY);
  rootMenu = menu;
  menu.querySelector('.ge-canvas-menu-item:not([disabled])')?.focus();

  const onDown = (ev) => {
    if (menu.contains(ev.target)) return;
    if (subMenu && subMenu.contains(ev.target)) return;
    closeCanvasContextMenu();
  };
  const onKey = (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); closeCanvasContextMenu(); }
    else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); moveFocus(menu, ev.key === 'ArrowDown' ? 1 : -1); }
    else if (ev.key === 'Enter') {
      const active = document.activeElement;
      if (active?.classList?.contains('ge-canvas-menu-item')) { ev.preventDefault(); active.click(); }
    }
  };
  const onAway = () => closeCanvasContextMenu();
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', onAway);
  window.addEventListener('blur', onAway);
  cleanups.push(() => document.removeEventListener('pointerdown', onDown, true));
  cleanups.push(() => document.removeEventListener('keydown', onKey, true));
  cleanups.push(() => window.removeEventListener('resize', onAway));
  cleanups.push(() => window.removeEventListener('blur', onAway));
}

/** Wire right-click on the image (and the surrounding canvas area). */
export function wireCanvasContextMenu(canvasArea, { signal } = {}) {
  const canvas = state.mainCanvas;
  if (!canvas) return;
  const opts = signal ? { signal } : undefined;
  canvas.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    openCanvasContextMenu(event);
  }, opts);
  canvasArea?.addEventListener('contextmenu', (event) => {
    if (event.target === canvas) return;
    if (event.target.closest?.('input, textarea, .ge-canvas-menu')) return;
    event.preventDefault();
    openCanvasContextMenu(event);
  }, opts);
}
