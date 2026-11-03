// Settings shell coordination.
//
// settings.js still owns every panel's data loading. This module owns the shell
// around them: what happens when a panel becomes active, where an
// admin-managed tab is handed off to admin.js, which elements are admin-only,
// and the public open/close entry points.
//
// It keeps no panel state. Everything it needs from the panels arrives through
// the options below, so panels can move out of settings.js one at a time
// without this file changing — and so the shell's behavior is testable without
// booting a panel.

import { activateSettingsPanel, getActiveSettingsTab } from './navigation.js';
import { showSettingsModal, hideSettingsModal } from './lifecycle.js';
import { isAdminManagedSettingsTab } from './registry.js';
import { syncSettingsPeek } from './peek.js';

/**
 * @param {object} options
 * @param {() => Element|null} options.getModal          the Settings modal, once initialized
 * @param {() => void} options.ensureInitialized         first-open panel initialization
 * @param {() => void} options.syncAppearanceCheckboxes  Appearance panel state refresh
 * @param {() => void} options.refreshAiModelEndpoints   AI panel endpoint refresh
 * @param {() => boolean} options.isAdmin                current admin status
 * @param {() => object|null} options.getAdminModule     the lazily loaded admin module
 */
export function createSettingsShell(options = {}) {
  const getModal = options.getModal;
  const ensureInitialized = options.ensureInitialized;
  const syncAppearanceCheckboxes = options.syncAppearanceCheckboxes;
  const refreshAiModelEndpoints = options.refreshAiModelEndpoints;
  const isAdmin = options.isAdmin;
  const getAdminModule = options.getAdminModule;

  const _modal = () => (typeof getModal === 'function' ? getModal() : null);
  const _admin = () => (typeof getAdminModule === 'function' ? getAdminModule() : null);

  function onPanelActivated(tab) {
    // Appearance keeps its existing transparent preview behavior.
    document.body.classList.toggle('settings-appearance-open', tab === 'appearance');
    syncSettingsPeek(_modal(), tab === 'appearance');

    // AI endpoints are intentionally refreshed only when entering the AI panel.
    if (tab === 'ai' && typeof refreshAiModelEndpoints === 'function') {
      refreshAiModelEndpoints();
    }
  }

  function openAdminTab(tab) {
    const admin = _admin();
    if (admin && typeof admin.open === 'function') {
      admin.open(tab);
      return true;
    }
    return false;
  }

  function syncAdminVisibility() {
    const modalEl = _modal();
    if (!modalEl) return;
    const admin = typeof isAdmin === 'function' ? !!isAdmin() : false;
    modalEl.querySelectorAll('.admin-only').forEach(el => {
      el.style.display = admin ? '' : 'none';
    });
  }

  function open(tab) {
    if (typeof ensureInitialized === 'function') ensureInitialized();

    if (typeof syncAppearanceCheckboxes === 'function') syncAppearanceCheckboxes();

    const modalEl = _modal();
    showSettingsModal(modalEl);
    syncAdminVisibility();

    if (tab) {
      activateSettingsPanel(modalEl, tab);
    }

    // Preserve existing panel-specific side effects when Settings is opened
    // directly to a tab as well as when the user navigates there.
    const activeTab = tab || getActiveSettingsTab(modalEl);
    onPanelActivated(activeTab);

    // Auto-init admin data if showing an admin tab.
    const admin = _admin();
    if (isAdminManagedSettingsTab(activeTab) && admin && !admin._initialized) {
      admin._initData();
    }
  }

  function close() {
    const modalEl = _modal();
    if (!modalEl) return;

    // Always clear the Appearance state so the rest of the app does not remain
    // dimmed if Settings is closed while that panel is active.
    document.body.classList.remove('settings-appearance-open');
    syncSettingsPeek(modalEl, false);

    hideSettingsModal(modalEl);
  }

  return { open, close, onPanelActivated, openAdminTab, syncAdminVisibility };
}
