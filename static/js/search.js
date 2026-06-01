// static/js/search.js

import i18n from './i18n.js';

/**
 * Search settings management — reads active provider from admin settings.
 */

let API_BASE = '';
let _provider = 'searxng';
let _loaded = false;

export function init(apiBase) {
  API_BASE = apiBase;
  // Fetch provider on init so it's ready when chat needs it
  _fetchProvider();
}

async function _fetchProvider() {
  try {
    const res = await fetch((API_BASE || '') + '/api/auth/settings', { credentials: 'same-origin' });
    const s = await res.json();
    _provider = s.search_provider || 'searxng';
    _loaded = true;
  } catch (e) { /* keep default */ }
}

export function getCurrentProvider() {
  return _provider;
}

const _labelKeys = {
  searxng: 'search.provider.searxng', brave: 'search.provider.brave',
  duckduckgo: 'search.provider.duckduckgo', google_pse: 'search.provider.google',
  tavily: 'search.provider.tavily', serper: 'search.provider.serper',
};

export function getProviderLabel() {
  if (_provider === 'disabled') return i18n.t('search.provider.disabled');
  const key = _labelKeys[_provider];
  return key ? i18n.t(key) : _provider;
}

/** Re-fetch after admin saves new settings */
export function refresh() {
  _fetchProvider();
}

const searchModule = {
  init,
  getCurrentProvider,
  getProviderLabel,
  refresh
};

export default searchModule;
