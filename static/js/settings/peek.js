// Settings window peek (transparency) chrome.
//
// The Appearance panel lets the user watch the rest of the UI react to a toggle
// without closing Settings. That is window chrome, not panel data, so it lives
// here rather than with the Appearance panel's checkbox handling: the fade is
// applied to the Settings window's own background via color-mix — never element
// opacity, so controls and text stay crisp — and it has to be cleared the
// moment the user leaves Appearance or closes the window. Mirrors the Theme
// customizer's slider.

import { byId } from './dom.js';

const PEEK_OPACITY = 55; // % opacity when the Peek toggle is on
const TOGGLE_ID = 'settings-opacity-wrap';

function _toggleEl() {
  return byId(TOGGLE_ID);
}

export function applySettingsPeek(modalEl, on) {
  const content = modalEl && modalEl.querySelector('.settings-modal-content, .modal-content');
  if (!content) return;
  const cards = content.querySelectorAll('.admin-card');
  if (on) {
    const bgMix = `color-mix(in srgb, var(--bg) ${PEEK_OPACITY}%, transparent)`;
    const panelMix = `color-mix(in srgb, var(--panel) ${PEEK_OPACITY}%, transparent)`;
    content.style.setProperty('background', bgMix, 'important');
    content.style.setProperty('backdrop-filter', 'none', 'important');
    content.style.setProperty('-webkit-backdrop-filter', 'none', 'important');
    cards.forEach(c => {
      c.style.setProperty('background', panelMix, 'important');
      c.style.setProperty('backdrop-filter', 'none', 'important');
      c.style.setProperty('-webkit-backdrop-filter', 'none', 'important');
    });
  } else {
    content.style.removeProperty('background');
    content.style.removeProperty('backdrop-filter');
    content.style.removeProperty('-webkit-backdrop-filter');
    cards.forEach(c => {
      c.style.removeProperty('background');
      c.style.removeProperty('backdrop-filter');
      c.style.removeProperty('-webkit-backdrop-filter');
    });
  }
}

// Show/hide the Peek toggle for the Appearance tab and apply or clear the fade.
export function syncSettingsPeek(modalEl, active) {
  const toggle = _toggleEl();
  if (toggle) toggle.classList.toggle('hidden', !active);
  if (active) {
    applySettingsPeek(modalEl, toggle ? toggle.classList.contains('active') : false);
  } else {
    applySettingsPeek(modalEl, false); // clear the fade off the Appearance tab
  }
}

export function bindSettingsPeekToggle(modalEl) {
  const toggle = _toggleEl();
  if (!toggle || toggle.dataset.bound === '1') return;
  toggle.dataset.bound = '1';
  toggle.addEventListener('click', () => {
    const on = !toggle.classList.contains('active');
    toggle.classList.toggle('active', on);
    toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
    applySettingsPeek(modalEl, on);
  });
}
