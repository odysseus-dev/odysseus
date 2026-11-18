// Text-to-speech and speech-to-text settings panels.
// Extracted verbatim from static/js/settings.js. Behaviour is unchanged: the
// functions moved, their bodies did not. settings.js imports them and calls
// them from the same places, so load order and init sequence are untouched.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';
import {
  inferTtsProvider,
  voiceOptionsForProvider,
  defaultVoiceForProvider,
  isTtsCapableEndpoint,
  isTtsModelForProvider,
  ttsModelsForEndpoint,
  ttsProviderForEndpoint,
} from './ttsVoices.js?v=20261103ttsclean1';

export async function initTtsSettings() {
  var provSel = el('set-ttsProviderSelect');
  var modelSelect = el('set-ttsModelSelect');
  var modelInput = el('set-ttsModelInput');
  var voiceSelect = el('set-ttsVoiceSelect');
  var voiceInput = el('set-ttsVoiceInput');
  var modelRow = el('set-ttsModelRow');
  var voiceRow = el('set-ttsVoiceRow');
  var speedSelect = el('set-ttsSpeedSelect');
  var speedRow = el('set-ttsSpeedRow');
  var endpointUrlRow = el('set-ttsEndpointUrlRow');
  var endpointUrlInput = el('set-ttsEndpointUrlInput');
  var apiKeyRow = el('set-ttsApiKeyRow');
  var apiKeyInput = el('set-ttsApiKeyInput');
  var endpointHint = el('set-ttsEndpointHint');
  var ttsEmptyState = el('set-ttsEmptyState');
  var ttsMsg = el('set-ttsSettingsMsg');
  var ttsEnabledToggle = el('set-ttsEnabledToggle');
  var ttsConfigWrap = provSel ? provSel.closest('div[style*="flex-direction"]') : null;

  var endpointDetails = {};
  var useVoiceSelect = false;
  var savedApiKey = false;
  var initialModel = '';
  var ttsCapableEndpointCount = 0;
  var endpointsLoaded = false;

  function providerKind() {
    var prov = provSel.value || '';
    if (prov === 'custom') return 'custom';
    if (prov.startsWith('endpoint:')) return 'endpoint';
    if (prov === 'browser' || prov === 'disabled') return prov;
    return 'disabled';
  }
  function isEndpoint() { return providerKind() === 'endpoint'; }
  function optionExists(select, value) {
    return Array.prototype.some.call(select.options, function(option) {
      return option.value === value;
    });
  }
  function updateTtsEmptyState() {
    if (!ttsEmptyState) return;
    ttsEmptyState.style.display = ttsCapableEndpointCount > 0 ? 'none' : 'block';
  }
  function getModel() {
    if (isEndpoint()) return modelSelect.value;
    return modelInput.value;
  }
  function getEndpointId() {
    var prov = provSel.value || '';
    return prov.startsWith('endpoint:') ? prov.slice('endpoint:'.length) : '';
  }
  function getEndpointDetails() {
    return endpointDetails[getEndpointId()] || null;
  }
  function getVoice() {
    return useVoiceSelect ? voiceSelect.value : voiceInput.value;
  }
  function getInferredProvider() {
    return inferTtsProvider({
      provider: provSel.value,
      endpoint: getEndpointDetails(),
      endpointUrl: providerKind() === 'custom' ? endpointUrlInput.value : '',
      model: getModel(),
    });
  }
  function voiceLabel(voice) {
    if (voice.indexOf('_') !== -1) return voice;
    return voice.charAt(0).toUpperCase() + voice.slice(1);
  }
  function populateVoiceSelect(voices, selected) {
    voiceSelect.innerHTML = '';
    voices.forEach(function(voice) {
      var opt = document.createElement('option');
      opt.value = voice;
      opt.textContent = voiceLabel(voice);
      voiceSelect.appendChild(opt);
    });
    voiceSelect.value = voices.indexOf(selected) !== -1 ? selected : voices[0];
  }

  function updateVoicePicker(options) {
    options = options || {};
    var provider = getInferredProvider();
    var voices = voiceOptionsForProvider(provider);
    var current = getVoice();
    if (options.resetForBrowser && provider === 'browser') current = '';

    if (voices && voices.length) {
      var selected = voices.indexOf(current) !== -1
        ? current
        : (defaultVoiceForProvider(provider) || voices[0]);
      populateVoiceSelect(voices, selected);
      voiceSelect.style.display = '';
      voiceInput.style.display = 'none';
      voiceInput.value = selected;
      voiceInput.placeholder = provider === 'xai' ? 'eve' : 'alloy';
      voiceInput.title = '';
      useVoiceSelect = true;
    } else {
      voiceSelect.style.display = 'none';
      voiceInput.style.display = provider === 'disabled' ? 'none' : '';
      voiceInput.value = current;
      voiceInput.placeholder = provider === 'browser' ? 'OS default voice'
        : 'voice id (provider-specific; check docs)';
      voiceInput.title = provider === 'generic'
        ? 'No built-in list for this endpoint; enter the voice ID from the provider documentation.'
        : '';
      useVoiceSelect = false;
    }
  }

  function rebuildModelOptions(endpointId, selected) {
    var endpoint = endpointDetails[endpointId];
    var models = ttsModelsForEndpoint(endpoint);
    var endpointProvider = ttsProviderForEndpoint(endpoint);
    if (selected && models.indexOf(selected) < 0 &&
        isTtsModelForProvider(endpointProvider, selected)) {
      models.unshift(selected);
    }
    modelSelect.innerHTML = '';
    models.forEach(function(m) {
      var opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      modelSelect.appendChild(opt);
    });
    if (selected) modelSelect.value = selected;
    if (!modelSelect.value && models.length) modelSelect.value = models[0];
  }

  function updateVisibility(selectedModel, voiceOptions) {
    var kind = providerKind();
    var showApi = kind === 'endpoint' || kind === 'custom';
    endpointUrlRow.style.display = kind === 'custom' ? 'flex' : 'none';
    apiKeyRow.style.display = kind === 'custom' ? 'flex' : 'none';
    if (endpointHint) endpointHint.style.display = showApi ? 'block' : 'none';
    modelRow.style.display = showApi ? 'flex' : 'none';
    voiceRow.style.display = kind === 'disabled' ? 'none' : 'flex';
    speedRow.style.display = kind === 'disabled' ? 'none' : 'flex';
    if (kind === 'endpoint') {
      modelSelect.style.display = ''; modelInput.style.display = 'none';
      rebuildModelOptions(getEndpointId(), selectedModel || modelSelect.value);
    } else {
      modelSelect.style.display = 'none'; modelInput.style.display = '';
    }
    updateVoicePicker(voiceOptions);
  }

  try {
    var epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    if (!epRes.ok) throw new Error('HTTP ' + epRes.status);
    var endpoints = await epRes.json();
    if (!Array.isArray(endpoints)) throw new Error('Unexpected endpoint list');
    endpoints.forEach(function(ep) {
      endpointDetails[ep.id] = ep;
      if (!ep.is_enabled) return;
      if (!isTtsCapableEndpoint(ep)) return;
      ttsCapableEndpointCount += 1;
      var opt = document.createElement('option');
      opt.value = 'endpoint:' + ep.id;
      opt.textContent = ep.name + ' (API)';
      provSel.appendChild(opt);
    });
    endpointsLoaded = true;
    updateTtsEmptyState();
  } catch (e) { console.warn('Failed to load endpoints for TTS', e); updateTtsEmptyState(); }

  var migration = {};
  try {
    var settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await settingsRes.json();
    var savedProvider = settings.tts_provider;
    if (savedProvider === 'local') {
      savedProvider = 'disabled';
      settings.tts_provider = 'disabled';
      migration.tts_provider = 'disabled';
    } else if (savedProvider === 'api' || savedProvider === 'custom_endpoint') {
      savedProvider = 'custom';
      settings.tts_provider = 'custom';
      migration.tts_provider = 'custom';
    }
    if (savedProvider && !optionExists(provSel, savedProvider)) {
      if (savedProvider.indexOf('endpoint:') === 0 && !endpointsLoaded) {
        // A failed endpoint fetch is not proof that a saved TTS endpoint is
        // invalid; keep it selectable instead of destructively disabling it.
        var unavailable = document.createElement('option');
        unavailable.value = savedProvider;
        unavailable.textContent = savedProvider + ' (unavailable)';
        provSel.appendChild(unavailable);
      } else {
        savedProvider = 'disabled';
        settings.tts_provider = 'disabled';
        migration.tts_provider = 'disabled';
      }
    }
    if (savedProvider) provSel.value = savedProvider;
    if (settings.tts_model) { initialModel = settings.tts_model; modelSelect.value = settings.tts_model; modelInput.value = settings.tts_model; }
    if (settings.tts_voice) { voiceSelect.value = settings.tts_voice; voiceInput.value = settings.tts_voice; }
    if (settings.tts_speed) { speedSelect.value = settings.tts_speed; }
    if (settings.tts_endpoint_url) endpointUrlInput.value = settings.tts_endpoint_url;
    savedApiKey = !!settings.tts_api_key;
    apiKeyInput.value = '';
    apiKeyInput.placeholder = savedApiKey ? 'Saved key (leave blank to keep)' : 'Optional API key';
    if (ttsEnabledToggle) ttsEnabledToggle.checked = settings.tts_enabled !== false;

    if (isEndpoint() && initialModel) {
      var endpoint = getEndpointDetails();
      var allowedModels = ttsModelsForEndpoint(endpoint);
      var endpointProvider = ttsProviderForEndpoint(endpoint);
      if (allowedModels.indexOf(initialModel) === -1 &&
          !isTtsModelForProvider(endpointProvider, initialModel)) {
        var nearestModel = allowedModels[0] || '';
        if (nearestModel) {
          initialModel = nearestModel;
          settings.tts_model = nearestModel;
          migration.tts_model = nearestModel;
          modelSelect.value = nearestModel;
          modelInput.value = nearestModel;
        }
      }
    }
  } catch (e) { console.warn('Failed to load TTS settings', e); }

  if (Object.keys(migration).length) {
    try { await _postSettings(migration); }
    catch (e) { console.warn('Failed to migrate TTS settings', e); }
  }

  function syncTtsDisabled() {
    var off = ttsEnabledToggle && !ttsEnabledToggle.checked;
    var card = ttsEnabledToggle ? ttsEnabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (ttsConfigWrap) ttsConfigWrap.style.pointerEvents = off ? 'none' : '';
  }
  syncTtsDisabled();
  updateVisibility(initialModel || modelSelect.value || modelInput.value);

  async function saveTTS() {
    try {
      var kind = providerKind();
      var payload = {
        tts_enabled: ttsEnabledToggle ? ttsEnabledToggle.checked : true,
        tts_provider: provSel.value,
        tts_model: getModel() || 'tts-1',
        tts_voice: getVoice() || defaultVoiceForProvider(getInferredProvider()) || 'alloy',
        tts_speed: speedSelect.value || '1'
      };
      if (kind === 'custom') {
        payload.tts_endpoint_url = endpointUrlInput.value.trim();
        var newKey = apiKeyInput.value.trim();
        if (newKey) payload.tts_api_key = newKey;
      }
      await _postSettings(payload);
      if (kind === 'custom' && apiKeyInput.value.trim()) {
        savedApiKey = true;
        apiKeyInput.value = '';
        apiKeyInput.placeholder = 'Saved key (leave blank to keep)';
      }
      ttsMsg.textContent = 'Saved'; ttsMsg.style.color = 'var(--fg)'; setTimeout(() => { ttsMsg.textContent = ''; }, 2000);
      if (window.aiTTSManager) window.aiTTSManager.checkAvailability();
    } catch (e) { ttsMsg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; ttsMsg.style.color = 'var(--red)'; }
  }

  async function saveAndClearCache() {
    await saveTTS();
    fetch('/api/tts/clear-cache', { method: 'POST', credentials: 'same-origin' }).catch(function(){});
  }

  provSel.addEventListener('change', function() {
    var kind = providerKind();
    if (kind === 'custom' && !modelInput.value) modelInput.value = 'tts-1';
    updateVisibility(
      kind === 'endpoint' ? modelSelect.value : modelInput.value,
      { resetForBrowser: kind === 'browser' }
    );
    saveTTS();
  });
  modelSelect.addEventListener('change', function() {
    updateVoicePicker();
    saveAndClearCache();
  });
  modelInput.addEventListener('change', function() {
    updateVoicePicker();
    saveTTS();
  });
  voiceSelect.addEventListener('change', saveAndClearCache);
  voiceInput.addEventListener('change', saveTTS);
  speedSelect.addEventListener('change', saveAndClearCache);
  endpointUrlInput.addEventListener('change', function() {
    updateVoicePicker();
    saveAndClearCache();
  });
  apiKeyInput.addEventListener('change', saveTTS);
  if (ttsEnabledToggle) ttsEnabledToggle.addEventListener('change', function() { syncTtsDisabled(); saveTTS(); });

  // Preview / test button
  var previewBtn = el('set-ttsPreviewBtn');
  if (previewBtn) {
    var previewAudio = null;
    var previewPlaying = false;
    function resetPreview() { previewPlaying = false; previewBtn.textContent = 'Preview'; previewBtn.style.borderColor = ''; }

    previewBtn.addEventListener('click', async function() {
      if (previewPlaying) {
        if (previewAudio) { previewAudio.pause(); previewAudio = null; }
        window.speechSynthesis.cancel();
        resetPreview(); return;
      }
      var prov = provSel.value;
      if (prov === 'disabled') {
        ttsMsg.textContent = 'Select a provider first'; ttsMsg.style.color = 'var(--red, #e55)';
        setTimeout(function() { ttsMsg.textContent = ''; }, 2000); return;
      }
      var testText = 'Hello, this is a test of text to speech.';
      previewPlaying = true; previewBtn.textContent = 'Loading...';
      try {
        if (providerKind() === 'browser') {
          if (!('speechSynthesis' in window)) throw new Error('Browser TTS not supported');
          var utt = new SpeechSynthesisUtterance(testText);
          var voiceVal = getVoice();
          if (voiceVal) {
            var voices = window.speechSynthesis.getVoices();
            var target = voiceVal.toLowerCase();
            var match = voices.find(function(v) { return v.name.toLowerCase() === target; }) ||
                        voices.find(function(v) { return v.name.toLowerCase().includes(target); });
            if (match) utt.voice = match;
          }
          utt.rate = parseFloat(speedSelect.value) || 1;
          previewBtn.textContent = 'Stop'; previewBtn.style.borderColor = 'var(--red, #e55)';
          await new Promise(function(resolve, reject) {
            utt.onend = resolve;
            utt.onerror = function(e) { reject(new Error('Browser TTS: ' + e.error)); };
            window.speechSynthesis.speak(utt);
          });
        } else {
          var res = await fetch('/api/tts/synthesize', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: testText, format: 'audio' })
          });
          if (!res.ok) { var err = await res.json().catch(function() { return {}; }); throw new Error(err.detail?.message || 'Synthesis failed'); }
          var blob = await res.blob();
          var url = URL.createObjectURL(blob);
          previewAudio = new Audio(url);
          previewBtn.textContent = 'Stop'; previewBtn.style.borderColor = 'var(--red, #e55)';
          await new Promise(function(resolve, reject) {
            previewAudio.onended = function() { URL.revokeObjectURL(url); previewAudio = null; resolve(); };
            previewAudio.onerror = function() { URL.revokeObjectURL(url); previewAudio = null; reject(new Error('Playback failed')); };
            previewAudio.play().catch(reject);
          });
        }
      } catch (e) {
        ttsMsg.textContent = 'Preview failed: ' + e.message; ttsMsg.style.color = 'var(--red, #e55)';
        setTimeout(function() { ttsMsg.textContent = ''; }, 3000);
      } finally {
        resetPreview();
      }
    });
  }
}

export async function initSttSettings() {
  var provSel = el('set-sttProviderSelect');
  var modelSelect = el('set-sttModelSelect');
  var modelInput = el('set-sttModelInput');
  var modelRow = el('set-sttModelRow');
  var langRow = el('set-sttLangRow');
  var langInput = el('set-sttLangInput');
  var sttMsg = el('set-sttSettingsMsg');
  var sttEnabledToggle = el('set-sttEnabledToggle');
  var sttConfigWrap = el('set-sttConfigWrap');
  // STT was removed from AI Defaults — bail if the UI isn't present.
  if (!provSel) return;

  function isEndpoint() { return provSel.value.startsWith('endpoint:'); }
  function getModel() { return isEndpoint() ? modelInput.value : modelSelect.value; }

  function updateVisibility() {
    var prov = provSel.value;
    var showModel = prov === 'local' || prov.startsWith('endpoint:');
    var showLang = prov !== 'disabled';
    modelRow.style.display = showModel ? 'flex' : 'none';
    langRow.style.display = showLang ? 'flex' : 'none';
    if (isEndpoint()) {
      modelSelect.style.display = 'none'; modelInput.style.display = '';
    } else {
      modelSelect.style.display = ''; modelInput.style.display = 'none';
    }
  }

  function syncSttDisabled() {
    var off = sttEnabledToggle && !sttEnabledToggle.checked;
    var card = sttEnabledToggle ? sttEnabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (sttConfigWrap) sttConfigWrap.style.pointerEvents = off ? 'none' : '';
  }

  // Effective provider: if toggle is off, treat as disabled regardless of provider select
  function effectiveProvider() {
    if (sttEnabledToggle && !sttEnabledToggle.checked) return 'disabled';
    return provSel.value;
  }

  // Add API endpoints that might support STT
  try {
    var epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    var endpoints = await epRes.json();
    endpoints.forEach(function(ep) {
      if (!ep.is_enabled) return;
      var opt = document.createElement('option'); opt.value = 'endpoint:' + ep.id; opt.textContent = ep.name + ' (API)'; provSel.appendChild(opt);
    });
  } catch (e) { console.warn('Failed to load endpoints for STT', e); }

  // Load saved settings
  try {
    var settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await settingsRes.json();
    if (settings.stt_provider) provSel.value = settings.stt_provider;
    if (settings.stt_model) { modelSelect.value = settings.stt_model; modelInput.value = settings.stt_model; }
    if (settings.stt_language) langInput.value = settings.stt_language;
    if (sttEnabledToggle) sttEnabledToggle.checked = settings.stt_enabled !== false;
  } catch (e) { console.warn('Failed to load STT settings', e); }

  syncSttDisabled();
  updateVisibility();

  async function saveSTT() {
    try {
      var enabled = sttEnabledToggle ? sttEnabledToggle.checked : false;
      await _postSettings({ stt_enabled: enabled, stt_provider: provSel.value, stt_model: getModel() || 'base', stt_language: langInput.value.trim() });
      sttMsg.textContent = 'Saved'; sttMsg.style.color = 'var(--fg)'; setTimeout(() => { sttMsg.textContent = ''; }, 2000);
      // Notify voiceRecorder of effective provider and update send button icon
      if (window.voiceRecorderModule) window.voiceRecorderModule._sttProvider = effectiveProvider();
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    } catch (e) { sttMsg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; sttMsg.style.color = 'var(--red)'; }
  }

  provSel.addEventListener('change', function() { updateVisibility(); saveSTT(); });
  modelSelect.addEventListener('change', saveSTT);
  modelInput.addEventListener('change', saveSTT);
  langInput.addEventListener('change', saveSTT);
  if (sttEnabledToggle) sttEnabledToggle.addEventListener('change', function() { syncSttDisabled(); saveSTT(); });
}
