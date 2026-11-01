import { invalidateSettings } from '../appConfig.js';

const USER_PREFERENCE_KEYS = ['image_model', 'image_edit_model', 'image_quality'];

export async function readImagePreferences() {
  const [prefsRes, settingsRes] = await Promise.all([
    fetch('/api/prefs', { credentials: 'same-origin' }).catch(() => null),
    fetch('/api/auth/settings', { credentials: 'same-origin' }).catch(() => null),
  ]);
  const prefs = (prefsRes && prefsRes.ok) ? await prefsRes.json().catch(() => ({})) : {};
  const settings = (settingsRes && settingsRes.ok) ? await settingsRes.json().catch(() => ({})) : {};
  if (!prefsRes?.ok && !settingsRes?.ok) throw new Error('Could not load image settings');
  return {
    ...settings,
    image_model: prefs?.image_model || settings?.image_model || '',
    image_edit_model: prefs?.image_edit_model || settings?.image_edit_model || '',
    image_quality: prefs?.image_quality || settings?.image_quality || 'medium',
  };
}

let saveQueue = Promise.resolve();
export function waitForImagePreferences() { return saveQueue; }
export function saveImagePreferences(patch) {
  const save = async () => {
    let response;
    try {
      response = await fetch('/api/auth/settings', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
      });
    } catch (netErr) {
      invalidateSettings();
      netErr.globalSaved = false;
      throw netErr;
    }
    invalidateSettings();
    if (response.status === 403) {
      const userEntries = Object.entries(patch).filter(([k]) => USER_PREFERENCE_KEYS.includes(k));
      if (userEntries.length === 0) {
        const err = new Error('Admin access is required to change these settings.');
        err.status = 403;
        err.globalSaved = false;
        throw err;
      }
      for (const [key, value] of userEntries) {
        const prefRes = await fetch(`/api/prefs/${encodeURIComponent(key)}`, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value }),
        });
        if (!prefRes.ok) {
          const err = new Error(await prefRes.text().catch(() => 'Could not save image preferences'));
          err.status = prefRes.status;
          err.globalSaved = false;
          throw err;
        }
      }
      response = typeof Response !== 'undefined'
        ? new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        : { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => '{"ok":true}' };
    } else if (response.ok) {
      const clearResults = await Promise.all(USER_PREFERENCE_KEYS.map(async key => {
        try {
          const res = await fetch(`/api/prefs/${encodeURIComponent(key)}`, {
            method: 'PUT', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: null }),
          });
          if (!res.ok) {
            const err = new Error(await res.text().catch(() => `HTTP ${res.status}`));
            err.status = res.status;
            return { ok: false, error: err };
          }
          return { ok: true };
        } catch (err) {
          return { ok: false, error: err };
        }
      }));
      const failure = clearResults.find(r => !r.ok);
      if (failure) {
        invalidateSettings();
        if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined') {
          window.dispatchEvent(new CustomEvent('image-preferences-changed', { detail: patch }));
        }
        const err = new Error(failure.error?.message || 'Could not clear personal preferences');
        err.status = failure.error?.status || 500;
        err.cause = failure.error;
        err.globalSaved = true;
        err.phase = 'cleanup';
        throw err;
      }
    } else {
      const err = new Error('Could not save image settings');
      err.status = response.status;
      err.globalSaved = false;
      throw err;
    }
    invalidateSettings();
    if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined') {
      window.dispatchEvent(new CustomEvent('image-preferences-changed', { detail: patch }));
    }
    return response;
  };
  const result = saveQueue.then(save);
  saveQueue = result.catch(() => {});
  return result;
}
