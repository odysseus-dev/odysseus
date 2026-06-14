// Composer controls for the ChatGPT Subscription / Codex endpoint.

const API_BASE = window.location.origin;

let _deps = null;
let _initialized = false;

const EFFORTS = new Set(['auto', 'low', 'medium', 'high', 'xhigh']);
const MODES = new Set(['normal', 'fast']);

function _el(id) {
  return document.getElementById(id);
}

function _normEffort(value) {
  const v = String(value || '').trim().toLowerCase();
  return EFFORTS.has(v) ? v : 'auto';
}

function _normMode(value) {
  const v = String(value || '').trim().toLowerCase();
  return MODES.has(v) ? v : 'normal';
}

function _endpointFromCache(endpointId) {
  if (!endpointId || !_deps || !_deps.modelsModule || !_deps.modelsModule.getCachedItems) return null;
  const items = _deps.modelsModule.getCachedItems() || [];
  return items.find(item => String(item.endpoint_id || '') === String(endpointId)) || null;
}

function _isChatGptSubscription(sel) {
  if (!sel) return false;
  const url = String(sel.url || '').toLowerCase();
  if (url.includes('chatgpt.com/backend-api/codex')) return true;
  const item = _endpointFromCache(sel.endpointId);
  const text = [
    item && item.endpoint_name,
    item && item.url,
    sel.endpointName,
  ].filter(Boolean).join(' ').toLowerCase();
  return text.includes('chatgpt subscription') || text.includes('chatgpt.com/backend-api/codex');
}

function _currentSelection() {
  if (!_deps || !_deps.sessionModule) return null;
  const currentId = _deps.sessionModule.getCurrentSessionId && _deps.sessionModule.getCurrentSessionId();
  const sessions = (_deps.sessionModule.getSessions && _deps.sessionModule.getSessions()) || [];
  const current = sessions.find(s => s.id === currentId);
  if (current && current.model) {
    return {
      model: current.model,
      url: current.endpoint_url || '',
      endpointId: current.endpoint_id || '',
      endpointName: current.endpoint_name || '',
    };
  }
  const pending = _deps.sessionModule.getPendingChat && _deps.sessionModule.getPendingChat();
  if (pending && pending.modelId) {
    const item = _endpointFromCache(pending.endpointId);
    return {
      model: pending.modelId,
      url: pending.url || (item && item.url) || '',
      endpointId: pending.endpointId || '',
      endpointName: (item && item.endpoint_name) || '',
    };
  }
  const fallback = window.__odysseusDefaultChat;
  if (fallback && fallback.model) {
    return {
      model: fallback.model,
      url: fallback.endpoint_url || '',
      endpointId: fallback.endpoint_id || '',
      endpointName: fallback.endpoint_name || '',
    };
  }
  return null;
}

function _syncVisibility() {
  const wrap = _el('chatgpt-controls');
  if (!wrap) return;
  const visible = _isChatGptSubscription(_currentSelection());
  wrap.classList.toggle('hidden', !visible);
}

async function _save() {
  const effort = _normEffort(_el('chatgpt-effort-select')?.value);
  const mode = _normMode(_el('chatgpt-mode-select')?.value);
  try {
    await fetch(API_BASE + '/api/auth/settings', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chatgpt_subscription_reasoning_effort: effort,
        chatgpt_subscription_response_mode: mode,
      }),
    });
  } catch (_) {
    // The form values are still sent with each chat request; save failure only
    // affects persistence across reloads.
  }
}

async function _load() {
  try {
    const res = await fetch(API_BASE + '/api/auth/settings', { credentials: 'same-origin' });
    const settings = await res.json();
    const effortSel = _el('chatgpt-effort-select');
    const modeSel = _el('chatgpt-mode-select');
    if (effortSel) effortSel.value = _normEffort(settings.chatgpt_subscription_reasoning_effort);
    if (modeSel) modeSel.value = _normMode(settings.chatgpt_subscription_response_mode);
  } catch (_) {}
}

export function getOptions() {
  return {
    effort: _normEffort(_el('chatgpt-effort-select')?.value),
    mode: _normMode(_el('chatgpt-mode-select')?.value),
    enabled: _isChatGptSubscription(_currentSelection()),
  };
}

export async function initChatGptControls(deps) {
  _deps = deps || {};
  if (_initialized) return;
  _initialized = true;
  await _load();
  _syncVisibility();
  _el('chatgpt-effort-select')?.addEventListener('change', _save);
  _el('chatgpt-mode-select')?.addEventListener('change', _save);
  document.addEventListener('odysseus:model-picked', _syncVisibility);
  document.addEventListener('odysseus:model-selection-changed', _syncVisibility);
  document.addEventListener('odysseus:models-refreshed', _syncVisibility);
}

export default {
  initChatGptControls,
  getOptions,
};
