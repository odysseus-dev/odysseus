// ============================================
// Odysseus — Settings → Appearance language selector wiring
// ============================================
// Reflects the active language in the #set-language <select>, reconciles it
// against the server-stored value, and persists changes via setLang() (which
// writes localStorage + /api/prefs/language and reloads to re-render the UI).

import { getLang, normalizeLang, setLang } from './i18n.js';

export function initLanguagePref() {
  const sel = document.getElementById('set-language');
  if (!sel) return;

  // localStorage was already applied at boot — show it immediately.
  sel.value = getLang();

  // Server is the source of truth across devices; reconcile the <select>.
  fetch('/api/prefs/language', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d && Object.prototype.hasOwnProperty.call(d, 'value')) {
        const remoteLang = normalizeLang(d.value);
        sel.value = remoteLang;
        if (remoteLang !== getLang()) {
          setLang(remoteLang);
          return;
        }
        try { localStorage.setItem('odysseus-language', remoteLang); } catch (_) {}
      }
    })
    .catch(() => {});

  if (sel.dataset.bound) return;
  sel.dataset.bound = '1';
  sel.addEventListener('change', () => {
    if (sel.value && sel.value !== getLang()) setLang(sel.value);
  });
}

export default { initLanguagePref };
