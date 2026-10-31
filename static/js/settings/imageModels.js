// Image-model settings panel.
// Extracted verbatim from static/js/settings.js. Behaviour is unchanged: the
// functions moved, their bodies did not. settings.js imports them and calls
// them from the same places, so load order and init sequence are untouched.

import { byId as el } from './dom.js';
import { saveImagePreferences as _postSettings } from './imagePreferences.js';
import { modelCaps } from '../editor/ai-models.js';
import { sortModelIds } from '../modelSort.js';
let preferencesListener = null;

export async function initImageSettings() {
  const modelSel = el('set-imgModelSelect');
  const editModelSel = el('set-imgEditModelSelect');
  const qualSel = el('set-imgQualitySelect');
  const msg = el('set-imgSettingsMsg');
  const enabledToggle = el('set-imgEnabledToggle');
  const configWrap = modelSel ? modelSel.closest('div[style*="flex-direction"]') : null;
  try {
    const endpointsRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    const endpoints = await endpointsRes.json();
    const imageModels = new Set();
    const editModels = new Set();
    (Array.isArray(endpoints) ? endpoints : []).forEach(endpoint => {
      if (!endpoint.is_enabled || !endpoint.online) return;
      (Array.isArray(endpoint.models) ? endpoint.models : []).forEach(modelId => {
        if (modelId && modelCaps(modelId, '', endpoint.model_type).gen) imageModels.add(String(modelId));
        if (modelId && modelCaps(modelId, '', endpoint.model_type).inpaint) editModels.add(String(modelId));
      });
    });
    modelSel.length = 1;
    sortModelIds(Array.from(imageModels)).forEach(mid => {
      const opt = document.createElement('option');
      opt.value = mid;
      opt.textContent = mid;
      modelSel.appendChild(opt);
    });
    if (editModelSel) {
      editModelSel.length = 1;
      for (const mid of sortModelIds(Array.from(editModels))) {
        const option = document.createElement('option');
        option.value = mid;
        option.textContent = mid;
        editModelSel.appendChild(option);
      }
    }
  } catch (e) { console.warn('Failed to load models for image settings', e); }
  try {
    const settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    const settings = await settingsRes.json();
    if (settings.image_model) modelSel.value = settings.image_model;
    if (editModelSel) editModelSel.value = settings.image_edit_model || '';
    if (settings.image_quality) qualSel.value = settings.image_quality;
    if (enabledToggle) enabledToggle.checked = settings.image_gen_enabled === true;
  } catch (e) { console.warn('Failed to load settings', e); }

  function syncImgDisabled() {
    var off = enabledToggle && !enabledToggle.checked;
    var card = enabledToggle ? enabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (configWrap) configWrap.style.pointerEvents = off ? 'none' : '';
  }
  syncImgDisabled();

  async function saveSettings() {
    try {
      const res = await _postSettings({ image_gen_enabled: enabledToggle ? enabledToggle.checked : false, image_model: modelSel.value, image_edit_model: editModelSel?.value || '', image_quality: qualSel.value });
      if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)'; setTimeout(() => { msg.textContent = ''; }, 2000);
    } catch (e) { msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; msg.style.color = 'var(--red)'; }
  }
  modelSel.addEventListener('change', saveSettings);
  editModelSel?.addEventListener('change', saveSettings);
  if (preferencesListener) window.removeEventListener('image-preferences-changed', preferencesListener);
  preferencesListener = event => {
    if ('image_model' in event.detail) modelSel.value = event.detail.image_model || '';
    if (editModelSel && 'image_edit_model' in event.detail) editModelSel.value = event.detail.image_edit_model || '';
  };
  window.addEventListener('image-preferences-changed', preferencesListener);
  qualSel.addEventListener('change', saveSettings);
  if (enabledToggle) enabledToggle.addEventListener('change', function() { syncImgDisabled(); saveSettings(); });
}
