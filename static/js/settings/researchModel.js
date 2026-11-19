// Deep Research model card and its token/timeout limits. Extracted from
// settings.js; the endpoint catalog and model-default plumbing are passed in.

import { byId as el } from './dom.js';
import {
  fillEndpointSelect as _fillEndpointSelect,
  fillModelSelect as _fillModelSelect,
  syncEndpointLogo as _syncEndpointLogo,
} from './modelSelects.js';
import { modelDefaultSaveError } from './modelDefaults.js';
import { postSettings as _postSettings } from './api.js';

/* ── Deep Research Model (AI tab) ── */
export async function initResearchSettings({ catalog, cards }) {
  var epSel = el('set-researchEndpoint');
  var modelSel = el('set-researchModel');
  var tokensInput = el('set-researchMaxTokens');
  var extractTimeoutInput = el('set-researchExtractTimeout');
  var extractConcurrencyInput = el('set-researchExtractConcurrency');
  var runTimeoutInput = el('set-researchRunTimeout');
  var msg = el('set-researchMsg');
  var endpoints = [];
  var researchSource = cards.createSource('research', function(nextEndpoints) {
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

  var researchLoader = cards.createLoader({
    label: 'research model',
    controls: function() { return [epSel, modelSel]; },
    msgEl: msg,
    load: researchSource.load,
    apply: applyStoredResearchModel,
  });

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

  epSel.addEventListener('change', async function() {
    refreshModels('');
    saveResearchModel();
  });
  modelSel.addEventListener('change', saveResearchModel);

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

  showStatus();

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

  tokensInput.addEventListener('change', saveResearch);
  extractTimeoutInput.addEventListener('change', saveResearch);
  extractConcurrencyInput.addEventListener('change', saveResearch);
  runTimeoutInput.addEventListener('change', saveResearch);

  catalog.registerAiEndpointRefresh(function(nextEndpoints) {
    endpoints = nextEndpoints;
    _fillEndpointSelect(epSel, endpoints, epSel.value, true);
    refreshModels(modelSel.value);
  });
}
