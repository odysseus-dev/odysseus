// static/js/ui.js

/**
 * UI utilities for toasts, modals, scrolling, and user feedback
 */

import themeModule from './theme.js';
import * as Modals from './modalManager.js';
import spinnerModule from './spinner.js';
import { registerMenuDismiss, dismissTopMenu, dismissOrRemove } from './escMenuStack.js';
import { nextToolWindowZ, topToolWindowZ } from './toolWindowZOrder.js';

let toastEl = null;
let autoScrollEnabled = true;
let hoveredToggleCard = null;
let hoveredToggleWindow = null;
let hoveredDockChip = null;
let _lastPointerClientX = null;
let _lastPointerClientY = null;

// Smooth scroll state
let _scrollRafId = null;
let _scrollBox = null;

function _isTextEditingTarget(target) {
  const el = target && target.nodeType === 1 ? target : target?.parentElement;
  return !!(el && el.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""]'));
}

function _targetEl(target) {
  return target && target.nodeType === 1 ? target : target?.parentElement || null;
}

const SPACE_CARD_SELECTOR = [
  '#email-lib-modal .doclib-card',
  '#doclib-modal .doclib-card',
  '#doclib-modal .doclib-chat-row',
  '#memory-modal .doclib-card',
  '#tasks-modal .task-card',
  '#tasks-modal .task-log-row',
  '#research-overlay [data-job-id]',
  '#cookbook-modal .doclib-card',
  '.email-reader-tab-modal .doclib-card',
  '.email-window-modal .doclib-card',
].join(', ');

const SPACE_BLOCKED_SELECTOR = [
  'button',
  'a',
  'input',
  'textarea',
  'select',
  '[contenteditable="true"]',
  '[contenteditable=""]',
  '.recipient-chip',
  '.doclib-card-dropdown',
  '.email-card-dropdown',
  '.task-log-row-actions',
  '.modal-header',
].join(', ');

function _visibleModalForSpace(win) {
  const modal = win?.closest?.('.modal[id]');
  if (!modal || modal.classList.contains('hidden') || modal.classList.contains('modal-minimized')) return null;
  return modal;
}

function _isSpaceVisible(el) {
  if (!el || !document.contains(el)) return false;
  if (el.closest?.('.modal.hidden, .modal.modal-minimized, [hidden]')) return false;
  return true;
}

function _spaceWindowId(win) {
  if (!win || !document.contains(win)) return null;
  const modal = _visibleModalForSpace(win);
  if (modal && Modals.isRegistered(modal.id)) return modal.id;
  if (win.closest?.('.doc-editor-pane') && Modals.isRegistered('doc-panel') && !Modals.isMinimized('doc-panel')) return 'doc-panel';
  return null;
}

function _windowAtPointer() {
  if (_lastPointerClientX == null || _lastPointerClientY == null) return null;
  const x = _lastPointerClientX;
  const y = _lastPointerClientY;
  const candidates = [
    ...document.querySelectorAll('.modal:not(.hidden):not(.modal-minimized) .modal-content'),
    ...document.querySelectorAll('.doc-editor-pane'),
  ].filter(el => {
    if (!document.contains(el)) return false;
    const r = el.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  });
  if (!candidates.length) return null;
  return candidates.reduce((top, el) => {
    const mz = parseInt(getComputedStyle(el.closest('.modal') || el).zIndex, 10) || 0;
    const tz = parseInt(getComputedStyle(top.closest('.modal') || top).zIndex, 10) || 0;
    return mz >= tz ? el : top;
  });
}

function _containsPointer(el) {
  if (!el || _lastPointerClientX == null || _lastPointerClientY == null) return false;
  const r = el.getBoundingClientRect();
  return _lastPointerClientX >= r.left && _lastPointerClientX <= r.right
    && _lastPointerClientY >= r.top && _lastPointerClientY <= r.bottom;
}

function _closeHoveredWindow() {
  let win = _windowAtPointer();
  if (!win) {
    try {
      const underPointer = document.elementFromPoint(_lastPointerClientX, _lastPointerClientY);
      win = underPointer?.closest?.('.modal:not(.hidden):not(.modal-minimized) .modal-content, .doc-editor-pane') || null;
    } catch {}
  }
  if (!win) win = hoveredToggleWindow;
  if (!win || !document.contains(win)) return false;
  const modalForWin = win.closest?.('.modal[id]');
  if (modalForWin?.id === 'email-lib-modal') {
    const closeBtn = document.getElementById('email-lib-close') || modalForWin.querySelector('.close-btn');
    if (closeBtn) {
      try { closeBtn.click(); return true; } catch {}
    }
    try { modalForWin.remove(); return true; } catch {}
  }
  const id = _spaceWindowId(win);
  if (id && Modals.isRegistered(id)) {
    Modals.close(id);
    return true;
  }
  const modal = _visibleModalForSpace(win);
  if (!modal) return false;
  const closeBtn = modal.querySelector('.close-btn, .modal-close, .modal-close-btn, [data-action="close"]');
  if (closeBtn) {
    try { closeBtn.click(); return true; } catch {}
  }
  try { modal.classList.add('hidden'); return true; } catch {}
  return false;
}

function _spaceIsBlocked(e, surface) {
  const target = _targetEl(e.target);
  if (!target) return false;
  if (_isTextEditingTarget(target)) return !surface || surface.contains(target);
  const blocked = target.closest?.(SPACE_BLOCKED_SELECTOR);
  return !!(blocked && (!surface || surface.contains(blocked)));
}

function _activateSpaceCard(card) {
  if (!card || !document.contains(card)) return false;
  if (card.matches('#tasks-modal .task-card')) {
    const titleRow = card.querySelector('.memory-item-title')?.closest('div');
    if (titleRow) {
      titleRow.click();
      return true;
    }
  }
  card.dataset.spaceToggle = '1';
  card.click();
  setTimeout(() => {
    try { delete card.dataset.spaceToggle; } catch {}
  }, 0);
  return true;
}

function _initHoverCardSpaceToggle() {
  if (document._odysseusHoverCardSpaceToggle) return;
  document._odysseusHoverCardSpaceToggle = true;
  document.addEventListener('pointerover', (e) => {
    _lastPointerClientX = e.clientX;
    _lastPointerClientY = e.clientY;
    const chip = e.target?.closest?.('.minimized-dock-chip[data-modal-id]');
    if (chip) hoveredDockChip = chip;
    const card = e.target?.closest?.(SPACE_CARD_SELECTOR);
    if (card) hoveredToggleCard = card;
    const win = e.target?.closest?.('.modal:not(.hidden):not(.modal-minimized) .modal-content, .doc-editor-pane');
    if (win) hoveredToggleWindow = win;
  }, true);
  document.addEventListener('pointermove', (e) => {
    _lastPointerClientX = e.clientX;
    _lastPointerClientY = e.clientY;
  }, true);
  document.addEventListener('pointerout', (e) => {
    const next = e.relatedTarget;
    if (hoveredDockChip && (!next || !hoveredDockChip.contains(next))) hoveredDockChip = null;
    if (hoveredToggleCard && (!next || !hoveredToggleCard.contains(next))) hoveredToggleCard = null;
    if (hoveredToggleWindow && (!next || !hoveredToggleWindow.contains(next))) hoveredToggleWindow = null;
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || e.repeat) return;
    if (hoveredToggleCard && _isSpaceVisible(hoveredToggleCard)) {
      if (_spaceIsBlocked(e, hoveredToggleCard)) return;
      e.preventDefault();
      _activateSpaceCard(hoveredToggleCard);
      return;
    }
    if (hoveredDockChip && document.contains(hoveredDockChip)) {
      if (_spaceIsBlocked(e, hoveredDockChip)) return;
      const id = hoveredDockChip.dataset.modalId;
      if (id && Modals.isRegistered(id)) {
        e.preventDefault();
        Modals.restore(id);
      }
      return;
    }
    const id = _spaceWindowId(hoveredToggleWindow);
    if (!id) return;
    if (_spaceIsBlocked(e, hoveredToggleWindow)) return;
    e.preventDefault();
    Modals.minimize(id);
  }, true);
}

_initHoverCardSpaceToggle();

/**
 * Copy text to clipboard
 */
export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast('Copied');
  }
  catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast('Copied');
  }
}

// Wire swipe-to-dismiss on the shared toast element. Runs once, the first
// time a toast is shown. Tracks horizontal touch drag; if the user drags
// more than DISMISS_PX, the toast slides off in the drag direction and
// hides early. Anything less snaps back. Desktop unaffected (touch
// listeners only fire from a touchscreen — mouse is handled by the
// existing × button and auto-hide timer).
function _wireToastSwipe(el) {
  if (!el || el._swipeWired) return;
  el._swipeWired = true;
  const DISMISS_PX = 70;
  let startX = 0, currentX = 0, swiping = false;
  el.addEventListener('touchstart', (e) => {
    if (!el.classList.contains('show')) return;
    const t = e.touches[0];
    if (!t) return;
    startX = t.clientX;
    currentX = t.clientX;
    swiping = true;
    // Kill the slide-in transition mid-flight so the touch tracks the
    // finger 1:1 instead of fighting a still-running animation.
    el.style.transition = 'none';
  }, { passive: true });
  el.addEventListener('touchmove', (e) => {
    if (!swiping) return;
    const t = e.touches[0];
    if (!t) return;
    currentX = t.clientX;
    const dx = currentX - startX;
    el.style.transform = `translateX(${dx}px)`;
    // Fade as the toast leaves the rest position — visual cue for
    // approaching the dismiss threshold.
    el.style.opacity = String(Math.max(0.2, 1 - Math.abs(dx) / 200));
  }, { passive: true });
  const endSwipe = () => {
    if (!swiping) return;
    swiping = false;
    const dx = currentX - startX;
    // Restore the transition so the next mutation animates.
    el.style.transition = '';
    if (Math.abs(dx) > DISMISS_PX) {
      // Fling off in the drag direction, then hide.
      el.style.transform = `translateX(${dx > 0 ? '120%' : '-120%'})`;
      el.style.opacity = '0';
      clearTimeout(el._hideTimer);
      setTimeout(() => {
        el.classList.remove('show');
        el.classList.add('exiting');
        el.style.transform = '';
        el.style.opacity = '';
      }, 180);
    } else {
      // Snap back to rest.
      el.style.transform = '';
      el.style.opacity = '';
    }
  };
  el.addEventListener('touchend', endSwipe);
  el.addEventListener('touchcancel', endSwipe);
}

/**
 * Show success toast message
 */
export function showToast(msg, durationOrOpts) {
  if (!toastEl) {
    toastEl = document.getElementById('toast');
  }
  _wireToastSwipe(toastEl);
  toastEl.textContent = '';
  toastEl.classList.remove('error');

  let duration = 1200, actionLabel = null, onAction = null, actionHint = null, actionIcon = null, leadingIcon = null;
  if (typeof durationOrOpts === 'object' && durationOrOpts) {
    duration = durationOrOpts.duration || 5000;
    actionLabel = durationOrOpts.action;
    onAction = durationOrOpts.onAction;
    actionHint = durationOrOpts.actionHint || null;
    actionIcon = durationOrOpts.actionIcon || null;
    leadingIcon = durationOrOpts.leadingIcon || null;
  } else if (typeof durationOrOpts === 'number') {
    duration = durationOrOpts;
  }

  const textSpan = document.createElement('span');
  if (leadingIcon === 'check') {
    const icon = document.createElement('span');
    icon.className = 'toast-checkmark';
    icon.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
    toastEl.appendChild(icon);
  } else if (leadingIcon === 'spinner') {
    const wp = spinnerModule.createWhirlpool(14);
    const icon = wp.element;
    icon.classList.add('toast-whirlpool');
    icon.style.cssText = 'width:14px;height:14px;margin:0 8px 0 0;display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;';
    toastEl.appendChild(icon);
  }
  textSpan.textContent = msg;
  toastEl.appendChild(textSpan);

  if (actionLabel && onAction) {
    // Wrap the action in a small column so we can stack a Ctrl-Z-style hint
    // directly under the button.
    const stack = document.createElement('span');
    stack.style.cssText = 'display:inline-flex;flex-direction:column;align-items:center;gap:1px;margin-left:10px;line-height:1;';

    const btn = document.createElement('button');
    if (actionIcon) {
      btn.innerHTML = `<span style="display:inline-flex;align-items:center;gap:5px;">${actionIcon}<span></span></span>`;
      btn.querySelector('span span').textContent = actionLabel;
    } else {
      btn.textContent = actionLabel;
    }
    btn.style.cssText = 'padding:2px 10px;border:1px solid var(--fg);border-radius:4px;background:none;color:var(--fg);cursor:pointer;font-size:12px;pointer-events:auto;display:inline-flex;align-items:center;';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      toastEl.classList.remove('show');
      onAction();
    });
    stack.appendChild(btn);

    if (actionHint && window.innerWidth > 768) {
      const hint = document.createElement('span');
      hint.textContent = actionHint;
      hint.style.cssText = 'font-size:9px;opacity:0.55;letter-spacing:0.4px;text-transform:uppercase;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;margin-top:1px;pointer-events:none;';
      stack.appendChild(hint);
    }

    toastEl.appendChild(stack);
    toastEl.style.pointerEvents = 'auto';
  } else {
    toastEl.style.pointerEvents = '';
  }

  // Close button for all toasts — dismiss without waiting for timeout.
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'toast-close-btn';
  closeBtn.setAttribute('aria-label', 'Dismiss');
  closeBtn.title = 'Dismiss';
  closeBtn.textContent = '×';
  closeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    e.preventDefault();
    clearTimeout(toastEl._hideTimer);
    toastEl.classList.add('exiting');
    toastEl.classList.remove('show');
    toastEl.style.pointerEvents = '';
  });
  toastEl.appendChild(closeBtn);

  // Pin to top-right via CSS — clear any legacy inline overrides so the
  // slide-in-from-right / slide-out-to-left transition can run cleanly.
  toastEl.style.left = '';
  toastEl.style.transform = '';
  toastEl.classList.remove('exiting');
  toastEl.classList.add('show');
  clearTimeout(toastEl._hideTimer);
  toastEl._hideTimer = setTimeout(() => {
    // Add `exiting` so the CSS rule slides it off to the LEFT instead of
    // back to the right (where it came from). We piggyback on the same
    // .toast base; .exiting overrides the resting transform.
    toastEl.classList.add('exiting');
    toastEl.classList.remove('show');
    // Reset pointer-events so an action-toast (which sets it to 'auto'
    // for its clickable button) doesn't leave the toast intercepting
    // clicks after it's slid away. Was previously only cleared on the
    // NEXT plain toast, so a lingering action-toast could appear to
    // "lock" interaction near the top-right.
    toastEl.style.pointerEvents = '';
  }, duration);
}

/**
 * Show error toast message
 */
export function showError(msg) {
  if (!toastEl) {
    toastEl = document.getElementById('toast');
  }
  _wireToastSwipe(toastEl);
  toastEl.textContent = '';
  toastEl.classList.add('error');
  toastEl.style.left = '';
  toastEl.style.transform = '';
  toastEl.classList.remove('exiting');
  toastEl.classList.add('show');
  clearTimeout(toastEl._hideTimer);

  const textSpan = document.createElement('span');
  textSpan.textContent = msg;
  toastEl.appendChild(textSpan);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'toast-close-btn';
  closeBtn.setAttribute('aria-label', 'Dismiss');
  closeBtn.title = 'Dismiss';
  closeBtn.textContent = '×';
  closeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    e.preventDefault();
    clearTimeout(toastEl._hideTimer);
    toastEl.classList.add('exiting');
    toastEl.classList.remove('show');
    toastEl.style.pointerEvents = '';
  });
  toastEl.appendChild(closeBtn);

  toastEl._hideTimer = setTimeout(() => {
    toastEl.classList.add('exiting');
    toastEl.classList.remove('show');
  }, 6000);
}

/**
 * Smooth-scroll chat history to bottom using rAF lerp.
 * Throttled during streaming so it doesn't fight user scrolling.
 */
let _scrollThrottleTimer = null;
export function scrollHistory() {
  if (!autoScrollEnabled) return;
  if (!_scrollBox) {
    _scrollBox = document.getElementById('chat-history');
  }
  // Throttle: only start a new scroll animation every 500ms
  if (_scrollThrottleTimer) return;
  _scrollThrottleTimer = setTimeout(() => { _scrollThrottleTimer = null; }, 500);
  if (!_scrollRafId) {
    _scrollRafId = requestAnimationFrame(_smoothScrollStep);
  }
}

function _smoothScrollStep() {
  const box = _scrollBox;
  if (!box || !autoScrollEnabled) {
    _scrollRafId = null;
    return;
  }
  const target = box.scrollHeight - box.clientHeight;
  const current = box.scrollTop;
  const diff = target - current;

  // If user scrolled up significantly, don't force them down
  if (diff > 300) {
    _scrollRafId = null;
    return;
  }

  if (diff <= 1) {
    box.scrollTop = target;
    _scrollRafId = null;
    return;
  }

  // Lerp: gentle catch-up
  const factor = window.innerWidth <= 768 ? 0.4 : 0.2;
  box.scrollTop = current + diff * factor;
  _scrollRafId = requestAnimationFrame(_smoothScrollStep);
}

/**
 * Instant scroll to bottom — use for non-streaming contexts
 * like loading history or switching sessions.
 */
export function scrollHistoryInstant() {
  if (!_scrollBox) {
    _scrollBox = document.getElementById('chat-history');
  }
  if (_scrollBox) {
    _scrollBox.scrollTop = _scrollBox.scrollHeight;
  }
}

/**
 * Enable/disable auto-scroll
 */
export function setAutoScroll(enabled) {
  autoScrollEnabled = enabled;
}

/**
 * Get auto-scroll state
 */
export function getAutoScroll() {
  return autoScrollEnabled;
}

/**
 * Auto-resize textarea based on content
 */
export function autoResize(textarea) {
  const lineHeight = parseInt(getComputedStyle(textarea).lineHeight);
  const isMobile = window.innerWidth <= 768;
  const maxHeight = isMobile ? 150 : lineHeight * 8;

  // Use a hidden clone to measure without disrupting the real textarea
  let clone = textarea._resizeClone;
  if (!clone) {
    clone = textarea.cloneNode(false);
    clone.style.cssText = getComputedStyle(textarea).cssText;
    clone.style.position = 'absolute';
    clone.style.visibility = 'hidden';
    clone.style.height = '0';
    clone.style.transition = 'none';
    clone.style.overflow = 'hidden';
    clone.style.pointerEvents = 'none';
    clone.style.zIndex = '-1';
    textarea.parentNode.appendChild(clone);
    textarea._resizeClone = clone;
  }
  clone.style.width = textarea.offsetWidth + 'px';
  clone.value = textarea.value;
  clone.style.height = '0';
  const newHeight = Math.min(Math.max(clone.scrollHeight, lineHeight), maxHeight);
  textarea.style.height = newHeight + 'px';
  textarea.style.overflow = newHeight >= maxHeight ? 'auto' : 'hidden';
}

/**
 * Debounce function for performance
 */
export function debounce(func, wait) {
  let timeout;
  return function(...args) {
    const later = () => {
      timeout = null;
      func.apply(this, args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

/**
 * Get element by ID (utility helper)
 */
export function el(id) {
  return document.getElementById(id);
}

/**
 * Styled confirm dialog — replaces native browser confirm().
 * Returns a Promise<boolean>.
 */
export function styledConfirm(message, { confirmText = 'Confirm', cancelText = 'Cancel', danger = false } = {}) {
  return new Promise(resolve => {
    // Reuse or create the modal
    let overlay = document.getElementById('styled-confirm-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'styled-confirm-overlay';
      overlay.className = 'modal';
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box" role="dialog" aria-modal="true" aria-labelledby="styled-confirm-title" aria-describedby="styled-confirm-msg">' +
          '<div class="modal-header"><h4 id="styled-confirm-title">Confirm</h4></div>' +
          '<div class="modal-body"><p id="styled-confirm-msg"></p></div>' +
          '<div class="modal-footer">' +
            '<button id="styled-confirm-cancel"></button>' +
            '<button id="styled-confirm-ok"></button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
    }

    const msgEl = document.getElementById('styled-confirm-msg');
    const okBtn = document.getElementById('styled-confirm-ok');
    const cancelBtn = document.getElementById('styled-confirm-cancel');

    msgEl.textContent = message;
    okBtn.textContent = confirmText;
    cancelBtn.textContent = cancelText;
    okBtn.className = danger ? 'confirm-btn confirm-btn-danger' : 'confirm-btn confirm-btn-primary';
    cancelBtn.className = 'confirm-btn confirm-btn-secondary';

    // Remember what had focus so we can restore it when the dialog closes.
    const _prevFocus = document.activeElement;
    overlay.classList.remove('hidden');
    overlay.style.display = '';

    function cleanup(result) {
      overlay.classList.add('hidden');
      overlay.style.display = 'none';
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey);
      try { _prevFocus && _prevFocus.focus && _prevFocus.focus(); } catch {}
      resolve(result);
    }
    function onOk() { cleanup(true); }
    function onCancel() { cleanup(false); }
    function onBackdrop(e) { if (e.target === overlay) cleanup(false); }
    function onKey(e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        const active = document.activeElement;
        if (active === okBtn) cancelBtn.focus();
        else okBtn.focus();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        cleanup(false);
      } else if (e.key === 'Tab') {
        // Trap focus inside the dialog so Tab can't wander to the page behind.
        e.preventDefault();
        const f = [cancelBtn, okBtn];
        const i = f.indexOf(document.activeElement);
        const n = e.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i >= f.length - 1 ? 0 : i + 1);
        f[n].focus();
      }
    }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey);
    okBtn.focus();
  });
}

/**
 * Styled text-input prompt — drop-in replacement for window.prompt().
 * Resolves to the trimmed string the user typed, or null on Cancel / Escape / backdrop.
 */
export function styledPrompt(message, {
  title = 'Name',
  defaultValue = '',
  placeholder = '',
  confirmText = 'Save',
  cancelText = 'Cancel',
  maxLength = 80,
} = {}) {
  return new Promise(resolve => {
    let overlay = document.getElementById('styled-prompt-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'styled-prompt-overlay';
      overlay.className = 'modal';
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box styled-prompt-box" role="dialog" aria-modal="true" aria-labelledby="styled-prompt-title" aria-describedby="styled-prompt-msg">' +
          '<div class="modal-header"><h4 id="styled-prompt-title"></h4></div>' +
          '<div class="modal-body">' +
            '<p id="styled-prompt-msg"></p>' +
            '<input type="text" id="styled-prompt-input" class="styled-prompt-input" />' +
          '</div>' +
          '<div class="modal-footer">' +
            '<button id="styled-prompt-cancel" class="confirm-btn confirm-btn-secondary"></button>' +
            '<button id="styled-prompt-ok" class="confirm-btn confirm-btn-primary"></button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
    }

    const titleEl = document.getElementById('styled-prompt-title');
    const msgEl = document.getElementById('styled-prompt-msg');
    const input = document.getElementById('styled-prompt-input');
    const okBtn = document.getElementById('styled-prompt-ok');
    const cancelBtn = document.getElementById('styled-prompt-cancel');

    titleEl.textContent = title;
    msgEl.textContent = message || '';
    msgEl.style.display = message ? '' : 'none';
    input.value = defaultValue || '';
    input.placeholder = placeholder || '';
    input.maxLength = maxLength;
    okBtn.textContent = confirmText;
    cancelBtn.textContent = cancelText;

    // Remember what had focus so we can restore it when the dialog closes.
    const _prevFocus = document.activeElement;
    overlay.classList.remove('hidden');
    overlay.style.display = '';

    function cleanup(result) {
      overlay.classList.add('hidden');
      overlay.style.display = 'none';
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey);
      input.removeEventListener('keydown', onInputKey);
      try { _prevFocus && _prevFocus.focus && _prevFocus.focus(); } catch {}
      resolve(result);
    }
    function onOk() { cleanup((input.value || '').trim()); }
    function onCancel() { cleanup(null); }
    function onBackdrop(e) { if (e.target === overlay) cleanup(null); }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        cleanup(null);
      } else if (e.key === 'Tab') {
        // Trap focus inside the dialog (input → Cancel → OK → input …).
        e.preventDefault();
        const f = [input, cancelBtn, okBtn];
        const i = f.indexOf(document.activeElement);
        const n = e.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i >= f.length - 1 ? 0 : i + 1);
        f[n].focus();
      }
    }
    function onInputKey(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        onOk();
      }
    }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey);
    input.addEventListener('keydown', onInputKey);

    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  });
}

// Lookup table for esc(); hoisted out of the replace callback so it is
// allocated once rather than per matched character.
const _ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/**
 * HTML-escape a string to prevent XSS.
 * Canonical implementation — other modules should use uiModule.esc() instead of local copies.
 */
export function esc(s) {
  return (s || '').replace(/[&<>"']/g, (m) => _ESC_MAP[m]);
}

// ── Mobile: suppress synthetic click/mousedown on backdrop ──
// When a touch starts inside .modal-content, set a flag so that
// synthetic mouse events on the backdrop are ignored.
let _touchInsideModal = false;
if ('ontouchstart' in window) {
  document.addEventListener('touchstart', (e) => {
    if (e.target.closest('.modal-content')) {
      _touchInsideModal = true;
    }
  }, { passive: true });
  document.addEventListener('touchend', () => {
    // Clear after a short delay — synthetic click fires ~300ms after touchend
    setTimeout(() => { _touchInsideModal = false; }, 400);
  }, { passive: true });
}

/**
 * Check if a backdrop dismiss should be suppressed on mobile.
 * Other modules can call this to guard their own backdrop handlers.
 */
export function isTouchInsideModal() {
  return _touchInsideModal;
}

// Close floating dropdowns/popups on scroll to prevent them drifting
function _initScrollDismiss() {
  const chatHistory = document.getElementById('chat-history');
  if (chatHistory) {
    chatHistory.addEventListener('scroll', () => {
      chatHistory.querySelectorAll('.dropdown.show').forEach(d => d.classList.remove('show'));
      document.querySelectorAll('.ctx-popup').forEach(dismissOrRemove);
    }, { passive: true });
  } else {
    // Retry once if element doesn't exist yet
    setTimeout(_initScrollDismiss, 500);
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initScrollDismiss);
} else {
  _initScrollDismiss();
}

/**
 * Returns the SVG string for an empty-state icon. `kind` is one of
 * 'smiley' | 'sad' | 'neutral'. The returned <svg> has NO inline style —
 * callers wrap with `<span style="vertical-align:-3px;margin-left:6px;">…</span>`
 * (or similar) to keep the per-site visual nudge they need.
 */
export function emptyStateIcon(kind) {
  const SVG_OPEN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">';
  const SVG_CLOSE = '</svg>';
  let inner;
  switch (kind) {
    case 'sad':
      inner = '<circle cx="12" cy="12" r="10"/><path d="M16 16s-1.5-2-4-2-4 2-4 2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>';
      break;
    case 'neutral':
      inner = '<circle cx="12" cy="12" r="10"/><line x1="8" y1="15" x2="16" y2="15"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>';
      break;
    case 'smiley':
    default:
      inner = '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>';
      break;
  }
  return SVG_OPEN + inner + SVG_CLOSE;
}

const uiModule = {
  copyToClipboard,
  showToast,
  showError,
  styledConfirm,
  styledPrompt,
  scrollHistory,
  scrollHistoryInstant,
  setAutoScroll,
  getAutoScroll,
  autoResize,
  debounce,
  el,
  esc,
  isTouchInsideModal,
  emptyStateIcon,
  registerMenuDismiss
};

export default uiModule;

// Expose the styled confirm globally so any module can replace the native
// browser confirm() with the themed dialog — even files that don't import
// uiModule. Usage: `if (!await window.styledConfirm(msg, { danger:true })) return;`
if (typeof window !== 'undefined') {
  window.styledConfirm = styledConfirm;
}

// ── Mobile: clear enter animation so inline transform works for dragging ──
// The CSS `animation: sheet-enter ... forwards` holds the final transform,
// blocking any inline style changes. We clear it once the animation completes.
if ('ontouchstart' in window || window.innerWidth <= 768) {
  document.addEventListener('animationend', (e) => {
    if (e.animationName === 'sheet-enter' &&
        (e.target.classList.contains('modal-content') || e.target.id === 'theme-popup')) {
      e.target.classList.add('sheet-ready');
    }
  });
  // When a modal is re-shown, remove sheet-ready so the enter animation plays again
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'attributes' && m.attributeName === 'class') {
        const modal = m.target;
        if (modal.classList.contains('modal') && !modal.classList.contains('hidden')) {
          const content = modal.querySelector('.modal-content') || modal.querySelector('#theme-popup');
          if (content) {
            content.classList.remove('sheet-ready', 'modal-closing');
          }
        }
      }
    }
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });
}

// ── Mobile swipe-down-to-dismiss for bottom sheet modals ──
// Finger-following drag with velocity-based dismiss.
// Works from grab handle, header, OR anywhere on the sheet when content is scrolled to top.
if ('ontouchstart' in window) {
  const DISMISS_THRESHOLD = 50;    // px — dismiss if dragged past this
  const VELOCITY_THRESHOLD = 0.3;  // px/ms — fast flick dismisses even below threshold
  const RUBBER_RESISTANCE = 0.35;  // drag resistance when pulling up past origin

  let _swipeTarget = null;
  let _startY = 0, _startX = 0;
  let _lastY = 0, _lastT = 0;
  let _velocity = 0;
  let _dragging = false;    // true once we've committed to a vertical drag
  let _cancelled = false;   // true if horizontal movement detected

  // Close any floating dropdowns/menus that hang off body via position:fixed.
  // Called when a swipe-dismiss gesture starts so the menu doesn't orphan over
  // the page after the sheet slides away.
  function _closeFloatingDropdownsForSwipe() {
    document.querySelectorAll(
      '.email-card-dropdown, .hwfit-cached-dropdown, .cookbook-saved-menu, .cookbook-dep-menu'
    ).forEach(d => {
      if (d._anchor) d._anchor.classList.remove('cookbook-menu-active', 'reader-more-active');
      // Registered menus tear down through their own dismiss (releasing the
      // Escape-stack entry); unregistered ones (email/dep) just get removed.
      dismissOrRemove(d);
    });
  }

  document.addEventListener('touchstart', (e) => {
    // Match .modal-content or #theme-popup (which acts as modal-content but uses its own ID)
    const content = e.target.closest('.modal-content') || e.target.closest('#theme-popup');
    if (!content) return;

    // The image editor owns all touches inside its container so the user
    // can paint / move layers / draw selections without the modal trying
    // to interpret it as a swipe-to-dismiss gesture. Skip the swipe init
    // entirely when the touch starts inside the editor area.
    if (e.target.closest('.gallery-editor, .gallery-editor-container')) return;
    // Internal vertical drag handles (e.g. the calendar's cal-splitter that
    // resizes the day-detail pane) consume vertical touches themselves. If
    // we don't bail here, the swipe-dismiss path also tracks the touch and
    // slides the whole modal down as the user drags the handle. The
    // [data-no-swipe-dismiss] hook lets other components opt out the same
    // way without having to hard-code their selector here.
    if (e.target.closest('.cal-splitter, [data-no-swipe-dismiss]')) return;

    // Only allow swipe-dismiss from header or grab handle (top 48px)
    const isHeader = !!e.target.closest('.modal-header');
    const isButton = !!e.target.closest('button, input, select, label');
    if (isHeader && isButton) return; // let button clicks through
    const touch = e.touches[0];
    const contentRect = content.getBoundingClientRect();
    const isGrabZone = (touch.clientY - contentRect.top) < 48;
    // Also allow swipe-dismiss from anywhere on the sheet when it's already
    // scrolled to the top — feels natural and matches iOS bottom-sheet UX.
    const isAtScrollTop = content.scrollTop <= 0;

    if (!isHeader && !isGrabZone && !isAtScrollTop) return; // body touches → let native scroll handle it

    _swipeTarget = content;
    // Ensure CSS animation is cleared so inline transform works
    content.classList.add('sheet-ready');
    content.style.animation = 'none';
    _startY = touch.clientY;
    _startX = touch.clientX;
    _lastY = _startY;
    _lastT = e.timeStamp;
    _velocity = 0;
    _dragging = false;
    _cancelled = false;
  }, { passive: true });

  document.addEventListener('touchmove', (e) => {
    if (!_swipeTarget || _cancelled) return;
    const touch = e.touches[0];
    const dx = Math.abs(touch.clientX - _startX);
    const dy = touch.clientY - _startY;

    // First few pixels: decide if this is horizontal scroll or content scroll
    if (!_dragging) {
      if (dx > 40 && dx > Math.abs(dy) * 2) {
        _swipeTarget.style.transform = '';
        _swipeTarget = null;
        _cancelled = true;
        return;
      }
      if (Math.abs(dy) > 8) {
        // Find the nearest scrollable ancestor of the touch point
        let scrollEl = e.target;
        while (scrollEl && scrollEl !== _swipeTarget) {
          if (scrollEl.scrollHeight > scrollEl.clientHeight + 1) {
            const ov = getComputedStyle(scrollEl).overflowY;
            if (ov === 'auto' || ov === 'scroll') break;
          }
          scrollEl = scrollEl.parentElement;
        }
        const hasScroller = scrollEl && scrollEl !== _swipeTarget;
        // If touch is inside a scrollable child, let native scroll handle it
        if (hasScroller) {
          _swipeTarget.style.transform = '';
          _swipeTarget = null;
          _cancelled = true;
          return;
        }
        // If swiping up and modal-content itself is scrollable, let native handle it
        if (dy < 0 && _swipeTarget.scrollHeight > _swipeTarget.clientHeight + 1) {
          _swipeTarget.style.transform = '';
          _swipeTarget = null;
          _cancelled = true;
          return;
        }
        // If swiping down but content isn't at the top, let native scroll
        if (dy > 0 && _swipeTarget.scrollTop > 0) {
          _swipeTarget.style.transform = '';
          _swipeTarget = null;
          _cancelled = true;
          return;
        }
        _dragging = true;
        _swipeTarget.style.transition = 'none';
        _swipeTarget.style.willChange = 'transform';
        // A swipe is starting — close any floating menus/dropdowns so they
        // don't orphan over the page once the sheet slides away. Covers the
        // email reader More menu, cookbook serve kebab + saved-configs, and
        // anything else hanging off body via _anchor.
        _closeFloatingDropdownsForSwipe();
      } else {
        return;
      }
    }

    // Track velocity (exponential moving average)
    const dt = e.timeStamp - _lastT;
    if (dt > 0) {
      const instantV = (touch.clientY - _lastY) / dt;
      _velocity = _velocity * 0.6 + instantV * 0.4;
    }
    _lastY = touch.clientY;
    _lastT = e.timeStamp;

    e.preventDefault();
    if (dy > 0) {
      _swipeTarget.style.transform = `translateY(${dy}px)`;
    } else {
      const rubberDy = dy * RUBBER_RESISTANCE;
      _swipeTarget.style.transform = `translateY(${rubberDy}px)`;
    }
  }, { passive: false });

  document.addEventListener('touchend', (e) => {
    if (!_swipeTarget || !_dragging) {
      _swipeTarget = null;
      return;
    }
    const el = _swipeTarget;
    _swipeTarget = null;

    const dy = _lastY - _startY;
    const shouldDismiss = dy > DISMISS_THRESHOLD || (dy > 20 && _velocity > VELOCITY_THRESHOLD);

    el.style.willChange = '';

    if (shouldDismiss) {
      // Animate out — use remaining distance to calculate duration
      const remaining = el.offsetHeight - dy;
      const speed = Math.max(Math.abs(_velocity), 0.8); // min speed
      const duration = Math.min(Math.max(remaining / speed, 120), 300);
      el.style.transition = `transform ${duration}ms cubic-bezier(0.2, 0, 0.4, 1)`;
      el.style.transform = 'translateY(100%)';
      setTimeout(() => {
        const modal = el.closest('.modal');
        if (modal) {
          modal.classList.add('hidden');
          // Some modals (calendar, email library) toggle visibility via
          // inline display style which would override .hidden — clear it
          // so the modal is actually dismissed.
          modal.style.display = '';
          document.querySelectorAll('#settings-menu-list .list-item.active').forEach(i => i.classList.remove('active'));
          // Notify modules so they can sync internal open-state flags
          window.dispatchEvent(new CustomEvent('modal-dismissed', { detail: { id: modal.id } }));
          // Swiping a tool away to reveal a new/empty chat replays the welcome
          // "splash" reveal — the same nice effect notes gives on dismiss.
          // Only when the welcome screen is already the active state (new chat),
          // so we never cover a chat that has messages.
          const ws = document.getElementById('welcome-screen');
          if (ws && !ws.classList.contains('hidden')) {
            window.chatModule?.showWelcomeScreen?.();
          }
        }
        el.classList.remove('sheet-ready');
        el.style.transform = '';
        el.style.transition = '';
        el.style.animation = '';
      }, duration + 10);
    } else {
      // Snap back with spring-like easing
      el.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
      el.style.transform = '';
      setTimeout(() => { el.style.transition = ''; el.style.animation = ''; }, 260);
    }
  }, { passive: true });
}

// ---- Bring modal to front on click ----
{
  const raiseModalToFront = (modal, floor = 250) => {
    const z = nextToolWindowZ({
      exclude: modal,
      current: getComputedStyle(modal).zIndex,
      floor,
    });
    modal.style.setProperty('z-index', String(z), 'important');
    return z;
  };

  document.addEventListener('mousedown', (e) => {
    const modalContent = e.target.closest('.modal-content');
    if (!modalContent) return;
    const modal = modalContent.closest('.modal');
    if (!modal) return;
    raiseModalToFront(modal);
  });

  // Backdrop tap to close — delegated for all modals
  document.addEventListener('mousedown', (e) => {
    if (_touchInsideModal) return; // suppress synthetic events from content scrolling
    if (!e.target.classList.contains('modal')) return;
    const modal = e.target;
    if (modal.classList.contains('hidden')) return;
    const content = modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => {
        modal.classList.add('hidden');
        content.classList.remove('modal-closing');
      }, { once: true });
      setTimeout(() => {
        if (!modal.classList.contains('hidden')) {
          modal.classList.add('hidden');
          content.classList.remove('modal-closing');
        }
      }, 300);
    } else {
      modal.classList.add('hidden');
    }
  });
}

// ── Mobile: keep focused inputs visible above the keyboard ──
// When an input inside a modal gets focus on mobile, the OS keyboard
// covers the bottom half of the screen. The browser is supposed to
// scroll the input into view, but in bottom-sheet modals with their
// own scrolling container that often fails — the user types blind.
// Scroll the input into the middle of the still-visible viewport
// after the keyboard has had a moment to animate in.
if ('ontouchstart' in window || window.innerWidth <= 768) {
  let _kbScrollTimer = null;
  document.addEventListener('focusin', (e) => {
    const el = e.target;
    if (!el || el.nodeType !== 1) return;
    const tag = el.tagName;
    const isText = tag === 'INPUT' || tag === 'TEXTAREA' ||
                   (tag === 'DIV' && el.isContentEditable);
    if (!isText) return;
    // Inputs of type button/checkbox/radio/range/etc. don't summon a keyboard
    if (tag === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (['button','submit','reset','checkbox','radio','range','color','file','image'].includes(t)) return;
    }
    if (_kbScrollTimer) clearTimeout(_kbScrollTimer);
    // The keyboard typically takes 200–300ms to slide up; do the scroll
    // after that so we know the final visible viewport height.
    _kbScrollTimer = setTimeout(() => {
      _kbScrollTimer = null;
      // Skip the scroll if the input is already visible inside the
      // current viewport (with a small comfort margin). Otherwise every
      // re-focus — including the programmatic refocus that happens when
      // a typeahead input rebuilds the DOM on every keystroke — would
      // re-scroll the modal and yank the page up and down as the user
      // types.
      try {
        const r = el.getBoundingClientRect();
        const vh = (window.visualViewport?.height) || window.innerHeight;
        const margin = 24;
        const fullyVisible = r.top >= margin && r.bottom <= vh - margin;
        if (fullyVisible) return;
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } catch {
        try { el.scrollIntoView(); } catch {}
      }
    }, 300);
  });
}

// ── Global Escape arbiter: close exactly one thing per press ──
// Priority: expanded library card → open chat thinking block → topmost modal.
// Runs capture-phase + stopImmediatePropagation so per-modal ESC listeners
// never also fire (which would otherwise close several modals at once).
if (!window._odyEscExpandGuard) {
  window._odyEscExpandGuard = true;

  // Auto-promote any modal that becomes visible to the top of the z-stack.
  // Every modal shares `z-index: 250` from the base `.modal` rule, so visual
  // stacking falls back to DOM order — which is unpredictable (cookbook is
  // a static HTML node, calendar gets appended once and stays, compare and
  // research get re-appended on each open). Result: opening compare AFTER
  // cookbook can render compare UNDER it. Bumping the z-index on every
  // open guarantees most-recently-opened wins both visually AND for ESC.
  let _zCounter = 1000;
  const _isVisible = (m) => !m.classList.contains('hidden') && getComputedStyle(m).display !== 'none';
  const _promote = (m) => {
    if (!m?.classList?.contains('modal') || !_isVisible(m)) return;
    // Re-entry guard: setting style.zIndex itself fires the observer that
    // calls us back. Skip if this element is already pinned to the top
    // (matches the current counter) so we don't spin into an infinite loop.
    const cur = parseInt(getComputedStyle(m).zIndex, 10) || 0;
    if (cur === _zCounter && cur > topToolWindowZ({ exclude: m })) return;
    const z = nextToolWindowZ({
      exclude: m,
      current: cur,
      floor: _zCounter,
    });
    _zCounter = Math.max(_zCounter, z);
    if (z !== cur) m.style.setProperty('z-index', String(z), 'important');
  };
  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === 'childList') m.addedNodes.forEach(n => n.nodeType === 1 && _promote(n));
      else if (m.type === 'attributes' && m.target?.classList?.contains('modal')) _promote(m.target);
    }
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] });
  document.querySelectorAll('.modal').forEach(_promote);

  const pickTopModal = () => {
    const modals = [...document.querySelectorAll('.modal')].filter(_isVisible);
    if (!modals.length) return null;
    return modals.reduce((top, m) =>
      (parseInt(getComputedStyle(m).zIndex, 10) || 0) >= (parseInt(getComputedStyle(top).zIndex, 10) || 0)
        ? m : top
    );
  };

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;

    // Find the single thing to close, in priority order. The first hit wins.
    // Important: if a thinking block is open we MUST handle it ourselves and
    // not fall through to closing a modal — even if its header is missing
    // (the live-stream chat rebuilds thinking DOM mid-stream so the header
    // can briefly be absent). Toggling the `expanded` class directly is the
    // fallback so ESC never bypasses the thinking block to hit a modal.
    if (_closeHoveredWindow()) {
      e.stopImmediatePropagation(); e.preventDefault();
      return;
    }
    // Transient ad-hoc menus (dropdowns / context popups) live outside the
    // .modal system and register a dismiss callback in escMenuStack. Close the
    // most-recently-opened one first — so a menu opened over a modal dismisses
    // before the modal — and do it BEFORE the text-input guard below, since a
    // menu may own the focused input (e.g. a search dropdown).
    if (dismissTopMenu()) {
      e.stopImmediatePropagation(); e.preventDefault();
      return;
    }
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    const expanded = document.querySelector('.doclib-card-expanded');
    const think = document.querySelector('.thinking-content.expanded');
    if (expanded) {
      e.stopImmediatePropagation(); e.preventDefault();
      try { expanded.click(); } catch {}
      return;
    }
    if (think) {
      e.stopImmediatePropagation(); e.preventDefault();
      const thinkHeader = think.closest('.thinking-section')?.querySelector('.thinking-header[data-thinking-id]');
      if (thinkHeader) { try { thinkHeader.click(); } catch {} }
      else {
        // No header found — collapse the content directly.
        try { think.classList.remove('expanded'); } catch {}
      }
      return;
    }
    const galleryEditor = document.getElementById('gallery-editor-container');
    const galleryModal = galleryEditor?.closest('.modal');
    const galleryEditing = !!(
      galleryEditor &&
      galleryModal &&
      !galleryModal.classList.contains('hidden') &&
      getComputedStyle(galleryEditor).display !== 'none' &&
      galleryEditor.querySelector('.gallery-editor')
    );
    if (galleryEditing) {
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
    const settingsModal = document.getElementById('settings-modal');
    if (settingsModal && _isVisible(settingsModal)) {
      const innerForm = settingsModal.querySelector('#unified-intg-form, #set-email-accounts-form');
      if (innerForm && innerForm.style.display !== 'none' && innerForm.children.length > 0) {
        e.preventDefault();
        e.stopImmediatePropagation();
        innerForm.style.display = 'none';
        innerForm.innerHTML = '';
        return;
      }
    }
    const topModal = pickTopModal();
    if (!topModal) return;
    const closeBtn = topModal.querySelector('.close-btn, .modal-close-btn, [data-action="close"]');
    e.stopImmediatePropagation();
    e.preventDefault();
    if (closeBtn) { try { closeBtn.click(); } catch {} }
    else { try { topModal.classList.add('hidden'); } catch {} }
  }, true);
}
