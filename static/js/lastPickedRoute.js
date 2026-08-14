// Per-session last-picked model route.
// A single window-level last pick leaked across chats: picking Model B in
// Chat B overwrote Chat A's next send for up to 10 minutes (#6023).

const TTL_MS = 10 * 60 * 1000;
export const PENDING_ROUTE_KEY = '__pending__';

function _store() {
  try {
    if (!window.__odysseusLastPickedRoutesBySession) {
      window.__odysseusLastPickedRoutesBySession = Object.create(null);
    }
    return window.__odysseusLastPickedRoutesBySession;
  } catch (_) {
    return null;
  }
}

export function lastPickedSessionKey(sessionId) {
  return sessionId || PENDING_ROUTE_KEY;
}

export function setLastPickedRoute(sessionId, route) {
  const store = _store();
  if (!store || !route) return null;
  const key = lastPickedSessionKey(sessionId);
  const rec = {
    model: route.model || '',
    endpoint_url: route.endpoint_url || '',
    endpoint_id: route.endpoint_id || '',
    display: route.display || '',
    picked_at: Date.now(),
    session_id: key,
  };
  store[key] = rec;
  try { window.__odysseusLastPickedRoute = rec; } catch (_) {}
  return rec;
}

export function getLastPickedRoute(sessionId, now = Date.now()) {
  const store = _store();
  if (!store) return null;
  const rec = store[lastPickedSessionKey(sessionId)];
  if (!rec || !rec.model) return null;
  if (now - (rec.picked_at || 0) >= TTL_MS) return null;
  return rec;
}
