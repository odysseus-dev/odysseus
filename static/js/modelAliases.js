// Shared per-browser display labels for model IDs (localStorage only).
import Storage from './storage.js';

export const MODEL_ALIASES_KEY = 'odysseus-model-aliases';

export function loadModelAliases() {
  return Storage.getJSON(MODEL_ALIASES_KEY, {});
}

export function modelBaseName(mid, fallback) {
  const raw = fallback || mid || '';
  return String(raw).split('/').pop();
}

export function modelDisplayName(mid, fallback) {
  if (!mid) return modelBaseName('', fallback);
  const alias = loadModelAliases()[mid];
  return alias || modelBaseName(mid, fallback);
}

export function saveModelAlias(mid, name) {
  if (!mid) return;
  const aliases = loadModelAliases();
  const trimmed = (name || '').trim();
  const base = modelBaseName(mid);
  if (trimmed && trimmed !== base) aliases[mid] = trimmed;
  else delete aliases[mid];
  Storage.setJSON(MODEL_ALIASES_KEY, aliases);
  try {
    document.dispatchEvent(new CustomEvent('odysseus:model-alias-changed', { detail: { mid, name: trimmed || base } }));
  } catch { /* non-DOM contexts */ }
}
