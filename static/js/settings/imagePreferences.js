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
    let response = await fetch('/api/auth/settings', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
    });
    invalidateSettings();
    if (response.status === 403) {
      const userEntries = Object.entries(patch).filter(([k]) => USER_PREFERENCE_KEYS.includes(k));
      for (const [key, value] of userEntries) {
        const prefRes = await fetch(`/api/prefs/${encodeURIComponent(key)}`, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value }),
        });
        if (!prefRes.ok) {
          const err = new Error(await prefRes.text().catch(() => 'Could not save image preferences'));
          err.status = prefRes.status;
          throw err;
        }
      }
      response = typeof Response !== 'undefined'
        ? new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        : { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => '{"ok":true}' };
    } else if (response.ok) {
      await Promise.all(USER_PREFERENCE_KEYS.map(key =>
        fetch(`/api/prefs/${encodeURIComponent(key)}`, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value: null }),
        }).catch(() => null)
      ));
    } else {
      throw new Error('Could not save image settings');
    }
    invalidateSettings();
    window.dispatchEvent(new CustomEvent('image-preferences-changed', { detail: patch }));
    return response;
  };
  const result = saveQueue.then(save);
  saveQueue = result.catch(() => {});
  return result;
}
