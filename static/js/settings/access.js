// What the caller may change in Settings, from /api/auth/status (see
// authStatus.js). Everything here is presentation: the server authorizes every
// write itself.
//
// The answer is unknown until /api/auth/status replies. While it is unknown,
// Settings offers only what every caller may use and stores no per-account
// choice anywhere (see resolve()).
import { loadSettingsAccess } from '../authStatus.js';

/** Model defaults are stored per account rather than server-wide. */
export function personalModelDefaults(access) {
  return Boolean(access && access.personalModelDefaults);
}

/**
 * One access resolver per Settings instance. `onResolved(access)` runs once,
 * when the first answer arrives (the coordinator uses it to show the access in
 * the modal if it is already built).
 */
export function createSettingsAccess({ onResolved }) {
  let access = null;
  let request = null;

  function canManage() {
    if (access) return access.canManage;
    return window._canManageSettings === true || !!window._isAdmin;
  }

  function accountsEnabled() {
    return !access || access.authEnabled;
  }

  function known() {
    return Boolean(access) || typeof window._canManageSettings === 'boolean';
  }

  /**
   * Whether the caller may not manage server-wide settings.
   * If capability is already established, resolves immediately. While access is
   * unknown, awaits /api/auth/status resolution so privileged initialization
   * never starts prematurely before the caller's role is known. If resolution
   * fails, treats the caller as restricted to prevent unauthorized requests;
   * when resolution recovers, the next call or listener reflects the newly
   * established access.
   */
  async function restricted() {
    if (!known()) {
      try {
        await resolve();
      } catch (_) {
        return true;
      }
    }
    return !canManage();
  }

  /**
   * Resolve what this caller may change. Rejects while /api/auth/status is
   * unavailable; the next call asks again (authStatus.js does not keep a
   * failure). Concurrent calls share one request.
   */
  function resolve() {
    if (access) return Promise.resolve(access);
    if (!request) {
      request = loadSettingsAccess()
        .then(function(answer) {
          if (!access) {
            access = answer;
            onResolved(answer);
          }
          return access;
        })
        .finally(function() { request = null; });
    }
    return request;
  }

  return { canManage, accountsEnabled, known, restricted, resolve, isResolved: function() { return Boolean(access); } };
}
