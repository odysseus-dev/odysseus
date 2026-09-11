// Model Picker — chatbox model selector dropdown
// Extracted from sessions.js

import { providerLogo } from './providers.js';
import uiModule from './ui.js';
import settingsModule from './settings.js';
import spinnerModule from './spinner.js';

const API_BASE = window.location.origin;

// ── Recent + Favorites persistence ──
// Recent is auto-tracked (last 5 picks, most-recent-first) and lives in its
// own key. Favorites is the SAME key the sidebar Models section uses, so a
// favorite toggled here shows up there and vice-versa.
const RECENT_KEY = 'odysseus-model-recent';
const FAVORITES_KEY = 'odysseus-model-favorites';
const RECENT_MAX = 5;
// Catalogs at or below this size are small enough that hiding everything
// behind search would be a regression — keep listing them in browse mode.
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

function _pickerModelKey(m) {
  if (!m) return '';
  return `${m.endpointId || m.url || m.epName || 'model'}::${m.mid || ''}`;
}

// ── Shared keyboard nav for model pickers ──
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
let _defaultChatPickInFlight = false;
let _defaultPendingSeq = 0;
let _curatedRoutes = [{ id: 'automatic', label: 'Automatic' }];
let _curatedFetched = false;

async function _refreshCuratedRoutes() {
  try {
    const response = await fetch(`${API_BASE}/api/chat-routes`, { credentials: 'same-origin' });
    if (!response.ok) return;
    const data = await response.json();
    const routes = Array.isArray(data && data.routes) ? data.routes : [];
    _curatedRoutes = routes.length ? routes.map(route => ({
      id: String(route.id || '').trim(),
      label: String(route.label || route.id || '').trim(),
    })).filter(route => route.id) : [{ id: 'automatic', label: 'Automatic' }];
    if (!_curatedRoutes.length) _curatedRoutes = [{ id: 'automatic', label: 'Automatic' }];
    _curatedFetched = true;
  } catch (_) { /* picker still shows Automatic */ }
}

function _automaticRoute() {
  return { url: '', modelId: 'automatic', endpointId: '', source: 'default' };
}

function _modelExists(modelId) {
  if (!modelId) return false;
  return _curatedRoutes.some(route => route.id === modelId);
}

async function _ensureDefaultPendingChat() {
  if (!_deps || _defaultChatPickInFlight) return;
  if (_deps.getCurrentSessionId && _deps.getCurrentSessionId()) return;
  const pending = _deps.getPendingChat && _deps.getPendingChat();
  if (pending && pending.modelId) return;
  _defaultChatPickInFlight = true;
  const seq = ++_defaultPendingSeq;
  try {
    await _refreshCuratedRoutes();
    if (seq !== _defaultPendingSeq) return;
    const latest = _deps.getPendingChat && _deps.getPendingChat();
    if (latest && latest.modelId && latest.source !== 'default' && latest.source !== 'fallback') return;
    _deps.setPendingChat(_automaticRoute());
    updateModelPicker();
  } finally {
    _defaultChatPickInFlight = false;
  }
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
  _initModelPickerDropdown();
}

function _initModelPickerDropdown() {
  const wrap = document.getElementById('model-picker-wrap');
  const btn = document.getElementById('model-picker-btn');
  const menu = document.getElementById('model-picker-menu');
  const search = document.getElementById('model-picker-search');
  const listEl = document.getElementById('model-picker-list');
  const searchRow = menu ? menu.querySelector('.model-picker-search-row') : null;
  const refreshBtn = document.getElementById('model-picker-refresh-btn');
  if (!wrap || !btn || !menu || !search || !listEl) return;
  if (wrap.dataset.modelPickerBound === '1') return;
  wrap.dataset.modelPickerBound = '1';

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

  let _pickerLoading = false;
  let _pickerLoadSeq = 0;

  function _getAllModels() {
    return _curatedRoutes.map(route => ({
      key: route.id,
      mid: route.id,
      display: route.label || route.id,
      url: '',
      endpointId: '',
      epName: '',
      category: 'curated',
      providerText: route.label || route.id,
      stale: false,
      staleReason: '',
      offline: false,
    }));
  }

  function _hasModelCache() {
    return _curatedFetched && _curatedRoutes.length > 0;
  }

  function _renderLoading(text = 'Loading models…') {
    listEl.innerHTML = '';
    listEl.classList.remove('is-empty');
    listEl.classList.add('is-loading');
    menu.classList.remove('no-models');
    if (search) search.placeholder = text;
    let row = null;
    try {
      row = spinnerModule.createLoadingRow(text, 15);
    } catch (_) {
      row = document.createElement('div');
      row.className = 'model-switch-empty';
      row.textContent = text;
    }
    row.classList.add('model-picker-loading-row');
    listEl.appendChild(row);
  }

  async function _refreshPickerModels({ force = false, showLoading = false } = {}) {
    const seq = ++_pickerLoadSeq;
    _pickerLoading = true;
    if (showLoading) _renderLoading(force ? 'Refreshing routes…' : 'Loading routes…');
    try {
      await _refreshCuratedRoutes();
    } finally {
      if (seq === _pickerLoadSeq) {
        _pickerLoading = false;
        listEl.classList.remove('is-loading');
      }
    }
  }

  // ── Provider display names and grouping ──
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
    'google': 'Google', 'gryphe': 'Gryphe', 'ibm': 'IBM',
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
  function _providerGroupKey(m) {
    if (m && m.category && m.category !== 'local' && m.epName) {
      return `~endpoint:${m.epName}`;
    }
    return _providerSlug((m && m.mid) || '');
  }
  function _providerGroupName(key) {
    if (String(key || '').startsWith('~endpoint:')) return String(key).slice('~endpoint:'.length);
    return _providerDisplayName(key);
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
    listEl.classList.remove('is-loading');
    const all = _getAllModels();
    const q = (filter || '').trim().toLowerCase();
    const hasAnyModel = all.length > 0;
    listEl.classList.toggle('is-empty', !hasAnyModel);
    menu.classList.toggle('no-models', !hasAnyModel);
    if (search) {
      search.placeholder = hasAnyModel ? 'Search models…' : 'No models connected';
    }
    if (searchRow) {
      searchRow.classList.toggle('searching', !!q);
    }

    if (!hasAnyModel) return; // collapsed empty list — nothing to render

    // Unique lookup so Recent/Favorites (stored as bare model IDs) can be
    // resolved back to full model objects; drops anything no longer offered.
    const byId = new Map();
    const byKey = new Map();
    all.forEach(m => {
      const key = _pickerModelKey(m);
      if (key && !byKey.has(key)) byKey.set(key, m);
      if (!byId.has(m.mid)) byId.set(m.mid, m);
    });

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
      if (m.stale) {
        row.classList.add('model-switch-stale');
        row.style.opacity = '0.45';
        row.title = `Local server appears offline: ${m.staleReason}. Click to try anyway, or relaunch in Cookbook.`;
      }
      const _mlogo = providerLogo(m.mid);
      if (_mlogo) {
        const logoSpan = document.createElement('span');
        logoSpan.className = 'provider-logo';
        logoSpan.style.opacity = '0.6';
        logoSpan.innerHTML = _mlogo;
        row.appendChild(logoSpan);
      }
      const nameSpan = document.createElement('span');
      nameSpan.className = 'mp-model-name';
      nameSpan.textContent = m.display;
      // Long model names are clipped with ellipsis — expose the full name on
      // hover so the suffix/variant tag is still discoverable (#1982).
      nameSpan.title = m.display;
      row.appendChild(nameSpan);
      // Offline state is already conveyed by the row's reduced opacity —
      // a redundant "offline" pill on top of that just added clutter.
      // (Class kept on `row` so the opacity rule still applies; the text
      // badge is gone.)
      const epSpan = document.createElement('span');
      epSpan.className = 'model-switch-ep';
      // Don't show endpoint name if it matches the model name (local self-hosted)
      const _epDisplay = m.epName && !m.display.toLowerCase().includes(m.epName.toLowerCase().split('/').pop()) ? m.epName : '';
      epSpan.textContent = _epDisplay;
      row.appendChild(epSpan);

      // Inline favorite dot — toggles favorite, never picks the model.
      const favDot = document.createElement('button');
      favDot.type = 'button';
      favDot.className = 'mp-fav-dot' + (favs.includes(m.mid) ? ' active' : '');
      favDot.textContent = '●';
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
        // Keep our in-memory copy aligned so a follow-up re-render is correct.
        const idx = favs.indexOf(m.mid);
        if (nowFav && idx < 0) favs.push(m.mid);
        else if (!nowFav && idx >= 0) favs.splice(idx, 1);
        if (uiModule && uiModule.showToast) uiModule.showToast(nowFav ? 'Favorited' : 'Unfavorited');
        // In browse mode the Favorites section membership changed — rebuild
        // (cheap: Recent + Favorites). In search mode the row stays put, so
        // the in-place favorite update above is enough.
        if (!q) {
          const st = listEl.scrollTop;
          _populate('');
          listEl.scrollTop = st;
        }
      });
      row.appendChild(favDot);

      row.addEventListener('click', () => _pick(m));
      listEl.appendChild(row);
    }

    // ── Search mode: flat, filtered results across the whole catalog ──
    if (q) {
      const matches = all.filter(m => {
        const provName = _providerDisplayName(_providerSlug(m.mid)).toLowerCase();
        return [m.mid, m.display, m.epName, m.providerText, provName]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
      if (matches.length === 0) _addEmpty('No matching models');
      else matches.forEach(_addRow);
      return;
    }

    // ── Browse mode: Favorites (manual) + Recent (auto), with dedupe. ──
    // Rules:
    //   1. Never list the same model twice in the dropdown. Favorites
    //      win over Recent (if you favorited it, that's where it
    //      belongs — Recent shouldn't show it again as duplicate).
    //   2. Small catalogs (≤ BROWSE_ALL_LIMIT total) skip the Recent
    //      section entirely — when there's only ~10 models, the whole
    //      list fits below as "All models" and a separate Recent
    //      section just duplicates rows.
    const shown = new Set();
    const favModels = favs.map(id => byKey.get(id) || byId.get(id)).filter(Boolean);
    if (favModels.length) {
      _addSection('Favorites');
      favModels.forEach(m => { shown.add(_pickerModelKey(m)); _addRow(m); });
    }
    // Recent: only render when the catalog is big enough that surfacing
    // a recency shortlist is actually useful, AND only models that
    // aren't already in Favorites (dedupe).
    if (all.length > BROWSE_ALL_LIMIT) {
      const recentModels = _loadRecent()
        .map(id => byKey.get(id) || byId.get(id))
        .filter(Boolean)
        .filter(m => !shown.has(_pickerModelKey(m)))
        .slice(0, RECENT_MAX);
      if (recentModels.length) {
        _addSection('Recent');
        recentModels.forEach(m => { shown.add(_pickerModelKey(m)); _addRow(m); });
      }
    }

    // Small catalogs: still list everything so users aren't forced to search.
    if (all.length <= BROWSE_ALL_LIMIT) {
      const rest = all.filter(m => !shown.has(_pickerModelKey(m)));
      if (rest.length) {
        if (shown.size) _addSection('All models');
        rest.forEach(_addRow);
      }
    } else {
      // Large catalog: show provider groups with collapsible sections.
      const rest = all.filter(m => !shown.has(_pickerModelKey(m)));
      const groups = new Map();
      rest.forEach(m => {
        const slug = _providerGroupKey(m);
        if (!groups.has(slug)) groups.set(slug, []);
        groups.get(slug).push(m);
      });
      const sorted = [...groups.keys()].sort((a, b) =>
        _providerGroupName(a).localeCompare(_providerGroupName(b)));

      sorted.forEach(provider => {
        const models = groups.get(provider);
        const isCollapsed = _collapsedProviders.has(provider);
        const header = document.createElement('div');
        header.className = 'mp-provider-header';
        header.innerHTML =
          `<svg class="mp-provider-chevron${isCollapsed ? ' collapsed' : ''}" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`
          + `<span class="mp-provider-name">${_providerGroupName(provider)}</span>`
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
    _defaultPendingSeq++;
    try {
      window.__odysseusLastPickedRoute = {
        model: m.mid || '',
        endpoint_url: m.url || '',
        endpoint_id: m.endpointId || '',
        display: m.display || m.mid || '',
        picked_at: Date.now(),
      };
    } catch (_) {}
    let switchDone = null;
    const switchPromise = new Promise(resolve => { switchDone = resolve; });
    try { window.__odysseusModelSwitchPromise = switchPromise; } catch (_) {}
    const finishSwitch = () => {
      try {
        if (switchDone) switchDone();
        if (window.__odysseusModelSwitchPromise === switchPromise) delete window.__odysseusModelSwitchPromise;
      } catch (_) {}
    };
    const currentSessionId = _deps.getCurrentSessionId();
    const _pendingChat = _deps.getPendingChat();

    // Remember this pick so it surfaces under "Recent" next time the picker
    // opens — the whole point of quick-switch.
    if (m && m.mid) _pushRecent(_pickerModelKey(m) || m.mid);

    // Broadcast immediately so listeners (e.g. the tour) can advance without
    // waiting for the async session-create/PATCH that follows.
    try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}

    // Blur search input before closing to dismiss keyboard on mobile
    if (document.activeElement) document.activeElement.blur();
    _close();
    // Refocus main textarea — skip on mobile to avoid keyboard bounce
    if (window.innerWidth >= 768) {
      const _ta = document.getElementById('message');
      if (_ta) setTimeout(() => _ta.focus(), 50);
    }
    if (!currentSessionId && _pendingChat) {
      // Already have a deferred session — just update the model
      _deps.setPendingChat({ url: m.url, modelId: m.mid, endpointId: m.endpointId, source: 'manual' });
      // Header stays as session name — model switch only updates picker
      updateModelPicker();
      uiModule.showToast(`Using ${m.display}`);
      finishSwitch();
      return;
    } else if (!currentSessionId) {
      // No session yet — create one with this model
      try {
        await _deps.createDirectChat(m.url, m.mid, m.endpointId);
      } catch (e) {
        uiModule.showError('Failed to start chat: ' + e);
        finishSwitch();
        return;
      }
    } else {
      // Existing session with no model — PATCH it
      const sessions = _deps.getSessions();
      const s = sessions.find(x => x.id === currentSessionId);
      if (s) { s.model = m.mid; s.endpoint_url = m.url; s.endpoint_id = m.endpointId || s.endpoint_id || ''; }
      updateModelPicker();
      const fd = new FormData();
      fd.append('model', m.mid);
      fd.append('endpoint_url', m.url);
      if (m.endpointId) fd.append('endpoint_id', m.endpointId);
      try {
        const res = await fetch(`${API_BASE}/api/session/${currentSessionId}`, { method: 'PATCH', body: fd });
        if (!res.ok) {
          uiModule.showError('Failed to set model');
          finishSwitch();
          return;
        }
        // Header stays as session name — model info shown in picker only
      } catch (e) {
        uiModule.showError('Failed to set model: ' + e);
        finishSwitch();
        return;
      }
    }
    // Update picker visibility — model is now set
    updateModelPicker();
    if (window.refreshChatContextHeader) window.refreshChatContextHeader('model-pick');
    uiModule.showToast(`Using ${m.display}`);
    finishSwitch();
  }

  document.addEventListener('odysseus:auto-select-model', async (e) => {
    const detail = (e && e.detail) || {};
    const currentSessionId = _deps.getCurrentSessionId();
    const sessions = _deps.getSessions();
    const current = sessions.find(x => x.id === currentSessionId);
    const pending = _deps.getPendingChat();
    if ((current && current.model) || (pending && pending.modelId)) return;

    await _refreshCuratedRoutes();
    const targetModel = detail.modelId || 'automatic';
    const routes = _getAllModels();
    const match = routes.find(route => route.mid === targetModel) || routes[0];
    if (match) await _pick(match);
  });

  btn.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
  });
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden') || menu.classList.contains('closing')) {
      // Force-clear any in-progress close animation
      menu.classList.remove('closing', 'hidden');
      const hasCache = _hasModelCache();
      if (hasCache) {
        _populate('');
      } else {
        _renderLoading('Loading routes…');
      }
      _refreshPickerModels({ force: hasCache, showLoading: !hasCache }).then(() => {
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
        updateModelPicker();
      }).catch(() => {});
      if (window.innerWidth >= 768) search.focus();
      // Hide scroll button so it doesn't overlap
      const _scrollBtn = document.getElementById('scroll-bottom-btn');
      if (_scrollBtn) _scrollBtn.style.display = 'none';
    } else {
      _close();
    }
  });

  search.addEventListener('input', () => {
    if (_pickerLoading) return;
    _populate(search.value);
  });
  search.addEventListener('click', (e) => e.stopPropagation());
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      refreshBtn.disabled = true;
      refreshBtn.classList.add('spinning');
      try {
        await _refreshPickerModels({ force: true, showLoading: true });
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
        updateModelPicker();
      } catch (_) {
        uiModule.showToast('Model refresh failed');
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.classList.remove('spinning');
      }
    });
  }
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
    if (!menu.classList.contains('hidden') && !wrap.contains(e.target)) {
      _close();
    }
  });
}

/**
 * Update the model picker label to show the current model.
 * Always visible — shows current model name or "Select model" if none.
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
  } else if (_pendingChat && _pendingChat.modelId) {
    modelId = _pendingChat.modelId;
    if (_pendingChat.source === 'fallback' && !_modelExists(modelId)) {
      _deps.setPendingChat(null);
      modelId = null;
    }
  }
  if (!modelId && !currentSessionId && !_pendingChat && _deps.setPendingChat) {
    modelId = 'automatic';
    _deps.setPendingChat(_automaticRoute());
  }
  if (
    modelId &&
    !currentSessionId &&
    _pendingChat &&
    _pendingChat.source !== 'manual' &&
    !_modelExists(modelId)
  ) {
    modelId = 'automatic';
    _deps.setPendingChat(_automaticRoute());
  }
  const latestPending = _deps.getPendingChat && _deps.getPendingChat();
  if (
    !currentSessionId &&
    !_autoSelectingDefault &&
    (!modelId || (latestPending && latestPending.source === 'fallback'))
  ) {
    _ensureDefaultPendingChat();
  }

  const displayName = modelId ? modelId.split('/').pop() : 'Select model';
  // The header indicator clips long names with ellipsis; show the full model
  // identifier on hover (#1982). No tooltip on the "Select model" placeholder.
  label.title = modelId || '';
  const logo = modelId ? providerLogo(modelId) : null;
  if (logo) {
    label.innerHTML = '<span class="model-picker-logo">' + logo + '</span> ' + displayName;
  } else {
    label.textContent = displayName;
  }
}
