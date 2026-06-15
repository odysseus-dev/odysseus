// static/js/search.js

import { api } from './axios/api.js';

/**
 * Search settings management — reads active provider from admin settings.
 */
let _provider = 'searxng';
let _loaded = false;

export function init(_apiBase) {
  // Fetch provider on init so it's ready when chat needs it
  _fetchProvider();
}

async function _fetchProvider() {
  try {
    const { data: s } = await api.get('/api/auth/settings');
    _provider = s.search_provider || 'searxng';
    _loaded = true;
  } catch (e) { /* keep default */ }
}

export function getCurrentProvider() {
  return _provider;
}

const _labels = {
  searxng: 'SearXNG', brave: 'Brave', duckduckgo: 'DuckDuckGo',
  google_pse: 'Google', tavily: 'Tavily', serper: 'Serper',
  disabled: 'search (disabled)',
};

export function getProviderLabel() {
  return _labels[_provider] || _provider;
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
