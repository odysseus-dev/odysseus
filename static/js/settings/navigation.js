// Settings navigation primitives.
//
// This module owns panel activation and sidebar click routing only. Individual
// panels continue to own their data loading and side effects.

import { DEFAULT_SETTINGS_PANEL_ID, isAdminManagedSettingsTab } from './registry.js';

const _boundModals = new WeakSet();

export function activateSettingsPanel(modalEl, tab) {
  if (!modalEl || !tab) return null;

  modalEl.querySelectorAll('[data-settings-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.settingsTab === tab);
  });
  modalEl.querySelectorAll('[data-settings-panel]').forEach(panel => {
    panel.classList.toggle('hidden', panel.dataset.settingsPanel !== tab);
  });
  return tab;
}

export function findSettingsSectionElement(modalEl, panelId, sectionId) {
  if (!modalEl || !panelId || !sectionId) return null;
  const panelEl = Array.from(modalEl.querySelectorAll('[data-settings-panel]'))
    .find(element => element.dataset.settingsPanel === panelId);
  if (!panelEl) return null;
  return Array.from(panelEl.querySelectorAll('[data-settings-section]'))
    .find(element => element.dataset.settingsSection === sectionId) || null;
}

/**
 * Whether a section card is currently presented to this user. The owning panel
 * itself is ignored (inactive panels are always hidden); only the card and its
 * ancestors inside the panel count.
 */
export function isSettingsSectionAvailable(sectionEl, options = {}) {
  const isAdmin = options.isAdmin === true;
  let node = sectionEl;
  while (node && !(node.dataset && node.dataset.settingsPanel)) {
    if (node.hidden === true) return false;
    if (typeof node.getAttribute === 'function') {
      if (node.getAttribute('hidden') !== null) return false;
      if (node.getAttribute('aria-hidden') === 'true') return false;
    }
    if (node.style && node.style.display === 'none') return false;
    if (!isAdmin && node.classList?.contains('admin-only')) return false;
    node = node.parentElement;
  }
  return Boolean(node);
}

const SECTION_TARGET_CLASS = 'settings-section-target';
const _targetTimers = new WeakMap();

/**
 * Scroll a section of the active panel into view and move focus to it, so a
 * search result or deep link lands on the control the user asked for rather
 * than on the top of the panel.
 */
export function revealSettingsSection(modalEl, panelId, sectionId) {
  const sectionEl = findSettingsSectionElement(modalEl, panelId, sectionId);
  if (!sectionEl) return false;

  if (sectionEl.getAttribute('tabindex') === null) {
    sectionEl.setAttribute('tabindex', '-1');
  }

  const reduceMotion = typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (typeof sectionEl.scrollIntoView === 'function') {
    sectionEl.scrollIntoView({ block: 'start', behavior: reduceMotion ? 'auto' : 'smooth' });
  }
  if (typeof sectionEl.focus === 'function') {
    sectionEl.focus({ preventScroll: true });
  }

  sectionEl.classList.remove(SECTION_TARGET_CLASS);
  // Restart the highlight when the same section is revealed twice in a row.
  void sectionEl.offsetWidth;
  sectionEl.classList.add(SECTION_TARGET_CLASS);
  clearTimeout(_targetTimers.get(sectionEl));
  _targetTimers.set(sectionEl, setTimeout(() => {
    sectionEl.classList.remove(SECTION_TARGET_CLASS);
  }, 1800));
  return true;
}

export function getActiveSettingsTab(modalEl, fallback = DEFAULT_SETTINGS_PANEL_ID) {
  if (!modalEl) return fallback;
  const active = modalEl.querySelector('[data-settings-tab].active');
  return active?.dataset?.settingsTab || fallback;
}

export function bindSettingsNavigation(modalEl, options = {}) {
  if (!modalEl || _boundModals.has(modalEl)) return;
  _boundModals.add(modalEl);

  const openAdminTab = options.openAdminTab;
  const onPanelActivated = options.onPanelActivated;

  modalEl.querySelectorAll('[data-settings-tab]').forEach(button => {
    button.addEventListener('click', () => {
      const tab = button.dataset.settingsTab;
      if (!tab) return;

      // Preserve the existing lazy-admin path: when the admin module accepts
      // the tab, it owns activation/rendering and the Settings shell does not
      // perform a second local switch.
      if (
        isAdminManagedSettingsTab(tab)
        && typeof openAdminTab === 'function'
        && openAdminTab(tab, button) === true
      ) {
        return;
      }

      activateSettingsPanel(modalEl, tab);
      if (typeof onPanelActivated === 'function') {
        onPanelActivated(tab, button);
      }
    });
  });
}
