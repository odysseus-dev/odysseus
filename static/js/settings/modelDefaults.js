// Storage for the model-default cards in Settings → AI Defaults.
//
// The server resolves these keys per account first (src/settings.py
// `_PER_USER_KEYS`, read by `resolve_endpoint(..., owner=...)` and the chat
// composer's default), then falls back to the server-wide value. So:
//
// - A signed-in non-admin edits their own preferences (`/api/prefs`); the
//   server-wide endpoint is admin-only and would refuse the write anyway.
// - An administrator (or the operator of an auth-disabled install) edits the
//   server-wide value. With accounts enabled their own personal value would
//   otherwise shadow what they just chose, so it is cleared, matching the
//   image-model preferences contract in imagePreferences.js.
//
// Saves are serialized, and a failure leaves the caller to re-read what is
// actually stored rather than trusting what the form shows.

import { postSettings } from './api.js';

export const MODEL_DEFAULT_KEYS = Object.freeze([
  'default_endpoint_id',
  'default_model',
  'utility_endpoint_id',
  'utility_model',
  'utility_reasoning_effort',
  'utility_model_fallbacks',
  'research_endpoint_id',
  'research_model',
]);

function isUnset(value) {
  return value === undefined || value === null || value === ''
    || (Array.isArray(value) && value.length === 0);
}

function pickModelDefaults(source) {
  const values = {};
  const object = source && typeof source === 'object' ? source : {};
  for (const key of MODEL_DEFAULT_KEYS) {
    if (!isUnset(object[key])) values[key] = object[key];
  }
  return values;
}

function assertModelDefaultKeys(patch) {
  const keys = Object.keys(patch || {});
  if (!keys.length) throw new Error('Nothing to save');
  for (const key of keys) {
    if (!MODEL_DEFAULT_KEYS.includes(key)) throw new Error(`Not a model default: ${key}`);
  }
  return keys;
}

async function patchPreferences(values) {
  const response = await fetch('/api/prefs', {
    method: 'PATCH',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
  if (!response.ok) {
    const error = new Error('Could not save your preference');
    error.status = response.status;
    throw error;
  }
  return response;
}

/**
 * @param {object} options
 * @param {boolean} options.personal          store in the caller's preferences
 * @param {boolean} options.clearOwnOverrides after a server-wide save, remove the
 *                                            caller's own personal values
 */
export function createModelDefaultsStore(options = {}) {
  const personal = options.personal === true;
  const clearOwnOverrides = !personal && options.clearOwnOverrides === true;
  let queue = Promise.resolve();

  async function load() {
    const url = personal ? '/api/prefs' : '/api/auth/settings';
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) {
      const error = new Error('Could not load model defaults');
      error.status = response.status;
      throw error;
    }
    return pickModelDefaults(await response.json());
  }

  async function write(patch) {
    const keys = assertModelDefaultKeys(patch);
    if (personal) {
      // An empty choice removes the personal value, rather than storing an
      // empty override; any fallback is decided where defaults are resolved.
      const values = {};
      for (const key of keys) values[key] = isUnset(patch[key]) ? null : patch[key];
      await patchPreferences(values);
      return;
    }

    await postSettings(patch);
    if (!clearOwnOverrides) return;
    try {
      const cleared = {};
      for (const key of keys) cleared[key] = null;
      await patchPreferences(cleared);
    } catch (cause) {
      const error = new Error('Saved for the server, but your personal override could not be cleared');
      error.status = cause.status;
      error.serverSaved = true;
      throw error;
    }
  }

  function save(patch) {
    const run = () => write(patch);
    const result = queue.then(run, run);
    queue = result.catch(() => {});
    return result;
  }

  return Object.freeze({ personal, load, save });
}

/** User-facing text for a failed model-default save. */
export function modelDefaultSaveError(error) {
  if (error && error.serverSaved) return error.message;
  if (error && error.status === 403) return 'You do not have permission to change this setting.';
  return 'Failed to save. The form shows what is currently stored.';
}
