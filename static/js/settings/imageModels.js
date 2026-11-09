// Image-model settings panel.
// Extracted verbatim from static/js/settings.js. Behaviour is unchanged: the
// functions moved, their bodies did not. settings.js imports them and calls
// them from the same places, so load order and init sequence are untouched.

import { byId as el } from './dom.js';
import { saveImagePreferences as _postSettings, readImagePreferences } from './imagePreferences.js';
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
  let currentEnabled = enabledToggle ? enabledToggle.checked : false;
  try {
    const settings = await readImagePreferences();
    if (modelSel && 'image_model' in settings) modelSel.value = settings.image_model || '';
    if (editModelSel && 'image_edit_model' in settings) editModelSel.value = settings.image_edit_model || '';
    if (qualSel && 'image_quality' in settings) qualSel.value = settings.image_quality || 'medium';
    if (enabledToggle) {
      enabledToggle.checked = settings.image_gen_enabled === true;
      currentEnabled = enabledToggle.checked;
    }
  } catch (e) { console.warn('Failed to load settings', e); }

  function syncImgDisabled() {
    var off = enabledToggle && !enabledToggle.checked;
    var card = enabledToggle ? enabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (configWrap) configWrap.style.pointerEvents = off ? 'none' : '';
  }
  syncImgDisabled();

  function checkIsAdmin() {
    return typeof window !== 'undefined' && !!window._isAdmin;
  }

  function applyAdminToggleState() {
    if (!enabledToggle) return;
    const admin = checkIsAdmin();
    if (!admin) {
      enabledToggle.disabled = true;
      const adminTitle = 'Admin access is required to change image generation availability.';
      enabledToggle.title = adminTitle;
      if (enabledToggle.parentElement) enabledToggle.parentElement.title = adminTitle;
    } else {
      enabledToggle.disabled = false;
      const adminTitle = 'Enable or disable image generation globally';
      enabledToggle.title = adminTitle;
      if (enabledToggle.parentElement) enabledToggle.parentElement.title = adminTitle;
    }
  }
  applyAdminToggleState();

  async function saveModelPreferences() {
    try {
      const res = await _postSettings({
        image_model: modelSel ? modelSel.value : '',
        image_edit_model: editModelSel?.value || '',
        image_quality: qualSel ? qualSel.value : 'medium',
      });
      if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)'; setTimeout(() => { msg.textContent = ''; }, 2000);
    } catch (e) {
      if (e.globalSaved) {
        msg.textContent = 'Policy saved, but failed to clear preferences: ' + (e.message || 'cleanup error');
        msg.style.color = 'var(--red)';
      } else {
        msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save';
        msg.style.color = 'var(--red)';
      }
    }
  }

  async function saveToggle() {
    if (!checkIsAdmin()) {
      if (enabledToggle) enabledToggle.checked = currentEnabled;
      syncImgDisabled();
      msg.textContent = 'Admin access is required to change these settings.';
      msg.style.color = 'var(--red)';
      return;
    }
    const targetState = enabledToggle ? enabledToggle.checked : false;
    try {
      const res = await _postSettings({ image_gen_enabled: targetState });
      if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
      if (enabledToggle) currentEnabled = enabledToggle.checked;
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)'; setTimeout(() => { msg.textContent = ''; }, 2000);
    } catch (e) {
      if (e.globalSaved) {
        currentEnabled = targetState;
        try {
          const authSettings = await readImagePreferences();
          if (typeof authSettings.image_gen_enabled === 'boolean') {
            currentEnabled = authSettings.image_gen_enabled;
          }
        } catch (_) {}
        if (enabledToggle) enabledToggle.checked = currentEnabled;
        syncImgDisabled();
        msg.textContent = 'Policy saved, but failed to clear preferences: ' + (e.message || 'cleanup error');
        msg.style.color = 'var(--red)';
      } else {
        try {
          const authSettings = await readImagePreferences();
          if (typeof authSettings.image_gen_enabled === 'boolean') {
            currentEnabled = authSettings.image_gen_enabled;
          }
        } catch (_) {}
        if (enabledToggle) enabledToggle.checked = currentEnabled;
        syncImgDisabled();
        msg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save';
        msg.style.color = 'var(--red)';
      }
    }
  }

  if (modelSel) modelSel.addEventListener('change', saveModelPreferences);
  if (editModelSel) editModelSel.addEventListener('change', saveModelPreferences);
  if (qualSel) qualSel.addEventListener('change', saveModelPreferences);
  if (enabledToggle) {
    enabledToggle.addEventListener('change', async function() {
      if (!checkIsAdmin()) {
        enabledToggle.checked = currentEnabled;
        syncImgDisabled();
        msg.textContent = 'Admin access is required to change these settings.';
        msg.style.color = 'var(--red)';
        return;
      }
      syncImgDisabled();
      await saveToggle();
    });
  }

  if (preferencesListener) window.removeEventListener('image-preferences-changed', preferencesListener);
  preferencesListener = event => {
    if ('image_model' in event.detail && modelSel) modelSel.value = event.detail.image_model || '';
    if (editModelSel && 'image_edit_model' in event.detail) editModelSel.value = event.detail.image_edit_model || '';
    if ('image_gen_enabled' in event.detail && enabledToggle) {
      enabledToggle.checked = event.detail.image_gen_enabled === true;
      currentEnabled = enabledToggle.checked;
      syncImgDisabled();
    }
  };
  window.addEventListener('image-preferences-changed', preferencesListener);
}
