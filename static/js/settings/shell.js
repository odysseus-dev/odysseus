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

import {
  activateSettingsPanel,
  getActiveSettingsTab,
  revealSettingsSection,
} from './navigation.js';
import { showSettingsModal, hideSettingsModal } from './lifecycle.js';
import {
  getFirstOfferedSettingsPanelId,
  getSettingsPanel,
  isAdminManagedSettingsTab,
  isSettingsPanelOffered,
} from './registry.js';
import { syncSettingsPeek } from './peek.js';

/**
 * @param {object} options
 * @param {() => Element|null} options.getModal          the Settings modal, once initialized
 * @param {() => void} options.ensureInitialized         first-open panel initialization
 * @param {() => void} options.syncAppearanceCheckboxes  Appearance panel state refresh
 * @param {() => void} options.refreshAiModelEndpoints   AI panel endpoint refresh
 * @param {() => boolean} options.isAdmin                may change server-wide settings
 * @param {() => boolean} [options.accountsEnabled]      false when authentication is disabled
 * @param {() => boolean} [options.accessKnown]          false until /api/auth/status has answered
 * @param {() => object|null} options.getAdminModule     the lazily loaded admin module
 */
export function createSettingsShell(options = {}) {
  const getModal = options.getModal;
  const ensureInitialized = options.ensureInitialized;
  const syncAppearanceCheckboxes = options.syncAppearanceCheckboxes;
  const refreshAiModelEndpoints = options.refreshAiModelEndpoints;
  const isAdmin = options.isAdmin;
  const accountsEnabled = options.accountsEnabled;
  const accessKnown = options.accessKnown;
  const getAdminModule = options.getAdminModule;

  const _modal = () => (typeof getModal === 'function' ? getModal() : null);
  const _admin = () => (typeof getAdminModule === 'function' ? getAdminModule() : null);
  const _access = () => ({
    isAdmin: typeof isAdmin === 'function' ? !!isAdmin() : false,
    accountsEnabled: typeof accountsEnabled === 'function' ? accountsEnabled() !== false : true,
  });
  const _accessKnown = () => (typeof accessKnown === 'function' ? accessKnown() !== false : true);
  // Panels any caller could be offered once their access is known.
  const _MOST_PERMISSIVE = Object.freeze({ isAdmin: true, accountsEnabled: true });

  // A panel requested by open() before access was known, applied (or
  // dropped) once it is. Cleared when the user goes to another panel, by the
  // next open() and by close().
  let _pendingOpen = null;

  function _afterActivation(modalEl, tab, section) {
    // Preserve existing panel-specific side effects when Settings is opened
    // directly to a tab as well as when the user navigates there.
    onPanelActivated(tab);

    // Auto-init admin data if showing an admin tab.
    const admin = _admin();
    if (isAdminManagedSettingsTab(tab) && admin && !admin._initialized) {
      admin._initData();
    }

    if (section) revealSettingsSection(modalEl, tab, section);
  }

  function _isOpen(modalEl) {
    return Boolean(modalEl) && !modalEl.classList.contains('hidden');
  }

  function onPanelActivated(tab) {
    _pendingOpen = null;
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

  function _syncVisibility(modalEl) {
    const admin = typeof isAdmin === 'function' ? !!isAdmin() : false;
    modalEl.querySelectorAll('.admin-only').forEach(el => {
      el.style.display = admin ? '' : 'none';
    });
    // Panels that need real accounts (user management) are not offered when
    // authentication is disabled, even to the local operator.
    const accounts = typeof accountsEnabled === 'function' ? accountsEnabled() !== false : true;
    modalEl.querySelectorAll('[data-settings-tab]').forEach(button => {
      const panel = getSettingsPanel(button.dataset.settingsTab);
      if (panel && panel.requiresAccounts && !accounts) button.style.display = 'none';
    });
  }

  /**
   * Re-apply what this caller is offered. Called again once
   * /api/auth/status answers, which also settles a panel requested before
   * then.
   */
  function syncAdminVisibility() {
    const modalEl = _modal();
    if (!modalEl) return;
    _syncVisibility(modalEl);

    if (!_accessKnown()) return;
    const access = _access();
    const pending = _pendingOpen;
    _pendingOpen = null;
    if (pending && _isOpen(modalEl) && isSettingsPanelOffered(pending.tab, access)) {
      activateSettingsPanel(modalEl, pending.tab);
      _afterActivation(modalEl, pending.tab, pending.section);
      return;
    }
    // Never leave this caller on a panel they are not offered.
    const current = getActiveSettingsTab(modalEl);
    if (!isSettingsPanelOffered(current, access)) {
      const next = getFirstOfferedSettingsPanelId(access);
      activateSettingsPanel(modalEl, next);
      if (_isOpen(modalEl)) _afterActivation(modalEl, next, '');
    }
  }

  /**
   * Open Settings, optionally on a panel and a section within it:
   * `open('ai', { section: 'default-chat' })` lands on that card.
   */
  function open(tab, openOptions = {}) {
    if (typeof ensureInitialized === 'function') ensureInitialized();

    if (typeof syncAppearanceCheckboxes === 'function') syncAppearanceCheckboxes();

    const modalEl = _modal();
    showSettingsModal(modalEl);
    _pendingOpen = null;
    if (modalEl) _syncVisibility(modalEl);

    const section = openOptions && typeof openOptions.section === 'string'
      ? openOptions.section
      : '';
    // Explicit navigation (other modules, slash commands, deep links) gets
    // the same rule as the sidebar: never a panel this caller is not offered,
    // such as the admin-only panels for a signed-in user or user management
    // when authentication is disabled. The server still authorizes every
    // request; this only keeps the UI from presenting controls that fail.
    const access = _access();
    const requested = tab && getSettingsPanel(tab) ? tab : '';
    const allowed = Boolean(requested) && isSettingsPanelOffered(requested, access);

    if (allowed) {
      activateSettingsPanel(modalEl, requested);
    } else {
      const current = getActiveSettingsTab(modalEl);
      if (!isSettingsPanelOffered(current, access)) {
        activateSettingsPanel(modalEl, getFirstOfferedSettingsPanelId(access));
      }
    }

    _afterActivation(modalEl, getActiveSettingsTab(modalEl), allowed ? section : '');

    if (requested && !allowed && !_accessKnown()
        && isSettingsPanelOffered(requested, _MOST_PERMISSIVE)) {
      // Who the caller is is not known yet; decide when it is.
      _pendingOpen = { tab: requested, section };
    }
  }

  function close() {
    _pendingOpen = null;
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
