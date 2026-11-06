// Explicit save for the Cookbook Hugging Face token.
//
// Why this exists: every background state sync sends its body through
// `_stripStateSecrets()` (cookbookRunning.js), which deletes `env.hfToken`, so
// `/api/cookbook/state` never received the token typed in Settings and the
// field could not persist across a reload (#6361). This is the one request that
// carries it, and it reports back what the server stored.
//
// Kept DOM-free with an injected fetch so the transport contract runs under
// node without importing the Cookbook UI.

export const HF_TOKEN_ENDPOINT = '/api/cookbook/hf-token';

export async function saveHfToken(token, { fetchImpl = globalThis.fetch } = {}) {
  const value = String(token ?? '').trim();
  // An empty field is "no change", not "clear": the stored token stays put.
  if (!value) return { ok: false, error: 'Nothing to save' };
  if (typeof fetchImpl !== 'function') return { ok: false, error: 'No fetch available' };

  let res = null;
  try {
    res = await fetchImpl(HF_TOKEN_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: value }),
    });
  } catch (e) {
    return { ok: false, error: String((e && e.message) || 'Network error') };
  }
  if (!res || res.ok !== true) {
    const status = res && res.status ? res.status : 'unknown';
    return { ok: false, error: `Save failed (HTTP ${status})` };
  }

  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!data || data.ok !== true) {
    return { ok: false, error: String((data && data.error) || 'Save rejected') };
  }
  return {
    ok: true,
    configured: data.hfTokenConfigured === true,
    masked: String(data.hfTokenMasked || ''),
  };
}
