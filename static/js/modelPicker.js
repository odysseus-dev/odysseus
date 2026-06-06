// Model Picker ??? chatbox model selector dropdown
// Extracted from sessions.js

import { providerLogo } from './providers.js';
import uiModule from './ui.js';
import settingsModule from './settings.js';
import { sortModelObjects, compareModelObjects } from './modelSort.js';

const API_BASE = window.location.origin;
const LOCAL_LLM_ROUTER_AUTO_MODEL_ID = '__auto_stack__';
const AUTO_SELECT_LABEL = 'Auto (Local LLMs)';
const LOCAL_LLM_ROUTER_NAME = 'Local-LLM-Router';
const LOCAL_LLM_ROUTER_PIP = 'local-llm-router[ollama]';
const AUTO_SELECT_TOOLTIP =
  'Auto (Local LLMs) ??? routes each message to the best local model by complexity (Local-LLM-Router). Fast small models for lookups; larger ones for design, code, and agent steps.';
const AUTO_SELECT_NEEDS_MODELS =
  'Auto (Local LLMs) needs 2+ models on a local endpoint. Open Cookbook to download models, then refresh endpoints.';
const AUTO_SELECT_NEEDS_ENDPOINT =
  'Auto (Local LLMs) needs a local endpoint (Ollama, etc.). Add one in Settings or Cookbook.';
const AUTO_SELECT_NEEDS_ONE_MORE =
  (n, name) => `Auto needs 2+ local models (you have ${n}${name ? `: ${name}` : ''}). Open Cookbook to add another.`;
const AUTO_SELECT_NEEDS_ROUTER =
  `Auto (Local LLMs) needs ${LOCAL_LLM_ROUTER_NAME}. Click Install or run pip install ${LOCAL_LLM_ROUTER_PIP}.`;
const AUTO_ICON_SVG =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h16M4 12h10M4 18h6"/></svg>';

// ?????? Recent + Favorites persistence ??????
// Recent is auto-tracked (last 5 picks, most-recent-first) and lives in its
// own key. Favorites is the SAME key the sidebar Models section uses, so a
// favorite toggled here shows up there and vice-versa.
const RECENT_KEY = 'odysseus-model-recent';
const FAVORITES_KEY = 'odysseus-model-favorites';
const RECENT_MAX = 5;
// Catalogs at or below this size are small enough that hiding everything
// behind search would be a regression ??? keep listing them in browse mode.
const BROWSE_ALL_LIMIT = 12;

function _loadList(key) {
  try {
    const a = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(a) ? a : [];
  } catch { return []; }
}
function _saveList(key, list) {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch { /* quota / private mode */ }
}
function _loadRecent() { return _loadList(RECENT_KEY); }
function _pushRecent(mid) {
  if (!mid) return;
  const next = _loadRecent().filter(x => x !== mid);
  next.unshift(mid);
  _saveList(RECENT_KEY, next.slice(0, RECENT_MAX));
}
function _loadFavorites() { return _loadList(FAVORITES_KEY); }
function _toggleFavorite(mid) {
  const favs = _loadFavorites();
  const i = favs.indexOf(mid);
  if (i >= 0) favs.splice(i, 1);
  else favs.push(mid);
  _saveList(FAVORITES_KEY, favs);
  // Keep the sidebar Models section (same key) in sync if it's mounted.
  try {
    if (window.modelsModule && typeof window.modelsModule.refreshModels === 'function') {
      window.modelsModule.refreshModels();
    }
  } catch { /* sidebar not present */ }
  return i < 0; // true when now favorited
}

// ?????? Shared keyboard nav for model pickers ??????
function _handlePickerKeydown(e, listEl, itemSelector, closeFn) {
  if (e.key === 'Escape') { closeFn(); return; }
  if (e.key === 'Enter') {
    e.preventDefault();
    const active = listEl.querySelector(itemSelector + '.kb-active') || listEl.querySelector(itemSelector);
    if (active) active.click();
    return;
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...listEl.querySelectorAll(itemSelector)].filter(el => el.style.display !== 'none');
    if (!items.length) return;
    const cur = items.findIndex(el => el.classList.contains('kb-active'));
    items.forEach(el => el.classList.remove('kb-active'));
    let next;
    if (e.key === 'ArrowDown') next = cur < items.length - 1 ? cur + 1 : 0;
    else next = cur > 0 ? cur - 1 : items.length - 1;
    items[next].classList.add('kb-active');
    items[next].scrollIntoView({ block: 'nearest' });
  }
}

// Dependencies injected via initModelPicker()
let _deps = null;
let _autoSelectingDefault = false;
let _localLlmRouterAvailable = true;
/** Last model Auto routed to (display-only while session stays on __auto_stack__). */
let _lastAutoResolvedModel = '';
let _lastAutoRouteReasons = [];
let _llrStatusCache = null;
const _LLR_STATUS_TTL_MS = 15000;

/** Called when SSE reports the resolved upstream model for an Auto session. */
export function noteAutoResolvedModel(model, reasons) {
  const name = (model || '').trim();
  if (!name || name === LOCAL_LLM_ROUTER_AUTO_MODEL_ID) return;
  _lastAutoResolvedModel = name;
  _lastAutoRouteReasons = Array.isArray(reasons) ? reasons.filter(Boolean) : [];
  updateModelPicker();
}

export function clearAutoResolvedModel() {
  _lastAutoResolvedModel = '';
  _lastAutoRouteReasons = [];
}

async function _refreshLocalLlmRouterStatus() {
  try {
    const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    if (!res.ok) return;
    const settings = await res.json();
    _localLlmRouterAvailable = settings.local_llm_router_available !== false;
  } catch (_) { /* keep prior value */ }
}

function _isLocalEndpointItem(item) {
  if (!item || item.offline) return false;
  if (item.category === 'local' || item.endpoint_kind === 'local') return true;
  const url = String(item.url || item.base_url || '').toLowerCase();
  return /localhost|127\.0\.0\.1|0\.0\.0\.0:11434|:11434/.test(url);
}

function _localEndpointModelCounts() {
  const items = (window.modelsModule && window.modelsModule.getCachedItems)
    ? window.modelsModule.getCachedItems() : [];
  let best = null;
  let bestCount = 0;
  let anyLocal = false;
  items.forEach(item => {
    if (!_isLocalEndpointItem(item) || item.offline) return;
    anyLocal = true;
    const count = (item.models || []).length + (item.models_extra || []).length;
    if (!best || count > bestCount) {
      best = item;
      bestCount = count;
    }
  });
  return { best, bestCount, anyLocal };
}

/** Whether Auto (Local LLMs) can route right now ??? used by picker and send guard. */
export function getLocalLlmRouterReadiness() {
  if (!_localLlmRouterAvailable) {
    return { ok: false, kind: 'router', message: AUTO_SELECT_NEEDS_ROUTER };
  }
  const { best, bestCount, anyLocal } = _localEndpointModelCounts();
  if (!anyLocal) {
    return { ok: false, kind: 'endpoint', message: AUTO_SELECT_NEEDS_ENDPOINT };
  }
  if (bestCount === 0) {
    return {
      ok: false,
      kind: 'no_models',
      message: 'No models installed yet. Open Cookbook to pull at least 2 local models, then refresh endpoints.',
    };
  }
  if (bestCount < 2) {
    const models = (best.models || []).concat(best.models_extra || []);
    const name = (models[0] || '').split('/').pop();
    return {
      ok: false,
      kind: 'insufficient',
      message: AUTO_SELECT_NEEDS_ONE_MORE(bestCount, name),
    };
  }
  return {
    ok: true,
    anchor: {
      mid: LOCAL_LLM_ROUTER_AUTO_MODEL_ID,
      display: AUTO_SELECT_LABEL,
      url: best.url,
      endpointId: best.endpoint_id,
      epName: best.endpoint_name || '',
      providerText: 'auto select local llm router routing stack tier optimization',
      isAuto: true,
      tooltip: AUTO_SELECT_TOOLTIP,
    },
  };
}

function _getLocalLlmRouterAnchor() {
  const readiness = getLocalLlmRouterReadiness();
  return readiness.ok ? readiness.anchor : null;
}

function _llrEndpointUrl(preferredUrl) {
  const url = (preferredUrl || '').trim();
  if (url) return url;
  const { best } = _localEndpointModelCounts();
  return (best && best.url) ? best.url : '';
}

async function _fetchLlrStatus(endpointUrl) {
  const url = _llrEndpointUrl(endpointUrl);
  if (!url) return null;
  const now = Date.now();
  if (
    _llrStatusCache
    && _llrStatusCache.url === url
    && now - _llrStatusCache.at < _LLR_STATUS_TTL_MS
  ) {
    return _llrStatusCache.data;
  }
  try {
    const params = new URLSearchParams({ endpoint_url: url });
    const res = await fetch(`${API_BASE}/api/local-llm-router/status?${params}`, { credentials: 'same-origin' });
    if (!res.ok) return null;
    const data = await res.json();
    _llrStatusCache = { url, at: now, data };
    return data;
  } catch (_) {
    return null;
  }
}

function _formatLlrStatusLines(status) {
  if (!status) return [];
  const lines = [];
  if (status.vram_gb != null && status.profile) {
    const srcNote = status.vram_source === 'manual'
      ? ' (manual)'
      : status.vram_source === 'fallback'
        ? ' (default)'
        : '';
    lines.push(`VRAM: ${status.vram_gb} GB -> profile ${status.profile}${srcNote}`);
  }
  if (status.quant) lines.push(`Quant: ${status.quant}`);
  if (status.pool && status.pool.length >= 2) {
    lines.push(`Pool: ${status.pool.join(', ')}`);
  } else if (status.installed && status.installed.length) {
    const shown = status.installed.slice(0, 8);
    lines.push(`Installed: ${shown.join(', ')}${status.installed.length > 8 ? '...' : ''}`);
  }
  if (status.missing_pulls && status.missing_pulls.length) {
    const pulls = status.missing_pulls.slice(0, 6);
    lines.push(`Pull to expand: ${pulls.join(', ')}${status.missing_pulls.length > 6 ? '...' : ''}`);
  }
  if (!status.ready && status.message) lines.push(status.message);
  return lines;
}

function _llrTooltipFromStatus(baseTooltip, status) {
  const extra = _formatLlrStatusLines(status);
  if (!extra.length) return baseTooltip || '';
  return [baseTooltip || '', ...extra].filter(Boolean).join('\n');
}

function _applyLlrStatusTooltip(el, baseTooltip, endpointUrl) {
  if (!el) return;
  _fetchLlrStatus(endpointUrl).then((status) => {
    if (!status) return;
    el.title = _llrTooltipFromStatus(baseTooltip, status);
  });
}

function _localLlmRouterPickerEntry() {
  const readiness = getLocalLlmRouterReadiness();
  if (readiness.ok) {
    return { ...readiness.anchor, pickable: true };
  }
  const { best } = _localEndpointModelCounts();
  return {
    mid: LOCAL_LLM_ROUTER_AUTO_MODEL_ID,
    display: AUTO_SELECT_LABEL,
    url: best?.url || '',
    endpointId: best?.endpoint_id || '',
    epName: best?.endpoint_name || '',
    providerText: 'auto select local llm router routing stack tier optimization',
    isAuto: true,
    pickable: false,
    unavailableKind: readiness.kind,
    tooltip: readiness.message,
  };
}

function _resolvePickTarget(m) {
  if (!m || !m.mid) return { error: 'Invalid model selection' };
  const isAuto = m.isAuto || m.mid === LOCAL_LLM_ROUTER_AUTO_MODEL_ID;
  if (isAuto) {
    const readiness = getLocalLlmRouterReadiness();
    if (!readiness.ok) return { error: readiness.message };
    return { ...readiness.anchor, ...m, pickable: true };
  }
  let endpointId = m.endpointId || m.endpoint_id || '';
  let url = m.url || '';
  const items = (window.modelsModule && window.modelsModule.getCachedItems)
    ? window.modelsModule.getCachedItems() : [];
  for (const item of items) {
    if (item.offline) continue;
    const models = (item.models || []).concat(item.models_extra || []);
    if (!models.includes(m.mid)) continue;
    if (!endpointId) endpointId = item.endpoint_id || '';
    if (!url) url = item.url || '';
    break;
  }
  if (!endpointId) {
    return {
      error: 'Could not resolve the endpoint for this model. Refresh endpoints in Settings, then try again.',
    };
  }
  return { ...m, endpointId, url };
}

async function _patchSessionModel(sessionId, target) {
  const fd = new FormData();
  fd.append('model', target.mid);
  fd.append('endpoint_id', String(target.endpointId));
  if (target.url) fd.append('endpoint_url', target.url);
  if (target.mid === LOCAL_LLM_ROUTER_AUTO_MODEL_ID) fd.append('skip_validation', 'true');
  const res = await fetch(`${API_BASE}/api/session/${sessionId}`, {
    method: 'PATCH',
    credentials: 'same-origin',
    body: fd,
  });
  if (!res.ok) {
    let msg = 'Failed to set model';
    try {
      const data = await res.json();
      const detail = data.detail ?? data.error ?? data.message;
      if (typeof detail === 'string') msg = detail;
      else if (Array.isArray(detail) && detail[0]?.msg) msg = detail[0].msg;
    } catch (_) {
      try {
        const text = await res.text();
        if (text) msg = text;
      } catch (_e) { /* ignore */ }
    }
    return { ok: false, error: msg };
  }
  return { ok: true };
}

async function _installLocalLlmRouter() {
  if (uiModule && uiModule.showToast) uiModule.showToast(`Installing ${LOCAL_LLM_ROUTER_NAME}???`);
  try {
    const r = await fetch('/api/cookbook/packages/install', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pip: LOCAL_LLM_ROUTER_PIP }),
    });
    const data = await r.json();
    if (data.ok) {
      _localLlmRouterAvailable = true;
      if (uiModule && uiModule.showToast) uiModule.showToast(`${LOCAL_LLM_ROUTER_NAME} installed`);
      try { document.dispatchEvent(new CustomEvent('odysseus:local-llm-router-installed')); } catch {}
      updateModelPicker();
      return true;
    }
    const err = data.error || 'Install failed (admin only)';
    if (uiModule && uiModule.showToast) uiModule.showToast(err);
  } catch (_) {
    if (uiModule && uiModule.showToast) uiModule.showToast('Install failed');
  }
  return false;
}

function _modelExists(modelId, url) {
  // Keep Auto selected in the composer even when models aren't ready yet.
  if (modelId === LOCAL_LLM_ROUTER_AUTO_MODEL_ID) return true;
  if (!modelId || !window.modelsModule || !window.modelsModule.getCachedItems) return false;
  const items = window.modelsModule.getCachedItems() || [];
  if (!items.length) return true;
  const targetUrl = (url || '').replace(/\/+$/, '');
  return items.some(item => {
    if (item.offline) return false;
    const itemUrl = (item.url || '').replace(/\/+$/, '');
    const models = (item.models || []).concat(item.models_extra || []);
    return models.includes(modelId) && (!targetUrl || itemUrl === targetUrl);
  });
}

/**
 * Initialize the model picker dropdown.
 * @param {Object} deps
 * @param {function} deps.getCurrentSessionId - returns current session ID
 * @param {function} deps.getSessions - returns sessions array
 * @param {function} deps.getPendingChat - returns _pendingChat object
 * @param {function} deps.setPendingChat - sets _pendingChat object
 * @param {function} deps.createDirectChat - creates a new direct chat session
 */
export function initModelPicker(deps) {
  _deps = deps;
  _refreshLocalLlmRouterStatus();
  _initModelPickerDropdown();
}

document.addEventListener('odysseus:local-llm-router-installed', () => {
  _localLlmRouterAvailable = true;
  updateModelPicker();
});

function _initModelPickerDropdown() {
  const wrap = document.getElementById('model-picker-wrap');
  const btn = document.getElementById('model-picker-btn');
  const menu = document.getElementById('model-picker-menu');
  const search = document.getElementById('model-picker-search');
  const listEl = document.getElementById('model-picker-list');
  const searchRow = menu ? menu.querySelector('.model-picker-search-row') : null;
  if (!wrap || !btn || !menu || !search || !listEl) return;

  function _close() {
    if (menu.classList.contains('hidden')) return;
    // Restore scroll button
    const _scrollBtn = document.getElementById('scroll-bottom-btn');
    if (_scrollBtn) _scrollBtn.style.display = '';
    menu.classList.add('closing');
    menu.addEventListener('animationend', function _onDone() {
      menu.removeEventListener('animationend', _onDone);
      menu.classList.remove('closing');
      menu.classList.add('hidden');
      search.value = '';
    }, { once: true });
    // Fallback if animationend doesn't fire
    setTimeout(() => {
      if (!menu.classList.contains('hidden')) {
        menu.classList.remove('closing');
        menu.classList.add('hidden');
        search.value = '';
      }
    }, 200);
  }

  function _openPickerShortcut(kind) {
    _close();
    try {
      if (kind === 'cookbook') {
        if (window.cookbookModule && typeof window.cookbookModule.open === 'function') {
          window.cookbookModule.open();
        } else {
          const btn = document.getElementById('tool-cookbook-btn') || document.getElementById('rail-cookbook');
          if (btn) btn.click();
          else location.hash = '#cookbook';
        }
      } else if (kind === 'settings') {
        if (settingsModule && typeof settingsModule.open === 'function') settingsModule.open();
      } else if (window.adminModule && typeof window.adminModule.open === 'function') {
        window.adminModule.open('services');
      } else if (settingsModule && typeof settingsModule.open === 'function') {
        settingsModule.open('services');
      }
    } catch (_) {}
  }

  // Local endpoint health ??? only probed for LOCAL endpoints, since
  // cloud APIs are essentially always up. Cached briefly on the
  // server side too (8s TTL). Picker opens trigger a refresh.
  let _localProbe = {};            // {endpoint_id: {alive, latency_ms, error}}
  let _localProbeFetchedAt = 0;
  const _LOCAL_PROBE_TTL_MS = 5000;

  async function _refreshLocalProbe() {
    const now = Date.now();
    if (now - _localProbeFetchedAt < _LOCAL_PROBE_TTL_MS) return;
    _localProbeFetchedAt = now;
    try {
      const r = await fetch('/api/model-endpoints/probe-local', { credentials: 'same-origin' });
      if (r.ok) _localProbe = (await r.json()) || {};
    } catch (_) { /* leave stale data; picker still works */ }
  }

  function _getAllModels() {
    const items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
    const result = [];
    const seen = new Set();
    items.forEach(item => {
      // Previously: offline endpoints were skipped entirely, so a server
      // that briefly went down disappeared from the picker ??? confusing
      // when the user can still see it (offline-tagged) in Settings.
      // Now: include offline-endpoint models too but flag them
      // `stale: true` so the row renderer dims them + shows the offline
      // pill. The user can still click and try anyway (matches the
      // existing "local server appears offline" path on line 301).
      const epOffline = !!item.offline;
      const allModels = (item.models || []).concat(item.models_extra || []);
      const allDisplay = (item.models_display || []).concat(item.models_extra_display || []);
      // Mark local endpoints whose live probe failed.
      const probeResult = item.endpoint_id ? _localProbe[item.endpoint_id] : null;
      const isLocalDead = !!(probeResult && probeResult.alive === false);
      allModels.forEach((mid, i) => {
        // Deduplicate by model ID ??? prefer ONLINE endpoint entries over
        // offline duplicates so the user gets a working endpoint first
        // when the same model is exposed by both.
        if (seen.has(mid)) return;
        seen.add(mid);
        result.push({
          mid,
          display: (allDisplay[i] || mid).split('/').pop(),
          url: item.url,
          endpointId: item.endpoint_id,
          epName: item.endpoint_name || '',
          providerText: [
            item.endpoint_name || '',
            item.category || '',
            item.host || '',
            item.url || '',
          ].filter(Boolean).join(' '),
          stale: isLocalDead || epOffline,
          staleReason: epOffline
            ? (item.ping_error || 'endpoint offline')
            : (isLocalDead ? (probeResult.error || 'not responding') : ''),
          offline: epOffline,
        });
      });
    });
    const autoEntry = _localLlmRouterPickerEntry();
    if (autoEntry && !seen.has(autoEntry.mid)) {
      result.push(autoEntry);
    }
    return sortModelObjects(result);
  }

  // ?????? Provider display names and grouping ??????
  const _PROVIDER_NAMES = {
    '01-ai': 'Yi', 'abacusai': 'Abacus AI', 'adept': 'Adept',
    'ai21': 'AI21 Labs', 'ai21labs': 'AI21 Labs', 'aion-labs': 'Aion Labs',
    'aisingapore': 'AI Singapore', 'allenai': 'Allen AI', 'amazon': 'Amazon',
    'anthracite-org': 'Anthracite', 'anthropic': 'Anthropic', 'arcee-ai': 'Arcee AI',
    'baai': 'BAAI', 'baidu': 'Baidu', 'bigcode': 'BigCode',
    'black-forest-labs': 'Black Forest Labs', 'bytedance': 'ByteDance',
    'bytedance-seed': 'ByteDance', 'cognitivecomputations': 'Cognitive Computations',
    'cohere': 'Cohere', 'databricks': 'Databricks', 'deepcogito': 'DeepCogito',
    'deepseek': 'DeepSeek', 'deepseek-ai': 'DeepSeek', 'essentialai': 'Essential AI',
    'google': 'Google', 'gryphe': 'Gryphe', 'ibm': 'IBM', 'other': 'Other',
    'ibm-granite': 'IBM Granite', 'inception': 'Inception',
    'inclusionai': 'Inclusion AI', 'inflection': 'Inflection',
    'kwaipilot': 'KwaiPilot', 'liquid': 'Liquid AI', 'mancer': 'Mancer',
    'meta': 'Llama', 'meta-llama': 'Llama', 'microsoft': 'Microsoft',
    'minimax': 'MiniMax', 'minimaxai': 'MiniMax', 'mistralai': 'Mistral',
    'moonshotai': 'Moonshot', 'morph': 'Morph', 'nex-agi': 'Nex AGI',
    'nousresearch': 'Nous Research', 'nv-mistralai': 'NVIDIA x Mistral',
    'nvidia': 'NVIDIA', 'openai': 'OpenAI', 'openrouter': 'OpenRouter',
    'perceptron': 'Perceptron', 'perplexity': 'Perplexity', 'poolside': 'Poolside',
    'prime-intellect': 'Prime Intellect', 'qwen': 'Qwen', 'rekaai': 'Reka',
    'relace': 'Relace', 'sao10k': 'Sao10k', 'sarvamai': 'Sarvam AI',
    'snowflake': 'Snowflake', 'stepfun': 'StepFun', 'stepfun-ai': 'StepFun',
    'stockmark': 'Stockmark', 'switchpoint': 'SwitchPoint', 'tencent': 'Tencent',
    'thedrummer': 'TheDrummer', 'undi95': 'Undi95', 'upstage': 'Upstage',
    'writer': 'Writer', 'x-ai': 'xAI', 'xiaomi': 'Xiaomi',
    'z-ai': 'Zhipu', 'zyphra': 'Zyphra',
    '~anthropic': 'Anthropic', '~google': 'Google',
    '~moonshotai': 'Moonshot', '~openai': 'OpenAI',
  };
  const _PROVIDER_ALIAS = {
    'meta-llama': 'meta', 'deepseek': 'deepseek-ai', 'minimaxai': 'minimax',
    'stepfun-ai': 'stepfun', 'ai21labs': 'ai21', 'ibm-granite': 'ibm',
    'bytedance-seed': 'bytedance', '~anthropic': 'anthropic',
    '~google': 'google', '~moonshotai': 'moonshotai', '~openai': 'openai',
  };
  function _providerDisplayName(slug) {
    return _PROVIDER_NAMES[slug] || slug.charAt(0).toUpperCase() + slug.slice(1).replace(/-/g, ' ');
  }
  function _providerSlug(mid) {
    const slash = mid.indexOf('/');
    let slug = slash > 0 ? mid.substring(0, slash) : 'other';
    return _PROVIDER_ALIAS[slug] || slug;
  }
  const _collapsedProviders = new Set(_loadList('odysseus-model-collapsed'));
  let _justExpandedProvider = null;

  function _populate(filter) {
    listEl.innerHTML = '';
    const all = _getAllModels();
    const q = (filter || '').trim().toLowerCase();
    const hasAnyModel = all.length > 0;
    listEl.classList.toggle('is-empty', !hasAnyModel);
    menu.classList.toggle('no-models', !hasAnyModel);
    if (search) {
      search.placeholder = hasAnyModel ? 'Search models???' : 'No models connected';
    }
    if (searchRow) {
      searchRow.classList.toggle('searching', !!q);
    }

    if (!hasAnyModel) return; // collapsed empty list ??? nothing to render

    // Unique lookup so Recent/Favorites (stored as bare model IDs) can be
    // resolved back to full model objects; drops anything no longer offered.
    const byId = new Map();
    all.forEach(m => { if (!byId.has(m.mid)) byId.set(m.mid, m); });

    const favs = _loadFavorites();

    function _addSection(label) {
      const el = document.createElement('div');
      el.className = 'mp-section-label';
      el.textContent = label;
      listEl.appendChild(el);
    }
    function _addEmpty(text) {
      const empty = document.createElement('div');
      empty.className = 'model-switch-empty';
      empty.textContent = text;
      listEl.appendChild(empty);
    }
    function _addRow(m) {
      const row = document.createElement('div');
      row.className = 'model-switch-item';
      const isAuto = m.isAuto || m.mid === LOCAL_LLM_ROUTER_AUTO_MODEL_ID;
      if (isAuto) {
        row.classList.add('model-switch-auto');
        const autoBaseTitle = m.pickable === false
          ? (m.tooltip
            || (m.unavailableKind === 'router' ? AUTO_SELECT_NEEDS_ROUTER : AUTO_SELECT_NEEDS_MODELS))
          : (m.tooltip || AUTO_SELECT_TOOLTIP);
        row.title = autoBaseTitle;
        if (m.pickable === false) {
          row.classList.add('model-switch-auto-unavailable');
          row.style.opacity = '0.55';
        }
        row.addEventListener('mouseenter', () => {
          _applyLlrStatusTooltip(row, autoBaseTitle, m.url);
        });
        _applyLlrStatusTooltip(row, autoBaseTitle, m.url);
      }
      if (m.stale) {
        row.classList.add('model-switch-stale');
        row.style.opacity = '0.45';
        row.title = `Local server appears offline: ${m.staleReason}. Click to try anyway, or relaunch in Cookbook.`;
      }
      if (isAuto) {
        const logoSpan = document.createElement('span');
        logoSpan.className = 'provider-logo';
        logoSpan.style.opacity = '0.6';
        logoSpan.innerHTML = AUTO_ICON_SVG;
        row.appendChild(logoSpan);
      } else {
        const _mlogo = providerLogo(m.mid);
        if (_mlogo) {
          const logoSpan = document.createElement('span');
          logoSpan.className = 'provider-logo';
          logoSpan.style.opacity = '0.6';
          logoSpan.innerHTML = _mlogo;
          row.appendChild(logoSpan);
        }
      }
      const nameSpan = document.createElement('span');
      nameSpan.className = 'mp-model-name';
      nameSpan.textContent = m.display;
      row.appendChild(nameSpan);
      if (m.stale) {
        const badge = document.createElement('span');
        badge.className = 'model-switch-stale-badge';
        badge.textContent = 'offline';
        badge.style.cssText = 'font-size:10px;opacity:0.7;padding:1px 6px;border:1px solid var(--border);border-radius:8px;margin-left:6px;';
        row.appendChild(badge);
      }
      const epSpan = document.createElement('span');
      epSpan.className = 'model-switch-ep';
      // Don't show endpoint name if it matches the model name (local self-hosted)
      const _epDisplay = m.epName && !m.display.toLowerCase().includes(m.epName.toLowerCase().split('/').pop()) ? m.epName : '';
      epSpan.textContent = _epDisplay;
      row.appendChild(epSpan);

      if (!isAuto) {
        // Inline favorite dot ??? toggles favorite, never picks the model.
        const favDot = document.createElement('button');
        favDot.type = 'button';
        favDot.className = 'mp-fav-dot' + (favs.includes(m.mid) ? ' active' : '');
        favDot.textContent = '???';
        const _setFavState = (on) => {
          favDot.classList.toggle('active', on);
          favDot.title = on ? 'Remove from favorites' : 'Add to favorites';
          favDot.setAttribute('aria-label', on ? 'Remove from favorites' : 'Add to favorites');
          favDot.setAttribute('aria-pressed', on ? 'true' : 'false');
        };
        _setFavState(favs.includes(m.mid));
        favDot.addEventListener('click', (e) => {
          e.stopPropagation();
          const nowFav = _toggleFavorite(m.mid);
          _setFavState(nowFav);
          favDot.classList.remove('pulse');
          void favDot.offsetWidth;
          favDot.classList.add('pulse');
          const idx = favs.indexOf(m.mid);
          if (nowFav && idx < 0) favs.push(m.mid);
          else if (!nowFav && idx >= 0) favs.splice(idx, 1);
          if (uiModule && uiModule.showToast) uiModule.showToast(nowFav ? 'Favorited' : 'Unfavorited');
          if (!q) {
            const st = listEl.scrollTop;
            _populate('');
            listEl.scrollTop = st;
          }
        });
        row.appendChild(favDot);
      }

      row.addEventListener('click', () => {
        if (isAuto && m.pickable === false) {
          if (m.unavailableKind === 'router') {
            _installLocalLlmRouter();
          } else if (uiModule && uiModule.showToast) {
            uiModule.showToast(m.tooltip || AUTO_SELECT_NEEDS_MODELS);
          }
          return;
        }
        _pick(m);
      });
      listEl.appendChild(row);
    }

    // ?????? Search mode: flat, filtered results across the whole catalog ??????
    if (q) {
      const matches = all.filter(m => {
        const provName = _providerDisplayName(_providerSlug(m.mid)).toLowerCase();
        return [m.mid, m.display, m.epName, m.providerText, provName]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
      if (!matches.length) _addEmpty('No matching models');
      else matches.forEach(_addRow);
      return;
    }

    // ?????? Browse mode: Favorites (manual) + Recent (auto), with dedupe. ??????
    // Rules:
    //   1. Never list the same model twice in the dropdown. Favorites
    //      win over Recent (if you favorited it, that's where it
    //      belongs ??? Recent shouldn't show it again as duplicate).
    //   2. Small catalogs (??? BROWSE_ALL_LIMIT total) skip the Recent
    //      section entirely ??? when there's only ~10 models, the whole
    //      list fits below as "All models" and a separate Recent
    //      section just duplicates rows.
    const shown = new Set();
    const _isAutoEntry = (m) => m && (m.isAuto || m.mid === LOCAL_LLM_ROUTER_AUTO_MODEL_ID);
    const favModels = favs.map(id => byId.get(id)).filter(Boolean).filter(m => !_isAutoEntry(m));
    if (favModels.length) {
      _addSection('Favorites');
      favModels.forEach(m => { shown.add(m.mid); _addRow(m); });
    }
    // Recent: only render when the catalog is big enough that surfacing
    // a recency shortlist is actually useful, AND only models that
    // aren't already in Favorites (dedupe).
    if (all.length > BROWSE_ALL_LIMIT) {
      const recentModels = _loadRecent()
        .map(id => byId.get(id))
        .filter(Boolean)
        .filter(m => !shown.has(m.mid) && !_isAutoEntry(m))
        .slice(0, RECENT_MAX);
      if (recentModels.length) {
        _addSection('Recent');
        recentModels.forEach(m => { shown.add(m.mid); _addRow(m); });
      }
    }

    // Small catalogs: still list everything so users aren't forced to search.
    if (all.length <= BROWSE_ALL_LIMIT) {
      const rest = all.filter(m => !shown.has(m.mid));
      const autoEntry = rest.find(_isAutoEntry);
      const restModels = rest.filter(m => !_isAutoEntry(m));
      if (restModels.length) {
        if (shown.size) _addSection('All models');
        restModels.forEach(_addRow);
      }
      if (autoEntry) {
        _addSection('Other');
        _addRow(autoEntry);
      }
    } else {
      // Large catalog: show provider groups with collapsible sections.
      const rest = all.filter(m => !shown.has(m.mid));
      const groups = new Map();
      rest.forEach(m => {
        const slug = _providerSlug(m.mid);
        if (!groups.has(slug)) groups.set(slug, []);
        groups.get(slug).push(m);
      });
      const sorted = [...groups.keys()].sort((a, b) =>
        _providerDisplayName(a).localeCompare(_providerDisplayName(b)));

      sorted.forEach(provider => {
        const models = groups.get(provider).slice().sort((a, b) => {
          if (_isAutoEntry(a)) return 1;
          if (_isAutoEntry(b)) return -1;
          return compareModelObjects(a, b);
        });
        const isCollapsed = _collapsedProviders.has(provider);
        const header = document.createElement('div');
        header.className = 'mp-provider-header';
        header.innerHTML =
          `<svg class="mp-provider-chevron${isCollapsed ? ' collapsed' : ''}" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`
          + `<span class="mp-provider-name">${_providerDisplayName(provider)}</span>`
          + `<span class="mp-provider-count">${models.length}</span>`;
        header.addEventListener('click', (e) => {
          e.stopPropagation();
          if (_collapsedProviders.has(provider)) {
            _collapsedProviders.delete(provider);
            _justExpandedProvider = provider;
          } else {
            _collapsedProviders.add(provider);
            _justExpandedProvider = null;
          }
          _saveList('odysseus-model-collapsed', [..._collapsedProviders]);
          const st = listEl.scrollTop;
          _populate('');
          listEl.scrollTop = st;
        });
        listEl.appendChild(header);
        if (!isCollapsed) {
          const group = document.createElement('div');
          group.className = 'mp-provider-group' + (_justExpandedProvider === provider ? ' mp-just-expanded' : '');
          models.forEach(m => {
            _addRow(m);
            // Move the just-appended row into the group container
            group.appendChild(listEl.lastElementChild);
          });
          listEl.appendChild(group);
          if (_justExpandedProvider === provider) _justExpandedProvider = null;
        }
      });
    }
  }

  async function _pick(m) {
    const target = _resolvePickTarget(m);
    if (target.error) {
      if (uiModule && uiModule.showError) uiModule.showError(target.error);
      else if (uiModule && uiModule.showToast) uiModule.showToast(target.error);
      return;
    }
    m = target;

    const currentSessionId = _deps.getCurrentSessionId();
    const _pendingChat = _deps.getPendingChat();

    // Remember this pick so it surfaces under "Recent" next time the picker
    // opens ??? the whole point of quick-switch.
    if (m && m.mid && m.mid !== LOCAL_LLM_ROUTER_AUTO_MODEL_ID) _pushRecent(m.mid);

    // Broadcast immediately so listeners (e.g. the tour) can advance without
    // waiting for the async session-create/PATCH that follows.
    try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}

    // Blur search input before closing to dismiss keyboard on mobile
    if (document.activeElement) document.activeElement.blur();
    _close();
    // Refocus main textarea ??? skip on mobile to avoid keyboard bounce
    if (window.innerWidth >= 768) {
      const _ta = document.getElementById('message');
      if (_ta) setTimeout(() => _ta.focus(), 50);
    }
    if (!currentSessionId && _pendingChat) {
      // Already have a deferred session ??? just update the model
      _deps.setPendingChat({ url: m.url, modelId: m.mid, endpointId: m.endpointId });
      // Header stays as session name ??? model switch only updates picker
      updateModelPicker();
      uiModule.showToast(`Using ${m.display}`);
      return;
    } else if (!currentSessionId) {
      // No session yet ??? create one with this model
      await _deps.createDirectChat(m.url, m.mid, m.endpointId);
    } else {
      // Existing session ??? PATCH model + endpoint
      try {
        const patched = await _patchSessionModel(currentSessionId, m);
        if (!patched.ok) {
          if (uiModule && uiModule.showError) uiModule.showError(patched.error);
          return;
        }
        const sessions = _deps.getSessions();
        const s = sessions.find(x => x.id === currentSessionId);
        if (s) {
          s.model = m.mid;
          s.endpoint_url = m.url || s.endpoint_url;
        }
      } catch (e) {
        if (uiModule && uiModule.showError) uiModule.showError('Failed to set model: ' + e);
        return;
      }
    }
    // Update picker visibility ??? model is now set
    updateModelPicker();
    uiModule.showToast(`Using ${m.display}`);
  }

  document.addEventListener('odysseus:auto-select-model', async (e) => {
    const detail = (e && e.detail) || {};
    const currentSessionId = _deps.getCurrentSessionId();
    const sessions = _deps.getSessions();
    const current = sessions.find(x => x.id === currentSessionId);
    const pending = _deps.getPendingChat();
    if ((current && current.model) || (pending && pending.modelId)) return;

    if (window.modelsModule && window.modelsModule.refreshModels) {
      try { await window.modelsModule.refreshModels(true); } catch (_) {}
    }
    const items = window.modelsModule && window.modelsModule.getCachedItems ? window.modelsModule.getCachedItems() : [];
    const targetEndpointId = detail.endpointId ? String(detail.endpointId) : '';
    const targetModel = detail.modelId || '';
    let match = null;
    for (const item of items) {
      if (item.offline) continue;
      if (targetEndpointId && String(item.endpoint_id || '') !== targetEndpointId) continue;
      const models = (item.models || []).concat(item.models_extra || []);
      const displays = (item.models_display || []).concat(item.models_extra_display || []);
      const idx = targetModel ? models.indexOf(targetModel) : (models.length ? 0 : -1);
      if (idx >= 0) {
        match = {
          mid: models[idx],
          display: (displays[idx] || models[idx]).split('/').pop(),
          url: item.url || detail.url || '',
          endpointId: item.endpoint_id || detail.endpointId || '',
          epName: item.endpoint_name || detail.endpointName || '',
          providerText: [item.endpoint_name || detail.endpointName || '', item.url || detail.url || ''].filter(Boolean).join(' '),
        };
        break;
      }
    }
    if (!match && detail.modelId && detail.url) {
      match = {
        mid: detail.modelId,
        display: String(detail.modelId).split('/').pop(),
        url: detail.url,
        endpointId: detail.endpointId || '',
        epName: detail.endpointName || '',
        providerText: [detail.endpointName || '', detail.url || ''].filter(Boolean).join(' '),
      };
    }
    if (match) await _pick(match);
  });

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden') || menu.classList.contains('closing')) {
      // Force-clear any in-progress close animation
      menu.classList.remove('closing', 'hidden');
      _refreshLocalLlmRouterStatus().finally(() => _populate(''));
      if (window.modelsModule && window.modelsModule.refreshModels) {
        window.modelsModule.refreshModels().then(() => {
          if (!menu.classList.contains('hidden')) _populate(search.value || '');
          updateModelPicker();
        }).catch(() => {});
      }
      // Kick off a local-endpoint probe ??? when it returns, re-render
      // the list so stale local servers get dimmed. Cloud entries
      // aren't probed; they stay visible.
      _refreshLocalProbe().then(() => {
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
      });
      if (window.innerWidth >= 768) search.focus();
      // Hide scroll button so it doesn't overlap
      const _scrollBtn = document.getElementById('scroll-bottom-btn');
      if (_scrollBtn) _scrollBtn.style.display = 'none';
    } else {
      _close();
    }
  });

  search.addEventListener('input', () => _populate(search.value));
  search.addEventListener('click', (e) => e.stopPropagation());
  search.addEventListener('keydown', (e) => {
    _handlePickerKeydown(e, listEl, '.model-switch-item', _close);
  });
  const addModelsBtn = document.getElementById('model-picker-add-models-btn');
  if (addModelsBtn) {
    addModelsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _openPickerShortcut('models');
    });
  }
  document.addEventListener('click', (e) => {
    if (!menu.classList.contains('hidden') && !menu.contains(e.target) && e.target !== btn) {
      _close();
    }
  });
}

/**
 * Update the model picker label to show the current model.
 * Always visible ??? shows current model name or "Select model" if none.
 * Called after selectSession, createDirectChat, and model switch.
 */
export function updateModelPicker() {
  if (!_deps) return;
  const label = document.getElementById('model-picker-label');
  if (!label) return;
  // Hide model picker when group chat is active
  const wrap = document.getElementById('model-picker-wrap');
  if (window.groupModule && window.groupModule.isActive()) {
    if (wrap) { wrap.style.display = 'none'; }
    return;
  }
  // Reset inline visibility (may have been hidden by typing in previous session)
  if (wrap) {
    wrap.style.display = '';
    wrap.style.opacity = '';
    wrap.style.pointerEvents = '';
  }
  const currentSessionId = _deps.getCurrentSessionId();
  const sessions = _deps.getSessions();
  const _pendingChat = _deps.getPendingChat();
  const s = sessions.find(x => x.id === currentSessionId);
  let modelId = null;
  if (s && s.model) {
    modelId = s.model;
    if (!_modelExists(modelId, s.endpoint_url || '')) {
      modelId = null;
    }
  } else if (_pendingChat && _pendingChat.modelId) {
    modelId = _pendingChat.modelId;
    if (!_modelExists(modelId, _pendingChat.url || '')) {
      _deps.setPendingChat(null);
      modelId = null;
    }
  }
  // SECURITY: deliberately NOT auto-injecting `odysseus-model-favorites[0]`
  // here. localStorage favorites are per-browser, not per-user, so on a
  // shared browser the previous account's first favorited model would
  // silently pre-populate the chatbox of the next user that signed in. If
  // we have no session model and no pending-chat pick, fall through to
  // the "Select model" placeholder below.

  // Check if selected model is still available ??? fall back ONLY for pending chats with no user selection
  // Never override an existing session's model ??? the user explicitly chose it
  if (modelId && modelId !== LOCAL_LLM_ROUTER_AUTO_MODEL_ID && !currentSessionId && _pendingChat && window.modelsModule && window.modelsModule.getCachedItems) {
    const items = window.modelsModule.getCachedItems();
    const allAvailable = [];
    items.forEach(item => {
      if (item.offline) return;
      (item.models || []).concat(item.models_extra || []).forEach(m => allAvailable.push(m));
    });
    if (allAvailable.length > 0 && !allAvailable.includes(modelId)) {
      // Model no longer available ??? switch to first available
      const fallback = items.find(item => !item.offline && (item.models || []).length > 0);
      if (fallback) {
        modelId = fallback.models[0];
        _deps.setPendingChat({ url: fallback.url, modelId, endpointId: fallback.endpoint_id });
      }
    }
  }
  if (!modelId && !_autoSelectingDefault && modelId !== LOCAL_LLM_ROUTER_AUTO_MODEL_ID && window.modelsModule && window.modelsModule.getCachedItems) {
    const items = window.modelsModule.getCachedItems();
    const first = items.find(item => !item.offline && ((item.models || []).length || (item.models_extra || []).length));
    if (first) {
      const models = (first.models || []).concat(first.models_extra || []);
      modelId = models[0];
      if (!currentSessionId) {
        _deps.setPendingChat({ url: first.url, modelId, endpointId: first.endpoint_id });
      } else {
        if (s) { s.model = modelId; s.endpoint_url = first.url; }
        _autoSelectingDefault = true;
        const fd = new FormData();
        fd.append('model', modelId);
        if (first.endpoint_id) fd.append('endpoint_id', String(first.endpoint_id));
        if (first.url) fd.append('endpoint_url', first.url);
        fetch(`${API_BASE}/api/session/${currentSessionId}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          body: fd,
        })
          .catch(() => {})
          .finally(() => { _autoSelectingDefault = false; });
      }
    }
  }

  const isAutoLabel = modelId === LOCAL_LLM_ROUTER_AUTO_MODEL_ID;
  if (!isAutoLabel) {
    _lastAutoResolvedModel = '';
    _lastAutoRouteReasons = [];
  }

  const _shortTag = (id) => (id ? id.split('/').pop() : '');
  let displayName;
  if (isAutoLabel) {
    displayName = _lastAutoResolvedModel
      ? `${AUTO_SELECT_LABEL} \u2192 ${_shortTag(_lastAutoResolvedModel)}`
      : AUTO_SELECT_LABEL;
  } else {
    displayName = modelId ? _shortTag(modelId) : 'Select model';
  }
  const logoModel = isAutoLabel && _lastAutoResolvedModel ? _lastAutoResolvedModel : modelId;
  const logo = logoModel && logoModel !== LOCAL_LLM_ROUTER_AUTO_MODEL_ID ? providerLogo(logoModel) : null;
  if (logo) {
    label.innerHTML = '<span class="model-picker-logo">' + logo + '</span> ' + displayName;
  } else {
    label.textContent = displayName;
  }
  let autoTitle = AUTO_SELECT_TOOLTIP;
  if (isAutoLabel) {
    const readiness = getLocalLlmRouterReadiness();
    if (!readiness.ok) autoTitle = readiness.message;
    else if (_lastAutoResolvedModel) {
      autoTitle = `${AUTO_SELECT_LABEL} \u2192 ${_lastAutoResolvedModel}`;
      if (_lastAutoRouteReasons.length) {
        autoTitle += '\n' + _lastAutoRouteReasons.join(' | ');
      }
    }
    label.title = autoTitle;
    const pickerBtn = document.getElementById('model-picker-btn');
    if (pickerBtn) pickerBtn.title = autoTitle;
    const epUrl = (s && s.endpoint_url) || (_pendingChat && _pendingChat.url)
      || (readiness.ok && readiness.anchor?.url) || _llrEndpointUrl('');
    _applyLlrStatusTooltip(label, autoTitle, epUrl);
    if (pickerBtn) _applyLlrStatusTooltip(pickerBtn, autoTitle, epUrl);
    return;
  }
  label.title = '';
  const pickerBtn = document.getElementById('model-picker-btn');
  if (pickerBtn) pickerBtn.title = '';
}
