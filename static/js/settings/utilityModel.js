// Utility model card (background tasks): endpoint, model and fallback chain.
// Extracted from settings.js; the endpoint catalog and model-default plumbing
// are passed in.

import { byId as el } from './dom.js';
import {
  fillEndpointSelect as _fillEndpointSelect,
  fillModelSelect as _fillModelSelect,
  syncEndpointLogo as _syncEndpointLogo,
} from './modelSelects.js';
import { bindFallbackWidget as _bindFallbackWidget } from './fallbackWidget.js';
import { modelDefaultSaveError } from './modelDefaults.js';

/* ── Utility Model ── */
export async function initUtilityModel({ catalog, cards }) {
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

  var utilitySource = cards.createSource('utility model', function(endpoints) {
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
        if (restored) cards.showMessage(msg, modelDefaultSaveError(error), true);
      },
      supportsEffort: true,
      getEffortLevels: function(epId, modelId) {
        var meta = (_levelsByEndpoint[epId] || {})[modelId] || {};
        return Array.isArray(meta.supported_reasoning_levels) ? meta.supported_reasoning_levels : [];
      },
      initial: initial,
    });
  }

  var utilityLoader = cards.createLoader({
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
      cards.showMessage(msg, 'Saved', false);
    } catch (e) {
      var restored = (e && e.serverSaved) ? true : await utilityLoader.run();
      if (restored) cards.showMessage(msg, modelDefaultSaveError(e), true);
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

  await utilityLoader.run();

  catalog.registerAiEndpointRefresh(async function(endpoints) {
    _endpoints = endpoints;
    _fillEndpointSelect(epSel, _endpoints, epSel.value, true);
    refreshModels(modelSel.value);
    await loadEffortLevels();
    refreshEffort(effortSel ? effortSel.value : '');
    if (fallbackWidget && fallbackWidget.refresh) fallbackWidget.refresh();
  });
}
