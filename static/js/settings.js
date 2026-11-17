// static/js/settings.js — Settings panel module (ES6)
// User-facing preferences: AI models, search, appearance

import uiModule from './ui.js?v=20260916largetoolscroll1';
import { byId } from './settings/dom.js';
import { postSettings as _postSettings } from './settings/api.js';
import { initTtsSettings, initSttSettings } from './settings/speech.js';
import { initDocumentWritingStyle } from './settings/writingStyle.js';
import { initImageSettings } from './settings/imageModels.js';
import { initAgentSettings } from './settings/agent.js';
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
import { createSettingsShell } from './settings/shell.js';
import { handleSettingsOauthReturn } from './settings/oauthReturn.js';
import { sortModelIds } from './modelSort.js';
import { providerLogo } from './providers.js';
import { loadSettingsAccess } from './authStatus.js';
import { applySettingsSectionScopes } from './settings/scope.js';
import { createModelDefaultsStore, modelDefaultSaveError } from './settings/modelDefaults.js';
import {
  captureFallbackWidgetFocus,
  cleanForegroundFallbackCandidates,
  createForegroundPreferenceSaveQueue,
  MAX_FOREGROUND_FALLBACKS,
  moveForegroundFallbackCandidate,
  nextForegroundFallbackCandidate,
  normalizeForegroundFallbackModelCatalog,
  normalizeForegroundFallbackPrefs,
  restoreFallbackWidgetFocus,
  summarizeForegroundFallbackCandidateEligibility,
} from './foregroundFallbackSettings.js';
import { initShortcuts } from './settings/shortcuts.js';
import { initSearchSettings, initResearchSearchSettings } from './settings/searchProviders.js';
import { initAppearance, syncAppearanceCheckboxes } from './settings/appearance.js';
import { initAccount } from './settings/account.js';
import { initReminderSettings } from './settings/reminders.js';
import { initEmailAccountsSettings, initEmailSettings } from './settings/email.js';
import { initIntegrations } from './settings/integrations/legacy.js';
import { initUnifiedIntegrations as _initUnifiedIntegrations } from './settings/integrations/unified.js';

let initialized = false;
let modalEl = null;
// What this caller may change, from /api/auth/status (see authStatus.js).
// null until that answer arrives. While it is unknown, Settings offers only
// what every caller may use and stores no per-account choice anywhere.
let _settingsAccess = null;
let _settingsAccessRequest = null;
function _canManageSettings() {
  if (_settingsAccess) return _settingsAccess.canManage;
  return window._canManageSettings === true || !!window._isAdmin;
}
function _accountsEnabled() {
  return !_settingsAccess || _settingsAccess.authEnabled;
}
function _settingsAccessKnown() {
  return Boolean(_settingsAccess) || typeof window._canManageSettings === 'boolean';
}
// Show the caller's access in the modal: offered panels and scope labels.
// Safe to repeat; scope labels are replaced, not added.
function _applySettingsAccess(access) {
  if (!modalEl) return;
  _shell.syncAdminVisibility();
  applySettingsSectionScopes(modalEl, access);
}
/**
 * Resolve what this caller may change. Rejects while /api/auth/status is
 * unavailable; the next call asks again (authStatus.js does not keep a
 * failure). The first answer is shown in the modal if it is already built.
 */
function _resolveSettingsAccess() {
  if (_settingsAccess) return Promise.resolve(_settingsAccess);
  if (!_settingsAccessRequest) {
    _settingsAccessRequest = loadSettingsAccess()
      .then(function(access) {
        if (!_settingsAccess) {
          _settingsAccess = access;
          _applySettingsAccess(access);
        }
        return _settingsAccess;
      })
      .finally(function() { _settingsAccessRequest = null; });
  }
  return _settingsAccessRequest;
}
let _modelDefaultsStorePromise = null;
function _personalModelDefaults(access) {
  return Boolean(access && access.personalModelDefaults);
}
/**
 * The model-defaults store for this caller. Which store (the caller's own
 * preferences or the server-wide settings) depends on who the caller is, so
 * none is chosen until that is known: this rejects instead, and the cards
 * stay read-only with Retry.
 */
function _getModelDefaultsStore() {
  if (!_modelDefaultsStorePromise) {
    _modelDefaultsStorePromise = _resolveSettingsAccess().then(function(access) {
      return createModelDefaultsStore({
        personal: _personalModelDefaults(access),
        clearOwnOverrides: Boolean(access.authEnabled && access.canManage),
      });
    });
    _modelDefaultsStorePromise.catch(function() { _modelDefaultsStorePromise = null; });
  }
  return _modelDefaultsStorePromise;
}
function _ensureBlankOption(selectEl, text) {
  if (!selectEl) return;
  var first = selectEl.options[0];
  if (first && first.value === '') { first.textContent = text; return; }
  var blank = document.createElement('option');
  blank.value = '';
  blank.textContent = text;
  selectEl.insertBefore(blank, first || null);
}
/**
 * Where a model-default card reads and writes, resolved on each load until it
 * succeeds: the store for this caller (none while access is unknown) and,
 * once, the endpoints offered to them. A Retry therefore recovers from a
 * failed status lookup without binding anything again.
 */
function _createModelDefaultSource(label, onEndpoints) {
  var store = null;
  var endpointsLoaded = false;
  return {
    store: function() { return store; },
    load: async function() {
      if (!store) store = await _getModelDefaultsStore();
      if (!endpointsLoaded) {
        try {
          onEndpoints(await _fetchModelDefaultEndpoints(), store);
          endpointsLoaded = true;
        } catch (e) { console.warn('Failed to load endpoints for ' + label, e); }
      }
      return store.load();
    },
  };
}

/**
 * Load state for a model-default card. The card's controls stay disabled
 * until its stored values load, so a save can never overwrite values the form
 * never showed; a failed load offers Retry. `run()` is also how a card shows
 * the stored values again after a failed save. Controls a card renders itself
 * (fallback rows) follow through `onDisabledChange`.
 */
function _createModelDefaultLoader(opts) {
  var loaded = false;
  var retryBtn = null;

  function setControlsDisabled(disabled) {
    opts.controls().forEach(function(control) { if (control) control.disabled = disabled; });
    if (typeof opts.onDisabledChange === 'function') opts.onDisabledChange(disabled);
  }

  function clearFailure() {
    if (!retryBtn) return;
    retryBtn.remove();
    retryBtn = null;
    if (opts.msgEl) opts.msgEl.textContent = '';
  }

  function showFailure() {
    if (!opts.msgEl) return;
    opts.msgEl.textContent = 'Could not load the saved choice. ';
    opts.msgEl.style.color = 'var(--red)';
    retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'settings-fallback-add settings-model-default-retry';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', async function() {
      var button = retryBtn;
      button.disabled = true;
      var ok = await run();
      if (!ok && button.isConnected) button.disabled = false;
      else if (ok && opts.controls()[0]) opts.controls()[0].focus();
    });
    opts.msgEl.appendChild(retryBtn);
  }

  async function run() {
    try {
      var stored = await opts.load();
      loaded = true;
      setControlsDisabled(false);
      clearFailure();
      opts.apply(stored);
      return true;
    } catch (error) {
      console.warn('Failed to load ' + opts.label, error);
      loaded = false;
      setControlsDisabled(true);
      clearFailure();
      showFailure();
      return false;
    }
  }

  setControlsDisabled(true);
  return { run: run, isLoaded: function() { return loaded; } };
}

function _showCardMessage(msgEl, text, failed) {
  if (!msgEl) return;
  msgEl.textContent = text;
  msgEl.style.color = failed ? 'var(--red)' : 'var(--fg)';
  if (!failed && text) {
    setTimeout(function() { if (msgEl.textContent === text) msgEl.textContent = ''; }, 2000);
  }
}

/**
 * POST a settings patch, then drop the shared snapshot in appConfig.js.
 *
 * Every write in this file goes through here so no save path can forget the
 * invalidation — a stale settings object served for the rest of the session is
 * a worse bug than the duplicate fetches the cache removes. The invalidation is
 * in a `finally` because a request that throws on the way back may still have
 * been applied server-side.
 *
 * Reads in this file deliberately stay direct fetches: this panel is the writer
 * and edits what it reads, so it must see the authoritative state, not a cache.
 */

const el = byId;
function esc(s) { return uiModule.esc(s); }
/* ── Settings shell coordination ──
   Panel activation side effects, the admin handoff, admin-only visibility and
   the public open/close API live in ./settings/shell.js. This coordinator only
   supplies the panel-owned pieces the shell needs. */
const _shell = createSettingsShell({
  getModal: () => modalEl,
  ensureInitialized: () => {
    if (!initialized) initAll();
    // Reopening Settings asks again after a failed status lookup.
    else if (!_settingsAccess) _resolveSettingsAccess().catch(function() {});
  },
  syncAppearanceCheckboxes: () => syncAppearanceCheckboxes(modalEl),
  refreshAiModelEndpoints: () => refreshAiModelEndpoints(),
  isAdmin: () => _canManageSettings(),
  accountsEnabled: () => _accountsEnabled(),
  accessKnown: () => _settingsAccessKnown(),
  getAdminModule: () => window.adminModule,
});

/* ═══════════════════════════════════════════
   AI TAB
   ═══════════════════════════════════════════ */

const _aiEndpointRefreshers = new Set();
const _foregroundFallbackEndpointRefreshers = new Set();
let _aiEndpointRefreshInFlight = null;

async function _fetchModelEndpoints() {
  const epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
  if (!epRes.ok) throw new Error(`HTTP ${epRes.status}`);
  const endpoints = await epRes.json();
  return Array.isArray(endpoints) ? endpoints : [];
}

function _selectableEndpointModels(endpoint) {
  if (!endpoint) return [];
  return Array.from(new Set([
    ...(Array.isArray(endpoint.models) ? endpoint.models : []),
    ...(Array.isArray(endpoint.pinned_models) ? endpoint.pinned_models : []),
  ].filter(Boolean).map(String)));
}

// Endpoints offered by the model-default cards. Administrators manage the
// whole pool (admin-only /api/model-endpoints); signed-in users choose from
// the owner-scoped, allowlist-filtered chat catalog the runtime resolver
// honors for them.
async function _fetchModelDefaultEndpoints() {
  const access = await _resolveSettingsAccess();
  return _personalModelDefaults(access)
    ? _fetchForegroundFallbackEndpoints()
    : _fetchModelEndpoints();
}

async function _fetchForegroundFallbackEndpoints() {
  const response = await fetch(
    '/api/models?background=false&foreground_fallback=true',
    { credentials: 'same-origin' }
  );
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return normalizeForegroundFallbackModelCatalog(await response.json());
}

function _endpointLabel(ep) {
  return ep.name + (ep.online ? '' : ' (offline)');
}

function _fillEndpointSelect(selectEl, endpoints, selected, keepBlank) {
  if (!selectEl) return;
  const previous = selected !== undefined ? selected : selectEl.value;
  const blankText = keepBlank && selectEl.options[0] && selectEl.options[0].value === ''
    ? selectEl.options[0].textContent
    : null;
  while (selectEl.options.length) selectEl.remove(0);
  if (blankText !== null) {
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = blankText;
    selectEl.appendChild(blank);
  }
  (endpoints || []).forEach(function(ep) {
    if (!ep.is_enabled) return;
    const opt = document.createElement('option');
    opt.value = ep.id;
    opt.textContent = _endpointLabel(ep);
    selectEl.appendChild(opt);
  });
  if (previous && Array.from(selectEl.options).some(function(o) { return o.value === previous; })) {
    selectEl.value = previous;
  } else if (blankText !== null) {
    selectEl.value = '';
  }
  _syncEndpointLogo(selectEl);
}

// Mirror the selected model's provider logo into a sibling <span id="<selectId>-logo">.
// Wires the change listener exactly once so we can call this every time the
// select is repopulated without piling on duplicate handlers.
function _syncModelLogo(selectEl) {
  if (!selectEl) return;
  const logoEl = document.getElementById(selectEl.id + '-logo');
  if (!logoEl) return;
  const apply = () => { logoEl.innerHTML = providerLogo(selectEl.value) || ''; };
  apply();
  if (!selectEl.dataset.logoSync) {
    selectEl.dataset.logoSync = '1';
    selectEl.addEventListener('change', apply);
  }
}

// Same idea but for endpoint dropdowns where the <option value="…">
// is an opaque endpoint UUID — fall back to the option's text label
// so providerLogo() can pattern-match (Anthropic, OpenAI, Ollama, …).
function _syncEndpointLogo(selectEl) {
  if (!selectEl) return;
  const logoEl = document.getElementById(selectEl.id + '-logo');
  if (!logoEl) return;
  const apply = () => {
    const opt = selectEl.options[selectEl.selectedIndex];
    const label = (opt && opt.textContent) || selectEl.value || '';
    logoEl.innerHTML = providerLogo(label) || '';
  };
  apply();
  if (!selectEl.dataset.epLogoSync) {
    selectEl.dataset.epLogoSync = '1';
    selectEl.addEventListener('change', apply);
  }
}

function _fillModelSelect(selectEl, models, selected, keepBlank) {
  if (!selectEl) return;
  const previous = selected !== undefined ? selected : selectEl.value;
  const blankText = keepBlank && selectEl.options[0] && selectEl.options[0].value === ''
    ? selectEl.options[0].textContent
    : null;
  while (selectEl.options.length) selectEl.remove(0);
  if (blankText !== null) {
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = blankText;
    selectEl.appendChild(blank);
  }
  sortModelIds(models).forEach(function(m) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = String(m).split('/').pop();
    selectEl.appendChild(opt);
  });
  if (previous && Array.from(selectEl.options).some(function(o) { return o.value === previous; })) {
    selectEl.value = previous;
  } else if (blankText !== null) {
    selectEl.value = '';
  }
  _syncModelLogo(selectEl);
}

function _registerAiEndpointRefresh(fn) {
  _aiEndpointRefreshers.add(fn);
}

function _registerForegroundFallbackEndpointRefresh(fn) {
  _foregroundFallbackEndpointRefreshers.add(fn);
}

export async function refreshAiModelEndpoints() {
  if (_aiEndpointRefreshInFlight) return _aiEndpointRefreshInFlight;
  _aiEndpointRefreshInFlight = (async function() {
    try {
      const endpoints = await _fetchModelDefaultEndpoints();
      _aiEndpointRefreshers.forEach(function(fn) {
        try { fn(endpoints); } catch (e) { console.warn('[settings] endpoint refresh handler failed', e); }
      });
    } catch (e) {
      console.warn('[settings] failed to refresh model endpoints', e);
    }
    try {
      const endpoints = await _fetchForegroundFallbackEndpoints();
      _foregroundFallbackEndpointRefreshers.forEach(function(fn) {
        try { fn(endpoints, null); } catch (e) { console.warn('[settings] foreground fallback endpoint refresh handler failed', e); }
      });
    } catch (e) {
      console.warn('[settings] failed to refresh foreground fallback endpoints', e);
      _foregroundFallbackEndpointRefreshers.forEach(function(fn) {
        try { fn(null, e); } catch (handlerError) { console.warn('[settings] foreground fallback endpoint refresh handler failed', handlerError); }
      });
    } finally {
      _aiEndpointRefreshInFlight = null;
    }
  })();
  return _aiEndpointRefreshInFlight;
}

/* Shared fallback-chain widget — mirrors the Default Chat Model fallback UI
 * for other model cards (Utility, Vision, …). Pass in the container/button
 * IDs, the endpoints list, the settings key to persist under, and the
 * model-filter (for Vision we exclude non-chat-capable models).
 */
function _bindFallbackWidget(opts) {
  var fbContainer = el(opts.containerId);
  var addBtn = el(opts.addBtnId);
  var endpointsRef = opts.endpoints;       // mutable list reference
  var modelsFilter = opts.modelsFilter || function() { return true; };
  var settingKey = opts.settingKey;
  var maxItems = Number.isInteger(opts.maxItems) ? opts.maxItems : null;
  var current = opts.cleanCandidates
    ? cleanForegroundFallbackCandidates(
        opts.initial || [],
        maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
      )
    : (opts.initial || []).slice();
  var allowReorder = opts.allowReorder === true;
  var controlsDisabled = opts.disabled === true;
  var supportsEffort = Boolean(opts.supportsEffort);
  var getEffortLevels = opts.getEffortLevels;
  var saveQueue = Promise.resolve();

  if (!fbContainer || !addBtn) return {
    setEndpoints: function() {},
    setInitial: function() {},
    setDisabled: function() {},
    refresh: function() {},
    getCurrent: function() { return []; },
  };

  function enabledEps() { return (endpointsRef() || []).filter(function(e) { return e.is_enabled; }); }
  function selectableEps() {
    if (!opts.requireModel) return enabledEps();
    return enabledEps().filter(function(ep) {
      return Array.isArray(ep.models) && ep.models.some(function(model) {
        return modelsFilter(model, ep);
      });
    });
  }
  function firstSelectableEndpoint() {
    return selectableEps()[0];
  }

  function firstUnusedCandidate() {
    return nextForegroundFallbackCandidate(
      selectableEps().map(function(ep) {
        return {
          id: ep.id,
          is_enabled: ep.is_enabled,
          models: sortModelIds(ep.models).filter(function(model) {
            return modelsFilter(model, ep);
          }),
        };
      }),
      current
    );
  }

  function currentForSave() {
    return opts.cleanCandidates
      ? cleanForegroundFallbackCandidates(
          current,
          maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
        )
      : current.filter(function(item) {
          return item && item.endpoint_id && item.model;
        }).map(function(f) {
          var item = { endpoint_id: f.endpoint_id, model: f.model };
          if (supportsEffort && typeof f.reasoning_effort !== 'undefined') {
            item.reasoning_effort = f.reasoning_effort || '';
          }
          return item;
        });
  }

  function fillModels(selectEl, epId, selected) {
    while (selectEl.options.length) selectEl.remove(0);
    var ep = (endpointsRef() || []).find(function(e) { return e.id === epId; });
    if (ep && ep.models) {
      sortModelIds(ep.models).forEach(function(m) {
        if (!modelsFilter(m, ep)) return;
        var o = document.createElement('option');
        o.value = m;
        o.textContent = m.split('/').pop();
        selectEl.appendChild(o);
      });
    }
    if (
      opts.preserveUnavailable
      && selected
      && !Array.from(selectEl.options).some(function(o) { return o.value === selected; })
    ) {
      var unavailable = document.createElement('option');
      unavailable.value = selected;
      unavailable.textContent = selected.split('/').pop() + ' (unavailable)';
      unavailable.disabled = true;
      selectEl.appendChild(unavailable);
    }
    if (selected) selectEl.value = selected;
  }

  function fillEffort(selectEl, epId, modelId, selected) {
    if (!selectEl) return;
    while (selectEl.options.length) selectEl.remove(0);
    var levels = getEffortLevels ? getEffortLevels(epId, modelId) : [];
    var base = document.createElement('option');
    base.value = '';
    base.textContent = 'Provider default';
    selectEl.appendChild(base);
    levels.forEach(function(lvl) {
      var val = typeof lvl === 'string' ? lvl : (lvl && lvl.effort);
      if (val) {
        var o = document.createElement('option');
        o.value = val.toLowerCase();
        o.textContent = val;
        selectEl.appendChild(o);
      }
    });
    var validValues = [''].concat(levels.map(function(l) { return (typeof l === 'string' ? l : l.effort).toLowerCase(); }));
    selectEl.value = validValues.indexOf((selected || '').toLowerCase()) >= 0 ? (selected || '').toLowerCase() : '';
    selectEl.style.display = levels.length > 0 ? '' : 'none';
  }

  async function persist() {
    var clean = currentForSave();
    if (typeof opts.saveList === 'function') {
      await opts.saveList(clean);
    } else {
      var body = {};
      body[settingKey] = clean;
      var response = await _postSettings(body);
      if (response && !response.ok) throw new Error('HTTP ' + response.status);
    }
    if (typeof opts.onSaved === 'function') opts.onSaved(clean);
  }

  function save() {
    saveQueue = saveQueue
      .catch(function() {})
      .then(persist)
      .catch(function(error) {
        if (typeof opts.onError === 'function') opts.onError(error);
        else console.warn('[fallback] save failed for ' + settingKey, error);
      });
    return saveQueue;
  }

  function render() {
    var focusState = captureFallbackWidgetFocus(fbContainer, document.activeElement);
    if (opts.cleanCandidates) {
      current = cleanForegroundFallbackCandidates(
        current,
        maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
      );
    }
    fbContainer.innerHTML = '';
    current.forEach(function(fb, idx) {
      var row = document.createElement('div');
      row.className = 'settings-fallback-row';

      var num = document.createElement('span');
      num.className = 'settings-fallback-num';
      num.textContent = (idx + 1) + '.';

      var epS = document.createElement('select');
      epS.className = 'settings-select';
      epS.dataset.fallbackFocus = 'endpoint';
      epS.disabled = controlsDisabled;
      epS.setAttribute('aria-label', 'Fallback ' + (idx + 1) + ' endpoint');
      selectableEps().forEach(function(ep) {
        var o = document.createElement('option');
        o.value = ep.id;
        o.textContent = ep.name + (ep.online ? '' : ' (offline)');
        epS.appendChild(o);
      });
      if (
        opts.preserveUnavailable
        && fb.endpoint_id
        && !Array.from(epS.options).some(function(o) { return o.value === fb.endpoint_id; })
      ) {
        var unavailable = document.createElement('option');
        unavailable.value = fb.endpoint_id;
        unavailable.textContent = 'Unavailable endpoint';
        unavailable.disabled = true;
        epS.appendChild(unavailable);
      }
      var first = firstSelectableEndpoint();
      epS.value = fb.endpoint_id || (first ? first.id : '');

      var mS = document.createElement('select');
      mS.className = 'settings-select';
      mS.dataset.fallbackFocus = 'model';
      mS.disabled = controlsDisabled;
      mS.setAttribute('aria-label', 'Fallback ' + (idx + 1) + ' model');
      fillModels(mS, epS.value, fb.model);

      fb.endpoint_id = epS.value;
      fb.model = mS.value;

      var effS = null;
      if (supportsEffort) {
        effS = document.createElement('select');
        effS.className = 'settings-select settings-fallback-effort';
        effS.disabled = controlsDisabled;
        fillEffort(effS, epS.value, mS.value, fb.reasoning_effort);
        fb.reasoning_effort = effS.value;
        effS.addEventListener('change', function() {
          fb.reasoning_effort = effS.value;
          save();
        });
      }

      epS.addEventListener('change', function() {
        fb.endpoint_id = epS.value;
        fillModels(mS, epS.value, '');
        fb.model = mS.value;
        if (effS) {
          fillEffort(effS, epS.value, mS.value, '');
          fb.reasoning_effort = effS.value;
        }
        if (opts.cleanCandidates) render();
        save();
      });
      mS.addEventListener('change', function() {
        fb.model = mS.value;
        if (effS) {
          fillEffort(effS, epS.value, mS.value, fb.reasoning_effort);
          fb.reasoning_effort = effS.value;
        }
        if (opts.cleanCandidates) render();
        save();
      });

      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'settings-fallback-remove';
      rm.dataset.fallbackFocus = 'remove';
      rm.disabled = controlsDisabled;
      rm.title = 'Remove fallback';
      rm.setAttribute('aria-label', 'Remove fallback ' + (idx + 1));
      rm.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';
      rm.addEventListener('click', function() {
        current.splice(idx, 1);
        render();
        save();
      });

      row.appendChild(num);
      row.appendChild(epS);
      row.appendChild(mS);
      if (effS) row.appendChild(effS);
      if (allowReorder) {
        var actions = document.createElement('span');
        actions.className = 'settings-fallback-actions';
        [
          {
            offset: -1,
            title: 'Move fallback up',
            path: '<path d="M6 15l6-6 6 6"/>',
          },
          {
            offset: 1,
            title: 'Move fallback down',
            path: '<path d="M6 9l6 6 6-6"/>',
          },
        ].forEach(function(action) {
          var move = document.createElement('button');
          move.type = 'button';
          move.className = 'settings-fallback-remove settings-fallback-move';
          var destination = idx + action.offset;
          var inBounds = destination >= 0 && destination < current.length;
          var accessibleTitle = inBounds
            ? action.title + ' ' + (idx + 1) + ' to position ' + (destination + 1)
            : action.title + ' ' + (idx + 1)
              + (action.offset < 0 ? ' (already first)' : ' (already last)');
          move.title = accessibleTitle;
          move.setAttribute('aria-label', accessibleTitle);
          move.dataset.moveOffset = String(action.offset);
          move.dataset.fallbackFocus = action.offset < 0 ? 'move-up' : 'move-down';
          move.disabled = controlsDisabled || !inBounds;
          move.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + action.path + '</svg>';
          move.addEventListener('click', function() {
            var from = idx;
            var to = idx + action.offset;
            current = moveForegroundFallbackCandidate(current, idx, action.offset);
            render();
            var movedRow = fbContainer.children[to];
            if (movedRow) {
              var focusTarget = movedRow.querySelector(
                '.settings-fallback-move[data-move-offset="' + action.offset + '"]:not(:disabled)'
              ) || movedRow.querySelector('.settings-fallback-move:not(:disabled)')
                || movedRow.querySelector('select');
              if (focusTarget) focusTarget.focus();
            }
            if (typeof opts.onReordered === 'function') {
              opts.onReordered({ from: from, to: to, total: current.length });
            }
            save();
          });
          actions.appendChild(move);
        });
        actions.appendChild(rm);
        row.appendChild(actions);
      } else {
        row.appendChild(rm);
      }
      fbContainer.appendChild(row);
    });
    var unusedCandidate = firstUnusedCandidate();
    if (opts.manageAddDisabled) {
      addBtn.disabled = controlsDisabled || !unusedCandidate
        || (maxItems !== null && current.length >= maxItems);
    }
    if (typeof opts.onRender === 'function') {
      opts.onRender(currentForSave());
    }
    restoreFallbackWidgetFocus(fbContainer, addBtn, focusState);
  }

  addBtn.addEventListener('click', function() {
    var unusedCandidate = firstUnusedCandidate();
    if (
      (opts.manageAddDisabled && !unusedCandidate)
      || (maxItems !== null && current.length >= maxItems)
    ) return;
    var first = firstSelectableEndpoint() || enabledEps()[0];
    var nextItem = unusedCandidate
      ? Object.assign({}, unusedCandidate)
      : { endpoint_id: first ? first.id : '', model: '' };
    if (supportsEffort) {
      nextItem.reasoning_effort = '';
    }
    current.push(nextItem);
    render();
    save();
  });

  render();

  return {
    setInitial: function(list) {
      current = opts.cleanCandidates
        ? cleanForegroundFallbackCandidates(
            list,
            maxItems === null ? MAX_FOREGROUND_FALLBACKS : maxItems
          )
        : (list || []).slice();
      render();
    },
    refresh: render,
    getCurrent: currentForSave,
    setDisabled: function(disabled) {
      controlsDisabled = disabled === true;
      render();
    },
  };
}

/* ── Default Chat Model ── */
async function initDefaultChat() {
  var epSel = el('set-defaultEpSelect');
  var modelSel = el('set-defaultModelSelect');
  var msg = el('set-defaultChatMsg');
  var fallbackToggle = el('set-foregroundFallbackToggle');
  var fallbackEditor = el('set-foregroundFallbackEditor');
  var fallbackState = el('set-foregroundFallbackState');
  var fallbackMsg = el('set-foregroundFallbackMsg');
  var fallbackRetry = el('set-foregroundFallbackRetry');
  var _endpoints = [];
  var _foregroundFallbackEndpoints = [];
  var fallbackWidget = null;
  var fallbackPrefsLoaded = false;
  var fallbackCatalogLoaded = false;

  if (fallbackToggle) fallbackToggle.disabled = true;
  if (fallbackState) fallbackState.textContent = 'Loading fallback settings…';

  function showFallbackMessage(message, failed) {
    if (!fallbackMsg) return;
    fallbackMsg.textContent = message;
    fallbackMsg.style.color = failed ? 'var(--red)' : 'var(--fg)';
    if (!failed && message) {
      setTimeout(function() {
        if (fallbackMsg.textContent === message) fallbackMsg.textContent = '';
      }, 1800);
    }
  }

  var fallbackPreferenceWriter = createForegroundPreferenceSaveQueue(
    async function(key, value) {
      var response = await fetch('/api/prefs/' + key, {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value: value }),
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
    },
    function() {
      fallbackPrefsLoaded = false;
      if (fallbackToggle) fallbackToggle.disabled = true;
      syncFallbackEditorReadiness();
      syncFallbackRetry();
      applyFallbackVisibility();
    }
  );

  function saveForegroundPref(key, value) {
    return fallbackPreferenceWriter.save(key, value);
  }

  function syncFallbackEditorReadiness() {
    if (fallbackWidget && fallbackWidget.setDisabled) {
      fallbackWidget.setDisabled(
        !fallbackPrefsLoaded || !fallbackCatalogLoaded
      );
    }
  }

  function applyFallbackVisibility() {
    if (!fallbackToggle || !fallbackEditor) return;
    if (!fallbackPrefsLoaded) {
      fallbackEditor.hidden = true;
      fallbackEditor.setAttribute('aria-hidden', 'true');
      if (fallbackState) {
        fallbackState.textContent = 'Fallback preferences are unavailable. Retry before changing this policy.';
      }
      return;
    }
    var enabled = fallbackToggle.checked === true;
    fallbackEditor.hidden = !enabled;
    fallbackEditor.setAttribute('aria-hidden', enabled ? 'false' : 'true');
    if (!fallbackState) return;
    var candidates = fallbackWidget ? fallbackWidget.getCurrent() : [];
    var count = candidates.length;
    var eligibility = fallbackCatalogLoaded
      ? summarizeForegroundFallbackCandidateEligibility(
          candidates,
          _foregroundFallbackEndpoints
        )
      : null;
    var stateText = enabled
      ? (
          !count
            ? 'No fallback candidates configured; the selected model remains strict.'
            : eligibility && eligibility.eligible === 0 && eligibility.unknown === 0
              ? count + (count === 1 ? ' fallback candidate is configured' : ' fallback candidates are configured')
                + ', but none is currently eligible; the selected model remains strict.'
              : eligibility && eligibility.eligible === 0
                ? count + (count === 1 ? ' fallback candidate is configured' : ' fallback candidates are configured')
                  + ', but eligibility cannot be verified from the current model catalog; saved routes may still be attempted.'
              : eligibility && (eligibility.eligible < count)
                ? count + (count === 1 ? ' fallback candidate is configured; ' : ' fallback candidates are configured; ')
                  + eligibility.eligible + (eligibility.eligible === 1 ? ' is currently eligible' : ' are currently eligible')
                  + (eligibility.unknown
                    ? ', and ' + eligibility.unknown + ' cannot be verified.'
                    : '.')
                : count + (count === 1 ? ' fallback candidate configured.' : ' fallback candidates configured.')
      )
      : 'The selected foreground model remains strict.';
    if (!fallbackCatalogLoaded) {
      stateText += ' Model choices are unavailable; retry to refresh the eligible endpoint list.';
    }
    fallbackState.textContent = stateText;
  }

  function syncFallbackRetry() {
    if (!fallbackRetry) return;
    var shouldHide = fallbackPrefsLoaded && fallbackCatalogLoaded;
    if (
      shouldHide
      && document.activeElement === fallbackRetry
      && fallbackToggle
      && typeof fallbackToggle.focus === 'function'
    ) {
      fallbackToggle.focus();
    }
    fallbackRetry.hidden = shouldHide;
  }

  async function loadForegroundPolicy() {
    try {
      var response = await fetch('/api/prefs', { credentials: 'same-origin' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return normalizeForegroundFallbackPrefs(await response.json());
    } catch (error) {
      console.warn('Failed to load foreground fallback preferences', error);
      showFallbackMessage('Failed to load fallback preferences', true);
      return null;
    }
  }

  async function loadForegroundEndpointCatalog() {
    try {
      _foregroundFallbackEndpoints = await _fetchForegroundFallbackEndpoints();
      fallbackCatalogLoaded = true;
      syncFallbackEditorReadiness();
      return true;
    } catch (error) {
      console.warn('Failed to load foreground fallback model choices', error);
      fallbackCatalogLoaded = false;
      syncFallbackEditorReadiness();
      showFallbackMessage('Failed to load fallback model choices', true);
      return false;
    }
  }

  // Fill any <select> with the models for a given endpoint id.
  function fillModels(selectEl, epId, selected) {
    var ep = _endpoints.find(function(e) { return e.id === epId; });
    _fillModelSelect(selectEl, _selectableEndpointModels(ep), selected, false);
  }

  var personalDefault = false;
  var defaultSource = _createModelDefaultSource('default chat', function(endpoints, store) {
    personalDefault = store.personal;
    // A personal default can be left unset; the server then applies its own
    // default for this account.
    if (personalDefault) _ensureBlankOption(epSel, 'Not set (server default)');
    _endpoints = endpoints;
    _fillEndpointSelect(epSel, _endpoints, epSel.value, personalDefault);
  });

  await loadForegroundEndpointCatalog();

  function refreshModels(selectedModel) {
    fillModels(modelSel, epSel.value, selectedModel);
    // No endpoint means no personal default yet; there is no model to pick.
    // Until the stored values load, the card stays read-only.
    modelSel.disabled = !epSel.value || !(defaultLoader && defaultLoader.isLoaded());
  }
  function refreshEndpointOptions(selectedEndpoint, selectedModel) {
    _fillEndpointSelect(epSel, _endpoints, selectedEndpoint !== undefined ? selectedEndpoint : epSel.value, personalDefault);
    refreshModels(selectedModel !== undefined ? selectedModel : modelSel.value);
  }

  function applyStoredDefault(stored) {
    var endpointId = stored.default_endpoint_id || '';
    if (endpointId
        && _endpoints.some(function(ep) { return ep.id === endpointId && ep.is_enabled; })) {
      epSel.value = endpointId;
    } else if (personalDefault) {
      epSel.value = '';
    }
    _syncEndpointLogo(epSel);
    refreshModels(stored.default_model || '');
  }

  var defaultLoader = _createModelDefaultLoader({
    label: 'default chat model',
    controls: function() { return [epSel, modelSel]; },
    msgEl: msg,
    load: defaultSource.load,
    apply: applyStoredDefault,
  });
  await defaultLoader.run();

  var foregroundPolicy = await loadForegroundPolicy();
  fallbackPrefsLoaded = foregroundPolicy !== null;

  if (fallbackToggle) {
    fallbackToggle.checked = foregroundPolicy ? foregroundPolicy.enabled : false;
    fallbackToggle.disabled = !fallbackPrefsLoaded;
  }
  fallbackWidget = _bindFallbackWidget({
    containerId: 'set-foregroundFallbacks',
    addBtnId: 'set-foregroundAddFallback',
    endpoints: function() { return _foregroundFallbackEndpoints; },
    initial: foregroundPolicy ? foregroundPolicy.candidates : [],
    cleanCandidates: true,
    requireModel: true,
    manageAddDisabled: true,
    allowReorder: true,
    maxItems: MAX_FOREGROUND_FALLBACKS,
    preserveUnavailable: true,
    saveList: function(list) {
      return saveForegroundPref('foreground_model_fallbacks', list);
    },
    onSaved: function() {
      showFallbackMessage('Fallback order saved', false);
    },
    onError: function(error) {
      console.warn('Failed to save foreground fallback list', error);
      showFallbackMessage('Failed to save fallback order', true);
    },
    onReordered: function(event) {
      showFallbackMessage(
        'Fallback moved to position ' + (event.to + 1) + ' of ' + event.total,
        false
      );
    },
    onRender: applyFallbackVisibility,
  });
  syncFallbackEditorReadiness();
  applyFallbackVisibility();
  syncFallbackRetry();

  if (fallbackRetry) {
    fallbackRetry.addEventListener('click', async function() {
      fallbackRetry.disabled = true;
      showFallbackMessage('Loading fallback settings…', false);
      if (!fallbackCatalogLoaded) await loadForegroundEndpointCatalog();
      if (!fallbackPrefsLoaded) {
        var loadedPolicy = await loadForegroundPolicy();
        if (loadedPolicy !== null) {
          foregroundPolicy = loadedPolicy;
          fallbackPrefsLoaded = true;
          fallbackPreferenceWriter.reset();
          fallbackWidget.setInitial(loadedPolicy.candidates);
          if (fallbackToggle) {
            fallbackToggle.checked = loadedPolicy.enabled;
            fallbackToggle.disabled = false;
          }
        }
      }
      syncFallbackEditorReadiness();
      fallbackRetry.disabled = false;
      syncFallbackRetry();
      applyFallbackVisibility();
      if (fallbackPrefsLoaded && fallbackCatalogLoaded) {
        showFallbackMessage('Fallback settings loaded', false);
      }
    });
  }

  if (fallbackToggle) {
    fallbackToggle.addEventListener('change', async function() {
      if (!fallbackPrefsLoaded) return;
      var requested = fallbackToggle.checked === true;
      fallbackToggle.disabled = true;
      applyFallbackVisibility();
      try {
        await saveForegroundPref('foreground_fallback_enabled', requested);
        showFallbackMessage(requested ? 'Fallback enabled' : 'Strict mode saved', false);
      } catch (error) {
        console.warn('Failed to save foreground fallback preference', error);
        fallbackToggle.checked = !requested;
        applyFallbackVisibility();
        showFallbackMessage('Failed to save fallback preference', true);
      } finally {
        fallbackToggle.disabled = !fallbackPrefsLoaded;
      }
    });
  }

  epSel.addEventListener('change', function() { refreshModels(''); saveDefault(); });
  modelSel.addEventListener('change', saveDefault);

  async function saveDefault() {
    if (!defaultLoader.isLoaded()) return;
    try {
      await defaultSource.store().save({
        default_endpoint_id: epSel.value,
        default_model: modelSel.value
      });
      _showCardMessage(msg, 'Saved', false);
    } catch (e) {
      // Show what is actually stored instead of the choice that was not saved.
      var restored = (e && e.serverSaved) ? true : await defaultLoader.run();
      if (restored) _showCardMessage(msg, modelDefaultSaveError(e), true);
    }
  }

  _registerAiEndpointRefresh(function(endpoints) {
    _endpoints = endpoints;
    refreshEndpointOptions(epSel.value, modelSel.value);
  });
  _registerForegroundFallbackEndpointRefresh(function(endpoints, error) {
    if (error) {
      var fallbackList = el('set-foregroundFallbacks');
      var focusWasInList = Boolean(
        fallbackList
        && document.activeElement
        && fallbackList.contains(document.activeElement)
      );
      fallbackCatalogLoaded = false;
      syncFallbackEditorReadiness();
      syncFallbackRetry();
      if (focusWasInList && fallbackRetry && typeof fallbackRetry.focus === 'function') {
        fallbackRetry.focus();
      }
      applyFallbackVisibility();
      showFallbackMessage('Failed to refresh fallback model choices', true);
      return;
    }
    _foregroundFallbackEndpoints = endpoints;
    fallbackCatalogLoaded = true;
    syncFallbackEditorReadiness();
    syncFallbackRetry();
  });
}

/* ── Utility Model ── */
async function initUtilityModel() {
  var epSel = el('set-utilityEpSelect');
  var modelSel = el('set-utilityModelSelect');
  var effortSel = el('set-utilityEffortSelect');
  var effortRow = el('set-utilityEffortRow');
  var effortHelp = el('set-utilityEffortHelp');
  var effortQualifier = el('set-utilityEffortQualifier');
  var msg = el('set-utilityChatMsg');
  var _endpoints = [];
  var fallbackWidget = null;
  var _levelsByEndpoint = {};
  if (epSel && epSel.options[0]) epSel.options[0].textContent = 'Same as chat';
  if (modelSel && modelSel.options[0]) modelSel.options[0].textContent = 'Same as chat';

  var utilitySource = _createModelDefaultSource('utility model', function(endpoints) {
    _endpoints = endpoints;
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
  });

  // Effort levels come from /api/models `models_metadata` (same evidence the
  // chat composer uses); /api/model-endpoints does not carry them.
  async function loadEffortLevels() {
    try {
      var r = await fetch('/api/models', { credentials: 'same-origin' });
      if (!r.ok) return;
      var data = await r.json();
      var items = Array.isArray(data) ? data : (data.models || data.items || []);
      _levelsByEndpoint = {};
      items.forEach(function(item) {
        if (item && item.endpoint_id) _levelsByEndpoint[item.endpoint_id] = item.models_metadata || {};
      });
    } catch (e) { console.warn('Failed to load reasoning levels for utility model', e); }
  }

  function refreshEffort(selected) {
    if (!effortSel) return;
    var targetEp = epSel.value;
    var targetModel = modelSel.value;
    if (!targetEp) {
      var defEp = el('set-defaultEpSelect');
      var defModel = el('set-defaultModelSelect');
      if (defEp) targetEp = defEp.value;
      if (defModel) targetModel = defModel.value;
    }
    var meta = (_levelsByEndpoint[targetEp] || {})[targetModel] || {};
    var levels = Array.isArray(meta.supported_reasoning_levels) ? meta.supported_reasoning_levels.slice() : [];
    var isInherited = !epSel.value;
    if (isInherited && levels.length === 0) {
      var allLevels = {};
      Object.keys(_levelsByEndpoint).forEach(function(epId) {
        var models = _levelsByEndpoint[epId] || {};
        Object.keys(models).forEach(function(mId) {
          var mLevels = (models[mId] || {}).supported_reasoning_levels;
          if (Array.isArray(mLevels)) {
            mLevels.forEach(function(lvl) {
              var val = typeof lvl === 'string' ? lvl : (lvl && lvl.effort);
              if (val) allLevels[val.toLowerCase()] = true;
            });
          }
        });
      });
      levels = Object.keys(allLevels);
      if (levels.length === 0) {
        levels = ['low', 'medium', 'high'];
      }
    }
    while (effortSel.options.length) effortSel.remove(0);
    var base = document.createElement('option');
    base.value = '';
    base.textContent = isInherited ? 'Same as chat' : 'Provider default';
    if (effortQualifier) effortQualifier.style.display = isInherited ? '' : 'none';
    effortSel.appendChild(base);
    if (isInherited) {
      var offOpt = document.createElement('option');
      offOpt.value = 'off';
      offOpt.textContent = 'Provider default';
      effortSel.appendChild(offOpt);
    }
    levels.forEach(function(level) {
      var opt = document.createElement('option');
      opt.value = level;
      opt.textContent = level;
      effortSel.appendChild(opt);
    });
    var validValues = [''].concat(isInherited ? ['off'] : []).concat(levels);
    effortSel.value = validValues.indexOf(selected) >= 0 ? selected : '';
    var show = isInherited || levels.length > 0;
    if (effortRow) effortRow.style.display = show ? '' : 'none';
    if (effortHelp) effortHelp.style.display = show ? '' : 'none';
  }

  function refreshModels(selectedModel) {
    var epId = epSel.value;
    var ep = _endpoints.find(function(e) { return e.id === epId; });
    _fillModelSelect(modelSel, ep ? ep.models : [], selectedModel, true);
  }

  await loadEffortLevels();

  function applyStoredUtility(settings) {
    epSel.value = settings.utility_endpoint_id || '';
    if (epSel.value !== (settings.utility_endpoint_id || '')) epSel.value = '';
    _syncEndpointLogo(epSel);
    refreshModels(settings.utility_model || '');
    refreshEffort(settings.utility_reasoning_effort || '');
  }

  function bindUtilityFallbacks(settings) {
    var initial = Array.isArray(settings.utility_model_fallbacks)
      ? settings.utility_model_fallbacks.map(function(f) {
          return {
            endpoint_id: (f && f.endpoint_id) || '',
            model: (f && f.model) || '',
            reasoning_effort: (f && f.reasoning_effort) || '',
          };
        })
      : [];
    if (fallbackWidget) {
      fallbackWidget.setInitial(initial);
      return;
    }
    fallbackWidget = _bindFallbackWidget({
      containerId: 'set-utilityFallbacks',
      addBtnId: 'set-utilityAddFallback',
      endpoints: function() { return _endpoints; },
      settingKey: 'utility_model_fallbacks',
      saveList: function(list) {
        return utilitySource.store().save({ utility_model_fallbacks: list });
      },
      onError: async function(error) {
        console.warn('Failed to save utility fallbacks', error);
        // Show the stored list again rather than the change that was not
        // saved; if that reload fails too, the card turns read-only.
        var restored = (error && error.serverSaved) ? true : await utilityLoader.run();
        if (restored) _showCardMessage(msg, modelDefaultSaveError(error), true);
      },
      supportsEffort: true,
      getEffortLevels: function(epId, modelId) {
        var meta = (_levelsByEndpoint[epId] || {})[modelId] || {};
        return Array.isArray(meta.supported_reasoning_levels) ? meta.supported_reasoning_levels : [];
      },
      initial: initial,
    });
  }

  var utilityLoader = _createModelDefaultLoader({
    label: 'utility model',
    controls: function() { return [epSel, modelSel, effortSel, el('set-utilityAddFallback')]; },
    onDisabledChange: function(disabled) {
      if (fallbackWidget) fallbackWidget.setDisabled(disabled);
    },
    msgEl: msg,
    load: utilitySource.load,
    apply: function(settings) {
      applyStoredUtility(settings);
      bindUtilityFallbacks(settings);
    },
  });
  await utilityLoader.run();

  // Persist whatever's currently selected. Empty endpoint or model → backend
  // transparently falls back to the chat model (mirrors the teacher panel:
  // no toggle, "—" means "unset, use chat").
  async function saveUtility() {
    if (!utilityLoader.isLoaded()) return;
    try {
      await utilitySource.store().save({
        utility_endpoint_id: epSel.value || '',
        utility_model: modelSel.value || '',
        utility_reasoning_effort: effortSel ? (effortSel.value || '') : ''
      });
      _showCardMessage(msg, 'Saved', false);
    } catch (e) {
      var restored = (e && e.serverSaved) ? true : await utilityLoader.run();
      if (restored) _showCardMessage(msg, modelDefaultSaveError(e), true);
    }
  }

  // A new endpoint/model may not advertise the saved effort; refreshEffort
  // resets it to the provider default, and saveUtility persists that.
  epSel.addEventListener('change', function() { refreshModels(''); refreshEffort(''); saveUtility(); });
  modelSel.addEventListener('change', function() { refreshEffort(effortSel ? effortSel.value : ''); saveUtility(); });
  if (effortSel) effortSel.addEventListener('change', saveUtility);
  var defEp = el('set-defaultEpSelect');
  var defModel = el('set-defaultModelSelect');
  if (defEp) defEp.addEventListener('change', function() { if (!epSel.value) refreshEffort(effortSel ? effortSel.value : ''); });
  if (defModel) defModel.addEventListener('change', function() { if (!epSel.value) refreshEffort(effortSel ? effortSel.value : ''); });

  _registerAiEndpointRefresh(async function(endpoints) {
    _endpoints = endpoints;
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
    refreshModels(modelSel.value);
    await loadEffortLevels();
    refreshEffort(effortSel ? effortSel.value : '');
    if (fallbackWidget && fallbackWidget.refresh) fallbackWidget.refresh();
  });
}

/* ── Teacher Model ── */
// SOTA model called automatically when a self-hosted student model
// fails an agent-mode task. Stored as a single `teacher_model` string
// in the form `model@endpoint_name` so the backend's _resolve_model
// can dispatch directly. Master toggle is the separate
// `teacher_enabled` flag so the user can pause the feature without
// losing their endpoint+model selection.
async function initTeacherModel() {
  var enabledToggle = el('set-teacherEnabledToggle');
  var epSel = el('set-teacherEpSelect');
  var modelSel = el('set-teacherModelSelect');
  var msg = el('set-teacherChatMsg');
  if (!epSel || !modelSel) return;
  var _endpoints = [];

  try {
    _endpoints = await _fetchModelEndpoints();
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
  } catch (e) { console.warn('Failed to load endpoints for teacher model', e); }

  function refreshModels(selectedModel) {
    var epId = epSel.value;
    var ep = _endpoints.find(function(e) { return e.id === epId; });
    _fillModelSelect(modelSel, ep ? ep.models : [], selectedModel, true);
  }

  // Disable / enable the endpoint+model dropdowns based on the
  // master switch. Greys them out so users see at a glance that the
  // selection is dormant.
  function syncEnabled() {
    var off = enabledToggle ? !enabledToggle.checked : true;
    // Dim the card when off as a "dormant" cue, but keep the endpoint+model
    // dropdowns INTERACTIVE — the toggle gates whether escalation runs, not
    // whether you can configure it. (Previously the config was inert when off,
    // so users couldn't pick an endpoint until they'd already enabled it.)
    var card = enabledToggle ? enabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.7' : '';
    var wrap = card ? card.querySelector('.settings-col') : null;
    if (wrap) wrap.style.pointerEvents = '';
    epSel.disabled = false;
    modelSel.disabled = false;
  }

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await res.json();
    if (enabledToggle) enabledToggle.checked = !!settings.teacher_enabled;
    // teacher_model is stored as "model@endpoint_name". Split on the
    // LAST `@` so model ids that contain @ aren't mangled.
    var spec = settings.teacher_model || '';
    var savedModel = spec;
    var savedEpName = '';
    var at = spec.lastIndexOf('@');
    if (at >= 0) {
      savedModel = spec.slice(0, at);
      savedEpName = spec.slice(at + 1);
    }
    if (savedEpName) {
      var match = _endpoints.find(function(ep) {
        return ep.name && ep.name.toLowerCase().indexOf(savedEpName.toLowerCase()) >= 0;
      });
      if (match) epSel.value = match.id;
    }
    refreshModels(savedModel);
    syncEnabled();
  } catch (e) { console.warn('Failed to load teacher model settings', e); }

  async function saveTeacher() {
    try {
      var spec = '';
      if (epSel.value && modelSel.value) {
        var ep = _endpoints.find(function(e) { return e.id === epSel.value; });
        spec = ep ? (modelSel.value + '@' + ep.name) : modelSel.value;
      }
      var enabled = enabledToggle ? !!enabledToggle.checked : false;
      await _postSettings({ teacher_enabled: enabled, teacher_model: spec });
      msg.textContent = enabled ? (spec ? 'Saved' : 'Pick an endpoint + model') : 'Disabled';
      msg.style.color = enabled && !spec ? 'var(--red)' : 'var(--fg)';
      setTimeout(function() { msg.textContent = ''; }, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  if (enabledToggle) {
    enabledToggle.addEventListener('change', function() {
      syncEnabled();
      saveTeacher();
    });
  }
  epSel.addEventListener('change', function() { refreshModels(''); saveTeacher(); });
  modelSel.addEventListener('change', saveTeacher);

  _registerAiEndpointRefresh(function(endpoints) {
    _endpoints = endpoints;
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
    refreshModels(modelSel.value);
  });
}

/* ── Image Generation ── */

/* ── Vision ── */
async function initVisionSettings() {
  const vlSel = el('set-vlModelSelect');
  const msg = el('set-visionSettingsMsg');
  const enabledToggle = el('set-visionEnabledToggle');
  const configWrap = vlSel ? vlSel.closest('div[style*="flex-direction"]') : null;
  var _visionEndpoints = [];
  var visionFallbackWidget = null;
  var _vlExclude = ['audio', 'realtime', 'tts', 'dall-e', 'embedding', 'search', 'whisper'];
  function _isVisionModel(mid) {
    var lower = String(mid || '').toLowerCase();
    return !_vlExclude.some(function(kw) { return lower.includes(kw); });
  }
  try {
    const modelsRes = await fetch('/api/models', { credentials: 'same-origin' });
    const modelsData = await modelsRes.json();
    const visionModels = [];
    (modelsData.items || []).forEach(item => {
      if (item.offline) return;
      (item.models || []).forEach(mid => {
        if (_isVisionModel(mid)) {
          visionModels.push(mid);
        }
      });
    });
    sortModelIds(visionModels).forEach(mid => {
      var opt = document.createElement('option'); opt.value = mid; opt.textContent = mid; vlSel.appendChild(opt);
    });
  } catch (e) { console.warn('Failed to load models for vision settings', e); }
  // Also pull the raw endpoint list so the fallback widget can resolve
  // endpoint-id → models the same way the other cards do.
  try {
    _visionEndpoints = await _fetchModelDefaultEndpoints();
  } catch (e) { console.warn('Failed to load endpoints for vision fallback', e); }
  try {
    const settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    const settings = await settingsRes.json();
    if (settings.vision_model) vlSel.value = settings.vision_model;
    _syncModelLogo(vlSel);
    if (enabledToggle) enabledToggle.checked = settings.vision_enabled !== false;
    visionFallbackWidget = _bindFallbackWidget({
      containerId: 'set-visionFallbacks',
      addBtnId: 'set-visionAddFallback',
      endpoints: function() { return _visionEndpoints; },
      // Vision fallback list filters to vision-capable models (same heuristic
      // as the primary select above — exclude audio/tts/embedding/etc.).
      modelsFilter: function(mid) { return _isVisionModel(mid); },
      settingKey: 'vision_model_fallbacks',
      initial: Array.isArray(settings.vision_model_fallbacks)
        ? settings.vision_model_fallbacks.map(function(f) { return { endpoint_id: (f && f.endpoint_id) || '', model: (f && f.model) || '' }; })
        : [],
    });
  } catch (e) { console.warn('Failed to load vision settings', e); }

  function syncVisionDisabled() {
    var off = enabledToggle && !enabledToggle.checked;
    var card = enabledToggle ? enabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (configWrap) configWrap.style.pointerEvents = off ? 'none' : '';
  }
  syncVisionDisabled();

  async function saveSettings() {
    try {
      await _postSettings({ vision_enabled: enabledToggle ? enabledToggle.checked : true, vision_model: vlSel.value });
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)'; setTimeout(() => { msg.textContent = ''; }, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }
  vlSel.addEventListener('change', saveSettings);
  if (enabledToggle) enabledToggle.addEventListener('change', function() { syncVisionDisabled(); saveSettings(); });

  _registerAiEndpointRefresh(function(endpoints) {
    _visionEndpoints = endpoints;
    if (visionFallbackWidget && visionFallbackWidget.refresh) visionFallbackWidget.refresh();
  });
}

/* ── Face Recognition ── */

/* ── Text to Speech ── */

/* ── Speech to Text ── */

/* ═══════════════════════════════════════════
   SEARCH TAB
   ═══════════════════════════════════════════ */

/* ── Deep Research Model (AI tab) ── */
async function initResearchSettings() {
  var epSel = el('set-researchEndpoint');
  var modelSel = el('set-researchModel');
  var tokensInput = el('set-researchMaxTokens');
  var extractTimeoutInput = el('set-researchExtractTimeout');
  var extractConcurrencyInput = el('set-researchExtractConcurrency');
  var runTimeoutInput = el('set-researchRunTimeout');
  var msg = el('set-researchMsg');
  var endpoints = [];
  var researchSource = _createModelDefaultSource('research', function(nextEndpoints) {
    endpoints = nextEndpoints;
    _fillEndpointSelect(epSel, endpoints, epSel.value, true);
  });

  function refreshModels(selectedModel) {
    var epId = epSel.value;
    var ep = endpoints.find(function(e) { return e.id === epId; });
    _fillModelSelect(modelSel, ep ? ep.models : [], selectedModel, true);
  }

  function applyStoredResearchModel(stored) {
    epSel.value = stored.research_endpoint_id || '';
    if (epSel.value !== (stored.research_endpoint_id || '')) epSel.value = '';
    _syncEndpointLogo(epSel);
    refreshModels(stored.research_model || '');
  }

  var researchLoader = _createModelDefaultLoader({
    label: 'research model',
    controls: function() { return [epSel, modelSel]; },
    msgEl: msg,
    load: researchSource.load,
    apply: applyStoredResearchModel,
  });
  await researchLoader.run();

  // Run limits are server-wide (Search → Deep Research).
  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    var settings = await res.json();
    if (settings.research_max_tokens) tokensInput.value = settings.research_max_tokens;
    if (settings.research_extraction_timeout_seconds) extractTimeoutInput.value = settings.research_extraction_timeout_seconds;
    if (settings.research_extraction_concurrency) extractConcurrencyInput.value = settings.research_extraction_concurrency;
    if (settings.research_run_timeout_seconds !== undefined && settings.research_run_timeout_seconds !== null) {
      runTimeoutInput.value = settings.research_run_timeout_seconds;
    }
  } catch (e) { console.warn('Failed to load research settings', e); }

  function showStatus() {
    // Keep a load failure and its Retry visible.
    if (!researchLoader.isLoaded()) return;
    var parts = [];
    if (epSel.value) {
      var epName = epSel.options[epSel.selectedIndex].textContent;
      var mName = modelSel.value ? modelSel.value.split('/').pop() : 'auto';
      parts.push(epName + ' / ' + mName);
    }
    if (tokensInput.value) {
      parts.push('Max tokens: ' + tokensInput.value);
    }
    if (extractTimeoutInput.value) {
      parts.push('Extract: ' + extractTimeoutInput.value + 's');
    }
    if (extractConcurrencyInput.value) {
      parts.push('Parallel: ' + extractConcurrencyInput.value);
    }
    if (runTimeoutInput.value !== '') {
      var rtv = parseInt(runTimeoutInput.value, 10);
      if (!isNaN(rtv)) {
        parts.push(rtv === 0 ? 'Max time: no limit' : 'Max time: ' + rtv + 's');
      }
    }
    if (parts.length) {
      msg.textContent = parts.join(' · ');
      msg.style.color = 'var(--fg)';
    } else {
      msg.textContent = 'Using chat defaults';
      msg.style.color = 'var(--fg)';
    }
  }
  showStatus();

  // The endpoint and model are a model default (per account for signed-in
  // users); the run limits below are server-wide. They save separately so a
  // user's model choice never carries server-wide fields.
  async function saveResearchModel() {
    if (!researchLoader.isLoaded()) return;
    try {
      await researchSource.store().save({
        research_endpoint_id: epSel.value,
        research_model: modelSel.value,
      });
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      setTimeout(showStatus, 2000);
    } catch (e) {
      var restored = (e && e.serverSaved) ? true : await researchLoader.run();
      if (restored) { msg.textContent = modelDefaultSaveError(e); msg.style.color = 'var(--red)'; }
    }
  }

  async function saveResearch() {
    var payload = {};
    var tv = parseInt(tokensInput.value, 10);
    if (tv && tv >= 1024) payload.research_max_tokens = tv;
    var et = parseInt(extractTimeoutInput.value, 10);
    if (et && et >= 15 && et <= 3600) payload.research_extraction_timeout_seconds = et;
    var ec = parseInt(extractConcurrencyInput.value, 10);
    if (ec && ec >= 1 && ec <= 12) payload.research_extraction_concurrency = ec;
    if (runTimeoutInput.value !== '') {
      var rt = parseInt(runTimeoutInput.value, 10);
      // 0 = no limit (disables the hard timeout); otherwise 60s..86400s (24h)
      if (!isNaN(rt) && (rt === 0 || (rt >= 60 && rt <= 86400))) {
        payload.research_run_timeout_seconds = rt;
      }
    }
    if (!Object.keys(payload).length) return;
    try {
      await _postSettings(payload);
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      setTimeout(showStatus, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  epSel.addEventListener('change', async function() {
    refreshModels('');
    saveResearchModel();
  });
  modelSel.addEventListener('change', saveResearchModel);
  tokensInput.addEventListener('change', saveResearch);
  extractTimeoutInput.addEventListener('change', saveResearch);
  extractConcurrencyInput.addEventListener('change', saveResearch);
  runTimeoutInput.addEventListener('change', saveResearch);

  _registerAiEndpointRefresh(function(nextEndpoints) {
    endpoints = nextEndpoints;
    _fillEndpointSelect(epSel, endpoints, epSel.value, true);
    refreshModels(modelSel.value);
  });
}

/* ── Agent Settings (AI tab) ── */

/* ═══════════════════════════════════════════
   SHORTCUTS TAB
   ═══════════════════════════════════════════ */

function initAll() {
  modalEl = el('settings-modal');

  // Who may change what comes from the server. Until it answers, the
  // model-default cards stay read-only; their Retry (or reopening Settings)
  // asks again. Access may already be known (an endpoint refresh can run
  // before Settings is first opened), so apply it here as well.
  _resolveSettingsAccess().then(_applySettingsAccess, function(error) {
    console.warn('Settings access is unknown until /api/auth/status answers', error);
  });

  bindSettingsNavigation(modalEl, {
    openAdminTab: _shell.openAdminTab,
    onPanelActivated: _shell.onPanelActivated,
  });

  bindSettingsSearch(modalEl, {
    isAdmin: () => _canManageSettings(),
    accountsEnabled: () => _accountsEnabled(),
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
  initDefaultChat();
  initTeacherModel();
  initUtilityModel();
  initImageSettings();
  initVisionSettings();
  initTtsSettings();
  initSttSettings();
  initSearchSettings();
  initResearchSettings();
  initResearchSearchSettings();
  initAgentSettings();
  initAppearance(modalEl, { canManage: _canManageSettings });
  initShortcuts();
  initAccount();
  initIntegrations();
  initEmailSettings();
  initEmailAccountsSettings({ openSettings: open });
  initReminderSettings(modalEl, { canManage: _canManageSettings });
  initUnifiedIntegrations();
  initDocumentWritingStyle();
}


/* ══ Unified Integrations ══ */

/* ═══════════════════════════════════════════
   PUBLIC API
   ═══════════════════════════════════════════ */
export function open(tab, options) { _shell.open(tab, options); }
export function close() { _shell.close(); }
// Public entry points keep their no-argument contract; the module needs open().
function initUnifiedIntegrations() { return _initUnifiedIntegrations({ openSettings: open }); }
function syncAdminVisibility() { _shell.syncAdminVisibility(); }

handleSettingsOauthReturn({ openSettings: open });

const settingsModule = { open, close, initIntegrations, initUnifiedIntegrations, syncAdminVisibility, refreshAiModelEndpoints };


export default settingsModule;
