/**
 * Layer panel renderer — rebuilds the right-side layer list from
 * `state.layers` every time it's called. The full row tree per layer:
 *
 *   parent row
 *     [drag handle] [eye] [name] [opacity slider] [FX] [dup] [mask] [merge-down] [×]
 *   adjustment sub-rows (FX entries)
 *     [eye] [name+icon] [opacity slider] [merge] [×]
 *   mask sub-rows
 *     [eye] [name] [merge-up?] [×]
 *
 * Reads/writes shared `state` directly (layers, activeLayerId,
 * layerOffsets, imgWidth, imgHeight, lassoPoints/lassoActive,
 * wandMask, maskCanvas/maskCtx, nextLayerId). Function deps are
 * orchestration callbacks still living in galleryEditor.js.
 *
 * Returns `{ render }` so the recursive self-call works via closure
 * over `render` rather than module-state lookup.
 *
 * @param {{
 *   composite:                       () => void,
 *   saveState:                       (label?: string) => void,
 *   showLayerThumb:                  (rowEl: HTMLElement, layer: object) => void,
 *   hideLayerThumb:                  () => void,
 *   loadLayerAlphaAsSelection:       (layer: object) => void,
 *   loadMaskAsSelection?:            (layer: object, mask: object) => void,
 *   getDocumentSelection?:           () => HTMLCanvasElement | null,
 *   openFxPopup:                     (layer: object, anchor: HTMLElement) => void,
 *   editAdjLayer:                    (layer: object, adj: object, anchor: HTMLElement) => void,
 *   createLayer:                     (name: string, w: number, h: number) => object,
 *   renderLayer?:                    (layer: object) => HTMLCanvasElement,
 *   replacePlacedLayer?:             (layer: object) => void,
 *   rasterizePlacedLayer?:           (layer: object) => void,
 *   lassoToMask:                     () => void,
 *   wandToMask:                      () => void,
 *   getActiveMaskLayer:              () => object | null,
 *   syncFxPanelToActiveLayerIfPresent: () => void,
 *   dragSortModule:                  object | null,
 *   uiModule:                        object | null,
 * }} deps
 */
import { state } from './state.js';
import { rasterizeTextLayer } from './text-layer.js';
import { rasterizeShapeLayer } from './shape-layer.js';
import {
  clonePlacedData,
  createPlacedData,
  rasterizePlacedLayer as rasterizePlacedPixels,
  renderPlacedLayer,
} from './placed-layer.js';
import {
  layerHasAdjustments,
  isLayerEmpty,
  isMaskCanvasEmpty,
  adjLayerLabel,
  ADJ_ICONS,
} from './layer-helpers.js';
import { applyAdjustment } from './fx/pixel-pass.js';
import { normalizeEffect, effectLabel } from './effects.js';
import { mergeLayerDownAtIndex } from './wire-merge-buttons.js';
import {
  normalizeLayerSelection,
  selectedLayers,
  selectAllLayers,
  selectLayerRange,
  selectOnlyLayer,
  toggleLayerSelection,
} from './layer-selection.js';
import {
  allLayerIdsInGroup,
  createGroupFromSelection,
  groupAncestors,
  groupDepth,
  groupSiblingUnits,
  groupsForLayer,
  isLayerPixelLocked,
  layerHasAnyLock,
  normalizeLayerGroups,
  normalizeLayerLocks,
  reorderGroupAmongSiblings,
  ungroupLayers,
} from './layer-groups.js';
import {
  canToggleLayerClipping,
  normalizeLayerClipping,
} from './layer-clipping.js';
import {
  alignSelectedLayers,
  distributeSelectedLayers,
} from './layer-geometry.js';
import { invertMaskInPlace, normalizeMaskDensity, normalizeMaskFeather } from './mask-utils.js';

const EYE_OPEN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF  = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';
const EYE_OPEN_SM = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF_SM  = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';

export function createLayerPanelRenderer(deps) {
  const {
    composite, saveState, showLayerThumb, hideLayerThumb,
    loadLayerAlphaAsSelection, loadMaskAsSelection, getDocumentSelection,
    openFxPopup, openLayerStyles, editAdjLayer, editRetainedEffect, addEffectMask, rasterizeEffects,
    createLayer, renderLayer, replacePlacedLayer, rasterizePlacedLayer,
    lassoToMask, wandToMask, getActiveMaskLayer,
    syncFxPanelToActiveLayerIfPresent, onSelectLayer,
    dragSortModule, uiModule,
  } = deps;

  function shouldIgnoreLayerTap() {
    return Date.now() < (window.__geSuppressLayerTapUntil || 0);
  }

  function rowTargets(layer) {
    const selected = selectedLayers(state);
    return selected.length > 1 && selected.some(item => item.id === layer.id)
      ? selected
      : [layer];
  }

  let previewSession;
  const previewSignatures = new Map();

  function refreshPreviews() {
    const list = document.getElementById('ge-layers-list');
    if (!list || !state.editorOpen) return;
    if (previewSession !== state.editorSessionToken) {
      previewSession = state.editorSessionToken;
      previewSignatures.clear();
    }
    const initialized = previewSignatures.size > 0;
    const currentIds = new Set();
    for (const row of list.children) {
      const id = row.dataset.layerId || row.dataset.groupId || row.dataset.maskId || row.dataset.groupMaskId;
      const thumb = row.querySelector('.ge-layer-inline-thumb');
      if (!id || !thumb) continue;
      currentIds.add(id);
      thumb._refreshPreview?.();
      const layer = state.layers.find(item => item.id === id);
      const signature = thumb.toDataURL() + JSON.stringify(layer ? {
        offset: state.layerOffsets.get(id), opacity: layer.opacity,
        visible: layer.visible, locked: layer.locked, name: layer.name,
      } : {});
      const previous = previewSignatures.get(id);
      previewSignatures.set(id, signature);
      if (initialized && previous !== signature) {
        row.classList.remove('ge-layer-action-flash');
        void row.offsetWidth;
        row.classList.add('ge-layer-action-flash');
        row.addEventListener('animationend', () => row.classList.remove('ge-layer-action-flash'), { once: true });
      }
    }
    for (const id of previewSignatures.keys()) {
      if (!currentIds.has(id)) previewSignatures.delete(id);
    }
  }

  function createInlineThumbnail(source, title, extraClass = '') {
    const thumb = document.createElement('canvas');
    thumb.className = `ge-layer-inline-thumb${extraClass ? ` ${extraClass}` : ''}`;
    thumb.width = 68;
    thumb.height = 52;
    thumb.title = title;
    thumb.setAttribute('role', 'img');
    thumb.setAttribute('aria-label', title);
    const ctx = thumb.getContext('2d');
    const draw = () => {
      const image = typeof source === 'function' ? source() : source;
      if (!ctx || !image?.width || !image?.height) return;
      const tile = 8;
      for (let y = 0; y < thumb.height; y += tile) {
        for (let x = 0; x < thumb.width; x += tile) {
          ctx.fillStyle = ((x / tile + y / tile) & 1) ? '#464646' : '#303030';
          ctx.fillRect(x, y, tile, tile);
        }
      }
      const scale = Math.min(thumb.width / image.width, thumb.height / image.height);
      const width = image.width * scale;
      const height = image.height * scale;
      ctx.drawImage(image, (thumb.width - width) / 2, (thumb.height - height) / 2, width, height);
    };
    thumb._refreshPreview = draw;
    draw();
    return thumb;
  }

  function createMaskThumbnail(mask) {
    return createInlineThumbnail(() => mask.canvas, `${mask.name || 'Mask'} preview`, 'ge-mask-inline-thumb');
  }

  function applyLayerMask(layer, mask) {
    if (!layer?.canvas || !mask?.canvas || mask.mode !== 'layer') return;
    saveState(`Apply mask "${mask.name || 'Layer Mask'}"`);
    const baked = document.createElement('canvas');
    baked.width = layer.canvas.width;
    baked.height = layer.canvas.height;
    const ctx = baked.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(layer.canvas, 0, 0);
    ctx.globalCompositeOperation = 'destination-in';
    ctx.globalAlpha = normalizeMaskDensity(mask.density);
    const feather = normalizeMaskFeather(mask.feather);
    ctx.filter = feather > 0 ? `blur(${feather}px)` : 'none';
    if (mask.space === 'document') {
      const offset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
      ctx.drawImage(mask.canvas, -(Number(offset.x) || 0), -(Number(offset.y) || 0));
    } else {
      ctx.drawImage(mask.canvas, Number(mask.offset?.x) || 0, Number(mask.offset?.y) || 0);
    }
    ctx.globalAlpha = 1;
    ctx.filter = 'none';
    ctx.globalCompositeOperation = 'source-over';
    layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    layer.ctx.drawImage(baked, 0, 0);
    layer.masks = (layer.masks || []).filter(item => item.id !== mask.id);
    if (layer.activeMaskId === mask.id) layer.activeMaskId = null;
    state.maskCanvas = null;
    state.maskCtx = null;
    state.maskInspectMode = false;
    composite();
    render();
  }

  function createMaskPropertiesControl(mask, label, onApply = null) {
    const details = document.createElement('details');
    details.className = 'ge-mask-properties';
    details.addEventListener('click', event => event.stopPropagation());
    const summary = document.createElement('summary');
    summary.title = `${label} properties`;
    summary.setAttribute('aria-label', summary.title);
    summary.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8h4m4 0h10M3 16h10m4 0h4M7 5v6m10 2v6"/></svg>';
    const panel = document.createElement('div');
    panel.className = 'ge-mask-properties-panel';
    panel.setAttribute('popover', 'manual');
    let positionFrame = 0;
    const positionPanel = () => {
      if (!details.open || !details.isConnected) {
        if (panel.matches(':popover-open')) panel.hidePopover();
        return;
      }
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0;
      const top = viewport?.offsetTop || 0;
      const width = viewport?.width || window.innerWidth;
      const height = viewport?.height || window.innerHeight;
      panel.style.maxWidth = `${Math.max(0, width - 12)}px`;
      panel.style.maxHeight = `${Math.max(0, height - 12)}px`;
      const rect = summary.getBoundingClientRect();
      const bounds = panel.getBoundingClientRect();
      const y = rect.bottom + 4 + bounds.height <= top + height - 6
        ? rect.bottom + 4 : rect.top - bounds.height - 4;
      panel.style.left = `${Math.max(left + 6, Math.min(left + width - bounds.width - 6, rect.right - bounds.width))}px`;
      panel.style.top = `${Math.max(top + 6, Math.min(top + height - bounds.height - 6, y))}px`;
      positionFrame = requestAnimationFrame(positionPanel);
    };
    details.addEventListener('toggle', () => {
      cancelAnimationFrame(positionFrame);
      if (!details.open) {
        if (panel.matches(':popover-open')) panel.hidePopover();
        return;
      }
      panel.showPopover();
      positionPanel();
    });
    const makeRange = (className, title, min, max, step, value, update, historyLabel) => {
      const row = document.createElement('label');
      row.className = 'ge-mask-property-row';
      const text = document.createElement('span');
      text.textContent = title;
      const output = document.createElement('span');
      const input = document.createElement('input');
      input.type = 'range';
      input.className = className;
      input.min = String(min);
      input.max = String(max);
      input.step = String(step);
      input.value = String(value);
      input.title = title;
      input.setAttribute('aria-label', title);
      output.textContent = className === 'ge-mask-density' ? `${value}%` : `${value}px`;
      let initial = input.value;
      let saved = false;
      input.addEventListener('input', event => {
        event.stopPropagation();
        if (!saved && input.value !== initial) {
          saveState(historyLabel);
          saved = true;
        }
        const numeric = Number(input.value);
        output.textContent = className === 'ge-mask-density' ? `${numeric}%` : `${numeric}px`;
        update(numeric);
        composite();
      });
      input.addEventListener('change', event => {
        event.stopPropagation();
        initial = input.value;
        saved = false;
      });
      row.append(text, input, output);
      return row;
    };
    panel.appendChild(makeRange(
      'ge-mask-density', 'Density', 0, 100, 1,
      Math.round(normalizeMaskDensity(mask.density) * 100),
      value => { mask.density = value / 100; },
      `Adjust ${label} density`,
    ));
    panel.appendChild(makeRange(
      'ge-mask-feather', 'Feather', 0, 200, 1,
      Math.round(normalizeMaskFeather(mask.feather)),
      value => { mask.feather = value; },
      `Adjust ${label} feather`,
    ));
    if (onApply) {
      const apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'ge-mask-apply-btn';
      apply.textContent = 'Apply mask';
      apply.title = 'Bake this mask into the layer and remove the mask';
      apply.addEventListener('click', event => {
        event.stopPropagation();
        onApply();
      });
      panel.appendChild(apply);
    }
    details.append(summary, panel);
    return details;
  }

  function createMaskInspectionControl(mask, owner, isGroup = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ge-layer-btn ge-mask-inspect-btn';
    button.title = 'Inspect mask';
    button.setAttribute('aria-label', button.title);
    const active = state.maskInspectMode && (isGroup
      ? owner.activeMaskId === mask.id
      : owner.activeMaskId === mask.id);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    if (active) button.classList.add('active');
    button.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"></rect><path d="M4 12h16M12 4v16"></path></svg>';
    button.addEventListener('click', event => {
      event.stopPropagation();
      if (isGroup) {
        state.activeGroupId = owner.id;
        owner.activeMaskId = mask.id;
        state.selectedLayerIds = allLayerIdsInGroup(state, owner);
        state.activeLayerId = state.selectedLayerIds[state.selectedLayerIds.length - 1] || state.activeLayerId;
        state.selectionAnchorId = state.activeLayerId;
      } else {
        state.activeGroupId = null;
        owner.activeMaskId = mask.id;
        selectOnlyLayer(state, owner.id);
      }
      state.maskCanvas = mask.canvas;
      state.maskCtx = mask.ctx;
      state.maskInspectMode = !state.maskInspectMode;
      composite();
      render();
    });
    return button;
  }

  function closeLayerLockMenu() {
    const menu = document.getElementById('ge-layer-lock-menu');
    if (!menu) return;
    if (menu._awayHandler) document.removeEventListener('pointerdown', menu._awayHandler, true);
    if (menu._escHandler) document.removeEventListener('keydown', menu._escHandler, true);
    menu.remove();
  }

  function openLayerLockMenu(layer, anchor) {
    closeLayerLockMenu();
    const targets = rowTargets(layer);
    const menu = document.createElement('div');
    menu.id = 'ge-layer-lock-menu';
    menu.className = 'ge-layer-lock-menu ge-frosted';
    const options = [
      { key: 'all', label: 'Lock all' },
      { key: 'pixels', label: 'Image pixels' },
      { key: 'transparency', label: 'Transparent pixels' },
      { key: 'position', label: 'Position' },
    ];
    const checked = key => targets.every(item => key === 'all'
      ? !!item.locked
      : normalizeLayerLocks(item.locks)[key]);
    for (const option of options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ge-layer-lock-option';
      button.dataset.lockType = option.key;
      button.setAttribute('role', 'menuitemcheckbox');
      button.setAttribute('aria-checked', String(checked(option.key)));
      button.innerHTML = `<span class="ge-layer-lock-check" aria-hidden="true">${checked(option.key) ? '&#10003;' : ''}</span><span>${option.label}</span>`;
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const next = !checked(option.key);
        const scope = targets.length > 1 ? `${targets.length} layers` : `"${layer.name}"`;
        saveState(`${next ? 'Lock' : 'Unlock'} ${option.label.toLowerCase()} on ${scope}`);
        for (const item of targets) {
          if (option.key === 'all') item.locked = next;
          else {
            item.locks = normalizeLayerLocks(item.locks);
            item.locks[option.key] = next;
          }
        }
        closeLayerLockMenu();
        render();
      });
      menu.appendChild(button);
    }
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    const mobile = window.matchMedia('(max-width: 700px)').matches;
    if (mobile) {
      menu.style.left = '8px';
      menu.style.right = '8px';
      menu.style.bottom = '8px';
    } else {
      const width = menu.offsetWidth || 176;
      const height = menu.offsetHeight || 132;
      menu.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width))}px`;
      menu.style.top = `${Math.max(8, Math.min(window.innerHeight - height - 8, rect.bottom + 4))}px`;
    }
    menu._awayHandler = event => {
      if (!menu.contains(event.target) && event.target !== anchor) closeLayerLockMenu();
    };
    menu._escHandler = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      closeLayerLockMenu();
      anchor.focus();
    };
    setTimeout(() => document.addEventListener('pointerdown', menu._awayHandler, true), 0);
    document.addEventListener('keydown', menu._escHandler, true);
    menu.querySelector('button')?.focus();
  }

  function wireGroupDrag(handle, row, group) {
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || group.locked) return;
      const units = groupSiblingUnits(state, group.parentId || null);
      if (units.length < 2) return;
      const list = row.parentElement;
      const rowForUnit = unit => Array.from(list.children).find(element =>
        unit.type === 'group'
          ? element.dataset.groupId === unit.id
          : element.dataset.layerId === unit.id
      );
      const visibleOthers = [...units].reverse()
        .filter(unit => !(unit.type === 'group' && unit.id === group.id))
        .map(unit => ({ unit, row: rowForUnit(unit) }))
        .filter(item => item.row);
      if (!visibleOthers.length) return;
      event.preventDefault();
      event.stopPropagation();
      const startY = event.clientY;
      let dragging = false;
      let targetIndex = units.findIndex(unit => unit.type === 'group' && unit.id === group.id);
      const line = document.createElement('div');
      line.className = 'ge-group-drop-line';

      const move = moveEvent => {
        if (!dragging && Math.abs(moveEvent.clientY - startY) < 4) return;
        if (!dragging) {
          dragging = true;
          row.classList.add('group-dragging');
          if (getComputedStyle(list).position === 'static') list.style.position = 'relative';
          list.appendChild(line);
        }
        moveEvent.preventDefault();
        const visualIndex = visibleOthers.filter(item => {
          const rect = item.row.getBoundingClientRect();
          return moveEvent.clientY > rect.top + rect.height / 2;
        }).length;
        targetIndex = units.length - 1 - visualIndex;
        const listRect = list.getBoundingClientRect();
        const anchor = visibleOthers[Math.min(visualIndex, visibleOthers.length - 1)];
        const anchorRect = anchor.row.getBoundingClientRect();
        const top = visualIndex < visibleOthers.length ? anchorRect.top : anchorRect.bottom;
        line.style.top = `${top - listRect.top + list.scrollTop}px`;
      };
      const end = () => {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', end);
        document.removeEventListener('pointercancel', end);
        row.classList.remove('group-dragging');
        line.remove();
        window.__geSuppressLayerTapUntil = Date.now() + 250;
        if (!dragging) return;
        const currentIndex = units.findIndex(unit => unit.type === 'group' && unit.id === group.id);
        if (targetIndex === currentIndex) return;
        saveState(`Reorder group "${group.name}"`);
        const result = reorderGroupAmongSiblings(state, group.id, targetIndex);
        if (!result?.changed) return;
        normalizeLayerClipping(state);
        composite();
        render();
      };
      document.addEventListener('pointermove', move, { passive: false });
      document.addEventListener('pointerup', end);
      document.addEventListener('pointercancel', end);
    });
  }

  async function deleteLayers(layers) {
    const targets = layers.filter(layer => state.layers.includes(layer));
    if (!targets.length || targets.length >= state.layers.length) return false;
    if (targets.some(layer => layer.isBase) && uiModule?.styledConfirm) {
      const ok = await uiModule.styledConfirm(
        `Delete ${targets.length === 1 ? 'the original photo layer' : `${targets.length} selected layers, including the original`}? Ctrl+Z brings ${targets.length === 1 ? 'it' : 'them'} back.`,
        { confirmText: 'Delete', cancelText: 'Cancel', danger: true },
      );
      if (!ok) return false;
    }
    saveState(targets.length === 1
      ? `Delete layer "${targets[0].name}"`
      : `Delete ${targets.length} layers`);
    const ids = new Set(targets.map(layer => layer.id));
    state.layers = state.layers.filter(layer => !ids.has(layer.id));
    for (const id of ids) state.layerOffsets.delete(id);
    normalizeLayerClipping(state);
    const fallback = state.layers[state.layers.length - 1];
    selectOnlyLayer(state, fallback.id);
    state.maskCanvas = null;
    state.maskCtx = null;
    composite();
    render();
    return true;
  }

  function syncSelectionUi() {
    const selectedIds = new Set(normalizeLayerSelection(state));
    const selected = selectedLayers(state);
    document.querySelectorAll('.ge-layers-list .ge-layer-item[data-layer-id]').forEach(row => {
      const id = row.dataset.layerId;
      const layer = state.layers.find(item => item.id === id);
      const active = id === state.activeLayerId;
      const maskActive = !!(active && layer?.activeMaskId && layer.masks?.some(mask => mask.id === layer.activeMaskId));
      row.classList.toggle('active', active && !maskActive);
      row.classList.toggle('active-parent', active && maskActive);
      row.classList.toggle('selected', selectedIds.has(id));
    });
    const bar = document.getElementById('ge-layer-selection-bar');
    const count = document.getElementById('ge-layer-selection-count');
    if (bar) bar.hidden = selected.length < 2;
    if (count) count.textContent = `${selected.length} layers`;
    const deleteButton = document.getElementById('ge-selected-delete');
    if (deleteButton) deleteButton.disabled = selected.length >= state.layers.length;
    const groupButton = document.getElementById('ge-group-selected');
    if (groupButton) groupButton.disabled = selected.length < 2;
  }

  function closeAlignMenu() {
    const menu = document.getElementById('ge-layer-align-menu');
    if (!menu) return;
    menu._awayHandler && document.removeEventListener('pointerdown', menu._awayHandler, true);
    menu._escHandler && document.removeEventListener('keydown', menu._escHandler, true);
    menu.remove();
  }

  function openAlignMenu(anchor) {
    closeAlignMenu();
    const menu = document.createElement('div');
    menu.id = 'ge-layer-align-menu';
    menu.className = 'ge-layer-lock-menu ge-layer-align-menu ge-frosted';
    const options = [
      ['left', 'Align left'], ['center', 'Align center'], ['right', 'Align right'],
      ['top', 'Align top'], ['middle', 'Align middle'], ['bottom', 'Align bottom'],
      ['horizontal', 'Distribute horizontally'], ['vertical', 'Distribute vertically'],
    ];
    for (const [action, label] of options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ge-layer-lock-option';
      button.textContent = label;
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const selected = selectedLayers(state);
        saveState(`${label} (${selected.length} layers)`);
        const changed = action === 'horizontal' || action === 'vertical'
          ? distributeSelectedLayers(state, selected, action)
          : alignSelectedLayers(state, selected, action);
        if (changed) {
          composite();
          render();
        }
        closeAlignMenu();
      });
      menu.appendChild(button);
    }
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    const mobile = window.matchMedia('(max-width: 700px)').matches;
    if (mobile) {
      menu.style.left = '8px';
      menu.style.right = '8px';
      menu.style.bottom = '8px';
    } else {
      const width = menu.offsetWidth || 210;
      const height = menu.offsetHeight || 220;
      menu.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width))}px`;
      menu.style.top = `${Math.max(8, Math.min(window.innerHeight - height - 8, rect.bottom + 4))}px`;
    }
    menu._awayHandler = event => {
      if (!menu.contains(event.target) && event.target !== anchor) closeAlignMenu();
    };
    menu._escHandler = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeAlignMenu();
      anchor.focus();
    };
    setTimeout(() => document.addEventListener('pointerdown', menu._awayHandler, true), 0);
    document.addEventListener('keydown', menu._escHandler, true);
    menu.querySelector('button')?.focus();
  }

  function wireSelectionActions() {
    const selectAll = document.getElementById('ge-select-all-layers');
    if (selectAll && !selectAll.dataset.wired) {
      selectAll.dataset.wired = 'true';
      selectAll.addEventListener('click', () => {
        selectAllLayers(state);
        render();
      });
    }
    const groupSelected = document.getElementById('ge-group-selected');
    if (groupSelected && !groupSelected.dataset.wired) {
      groupSelected.dataset.wired = 'true';
      groupSelected.addEventListener('click', () => {
        const selected = selectedLayers(state);
        if (selected.length < 2) return;
        saveState(`Group ${selected.length} layers`);
        const group = createGroupFromSelection(state);
        if (!group) return;
        normalizeLayerClipping(state);
        composite();
        render();
        uiModule?.showToast?.(`${group.name} created`);
      });
    }
    const visibility = document.getElementById('ge-selected-visibility');
    if (visibility && !visibility.dataset.wired) {
      visibility.dataset.wired = 'true';
      visibility.addEventListener('click', () => {
        const selected = selectedLayers(state);
        if (selected.length < 2) return;
        const visible = !selected.every(layer => layer.visible !== false);
        saveState(`${visible ? 'Show' : 'Hide'} ${selected.length} layers`);
        for (const layer of selected) layer.visible = visible;
        composite();
        render();
      });
    }
    const lock = document.getElementById('ge-selected-lock');
    if (lock && !lock.dataset.wired) {
      lock.dataset.wired = 'true';
      lock.addEventListener('click', () => {
        const selected = selectedLayers(state);
        if (selected.length < 2) return;
        const locked = !selected.every(layer => layer.locked);
        saveState(`${locked ? 'Lock' : 'Unlock'} ${selected.length} layers`);
        for (const layer of selected) layer.locked = locked;
        render();
      });
    }
    const align = document.getElementById('ge-selected-align');
    if (align && !align.dataset.wired) {
      align.dataset.wired = 'true';
      align.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        openAlignMenu(align);
      });
    }
    const remove = document.getElementById('ge-selected-delete');
    if (remove && !remove.dataset.wired) {
      remove.dataset.wired = 'true';
      remove.addEventListener('click', () => deleteLayers(selectedLayers(state)));
    }
  }

  function appendGroupRow(list, group, depth = 0) {
    const row = document.createElement('div');
    row.className = 'ge-layer-group-row' + (state.activeGroupId === group.id ? ' active' : '');
    row.dataset.groupId = group.id;
    row.style.setProperty('--group-depth', String(depth));
    row.tabIndex = 0;
    row.setAttribute('aria-label', `${group.name} group`);
    row.setAttribute('aria-pressed', state.activeGroupId === group.id ? 'true' : 'false');

    const handle = document.createElement('span');
    handle.className = 'ge-layer-drag ge-layer-group-drag';
    handle.title = group.locked ? 'Unlock group to reorder' : 'Drag group to reorder';
    handle.setAttribute('aria-label', handle.title);
    handle.innerHTML = '<svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor"><circle cx="2" cy="2" r="1"/><circle cx="6" cy="2" r="1"/><circle cx="2" cy="7" r="1"/><circle cx="6" cy="7" r="1"/><circle cx="2" cy="12" r="1"/><circle cx="6" cy="12" r="1"/></svg>';
    wireGroupDrag(handle, row, group);

    const collapse = document.createElement('button');
    collapse.className = 'ge-layer-group-toggle' + (group.collapsed ? '' : ' expanded');
    collapse.title = group.collapsed ? 'Expand group' : 'Collapse group';
    collapse.setAttribute('aria-label', collapse.title);
    collapse.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>';
    collapse.addEventListener('click', event => {
      event.stopPropagation();
      saveState(`${group.collapsed ? 'Expand' : 'Collapse'} "${group.name}"`);
      group.collapsed = !group.collapsed;
      render();
    });

    const visibility = document.createElement('button');
    visibility.className = 'ge-layer-vis' + (group.visible ? ' visible' : '');
    visibility.innerHTML = group.visible ? EYE_OPEN : EYE_OFF;
    visibility.title = group.visible ? 'Hide group' : 'Show group';
    visibility.addEventListener('click', event => {
      event.stopPropagation();
      saveState(`${group.visible ? 'Hide' : 'Show'} group "${group.name}"`);
      group.visible = !group.visible;
      composite();
      render();
    });

    const groupLayers = allLayerIdsInGroup(state, group)
      .map(id => state.layers.find(layer => layer.id === id))
      .filter(layer => layer && layer.visible !== false);
    const groupPreview = state.groupCompositeCanvases?.get?.(group.id)
      || groupLayers.slice().reverse()
        .map(layer => renderLayer?.(layer) || layer.canvas)
        .find(canvas => canvas?.width && canvas?.height);
    const groupThumb = createInlineThumbnail(groupPreview, `${group.name} preview`, 'ge-group-inline-thumb');

    const name = document.createElement('span');
    name.className = 'ge-layer-group-name';
    name.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h7l2 2h9v10H3z"/></svg><span></span>';
    name.lastElementChild.textContent = group.name;
    // Keep the row-selection click from re-rendering the name span between
    // the two clicks that make up a double-click rename gesture.
    name.addEventListener('click', event => event.stopPropagation());
    name.addEventListener('dblclick', event => {
      event.stopPropagation();
      const input = document.createElement('input');
      input.className = 'ge-layer-name-input';
      input.value = group.name;
      name.replaceWith(input);
      input.focus();
      const finish = () => {
        const value = input.value.trim();
        if (value && value !== group.name) {
          saveState(`Rename group "${group.name}"`);
          group.name = value;
        }
        render();
      };
      input.addEventListener('blur', finish, { once: true });
      input.addEventListener('keydown', event => { if (event.key === 'Enter') input.blur(); });
    });

    const opacity = document.createElement('input');
    opacity.type = 'range';
    opacity.min = '0';
    opacity.max = '100';
    opacity.value = String(Math.round(group.opacity * 100));
    opacity.className = 'ge-layer-opacity';
    opacity.title = 'Group opacity';
    opacity.addEventListener('pointerdown', event => {
      event.stopPropagation();
      state.selectedLayerIds = allLayerIdsInGroup(state, group);
      state.activeLayerId = state.selectedLayerIds[state.selectedLayerIds.length - 1] || null;
      state.selectionAnchorId = state.activeLayerId;
      state.activeGroupId = group.id;
    });
    let opacitySaved = false;
    opacity.addEventListener('input', event => {
      event.stopPropagation();
      const nextOpacity = Number(opacity.value) / 100;
      if (Math.abs(group.opacity - nextOpacity) < 0.0001) return;
      if (!opacitySaved) {
        saveState(`Change opacity of group "${group.name}"`);
        opacitySaved = true;
      }
      group.opacity = nextOpacity;
      composite();
    });
    opacity.addEventListener('change', () => { opacitySaved = false; });

    const controls = document.createElement('div');
    controls.className = 'ge-layer-controls';
    const effectsButton = document.createElement('button');
    effectsButton.className = 'ge-layer-btn';
    effectsButton.type = 'button';
    effectsButton.textContent = 'FX';
    effectsButton.title = group.effects?.length ? `Edit group effects (${group.effects.length}) in Filter menu` : 'Add group effect in Filter menu';
    effectsButton.addEventListener('click', event => {
      event.stopPropagation();
      state.activeGroupId = group.id;
      state.selectedLayerIds = allLayerIdsInGroup(state, group);
      state.activeLayerId = state.selectedLayerIds[state.selectedLayerIds.length - 1] || null;
      document.getElementById('ge-filter-menu-btn')?.click();
    });
    const maskButton = document.createElement('button');
    maskButton.className = 'ge-layer-btn ge-layer-mask-btn ge-group-mask-btn';
    maskButton.title = 'Add group mask (white reveals; erase hides)';
    maskButton.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="5" fill="currentColor"/></svg>';
    maskButton.addEventListener('click', event => {
      event.stopPropagation();
      saveState(`Add group mask to "${group.name}"`);
      const canvas = document.createElement('canvas');
      canvas.width = state.imgWidth;
      canvas.height = state.imgHeight;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      if (!Array.isArray(group.masks)) group.masks = [];
      const mask = {
        id: `mask-${state.nextLayerId++}`,
        name: group.masks.length ? `Group Mask ${group.masks.length + 1}` : 'Group Mask',
        visible: true,
        mode: 'group',
        space: 'document',
        canvas,
        ctx,
      };
      group.masks.push(mask);
      group.activeMaskId = mask.id;
      state.activeGroupId = group.id;
      state.selectedLayerIds = allLayerIdsInGroup(state, group);
      state.activeLayerId = state.selectedLayerIds[state.selectedLayerIds.length - 1];
      state.maskCanvas = canvas;
      state.maskCtx = ctx;
      composite();
      render();
      uiModule?.showToast?.('Group mask added — use Eraser to hide, Brush to reveal');
    });
    const lock = document.createElement('button');
    lock.className = 'ge-layer-btn ge-layer-lock-btn' + (group.locked ? ' active' : '');
    lock.title = group.locked ? 'Unlock group' : 'Lock group';
    lock.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>';
    lock.addEventListener('click', event => {
      event.stopPropagation();
      saveState(`${group.locked ? 'Unlock' : 'Lock'} group "${group.name}"`);
      group.locked = !group.locked;
      render();
    });
    const ungroup = document.createElement('button');
    ungroup.className = 'ge-layer-btn';
    ungroup.title = group.masks?.length ? 'Delete group masks before ungrouping' : 'Ungroup layers';
    ungroup.disabled = !!group.masks?.length;
    ungroup.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h7l2 2h9v10H3z"/><path d="M8 12h8"/></svg>';
    ungroup.addEventListener('click', event => {
      event.stopPropagation();
      saveState(`Ungroup "${group.name}"`);
      ungroupLayers(state, group.id);
      normalizeLayerClipping(state);
      composite();
      render();
    });
    const opacityRow = document.createElement('div');
    opacityRow.className = 'ge-layer-opacity-row';
    opacityRow.append(opacity);
    controls.append(effectsButton, maskButton, ungroup);
    row.append(handle, collapse, visibility, groupThumb, name, lock, opacityRow, controls);
    row.addEventListener('click', event => {
      if (event.target.closest('button,input')) return;
      state.selectedLayerIds = allLayerIdsInGroup(state, group);
      state.activeLayerId = state.selectedLayerIds[state.selectedLayerIds.length - 1];
      state.selectionAnchorId = state.activeLayerId;
      state.activeGroupId = group.id;
      group.activeMaskId = null;
      state.maskCanvas = null;
      state.maskCtx = null;
      document.querySelectorAll('.ge-layer-group-row').forEach(item => {
        item.classList.toggle('active', item.dataset.groupId === group.id);
      });
      syncSelectionUi();
      render();
      onSelectLayer?.(state.layers.find(layer => layer.id === state.activeLayerId));
    });
    row.addEventListener('keydown', event => {
      if (event.target !== row || (event.key !== 'Enter' && event.key !== ' ')) return;
      event.preventDefault();
      row.click();
    });
    list.appendChild(row);

    // Group effects sit below the group row and above its masks. They use
    // the same retained-effect controls as layer effects, except that
    // rasterization and layer-local masks do not apply to a group.
    for (let effectIndex = 0; effectIndex < (group.effects || []).length; effectIndex += 1) {
      const effect = normalizeEffect(group.effects[effectIndex]);
      group.effects[effectIndex] = effect;
      const sub = document.createElement('div');
      sub.className = 'ge-layer-item ge-adj-sub-item ge-effect-sub-item ge-group-effect-sub-item';
      sub.dataset.effectId = effect.id;
      sub.dataset.groupEffectId = group.id;

      const visibility = document.createElement('button');
      visibility.className = 'ge-layer-vis' + (effect.visible ? ' visible' : '');
      visibility.innerHTML = effect.visible ? EYE_OPEN_SM : EYE_OFF_SM;
      visibility.title = effect.visible ? 'Hide group effect' : 'Show group effect';
      visibility.addEventListener('click', event => {
        event.stopPropagation();
        saveState(`${effect.visible ? 'Hide' : 'Show'} group ${effectLabel(effect.type)}`);
        effect.visible = !effect.visible;
        composite();
        render();
      });

      const effectName = document.createElement('span');
      effectName.className = 'ge-layer-name ge-adj-sub-name';
      effectName.innerHTML = '<span class="ge-adj-sub-icon">◌</span><span></span>';
      effectName.lastElementChild.textContent = effectLabel(effect.type);
      effectName.title = 'Edit group effect';
      effectName.addEventListener('click', event => {
        event.stopPropagation();
        editRetainedEffect?.(group, effect, effectName);
      });

      const effectOpacity = document.createElement('input');
      effectOpacity.type = 'range';
      effectOpacity.min = '0';
      effectOpacity.max = '100';
      effectOpacity.value = String(Math.round(effect.opacity * 100));
      effectOpacity.className = 'ge-layer-opacity';
      effectOpacity.title = 'Group effect opacity';
      let effectOpacitySaved = false;
      effectOpacity.addEventListener('input', event => {
        event.stopPropagation();
        const next = Number(effectOpacity.value) / 100;
        if (Math.abs(effect.opacity - next) < 0.0001) return;
        if (!effectOpacitySaved) {
          saveState(`Change group ${effectLabel(effect.type)} opacity`);
          effectOpacitySaved = true;
        }
        effect.opacity = next;
        composite();
      });
      effectOpacity.addEventListener('change', () => { effectOpacitySaved = false; });

      const effectControls = document.createElement('div');
      effectControls.className = 'ge-layer-controls';
      for (const [direction, symbol, title] of [[-1, '↑', 'Move group effect up'], [1, '↓', 'Move group effect down']]) {
        const move = document.createElement('button');
        move.className = 'ge-layer-btn';
        move.type = 'button';
        move.textContent = symbol;
        move.title = title;
        move.disabled = effectIndex + direction < 0 || effectIndex + direction >= group.effects.length;
        move.addEventListener('click', event => {
          event.stopPropagation();
          const nextIndex = effectIndex + direction;
          if (nextIndex < 0 || nextIndex >= group.effects.length) return;
          saveState(`${title} ${effectLabel(effect.type)}`);
          [group.effects[effectIndex], group.effects[nextIndex]] = [group.effects[nextIndex], group.effects[effectIndex]];
          composite();
          render();
        });
        effectControls.appendChild(move);
      }
      const remove = document.createElement('button');
      remove.className = 'ge-layer-btn danger';
      remove.type = 'button';
      remove.textContent = '×';
      remove.title = 'Delete group effect';
      remove.addEventListener('click', event => {
        event.stopPropagation();
        saveState(`Delete group ${effectLabel(effect.type)}`);
        group.effects.splice(effectIndex, 1);
        composite();
        render();
      });
      effectControls.appendChild(remove);
      sub.append(visibility, effectName, effectOpacity, effectControls);
      list.appendChild(sub);
    }

    for (const mask of group.masks || []) {
      const sub = document.createElement('div');
      sub.className = 'ge-layer-item ge-adj-sub-item ge-mask-sub-item ge-group-mask-sub-item' +
        (state.activeGroupId === group.id && group.activeMaskId === mask.id ? ' active' : '');
      sub.dataset.groupMaskId = mask.id;
      sub.style.setProperty('--group-mask-depth', String(depth + 1));
      const visibility = document.createElement('button');
      visibility.className = 'ge-layer-vis' + (mask.visible !== false ? ' visible' : '');
      visibility.innerHTML = mask.visible !== false ? EYE_OPEN_SM : EYE_OFF_SM;
      visibility.title = mask.visible !== false ? 'Disable group mask' : 'Enable group mask';
      visibility.addEventListener('click', event => {
        event.stopPropagation();
        saveState(`${mask.visible !== false ? 'Disable' : 'Enable'} group mask "${mask.name}"`);
        mask.visible = mask.visible === false;
        composite();
        render();
      });
      const name = document.createElement('span');
      name.className = 'ge-layer-name ge-adj-sub-name';
      name.innerHTML = '<span class="ge-adj-sub-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="5" fill="currentColor"/></svg></span><span></span>';
      name.lastElementChild.textContent = mask.name || 'Group Mask';
          const maskControls = document.createElement('div');
          maskControls.className = 'ge-layer-controls';
          const invertGroupMask = document.createElement('button');
          invertGroupMask.className = 'ge-layer-btn';
          invertGroupMask.title = 'Invert group mask';
          invertGroupMask.setAttribute('aria-label', invertGroupMask.title);
          invertGroupMask.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 17h16"/><path d="m8 4-4 3 4 3M16 14l4 3-4 3"/></svg>';
          invertGroupMask.addEventListener('click', event => {
            event.stopPropagation();
            saveState(`Invert group mask "${mask.name}"`);
            invertMaskInPlace(mask.canvas);
            if (state.activeGroupId === group.id && group.activeMaskId === mask.id) {
              state.maskCanvas = mask.canvas;
              state.maskCtx = mask.ctx;
            }
            composite();
            render();
          });
          maskControls.appendChild(createMaskInspectionControl(mask, group, true));
          maskControls.appendChild(createMaskPropertiesControl(mask, 'Group mask'));
          maskControls.appendChild(invertGroupMask);
          const remove = document.createElement('button');
      remove.className = 'ge-layer-btn danger';
      remove.title = 'Delete group mask';
      remove.textContent = '×';
      remove.addEventListener('click', event => {
        event.stopPropagation();
        saveState(`Delete group mask "${mask.name}"`);
        group.masks = group.masks.filter(item => item.id !== mask.id);
        if (group.activeMaskId === mask.id) group.activeMaskId = null;
        state.maskCanvas = null;
        state.maskCtx = null;
        composite();
        render();
      });
      maskControls.append(remove);
      sub.append(visibility, createMaskThumbnail(mask), name, maskControls);
      sub.addEventListener('click', event => {
        if (event.target.closest('button')) return;
        event.stopPropagation();
        group.activeMaskId = mask.id;
        state.activeGroupId = group.id;
        state.selectedLayerIds = allLayerIdsInGroup(state, group);
        state.activeLayerId = state.selectedLayerIds[state.selectedLayerIds.length - 1];
        state.selectionAnchorId = state.activeLayerId;
        state.maskCanvas = mask.canvas;
        state.maskCtx = mask.ctx;
        render();
        composite();
      });
      list.appendChild(sub);
    }
  }

  function render() {
    // FX panel mirrors the active layer's adjustments — re-sync on
    // every layer event (activation, add, delete, etc).
    try { syncFxPanelToActiveLayerIfPresent(); } catch {}
    const list = document.getElementById('ge-layers-list');
    if (!list) return;
    const sharedTools = document.getElementById('ge-layer-tools');
    if (sharedTools) sharedTools.hidden = true;
    const blendSelect = document.getElementById('ge-layer-blend');
    normalizeLayerSelection(state);
    wireSelectionActions();
    const selectedLayer = state.layers.find(layer => layer.id === state.activeLayerId) || null;
    const selectedGroup = normalizeLayerGroups(state).find(group => group.id === state.activeGroupId) || null;
    normalizeLayerClipping(state);
    if (blendSelect) {
      blendSelect.disabled = !selectedLayer && !selectedGroup;
      blendSelect.value = selectedGroup?.blendMode || selectedLayer?.blendMode || 'source-over';
      if (!blendSelect.dataset.wired) {
        blendSelect.dataset.wired = 'true';
        blendSelect.addEventListener('change', () => {
          const activeGroup = normalizeLayerGroups(state).find(group => group.id === state.activeGroupId);
          if (activeGroup) {
            if (activeGroup.blendMode === blendSelect.value) return;
            saveState(`Change blend mode of group "${activeGroup.name}"`);
            activeGroup.blendMode = blendSelect.value;
            composite();
            return;
          }
          const active = state.layers.find(layer => layer.id === state.activeLayerId);
          if (!active) return;
          const targets = rowTargets(active);
          if (targets.every(layer => layer.blendMode === blendSelect.value)) return;
          saveState(targets.length > 1 ? `Change blend mode of ${targets.length} layers` : `Change blend mode of "${active.name}"`);
          for (const layer of targets) layer.blendMode = blendSelect.value;
          composite();
        });
      }
    }
    // Mobile bottom-sheet peek height — header + N rows, capped so a
    // 20-layer document doesn't get a peek that eats the canvas.
    const panel = document.querySelector('.ge-right-panel');
    if (panel) {
      requestAnimationFrame(() => {
        const header = panel.querySelector('.ge-layers-header');
        const firstRow = list.querySelector('.ge-layer-item');
        const headerH = header ? header.offsetHeight : 52;
        const rowH = firstRow ? firstRow.offsetHeight : 36;
        const allRows = list.querySelectorAll('.ge-layer-item').length;
        const MAX_ROWS = 2;
        const rows = Math.min(allRows, MAX_ROWS);
        panel.style.setProperty('--peek-height', `${headerH + rows * rowH + 6}px`);
      });
    }
    list.innerHTML = '';
    const groups = normalizeLayerGroups(state);
    const groupByLayer = new Map();
    const topIndexByGroup = new Map();
    for (const group of groups) {
      for (const id of group.layerIds) groupByLayer.set(id, group);
      for (const id of allLayerIdsInGroup(state, group)) {
        const index = state.layers.findIndex(layer => layer.id === id);
        if (index >= 0) topIndexByGroup.set(group.id, Math.max(topIndexByGroup.get(group.id) ?? -1, index));
      }
    }
    const groupsAtTopIndex = new Map();
    for (const group of groups) {
      const index = topIndexByGroup.get(group.id);
      if (!Number.isInteger(index)) continue;
      if (!groupsAtTopIndex.has(index)) groupsAtTopIndex.set(index, []);
      groupsAtTopIndex.get(index).push(group);
    }

    // Render in reverse order (top layer first).
    for (let i = state.layers.length - 1; i >= 0; i--) {
      const layer = state.layers[i];
      for (const group of (groupsAtTopIndex.get(i) || []).sort((a, b) => groupDepth(state, a) - groupDepth(state, b))) {
        if (groupAncestors(state, group).some(parent => parent.collapsed)) continue;
        appendGroupRow(list, group, groupDepth(state, group));
      }
      const lineage = groupsForLayer(state, layer.id);
      const group = lineage[0] || null;
      if (lineage.some(item => item.collapsed)) continue;
      const item = document.createElement('div');
      // Parent row is highlighted ONLY when it's actually the paint
      // target — activated AND no mask sub-layer is currently active.
      const parentIsPaintTarget = layer.id === state.activeLayerId &&
        !(layer.masks && layer.activeMaskId && layer.masks.some(m => m.id === layer.activeMaskId));
      item.className = 'ge-layer-item' +
        (parentIsPaintTarget ? ' active' : '') +
        (layer.id === state.activeLayerId && !parentIsPaintTarget ? ' active-parent' : '') +
        (state.selectedLayerIds.includes(layer.id) ? ' selected' : '') +
        (group ? ' grouped' : '') +
        (layer.clipped ? ' clipped' : '') +
        (layer.kind === 'adjustment' ? ' ge-adjustment-layer-item' : '');
      item.dataset.layerId = layer.id;
      item.tabIndex = 0;
      item.setAttribute('aria-label', `${layer.name} layer`);
      item.setAttribute('aria-pressed', state.selectedLayerIds.includes(layer.id) ? 'true' : 'false');
      if (group) item.style.setProperty('--layer-group-depth', String(lineage.length));
      // Hover thumbnail.
      item.addEventListener('mouseenter', () => {
        if (layer.kind !== 'adjustment') showLayerThumb(item, layer);
      });
      item.addEventListener('mouseleave', () => hideLayerThumb());
      item.addEventListener('click', (e) => {
        if (shouldIgnoreLayerTap()) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        // Alt+click keeps the legacy transparency-to-selection shortcut.
        if (e.altKey && layer.kind !== 'adjustment') {
          e.preventDefault();
          loadLayerAlphaAsSelection(layer);
          return;
        }
        if (!e.shiftKey && !e.ctrlKey && !e.metaKey &&
            state.activeLayerId === layer.id && !state.activeGroupId &&
            !layer.activeMaskId && state.selectedLayerIds.length === 1 &&
            state.selectedLayerIds[0] === layer.id) return;
        if (e.shiftKey) selectLayerRange(state, layer.id);
        else if (e.ctrlKey || e.metaKey) toggleLayerSelection(state, layer.id);
        else selectOnlyLayer(state, layer.id);
        const active = state.layers.find(item => item.id === state.activeLayerId) || layer;
        active.activeMaskId = null;
        state.maskInspectMode = false;
        state.maskCanvas = null;
        state.maskCtx = null;
        syncSelectionUi();
        document.querySelectorAll('.ge-layer-group-row.active').forEach(row => row.classList.remove('active'));
        onSelectLayer?.(active);
        composite();
        // Preserve the name element across the first click of a double-click
        // so inline renaming can receive the browser's dblclick event.
        if (!e.target.closest('.ge-layer-name')) render();
      });
      item.addEventListener('keydown', (e) => {
        if (e.target !== item || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        item.click();
      });

      // Drag handle — grip dots; dragSortModule.enable() below scopes
      // drag-init to this handle so row body clicks still activate.
      const handle = document.createElement('span');
      handle.className = 'ge-layer-drag';
      handle.title = 'Drag to reorder';
      handle.innerHTML = '<svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor"><circle cx="2" cy="2" r="1"/><circle cx="6" cy="2" r="1"/><circle cx="2" cy="7" r="1"/><circle cx="6" cy="7" r="1"/><circle cx="2" cy="12" r="1"/><circle cx="6" cy="12" r="1"/></svg>';
      item.appendChild(handle);

      const visBtn = document.createElement('button');
      visBtn.className = 'ge-layer-vis' + (layer.visible ? ' visible' : '');
      visBtn.innerHTML = layer.visible ? EYE_OPEN : EYE_OFF;
      visBtn.title = layer.visible ? 'Hide layer' : 'Show layer';
      visBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const targets = rowTargets(layer);
        const visible = !targets.every(item => item.visible !== false);
        saveState(targets.length > 1 ? `${visible ? 'Show' : 'Hide'} ${targets.length} layers` : `${layer.visible ? 'Hide' : 'Show'} layer "${layer.name}"`);
        for (const item of targets) item.visible = visible;
        composite();
        render();
      });

      // Retained text/shape layers keep their editable metadata separately
      // from the raster canvas. Use the normal renderer when available so
      // their layer previews match what the document actually displays.
      const thumb = createInlineThumbnail(() => renderLayer?.(layer) || layer.canvas, `${layer.name} preview`);
      thumb.title = `${layer.name} preview (Ctrl-click to select pixels)`;
      thumb.addEventListener('click', (event) => {
        if (!(event.ctrlKey || event.metaKey) || layer.kind === 'adjustment') return;
        event.preventDefault();
        event.stopPropagation();
        selectOnlyLayer(state, layer.id);
        layer.activeMaskId = null;
        state.maskInspectMode = false;
        state.maskCanvas = null;
        state.maskCtx = null;
        syncSelectionUi();
        onSelectLayer?.(layer);
        loadLayerAlphaAsSelection(layer);
        render();
      });
      item.appendChild(thumb);

      const nameEl = document.createElement('span');
      nameEl.className = 'ge-layer-name';
      if (layer.kind === 'placed') {
        const marker = document.createElement('span');
        marker.className = 'ge-layer-placed-marker';
        marker.title = 'Placed image';
        marker.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="position:relative;top:2px"><rect x="3" y="3" width="18" height="18" rx="1"/><rect x="7" y="7" width="10" height="10" rx="1"/></svg>';
        nameEl.appendChild(marker);
      }
      if (layer.kind === 'text' || layer.kind === 'shape') {
        const marker = document.createElement('span');
        marker.className = 'ge-layer-placed-marker';
        marker.title = layer.kind === 'text' ? 'Editable text' : 'Editable shape';
        marker.textContent = layer.kind === 'text' ? 'T' : '◇';
        nameEl.appendChild(marker);
      }
      if (layer.kind === 'adjustment') {
        const marker = document.createElement('span');
        marker.className = 'ge-layer-placed-marker ge-layer-adjustment-marker';
        marker.title = 'Adjustment layer';
        marker.innerHTML = ADJ_ICONS[layer.adjustment?.type] || ADJ_ICONS.levels || '';
        nameEl.appendChild(marker);
      }
      if (layer.clipped) {
        const marker = document.createElement('span');
        marker.className = 'ge-layer-clipped-marker';
        marker.title = 'Clipped to layer below';
        marker.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4v7a4 4 0 0 0 4 4h10"/><path d="m15 11 4 4-4 4"/></svg>';
        nameEl.appendChild(marker);
      }
      nameEl.appendChild(document.createTextNode(layer.name + (layer.kind !== 'adjustment' && isLayerEmpty(layer) ? ' (empty)' : '')));
      // Keep the name node stable across the first click of a double-click
      // so inline renaming can receive the browser's dblclick event.
      nameEl.addEventListener('dblclick', event => {
        event.preventDefault();
        event.stopPropagation();
        const input = document.createElement('input');
        input.type = 'text';
        input.value = layer.name;
        input.className = 'ge-layer-name-input';
        nameEl.replaceWith(input);
        input.focus();
        let finished = false;
        let cancelRename = false;
        const finish = (commit) => {
          if (finished) return;
          finished = true;
          if (!commit) {
            input.replaceWith(nameEl);
            return;
          }
          const nextName = input.value.trim() || layer.name;
          if (nextName !== layer.name) {
            saveState(`Rename layer "${layer.name}"`);
            layer.name = nextName;
          }
          render();
        };
        input.addEventListener('blur', () => finish(!cancelRename && input.dataset.cancelRename !== 'true'));
        input.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
          if (ev.key === 'Escape') {
            ev.preventDefault();
            ev.stopPropagation();
            cancelRename = true;
            finish(false);
          }
        }, true);
      });

    const opSlider = document.createElement('input');
      opSlider.type = 'range';
      opSlider.min = '0';
      opSlider.max = '100';
      opSlider.value = String(Math.round(layer.opacity * 100));
      opSlider.className = 'ge-layer-opacity';
      opSlider.title = 'Opacity';
      let opacityHistorySaved = false;
      opSlider.addEventListener('input', (e) => {
        e.stopPropagation();
        const targets = rowTargets(layer);
        const nextOpacity = parseInt(e.target.value, 10) / 100;
        if (targets.every(item => Math.abs(item.opacity - nextOpacity) < 0.0001)) return;
        if (!opacityHistorySaved) {
          saveState(targets.length > 1 ? `Change opacity of ${targets.length} layers` : `Change opacity of "${layer.name}"`);
          opacityHistorySaved = true;
        }
        for (const item of targets) item.opacity = nextOpacity;
        composite();
      });
      opSlider.addEventListener('change', () => { opacityHistorySaved = false; });
      // Browser :active drops the moment the cursor leaves the slider
      // hit-area in some browsers; a JS-managed `dragging` class
      // survives the OS pointer-capture so the slider stays expanded
      // for the whole drag.
      opSlider.addEventListener('pointerdown', () => {
        opSlider.classList.add('dragging');
        const onUp = () => {
          opSlider.classList.remove('dragging');
          window.removeEventListener('pointerup', onUp);
        };
        window.addEventListener('pointerup', onUp);
      });

      const controls = document.createElement('div');
      controls.className = 'ge-layer-controls';

      const lockBtn = document.createElement('button');
      lockBtn.className = 'ge-layer-btn ge-layer-lock-btn' + (layerHasAnyLock(layer) ? ' active' : '');
      lockBtn.title = 'Layer locks';
      lockBtn.setAttribute('aria-label', `Layer locks for ${layer.name}`);
      lockBtn.setAttribute('aria-haspopup', 'menu');
      lockBtn.innerHTML = layerHasAnyLock(layer)
        ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>'
        : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.5-2"/></svg>';
      lockBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openLayerLockMenu(layer, lockBtn);
      });
      const clipBtn = document.createElement('button');
      const clipAllowed = canToggleLayerClipping(state, layer.id);
      clipBtn.className = 'ge-layer-btn ge-layer-clip-btn' + (layer.clipped ? ' active' : '');
      clipBtn.title = layer.clipped
        ? 'Release clipping mask'
        : (clipAllowed ? 'Create clipping mask from layer below' : 'Clipping requires a layer below in the same group');
      clipBtn.setAttribute('aria-label', clipBtn.title);
      clipBtn.disabled = !clipAllowed;
      clipBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4v7a4 4 0 0 0 4 4h10"/><path d="m15 11 4 4-4 4"/></svg>';
      clipBtn.addEventListener('click', event => {
        event.stopPropagation();
        if (!canToggleLayerClipping(state, layer.id)) return;
        saveState(`${layer.clipped ? 'Release' : 'Create'} clipping mask for "${layer.name}"`);
        layer.clipped = !layer.clipped;
        normalizeLayerClipping(state);
        composite();
        render();
      });
      controls.appendChild(clipBtn);

      // FX (adjustments) — opens a floating popup bound to this layer.
      const fxBtn = document.createElement('button');
      fxBtn.className = 'ge-layer-btn ge-layer-fx-btn' + (layerHasAdjustments(layer) ? ' active' : '');
      fxBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor"/></svg>';
      fxBtn.title = 'Adjust layer (Brightness, Contrast, Saturation, Hue, Levels, Color Balance)';
      fxBtn.style.touchAction = 'manipulation';
      let lastFxPointerOpenAt = 0;
      let fxOpenTimer = null;
      const openLayerFx = (e, delay = 0) => {
        e.preventDefault?.();
        e.stopPropagation();
        window.__geSuppressLayerTapUntil = 0;
        if (fxOpenTimer) clearTimeout(fxOpenTimer);
        fxOpenTimer = setTimeout(() => {
          fxOpenTimer = null;
          openFxPopup(layer, fxBtn);
        }, delay);
      };
      fxBtn.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
      });
      fxBtn.addEventListener('pointerup', (e) => {
        lastFxPointerOpenAt = Date.now();
        const delay = e.pointerType === 'touch' || e.pointerType === 'pen' ? 120 : 0;
        openLayerFx(e, delay);
      });
      fxBtn.addEventListener('click', (e) => {
        if (Date.now() - lastFxPointerOpenAt < 500) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        openLayerFx(e);
      });
      controls.appendChild(fxBtn);
      const stylesButton = document.createElement('button');
      stylesButton.type = 'button';
      stylesButton.className = 'ge-layer-btn ge-layer-style-btn';
      stylesButton.textContent = 'fx';
      stylesButton.title = 'Layer styles';
      stylesButton.setAttribute('aria-label', 'Layer styles');
      stylesButton.setAttribute('aria-haspopup', 'menu');
      stylesButton.setAttribute('aria-expanded', 'false');
      stylesButton.addEventListener('click', event => {
        event.stopPropagation(); openLayerStyles?.(layer, stylesButton);
      });
      if (layer.kind !== 'adjustment') controls.appendChild(stylesButton);

      // Duplicate — clones pixels + offset + opacity + masks + adjLayers
      // + visibility; inserts above the original; new copy becomes
      // active.
      const dupBtn = document.createElement('button');
      dupBtn.className = 'ge-layer-btn';
      dupBtn.title = 'Duplicate layer';
      dupBtn.dataset.layerId = layer.id;
      dupBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      dupBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        saveState(`Duplicate "${layer.name}"`);
        const copy = createLayer(layer.name + ' copy', layer.canvas.width, layer.canvas.height);
        copy.ctx.drawImage(layer.canvas, 0, 0);
        copy.opacity = layer.opacity;
        copy.visible = layer.visible;
        copy.locked = layer.locked;
        copy.locks = normalizeLayerLocks(layer.locks);
        copy.clipped = !!layer.clipped;
        copy.blendMode = layer.blendMode || 'source-over';
        copy.kind = layer.kind || 'raster';
        copy.text = layer.text ? JSON.parse(JSON.stringify(layer.text)) : null;
        copy.shape = layer.shape ? JSON.parse(JSON.stringify(layer.shape)) : null;
        copy.adjustment = layer.adjustment ? JSON.parse(JSON.stringify(layer.adjustment)) : null;
        // Give duplicated placed layers their own immutable source surface.
        // This keeps future source replacement/editing isolated between the
        // original and its copy instead of relying on reference sharing.
        copy.placed = layer.kind === 'placed' ? clonePlacedData(layer.placed, { copySource: true }) : null;
        copy.adjustments = JSON.parse(JSON.stringify(layer.adjustments || {}));
        copy.effects = (layer.effects || []).map(effect => ({
          ...normalizeEffect(effect),
          id: 'effect-' + Math.random().toString(36).slice(2, 9),
          params: JSON.parse(JSON.stringify(effect.params || {})),
        }));
        const srcOff = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
        state.layerOffsets.set(copy.id, { x: srcOff.x, y: srcOff.y });
        if (Array.isArray(layer.masks) && layer.masks.length) {
          copy.masks = layer.masks.map(m => {
            const c = document.createElement('canvas');
            c.width = m.canvas.width; c.height = m.canvas.height;
            c.getContext('2d').drawImage(m.canvas, 0, 0);
            return {
              id: 'mask-' + (state.nextLayerId++),
              name: m.name,
              canvas: c,
              ctx: c.getContext('2d'),
              visible: m.visible !== false,
              mode: m.mode || 'selection',
              space: m.space || (m.mode === 'layer' ? 'layer' : 'document'),
              linked: m.mode === 'layer' ? m.linked !== false : true,
              offset: { x: Number(m.offset?.x) || 0, y: Number(m.offset?.y) || 0 },
            };
          });
          const activeMaskIndex = layer.masks.findIndex(m => m.id === layer.activeMaskId);
          copy.activeMaskId = activeMaskIndex >= 0 ? copy.masks[activeMaskIndex]?.id || null : null;
        }
        if (Array.isArray(layer.adjLayers) && layer.adjLayers.length) {
          copy.adjLayers = layer.adjLayers.map(a => ({
            id: 'adj-' + Math.random().toString(36).slice(2, 9),
            type: a.type,
            name: a.name,
            visible: a.visible !== false,
            opacity: a.opacity != null ? a.opacity : 1,
            params: JSON.parse(JSON.stringify(a.params || {})),
          }));
        }
        const idx = state.layers.findIndex(l => l.id === layer.id);
        if (idx >= 0) state.layers.splice(idx + 1, 0, copy);
        else state.layers.push(copy);
        state.activeLayerId = copy.id;
        composite();
        render();
        if (uiModule) uiModule.showToast('Layer duplicated');
      });
      controls.appendChild(dupBtn);

      if (layer.kind === 'raster') {
        const convertBtn = document.createElement('button');
        convertBtn.className = 'ge-layer-btn';
        convertBtn.title = 'Convert to editable source';
        convertBtn.setAttribute('aria-label', 'Convert to editable source');
        convertBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 12h10M12 7v10"/></svg>';
        convertBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          if (!layer.canvas?.width || !layer.canvas?.height) return;
          saveState(`Convert "${layer.name}" to editable source`);
          layer.placed = createPlacedData(layer.canvas, [1, 0, 0, 1, 0, 0], layer.name || 'Raster layer');
          layer.kind = 'placed';
          renderPlacedLayer(layer);
          composite();
          render();
          uiModule?.showToast('Layer converted to editable source');
        });
        controls.appendChild(convertBtn);
      }

      if (layer.kind === 'placed') {
        const replaceBtn = document.createElement('button');
        replaceBtn.className = 'ge-layer-btn';
        replaceBtn.title = 'Replace placed image';
        replaceBtn.setAttribute('aria-label', 'Replace placed image');
        replaceBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><polyline points="20 4 20 11 13 11"/></svg>';
        replaceBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          replacePlacedLayer?.(layer);
        });
        controls.appendChild(replaceBtn);

        const rasterizeBtn = document.createElement('button');
        rasterizeBtn.className = 'ge-layer-btn';
        rasterizeBtn.title = 'Rasterize placed layer';
        rasterizeBtn.setAttribute('aria-label', 'Rasterize placed layer');
        rasterizeBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>';
        rasterizeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          rasterizePlacedLayer?.(layer);
        });
        controls.appendChild(rasterizeBtn);
      }

      // Add-mask — if a lasso/wand selection is active, bake it into a
      // mask sub-layer on this layer; otherwise create an empty mask
      // for the user to paint with the Brush tool.
      const hasLassoSelInitial = state.lassoPoints.length >= 3 && !state.lassoActive;
      const hasWandSelInitial = !!state.wandMask;
      const maskBtn = document.createElement('button');
      maskBtn.className = 'ge-layer-btn ge-layer-mask-btn' +
        ((hasLassoSelInitial || hasWandSelInitial) ? ' from-selection' : '');
      maskBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12c4 0 4-4 8-4s4 4 8 4-4 4-8 4-4-4-8-4z" fill="currentColor"/></svg>';
      maskBtn.title = (hasLassoSelInitial || hasWandSelInitial)
        ? 'Make mask from current selection'
        : 'Add empty mask (paint with Brush)';
      maskBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        // Activate this layer first so the new mask attaches here.
        state.activeLayerId = layer.id;
        // Re-check selection state AT CLICK TIME — captured vars may
        // be stale if a selection was drawn after the panel paint.
        const hasLassoSel = state.lassoPoints.length >= 3 && !state.lassoActive;
        const hasWandSel = !!state.wandMask;
        if (hasLassoSel) {
          saveState(`Mask from lasso on "${layer.name}"`);
          // Force a fresh mask sub-layer for this conversion so each
          // selection becomes its own mask instead of merging into the
          // previously active one.
          layer.activeMaskId = null;
          lassoToMask();
        } else if (hasWandSel) {
          saveState(`Mask from wand on "${layer.name}"`);
          layer.activeMaskId = null;
          wandToMask();
        } else {
          saveState(`Add mask to "${layer.name}"`);
          const c = document.createElement('canvas');
          c.width = state.imgWidth;
          c.height = state.imgHeight;
          if (!layer.masks) layer.masks = [];
          const mask = {
            id: 'mask-' + (state.nextLayerId++),
            name: 'Mask ' + (layer.masks.length + 1),
            canvas: c,
            ctx: c.getContext('2d'),
            visible: true,
            mode: 'selection',
            space: 'document',
          };
          layer.masks.push(mask);
          layer.activeMaskId = mask.id;
          state.maskCanvas = mask.canvas;
          state.maskCtx = mask.ctx;
          composite();
          render();
        }
      });
      controls.appendChild(maskBtn);

      const layerMaskBtn = document.createElement('button');
      layerMaskBtn.className = 'ge-layer-btn ge-layer-mask-btn ge-true-mask-btn';
      layerMaskBtn.title = 'Add layer mask (white reveals; erase hides)';
      layerMaskBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="5" fill="currentColor"/></svg>';
      layerMaskBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        saveState(`Add layer mask to "${layer.name}"`);
        const c = document.createElement('canvas');
        c.width = layer.canvas.width;
        c.height = layer.canvas.height;
        const ctx = c.getContext('2d');
        const selection = getDocumentSelection?.();
        if (selection) {
          const layerOffset = state.layerOffsets.get(layer.id) || { x: 0, y: 0 };
          ctx.drawImage(selection, -layerOffset.x, -layerOffset.y);
        } else {
          ctx.fillStyle = '#fff';
          ctx.fillRect(0, 0, c.width, c.height);
        }
        if (!layer.masks) layer.masks = [];
        const mask = {
          id: 'mask-' + (state.nextLayerId++),
          name: 'Layer Mask',
          canvas: c,
          ctx,
          visible: true,
          mode: 'layer',
          space: 'layer',
          linked: true,
          offset: { x: 0, y: 0 },
        };
        layer.masks.push(mask);
        layer.activeMaskId = mask.id;
        state.activeLayerId = layer.id;
        state.maskCanvas = c;
        state.maskCtx = ctx;
        composite();
        render();
        uiModule.showToast(selection ? 'Layer mask created from selection' : 'Layer mask added — use Eraser to hide, Brush to reveal');
      });
      controls.appendChild(layerMaskBtn);

      // Per-row Merge Down — bakes this layer into the one beneath.
      // Hidden on the bottom layer in the visual stack (idx 0 forward).
      const lowerLayer = state.layers[i - 1] || null;
      const sameMergeScope = lowerLayer && (groupByLayer.get(lowerLayer.id)?.id || null) === (group?.id || null);
      if (i > 0 && layer.kind !== 'adjustment' && lowerLayer.kind !== 'adjustment' && !lowerLayer.clipped && sameMergeScope) {
        const mergeDownBtn = document.createElement('button');
        mergeDownBtn.className = 'ge-layer-btn';
        mergeDownBtn.title = 'Merge down into layer below';
        mergeDownBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="6 13 12 19 18 13"/></svg>';
        mergeDownBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          saveState(`Merge "${layer.name}" down`);
          const merged = mergeLayerDownAtIndex(i, renderLayer);
          if (!merged) return;
          composite();
          render();
          uiModule.showToast('Layer merged down');
        });
        controls.appendChild(mergeDownBtn);
      }

      // Delete — shown for every layer except when this is the last
      // remaining one. Base photo is deletable too; Ctrl+Z brings it
      // back from history. Extra confirm for the base layer.
      if (state.layers.length > 1) {
        const delBtn = document.createElement('button');
        delBtn.className = 'ge-layer-btn danger';
        delBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
        delBtn.title = layer.isBase ? 'Delete original layer (Ctrl+Z to undo)' : 'Delete layer';
        delBtn.setAttribute('aria-label', delBtn.title);
        delBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          await deleteLayers(rowTargets(layer));
        });
        controls.appendChild(delBtn);
      }

      const opacityRow = document.createElement('div');
      opacityRow.className = 'ge-layer-opacity-row';
      opacityRow.append(opSlider);
      item.appendChild(visBtn);
      item.appendChild(nameEl);
      item.appendChild(lockBtn);
      item.appendChild(opacityRow);
      item.appendChild(controls);

      list.appendChild(item);

      // Adjustment sub-layer rows, indented under the parent.
      if (layer.adjLayers && layer.adjLayers.length) {
        for (const adj of layer.adjLayers) {
          const sub = document.createElement('div');
          sub.className = 'ge-layer-item ge-adj-sub-item';
          sub.dataset.adjId = adj.id;
          sub.tabIndex = 0;
          sub.setAttribute('aria-label', `${adj.name || adjLayerLabel(adj.type)} adjustment`);
          const sVis = document.createElement('button');
          sVis.className = 'ge-layer-vis' + (adj.visible ? ' visible' : '');
          sVis.innerHTML = adj.visible ? EYE_OPEN_SM : EYE_OFF_SM;
          sVis.title = adj.visible ? 'Hide adjustment' : 'Show adjustment';
          sVis.addEventListener('click', (e) => {
            e.stopPropagation();
            saveState(`${adj.visible ? 'Hide' : 'Show'} ${adjLayerLabel(adj.type)}`);
            adj.visible = !adj.visible;
            layer._adjFinalKey = null;
            composite();
            render();
          });
          const sName = document.createElement('span');
          sName.className = 'ge-layer-name ge-adj-sub-name';
          sName.innerHTML = `<span class="ge-adj-sub-icon">${ADJ_ICONS[adj.type] || ''}</span><span>${(adj.name || adjLayerLabel(adj.type)).replace(/[<>&]/g,'')}</span>`;
          const sOp = document.createElement('input');
          sOp.type = 'range';
          sOp.min = '0'; sOp.max = '100';
          sOp.value = Math.round(adj.opacity * 100);
          sOp.className = 'ge-layer-opacity';
          sOp.title = 'Adjustment opacity';
          let adjOpacityHistorySaved = false;
          sOp.addEventListener('input', () => {
            const nextOpacity = parseInt(sOp.value, 10) / 100;
            if (Math.abs(adj.opacity - nextOpacity) < 0.0001) return;
            if (!adjOpacityHistorySaved) {
              saveState(`Change ${adjLayerLabel(adj.type)} opacity`);
              adjOpacityHistorySaved = true;
            }
            adj.opacity = nextOpacity;
            layer._adjFinalKey = null;
            composite();
          });
          sOp.addEventListener('change', () => { adjOpacityHistorySaved = false; });
          const sControls = document.createElement('div');
          sControls.className = 'ge-layer-controls';
          const mergeBtn = document.createElement('button');
          mergeBtn.className = 'ge-layer-btn';
          mergeBtn.title = isLayerPixelLocked(state, layer)
            ? 'Unlock image pixels before baking this adjustment'
            : 'Merge into layer (bake)';
          mergeBtn.disabled = isLayerPixelLocked(state, layer);
          mergeBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
          mergeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (isLayerPixelLocked(state, layer)) return;
            // Bake just this adjustment into layer.canvas, then drop it.
            saveState(`Merge ${adjLayerLabel(adj.type)}`);
            rasterizePlacedPixels(layer);
            const baked = applyAdjustment(layer.canvas, adj);
            layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
            layer.ctx.drawImage(baked, 0, 0);
            rasterizeTextLayer(layer);
            rasterizeShapeLayer(layer);
            layer.adjLayers = layer.adjLayers.filter(x => x.id !== adj.id);
            layer._adjFinalKey = null;
            composite();
            render();
          });
          sControls.appendChild(mergeBtn);
          const delBtn = document.createElement('button');
          delBtn.className = 'ge-layer-btn danger';
          delBtn.textContent = '×';
          delBtn.title = 'Delete adjustment';
          delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            saveState(`Delete ${adjLayerLabel(adj.type)}`);
            layer.adjLayers = layer.adjLayers.filter(x => x.id !== adj.id);
            layer._adjFinalKey = null;
            composite();
            render();
          });
          sControls.appendChild(delBtn);

          sub.appendChild(sVis);
          sub.appendChild(sName);
          sub.appendChild(sOp);
          sub.appendChild(sControls);
          // Single-click on the sub-row (outside the inline controls)
          // reopens the adj popup with this sub-layer's params staged.
          sub.addEventListener('click', (e) => {
            if (shouldIgnoreLayerTap()) {
              e.preventDefault();
              e.stopPropagation();
              return;
            }
            if (e.target.closest('.ge-layer-vis, .ge-layer-opacity, .ge-layer-btn')) return;
            if (!e.target.closest('.ge-adj-sub-name')) return;
            e.stopPropagation();
            editAdjLayer(layer, adj, sub);
          });
          sub.addEventListener('keydown', event => {
            if (event.target !== sub || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            editAdjLayer(layer, adj, sub);
          });
          list.appendChild(sub);
        }
      }

      // Retained effect rows. Effects are ordered bottom-to-top in the
      // array; the arrow buttons change that order without rasterizing.
      if (layer.effects && layer.effects.length) {
        for (let effectIndex = 0; effectIndex < layer.effects.length; effectIndex += 1) {
          const effect = normalizeEffect(layer.effects[effectIndex]);
          layer.effects[effectIndex] = effect;
          const sub = document.createElement('div');
          sub.className = 'ge-layer-item ge-adj-sub-item ge-effect-sub-item';
          sub.dataset.effectId = effect.id;
          sub.tabIndex = 0;
          sub.setAttribute('aria-label', `${effectLabel(effect.type)} effect`);
          const sVis = document.createElement('button');
          sVis.className = 'ge-layer-vis' + (effect.visible ? ' visible' : '');
          sVis.innerHTML = effect.visible ? EYE_OPEN_SM : EYE_OFF_SM;
          sVis.title = effect.visible ? 'Hide effect' : 'Show effect';
          sVis.addEventListener('click', event => {
            event.stopPropagation();
            saveState(`${effect.visible ? 'Hide' : 'Show'} ${effectLabel(effect.type)}`);
            effect.visible = !effect.visible;
            composite();
            render();
          });
          const sName = document.createElement('span');
          sName.className = 'ge-layer-name ge-adj-sub-name';
          sName.innerHTML = `<span class="ge-adj-sub-icon">◌</span><span>${effectLabel(effect.type)}</span>`;
          sName.title = 'Edit effect';
          sName.addEventListener('click', event => {
            event.stopPropagation();
            editRetainedEffect?.(layer, effect, sName);
          });
          sub.addEventListener('keydown', event => {
            if (event.target !== sub || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            editRetainedEffect?.(layer, effect, sub);
          });
          const sOp = document.createElement('input');
          sOp.type = 'range'; sOp.min = '0'; sOp.max = '100';
          sOp.value = String(Math.round(effect.opacity * 100));
          sOp.className = 'ge-layer-opacity';
          sOp.title = 'Effect opacity';
          let effectOpacitySaved = false;
          sOp.addEventListener('input', event => {
            event.stopPropagation();
            const next = Number(sOp.value) / 100;
            if (Math.abs(effect.opacity - next) < 0.0001) return;
            if (!effectOpacitySaved) {
              saveState(`Change ${effectLabel(effect.type)} opacity`);
              effectOpacitySaved = true;
            }
            effect.opacity = next;
            composite();
          });
          sOp.addEventListener('change', () => { effectOpacitySaved = false; });
          const sControls = document.createElement('div');
          sControls.className = 'ge-layer-controls';
          const maskBtn = document.createElement('button');
          maskBtn.className = 'ge-layer-btn';
          maskBtn.type = 'button';
          maskBtn.textContent = effect.mask ? '◉' : '○';
          maskBtn.title = effect.mask
            ? (effect.mask.visible === false ? 'Show effect mask' : 'Hide effect mask')
            : 'Add effect mask from selection';
          maskBtn.addEventListener('click', event => {
            event.stopPropagation();
            if (!effect.mask) {
              addEffectMask?.(layer, effect);
              return;
            }
            saveState(`${effect.mask.visible === false ? 'Show' : 'Hide'} ${effectLabel(effect.type)} mask`);
            effect.mask.visible = effect.mask.visible === false;
            composite();
            render();
          });
          sControls.appendChild(maskBtn);
          if (effect.mask) {
            const removeMask = document.createElement('button');
            removeMask.className = 'ge-layer-btn';
            removeMask.type = 'button';
            removeMask.textContent = '×';
            removeMask.title = 'Remove effect mask';
            removeMask.addEventListener('click', event => {
              event.stopPropagation();
              saveState(`Remove ${effectLabel(effect.type)} mask`);
              effect.mask = null;
              composite();
              render();
            });
            sControls.appendChild(removeMask);
          }
          const bake = document.createElement('button');
          bake.className = 'ge-layer-btn';
          bake.type = 'button';
          bake.textContent = '◆';
          bake.title = 'Rasterize effects';
          bake.addEventListener('click', event => {
            event.stopPropagation();
            rasterizeEffects?.(layer);
          });
          sControls.appendChild(bake);
          for (const [direction, symbol, title] of [[-1, '↑', 'Move effect up'], [1, '↓', 'Move effect down']]) {
            const move = document.createElement('button');
            move.className = 'ge-layer-btn';
            move.type = 'button';
            move.textContent = symbol;
            move.title = title;
            move.disabled = effectIndex + direction < 0 || effectIndex + direction >= layer.effects.length;
            move.addEventListener('click', event => {
              event.stopPropagation();
              const nextIndex = effectIndex + direction;
              if (nextIndex < 0 || nextIndex >= layer.effects.length) return;
              saveState(`${title} ${effectLabel(effect.type)}`);
              [layer.effects[effectIndex], layer.effects[nextIndex]] = [layer.effects[nextIndex], layer.effects[effectIndex]];
              composite();
              render();
            });
            sControls.appendChild(move);
          }
          const remove = document.createElement('button');
          remove.className = 'ge-layer-btn danger';
          remove.type = 'button';
          remove.textContent = '×';
          remove.title = 'Delete effect';
          remove.addEventListener('click', event => {
            event.stopPropagation();
            saveState(`Delete ${effectLabel(effect.type)}`);
            layer.effects.splice(effectIndex, 1);
            composite();
            render();
          });
          sControls.appendChild(remove);
          sub.append(sVis, sName, sOp, sControls);
          list.appendChild(sub);
        }
      }

      // Mask sub-layer rows.
      if (layer.masks && layer.masks.length) {
        for (let mi = 0; mi < layer.masks.length; mi++) {
          const mk = layer.masks[mi];
          const sub = document.createElement('div');
          sub.className = 'ge-layer-item ge-adj-sub-item ge-mask-sub-item' +
            (layer.activeMaskId === mk.id ? ' active' : '');
          sub.dataset.maskId = mk.id;
          sub.tabIndex = 0;
          sub.setAttribute('aria-label', `${mk.name || (mk.mode === 'layer' ? 'Layer Mask' : 'AI Mask')} mask`);
          const sVis = document.createElement('button');
          sVis.className = 'ge-layer-vis' + (mk.visible ? ' visible' : '');
          sVis.innerHTML = mk.visible ? EYE_OPEN_SM : EYE_OFF_SM;
          sVis.title = mk.visible ? 'Hide mask' : 'Show mask';
          sVis.addEventListener('click', (e) => {
            e.stopPropagation();
            saveState(`${mk.visible ? 'Hide' : 'Show'} mask "${mk.name || 'Mask'}"`);
            mk.visible = !mk.visible;
            composite();
            render();
          });
          const sName = document.createElement('span');
          sName.className = 'ge-layer-name ge-adj-sub-name';
          const maskIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12c4 0 4-4 8-4s4 4 8 4-4 4-8 4-4-4-8-4z" fill="currentColor"/></svg>';
          const mkName = String(mk.name || (mk.mode === 'layer' ? 'Layer Mask' : 'AI Mask')).replace(/[<>&]/g, '');
          const mkEmpty = isMaskCanvasEmpty(mk.canvas) ? ' <span style="opacity:0.55;">(empty)</span>' : '';
          sName.innerHTML = `<span class="ge-adj-sub-icon">${maskIcon}</span><span>${mkName}${mkEmpty}</span>`;
          const sControls = document.createElement('div');
          sControls.className = 'ge-layer-controls';
          const invertMask = document.createElement('button');
          invertMask.className = 'ge-layer-btn';
          invertMask.title = 'Invert mask';
          invertMask.setAttribute('aria-label', invertMask.title);
          invertMask.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 17h16"/><path d="m8 4-4 3 4 3M16 14l4 3-4 3"/></svg>';
          invertMask.addEventListener('click', event => {
            event.stopPropagation();
            saveState(`Invert mask "${mk.name || 'Mask'}"`);
            invertMaskInPlace(mk.canvas);
            if (layer.activeMaskId === mk.id) {
              state.maskCanvas = mk.canvas;
              state.maskCtx = mk.ctx;
            }
            composite();
            render();
          });
          sControls.appendChild(createMaskInspectionControl(mk, layer));
          sControls.appendChild(createMaskPropertiesControl(
            mk,
            mk.mode === 'layer' ? 'Layer mask' : 'Mask',
            mk.mode === 'layer' ? () => applyLayerMask(layer, mk) : null,
          ));
          sControls.appendChild(invertMask);
          if (mk.mode === 'layer') {
            const linked = mk.linked !== false;
            const linkBtn = document.createElement('button');
            linkBtn.className = 'ge-layer-btn ge-mask-link-btn' + (linked ? ' active' : '');
            linkBtn.title = linked
              ? 'Unlink mask from layer position'
              : 'Link mask to layer position';
            linkBtn.setAttribute('aria-label', linkBtn.title);
            linkBtn.setAttribute('aria-pressed', linked ? 'true' : 'false');
            linkBtn.innerHTML = linked
              ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'
              : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m2 2 20 20"/><path d="M10 13a5 5 0 0 0 7.54.54l1.2-1.2"/><path d="m14.5 5.5 1-1a5 5 0 0 1 7.07 7.07l-1 1"/><path d="M9.5 18.5l-1 1a5 5 0 0 1-7.07-7.07l1-1"/><path d="M14 11a5 5 0 0 0-7.54-.54l-1.2 1.2"/></svg>';
            linkBtn.addEventListener('click', event => {
              event.stopPropagation();
              saveState(`${linked ? 'Unlink' : 'Link'} mask "${mk.name || 'Layer Mask'}"`);
              mk.linked = !linked;
              if (!mk.offset || typeof mk.offset !== 'object') mk.offset = { x: 0, y: 0 };
              composite();
              render();
            });
            sControls.appendChild(linkBtn);
          }
          const selectBtn = document.createElement('button');
          selectBtn.className = 'ge-layer-btn';
          selectBtn.title = 'Load mask as selection';
          selectBtn.setAttribute('aria-label', 'Load mask as selection');
          selectBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="3 2"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>';
          selectBtn.addEventListener('click', event => {
            event.stopPropagation();
            loadMaskAsSelection?.(layer, mk);
          });
          sControls.appendChild(selectBtn);
          // Merge-up — combine this mask into the one above (lower mi).
          if (mi > 0) {
            const mergeBtn = document.createElement('button');
            mergeBtn.className = 'ge-layer-btn';
            mergeBtn.title = 'Merge into mask above';
            mergeBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="6 11 12 5 18 11"/></svg>';
            mergeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const above = layer.masks[mi - 1];
              if (!above) return;
              saveState(`Merge mask "${mk.name}" into "${above.name}"`);
              // Union of alpha — `source-over` already does max for
              // fully opaque white masks; this also handles partial alpha.
              above.ctx.save();
              above.ctx.globalCompositeOperation = 'source-over';
              const aboveOffset = above.offset || { x: 0, y: 0 };
              const maskOffset = mk.offset || { x: 0, y: 0 };
              above.ctx.drawImage(
                mk.canvas,
                (Number(maskOffset.x) || 0) - (Number(aboveOffset.x) || 0),
                (Number(maskOffset.y) || 0) - (Number(aboveOffset.y) || 0),
              );
              above.ctx.restore();
              layer.masks = layer.masks.filter(x => x.id !== mk.id);
              if (layer.activeMaskId === mk.id) layer.activeMaskId = above.id;
              const a = getActiveMaskLayer();
              if (a) { state.maskCanvas = a.canvas; state.maskCtx = a.ctx; }
              else   { state.maskCanvas = null;     state.maskCtx = null; }
              composite();
              render();
            });
            sControls.appendChild(mergeBtn);
          }
          const delBtn = document.createElement('button');
          delBtn.className = 'ge-layer-btn danger';
          delBtn.textContent = '×';
          delBtn.title = 'Delete mask';
          delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            saveState(`Delete mask "${mk.name}"`);
            layer.masks = layer.masks.filter(x => x.id !== mk.id);
            if (layer.activeMaskId === mk.id) {
              layer.activeMaskId = layer.masks[layer.masks.length - 1]?.id || null;
            }
            // Sync global mask plumbing.
            const a = getActiveMaskLayer();
            if (a) { state.maskCanvas = a.canvas; state.maskCtx = a.ctx; }
            else   { state.maskCanvas = null;     state.maskCtx = null; }
            composite();
            render();
          });
          sControls.appendChild(delBtn);
          sub.appendChild(sVis);
          sub.appendChild(createMaskThumbnail(mk));
          sub.appendChild(sName);
          sub.appendChild(sControls);
          const activateMask = () => {
            layer.activeMaskId = mk.id;
            selectOnlyLayer(state, layer.id);
            state.maskCanvas = mk.canvas;
            state.maskCtx = mk.ctx;
            render();
            composite();
          };
          sub.addEventListener('click', (e) => {
            if (e.target.closest('.ge-layer-vis, .ge-layer-btn')) return;
            e.stopPropagation();
            // Activate this mask: paint/inpaint/generate target.
            activateMask();
          });
          sub.addEventListener('keydown', event => {
            if (event.target !== sub || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            activateMask();
          });
          list.appendChild(sub);
        }
      }
    }

    // Wire the shared dragSort module — limit drag-init to the grip
    // handle so row body clicks still activate. Called every render
    // because `enable()` cleans up the previous instance keyed on
    // instanceKey.
    if (dragSortModule) {
      dragSortModule.enable('ge-layers-list', '.ge-layer-item', {
        instanceKey: 'ge-layers',
        handleSelector: '.ge-layer-drag',
        onReorder: (orderedItems) => {
          // DOM is top→bottom = reverse of array order, so the new
          // array is the reverse of the DOM order.
          const byId = new Map(state.layers.map(l => [l.id, l]));
          const newLayers = orderedItems
            .map(el => byId.get(el.dataset.layerId))
            .filter(Boolean)
            .reverse();
          if (newLayers.length === state.layers.length) {
            const positions = new Map(newLayers.map((layer, index) => [layer.id, index]));
            const groupsStayContiguous = normalizeLayerGroups(state).every(group => {
              const indexes = allLayerIdsInGroup(state, group).map(id => positions.get(id)).filter(Number.isInteger).sort((a, b) => a - b);
              return indexes.length < 2 || indexes[indexes.length - 1] - indexes[0] + 1 === indexes.length;
            });
            if (!groupsStayContiguous) {
              uiModule?.showToast?.('Ungroup layers before moving them outside their group');
              render();
              return;
            }
            saveState('Reorder layers');
            state.layers = newLayers;
            normalizeLayerClipping(state);
            composite();
          }
        },
      });
    }
    syncSelectionUi();
  }

  // Reuse the guarded deletion path from the selection toolbar for keyboard
  // shortcuts so undo, confirmations, and layer normalization stay aligned.
  return {
    render,
    refreshPreviews,
    deleteSelectedLayers: () => deleteLayers(selectedLayers(state)),
    duplicateActiveLayer: () => {
      const button = [...document.querySelectorAll('button[title="Duplicate layer"]')]
        .find(item => item.dataset.layerId === state.activeLayerId);
      if (!button || button.disabled) return false;
      button.click();
      return true;
    },
  };
}
