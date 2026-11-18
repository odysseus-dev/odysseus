// Default Chat model card: endpoint and model choice, the foreground fallback
// chain and their save/Retry states. Extracted from settings.js; the endpoint
// catalog and model-default plumbing are passed in.

import { byId as el } from './dom.js';
import {
  createForegroundPreferenceSaveQueue,
  summarizeForegroundFallbackCandidateEligibility,
  normalizeForegroundFallbackPrefs,
  MAX_FOREGROUND_FALLBACKS,
} from '../foregroundFallbackSettings.js';
import {
  fillModelSelect as _fillModelSelect,
  selectableEndpointModels as _selectableEndpointModels,
  ensureBlankOption as _ensureBlankOption,
  fillEndpointSelect as _fillEndpointSelect,
  syncEndpointLogo as _syncEndpointLogo,
} from './modelSelects.js';
import { bindFallbackWidget as _bindFallbackWidget } from './fallbackWidget.js';
import { modelDefaultSaveError } from './modelDefaults.js';

/* ── Default Chat Model ── */
export async function initDefaultChat({ catalog, cards }) {
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
      _foregroundFallbackEndpoints = await catalog.fetchForegroundFallbackEndpoints();
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
  var defaultSource = cards.createSource('default chat', function(endpoints, store) {
    personalDefault = store.personal;
    // A personal default can be left unset. Whether a server default then
    // applies depends on the server's sharing setting, so the label only says
    // that this account has no personal choice.
    if (personalDefault) _ensureBlankOption(epSel, 'No personal default');
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

  var defaultLoader = cards.createLoader({
    label: 'default chat model',
    controls: function() { return [epSel, modelSel]; },
    msgEl: msg,
    load: defaultSource.load,
    apply: applyStoredDefault,
  });

  async function saveDefault() {
    if (!defaultLoader.isLoaded()) return;
    try {
      await defaultSource.store().save({
        default_endpoint_id: epSel.value,
        default_model: modelSel.value
      });
      cards.showMessage(msg, 'Saved', false);
    } catch (e) {
      // Show what is actually stored instead of the choice that was not saved.
      var restored = (e && e.serverSaved) ? true : await defaultLoader.run();
      if (restored) cards.showMessage(msg, modelDefaultSaveError(e), true);
    }
  }

  epSel.addEventListener('change', function() { refreshModels(''); saveDefault(); });
  modelSel.addEventListener('change', saveDefault);

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

  catalog.registerAiEndpointRefresh(function(endpoints) {
    _endpoints = endpoints;
    refreshEndpointOptions(epSel.value, modelSel.value);
  });
  catalog.registerForegroundFallbackEndpointRefresh(function(endpoints, error) {
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
