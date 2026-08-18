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
