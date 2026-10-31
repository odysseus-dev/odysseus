import { invalidateSettings } from '../appConfig.js';

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
    if (response.status === 403) {
      for (const [key, value] of Object.entries(patch)) {
        const prefRes = await fetch(`/api/prefs/${encodeURIComponent(key)}`, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value }),
        });
        if (!prefRes.ok) {
          throw new Error('Could not save image preferences');
        }
      }
    } else if (!response.ok) {
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
