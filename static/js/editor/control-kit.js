/*
 * Editor control kit — shared theming for the range/number/select/checkbox
 * controls scattered across the editor. It only ADDS classes and small helper
 * affordances (range fill, custom steppers, scrubby labels); it never changes
 * the value semantics of a control. It is strictly SCOPED to editor surfaces
 * (the editor shell and its dialogs) so the rest of the app is untouched, and
 * it skips controls that already have a bespoke implementation (e.g. the
 * transform popup's own spinner).
 *
 * Styling lives in static/css/13-image-editor.css (section "Editor control kit").
 */

export const CONTROL_KIT_VERSION = '1.0';

const RANGE_SELECTOR = 'input[type="range"]';
const NUMBER_SELECTOR = 'input[type="number"]';
const CHECK_SELECTOR = 'input[type="checkbox"]';
const SELECT_SELECTOR = 'select';

// Only controls inside one of these surfaces are themed. Dialogs are appended
// to <body> outside #gallery-editor-container, so they are listed explicitly.
const EDITOR_ROOT_SELECTOR = [
  '#gallery-editor-container',
  '.ge-adj-popup',
  '.ge-filter-overlay',
  '.ge-canvas-size-overlay',
  '#ge-canvas-size-overlay',
  '.ge-float-window',
  '.ge-transform-popup',
].join(', ');

function inEditor(el) {
  return !!(el && el.closest && el.closest(EDITOR_ROOT_SELECTOR));
}

function isSkipped(el) {
  if (!el || el.__geKit) return true;
  if (!inEditor(el)) return true;
  if (el.closest('[data-ge-nokit]')) return true;
  // The transform popup renders its own themed spinner for its inputs.
  if (el.closest('.ge-transform-field')) return true;
  return false;
}

function numberStep(el) {
  const raw = Number(el.getAttribute('step'));
  if (Number.isFinite(raw) && raw > 0) return raw;
  return 1;
}

function clampToRange(el, value) {
  const min = el.hasAttribute('min') ? Number(el.min) : -Infinity;
  const max = el.hasAttribute('max') ? Number(el.max) : Infinity;
  let v = value;
  if (Number.isFinite(min)) v = Math.max(min, v);
  if (Number.isFinite(max)) v = Math.min(max, v);
  return v;
}

function attachScrub(handle, el) {
  handle.classList.add('ge-scrub');
  handle.addEventListener('pointerdown', (e) => {
    if (el.disabled || el.readOnly) return;
    e.preventDefault();
    handle.classList.add('ge-scrubbing');
    const startX = e.clientX;
    const startVal = Number(el.value) || 0;
    const step = numberStep(el);
    let moved = false;
    const move = (ev) => {
      const dx = ev.clientX - startX;
      if (Math.abs(dx) < 2) return;
      moved = true;
      const factor = ev.shiftKey ? 10 : 1;
      const next = clampToRange(el, startVal + Math.round(dx) * step * factor * 0.1);
      if (String(next) !== el.value) {
        el.value = String(next);
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      handle.classList.remove('ge-scrubbing');
      if (moved) el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  });
}

function decorateNumber(el) {
  if (isSkipped(el)) return;
  el.classList.add('ge-ctl-num');
  el.__geKit = true;
  if (el.disabled || el.readOnly) return;
  // Photoshop-style scrub-on-label: drag a sibling <span> label to change the
  // value; the field also keeps native arrow-key stepping. We do NOT inject a
  // separate +/- stepper — most editor number fields live in display:grid
  // labels, where an injected element wraps onto its own grid row and shows as
  // an empty pill.
  const label = el.closest('label');
  if (label) {
    const handle = label.querySelector(':scope > span');
    if (handle && !handle.classList.contains('ge-scrub')) attachScrub(handle, el);
  }
}

function updateRangeFill(el) {
  const min = Number(el.min);
  const max = Number(el.max);
  const value = Number(el.value);
  if (Number.isFinite(min) && Number.isFinite(max) && max > min) {
    const pct = ((value - min) / (max - min)) * 100;
    el.style.setProperty('--ge-pct', `${Math.max(0, Math.min(100, pct))}%`);
  } else {
    el.style.setProperty('--ge-pct', '50%');
  }
}

function decorateRange(el) {
  if (isSkipped(el)) return;
  el.classList.add('ge-ctl-range');
  el.__geKit = true;
  updateRangeFill(el);
}

function decorateSelect(el) {
  if (isSkipped(el)) return;
  el.classList.add('ge-ctl-select');
  el.__geKit = true;
}

function decorateCheck(el) {
  if (isSkipped(el)) return;
  el.classList.add('ge-ctl-check');
  el.__geKit = true;
}

export function initControlKit(root = document) {
  if (!root || !root.querySelectorAll) return 0;
  // If the root itself is a control, decorate just it; otherwise its subtree.
  let count = 0;
  const decorate = (el) => {
    if (el.matches(RANGE_SELECTOR)) { if (!isSkipped(el)) { decorateRange(el); count += 1; } }
    else if (el.matches(NUMBER_SELECTOR)) { if (!isSkipped(el)) { decorateNumber(el); count += 1; } }
    else if (el.matches(SELECT_SELECTOR)) { if (!isSkipped(el)) { decorateSelect(el); count += 1; } }
    else if (el.matches(CHECK_SELECTOR)) { if (!isSkipped(el)) { decorateCheck(el); count += 1; } }
  };
  if (root.nodeType === 1) decorate(root);
  root.querySelectorAll(RANGE_SELECTOR).forEach((el) => { if (!isSkipped(el)) { decorateRange(el); count += 1; } });
  root.querySelectorAll(NUMBER_SELECTOR).forEach((el) => { if (!isSkipped(el)) { decorateNumber(el); count += 1; } });
  root.querySelectorAll(SELECT_SELECTOR).forEach((el) => { if (!isSkipped(el)) { decorateSelect(el); count += 1; } });
  root.querySelectorAll(CHECK_SELECTOR).forEach((el) => { if (!isSkipped(el)) { decorateCheck(el); count += 1; } });
  return count;
}

let installed = false;

/**
 * Install once: decorates the current editor DOM, keeps range fills in sync,
 * and watches for controls that appear later (dialogs, popups, dock panels).
 * Only nodes that are (or contain) an editor surface are processed.
 */
export function installControlKit() {
  if (installed) return;
  installed = true;
  const boot = () => {
    document.querySelectorAll(EDITOR_ROOT_SELECTOR).forEach((root) => initControlKit(root));
  };
  boot();

  document.addEventListener(
    'input',
    (e) => {
      const t = e.target;
      if (t && t.matches && t.matches(RANGE_SELECTOR)) updateRangeFill(t);
    },
    true
  );

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        const isRoot = node.matches && node.matches(EDITOR_ROOT_SELECTOR);
        const containsRoot = node.querySelector && node.querySelector(EDITOR_ROOT_SELECTOR);
        if (isRoot) {
          initControlKit(node);
        } else if (containsRoot) {
          node.querySelectorAll(EDITOR_ROOT_SELECTOR).forEach((root) => initControlKit(root));
        } else if (inEditor(node)) {
          initControlKit(node);
        }
      });
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

export default { CONTROL_KIT_VERSION, initControlKit, installControlKit };
