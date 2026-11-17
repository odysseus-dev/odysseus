// Endpoint catalogs for the model cards, and the registry of cards that want to
// be told when a catalog changes.
//
// The registries and the in-flight refresh live together on purpose: a refresh
// is single-flight per catalog, and the handlers it notifies are the ones
// registered on that same catalog. `resolveAccess` is injected so which catalog
// a caller sees is decided by who they are, never guessed while that is unknown.
import { personalModelDefaults } from './access.js';
import { normalizeForegroundFallbackModelCatalog } from '../foregroundFallbackSettings.js';

export function createEndpointCatalog({ resolveAccess }) {
  const _aiEndpointRefreshers = new Set();
  const _foregroundFallbackEndpointRefreshers = new Set();
  let _aiEndpointRefreshInFlight = null;
  let _lastModelDefaultEndpoints = null;
  let _lastForegroundFallbackEndpoints = null;
  let _lastForegroundFallbackError = null;

  async function fetchModelEndpoints() {
    const epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    if (!epRes.ok) throw new Error(`HTTP ${epRes.status}`);
    const endpoints = await epRes.json();
    return Array.isArray(endpoints) ? endpoints : [];
  }

  async function fetchForegroundFallbackEndpoints() {
    const response = await fetch(
      '/api/models?background=false&foreground_fallback=true',
      { credentials: 'same-origin' }
    );
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return normalizeForegroundFallbackModelCatalog(await response.json());
  }

  // Endpoints offered by the model-default cards. Administrators manage the
  // whole pool (admin-only /api/model-endpoints); signed-in users choose from
  // the owner-scoped, allowlist-filtered chat catalog the runtime resolver
  // honors for them.
  async function fetchModelDefaultEndpoints() {
    const access = await resolveAccess();
    return personalModelDefaults(access)
      ? fetchForegroundFallbackEndpoints()
      : fetchModelEndpoints();
  }

  function registerAiEndpointRefresh(fn) {
    _aiEndpointRefreshers.add(fn);
    if (_lastModelDefaultEndpoints !== null) {
      try { fn(_lastModelDefaultEndpoints); } catch (e) { console.warn('[settings] endpoint refresh handler failed', e); }
    }
  }

  function registerForegroundFallbackEndpointRefresh(fn) {
    _foregroundFallbackEndpointRefreshers.add(fn);
    if (_lastForegroundFallbackEndpoints !== null || _lastForegroundFallbackError !== null) {
      try { fn(_lastForegroundFallbackEndpoints, _lastForegroundFallbackError); } catch (e) { console.warn('[settings] foreground fallback endpoint refresh handler failed', e); }
    }
  }

  async function refresh() {
    if (_aiEndpointRefreshInFlight) return _aiEndpointRefreshInFlight;
    _aiEndpointRefreshInFlight = (async function() {
      try {
        const endpoints = await fetchModelDefaultEndpoints();
        _lastModelDefaultEndpoints = endpoints;
        _aiEndpointRefreshers.forEach(function(fn) {
          try { fn(endpoints); } catch (e) { console.warn('[settings] endpoint refresh handler failed', e); }
        });
      } catch (e) {
        console.warn('[settings] failed to refresh model endpoints', e);
      }
      try {
        const endpoints = await fetchForegroundFallbackEndpoints();
        _lastForegroundFallbackEndpoints = endpoints;
        _lastForegroundFallbackError = null;
        _foregroundFallbackEndpointRefreshers.forEach(function(fn) {
          try { fn(endpoints, null); } catch (e) { console.warn('[settings] foreground fallback endpoint refresh handler failed', e); }
        });
      } catch (e) {
        console.warn('[settings] failed to refresh foreground fallback endpoints', e);
        _lastForegroundFallbackEndpoints = null;
        _lastForegroundFallbackError = e;
        _foregroundFallbackEndpointRefreshers.forEach(function(fn) {
          try { fn(null, e); } catch (handlerError) { console.warn('[settings] foreground fallback endpoint refresh handler failed', handlerError); }
        });
      } finally {
        _aiEndpointRefreshInFlight = null;
      }
    })();
    return _aiEndpointRefreshInFlight;
  }

  return {
    fetchModelEndpoints,
    fetchForegroundFallbackEndpoints,
    fetchModelDefaultEndpoints,
    registerAiEndpointRefresh,
    registerForegroundFallbackEndpointRefresh,
    refresh,
    getLastModelDefaultEndpoints: function() { return _lastModelDefaultEndpoints; },
    getLastForegroundFallbackEndpoints: function() { return _lastForegroundFallbackEndpoints; },
  };
}
