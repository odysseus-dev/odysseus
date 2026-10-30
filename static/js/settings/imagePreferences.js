import { invalidateSettings } from '../appConfig.js';

export async function readImagePreferences() {
  const response = await fetch('/api/auth/settings', { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Could not load image settings');
  return response.json();
}

let saveQueue = Promise.resolve();
export function waitForImagePreferences() { return saveQueue; }
export function saveImagePreferences(patch) {
  const save = async () => {
    const response = await fetch('/api/auth/settings', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
    });
    if (!response.ok) throw new Error(response.status === 403
      ? 'Admin access is required to change image settings.' : 'Could not save image settings');
    invalidateSettings();
    window.dispatchEvent(new CustomEvent('image-preferences-changed', { detail: patch }));
    return response;
  };
  const result = saveQueue.then(save);
  saveQueue = result.catch(() => {});
  return result;
}
