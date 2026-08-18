// static/js/search.js

/**
 * Search settings management — reads active provider from admin settings.
 */

import { getSettings, invalidateSettings } from './appConfig.js';

let _provider = 'searxng';
let _loaded = false;

// No API base parameter any more: the settings request lives in appConfig.js and
// resolves against the document origin, which is exactly what API_BASE held.
export function init() {
  // Fetch provider on init so it's ready when chat needs it
  _fetchProvider();
}

async function _fetchProvider() {
  try {
    const s = await getSettings();
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
  // Drop the shared snapshot first: the point of this call is to observe the
  // settings that were just written, so it must not be served from cache.
  invalidateSettings();
  _fetchProvider();
}

const searchModule = {
  init,
  getCurrentProvider,
  getProviderLabel,
  refresh
};

export default searchModule;
