// static/js/settings.js - Settings entry point and coordinator (ES6)
//
// The panels live in ./settings/*. This module builds the shared services
// (access resolution, the endpoint catalog, the model-default plumbing), wires
// the shell and initializes the panels once, in a fixed order. It keeps the
// public API that app.js, admin.js and the other callers depend on.

import uiModule from './ui.js?v=20260916largetoolscroll1';
import { byId } from './settings/dom.js';
import { createSettingsAccess } from './settings/access.js';
import { createEndpointCatalog } from './settings/endpointCatalog.js';
import { createModelDefaultCards } from './settings/modelDefaultCards.js';
import { createSettingsShell } from './settings/shell.js';
import { applySettingsSectionScopes } from './settings/scope.js';
import { getSettingsRegistryIssues } from './settings/registry.js';
import { bindSettingsSearch } from './settings/search.js';
import { bindSettingsSidebar } from './settings/sidebar.js';
import { bindSettingsNavigation } from './settings/navigation.js';
import {
  bindSettingsDrag,
  bindSettingsClose,
  bindOpenPromptModalLink,
} from './settings/lifecycle.js';
import { bindSettingsPeekToggle } from './settings/peek.js';
import { handleSettingsOauthReturn } from './settings/oauthReturn.js';
import { initDefaultChat } from './settings/defaultChat.js';
import { initTeacherModel } from './settings/teacherModel.js';
import { initUtilityModel } from './settings/utilityModel.js';
import { initImageSettings } from './settings/imageModels.js';
import { initVisionSettings } from './settings/visionModel.js';
import { initTtsSettings, initSttSettings } from './settings/speech.js';
import { initSearchSettings, initResearchSearchSettings } from './settings/searchProviders.js';
import { initResearchSettings } from './settings/researchModel.js';
import { initAgentSettings } from './settings/agent.js';
import { initAppearance, syncAppearanceCheckboxes } from './settings/appearance.js';
import { initShortcuts } from './settings/shortcuts.js';
import { initAccount } from './settings/account.js';
import { initIntegrations } from './settings/integrations/legacy.js';
import { initEmailSettings, initEmailAccountsSettings } from './settings/email.js';
import { initReminderSettings } from './settings/reminders.js';
import { initUnifiedIntegrations as _initUnifiedIntegrations } from './settings/integrations/unified.js';
import { initDocumentWritingStyle } from './settings/writingStyle.js';

let initialized = false;
let modalEl = null;

// Show the caller's access in the modal: offered panels and scope labels.
// Safe to repeat; scope labels are replaced, not added.
function _applySettingsAccess(access) {
  if (!modalEl) return;
  _shell.syncAdminVisibility();
  applySettingsSectionScopes(modalEl, access);
}

// What this caller may change (see settings/access.js). Unknown until
// /api/auth/status answers; the first answer is shown in the modal if it is
// already built. The model catalogs and defaults depend on it, so they are
// built on the same resolver.
const _access = createSettingsAccess({ onResolved: _applySettingsAccess });
const _catalog = createEndpointCatalog({ resolveAccess: _access.resolve });
const _cards = createModelDefaultCards({ resolveAccess: _access.resolve, catalog: _catalog });

/* ── Settings shell coordination ──
   Panel activation side effects, the admin handoff, admin-only visibility and
   the public open/close API live in ./settings/shell.js. This coordinator only
   supplies the panel-owned pieces the shell needs. */
const _shell = createSettingsShell({
  getModal: () => modalEl,
  ensureInitialized: () => {
    if (!initialized) initAll();
    // Reopening Settings asks again after a failed status lookup.
    else if (!_access.isResolved()) _access.resolve().catch(function() {});
  },
  syncAppearanceCheckboxes: () => syncAppearanceCheckboxes(modalEl),
  refreshAiModelEndpoints: () => refreshAiModelEndpoints(),
  isAdmin: () => _access.canManage(),
  accountsEnabled: () => _access.accountsEnabled(),
  accessKnown: () => _access.known(),
  getAdminModule: () => window.adminModule,
});

function initAll() {
  modalEl = byId('settings-modal');

  // Who may change what comes from the server. Until it answers, the
  // model-default cards stay read-only; their Retry (or reopening Settings)
  // asks again. Access may already be known (an endpoint refresh can run
  // before Settings is first opened), so apply it here as well.
  _access.resolve().then(_applySettingsAccess, function(error) {
    console.warn('Settings access is unknown until /api/auth/status answers', error);
  });

  bindSettingsNavigation(modalEl, {
    openAdminTab: _shell.openAdminTab,
    onPanelActivated: _shell.onPanelActivated,
  });

  bindSettingsSearch(modalEl, {
    isAdmin: () => _access.canManage(),
    accountsEnabled: () => _access.accountsEnabled(),
    openPanel(tab) {
      const button = modalEl.querySelector(`[data-settings-tab="${tab}"]`);
      if (button) button.click();
    },
  });

  bindSettingsSidebar(modalEl);

  const registryIssues = getSettingsRegistryIssues(modalEl);
  if (registryIssues.length) {
    console.warn('Settings registry/DOM mismatch:', registryIssues);
  }

  bindSettingsDrag(modalEl);

  bindSettingsClose(modalEl, {
    closeSettings: close,
    isTouchInsideModal: () => uiModule.isTouchInsideModal(),
  });

  bindOpenPromptModalLink({
    getModal: () => modalEl,
    closeSettings: close,
  });

  bindSettingsPeekToggle(modalEl);
  initialized = true;
  initDefaultChat({ catalog: _catalog, cards: _cards });
  initTeacherModel({ catalog: _catalog });
  initUtilityModel({ catalog: _catalog, cards: _cards });
  initImageSettings();
  initVisionSettings({ catalog: _catalog });
  initTtsSettings();
  initSttSettings();
  initSearchSettings();
  initResearchSettings({ catalog: _catalog, cards: _cards });
  initResearchSearchSettings();
  initAgentSettings();
  initAppearance(modalEl, { canManage: _access.canManage });
  initShortcuts();
  initAccount();
  initIntegrations();
  initEmailSettings();
  initEmailAccountsSettings({ openSettings: open });
  initReminderSettings(modalEl, { canManage: _access.canManage });
  initUnifiedIntegrations();
  initDocumentWritingStyle();
}

/* ═══════════════════════════════════════════
   PUBLIC API
   ═══════════════════════════════════════════ */
export function open(tab, options) { _shell.open(tab, options); }
export function close() { _shell.close(); }
export function refreshAiModelEndpoints() { return _catalog.refresh(); }
// The integrations entry points keep their no-argument contract; the unified
// panel is handed open() so it never imports this module back.
function initUnifiedIntegrations() { return _initUnifiedIntegrations({ openSettings: open }); }
function syncAdminVisibility() { _shell.syncAdminVisibility(); }

handleSettingsOauthReturn({ openSettings: open });

const settingsModule = { open, close, initIntegrations, initUnifiedIntegrations, syncAdminVisibility, refreshAiModelEndpoints };

export default settingsModule;
