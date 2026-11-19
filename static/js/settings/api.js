// Lifted verbatim from static/js/settings.js so extracted panels can post
// settings without importing the module they were extracted from.
import uiModule from '../ui.js?v=20260916largetoolscroll1';
import { invalidateSettings } from '../appConfig.js';

/**
 * POST a settings patch, then drop the shared snapshot in appConfig.js.
 *
 * Every Settings write goes through here so no save path can forget the
 * invalidation — a stale settings object served for the rest of the session is
 * a worse bug than the duplicate fetches the cache removes. The invalidation is
 * in a `finally` because a request that throws on the way back may still have
 * been applied server-side.
 *
 * Reads in the Settings modules deliberately stay direct fetches: the panel is
 * the writer and edits what it reads, so it must see the authoritative state,
 * not a cache.
 */
export async function postSettings(body) {
  try {
    const response = await fetch('/api/auth/settings', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const error = new Error(response.status === 403
        ? 'Admin access is required to change these settings.'
        : 'Failed to save');
      error.status = response.status;
      if (response.status === 403) uiModule.showError(error.message);
      throw error;
    }
    return response;
  } finally {
    invalidateSettings();
  }
}
