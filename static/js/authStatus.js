// static/js/authStatus.js
//
// One shared request for /api/auth/status, plus the Settings access summary
// derived from it. app.js and Settings both need the caller's role; sharing the
// promise means Settings can wait for the real answer instead of reading a
// global that may not be set yet.
//
// Everything here is presentation. The server authorizes every write itself;
// these values only decide which controls are offered and how their scope is
// labelled.

let _statusPromise = null;

export const SESSION_ENDED_EVENT = 'odysseus:session-ended';

export function loadAuthStatus() {
  if (!_statusPromise) {
    _statusPromise = fetch('/api/auth/status', { credentials: 'same-origin' })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .catch(error => {
        // Let the next caller retry rather than caching a failure.
        _statusPromise = null;
        throw error;
      });
  }
  return _statusPromise;
}

/**
 * Summarize what Settings may offer this caller.
 *
 * - `canManage`: may change server-wide settings (an admin, or the operator
 *   when authentication is explicitly disabled).
 * - `authEnabled`: accounts exist, so "only you" versus "everyone" is a real
 *   distinction.
 * - `personalModelDefaults`: model defaults are stored per account rather than
 *   server-wide (signed-in non-admin users).
 */
export function settingsAccessFromStatus(status) {
  const value = status && typeof status === 'object' ? status : {};
  const authEnabled = value.auth_enabled !== false;
  const canManage = value.can_manage_settings === true || value.is_admin === true;
  return Object.freeze({
    authEnabled,
    canManage,
    personalModelDefaults: authEnabled && !canManage,
  });
}

export async function loadSettingsAccess() {
  return settingsAccessFromStatus(await loadAuthStatus());
}
