// static/js/localeSelector.js
// Populates the in-app language <select> (Settings → Appearance) and applies
// the user's choice via the i18n runtime (which persists to the per-user
// `locale` pref and re-translates the live DOM).
import {
  SUPPORTED_LOCALES, LOCALE_NAMES, getLocale, changeLocale, initI18n,
} from '/static/js/i18n.js';

function populate(sel) {
  sel.innerHTML = '';
  for (const loc of SUPPORTED_LOCALES) {
    const opt = document.createElement('option');
    opt.value = loc;
    opt.textContent = LOCALE_NAMES[loc] || loc;
    sel.appendChild(opt);
  }
}

async function setup() {
  const sel = document.getElementById('locale-select');
  if (!sel) return;
  // initI18n() is idempotent — ensures the locale is resolved before we sync
  // the selector's current value.
  await initI18n();
  populate(sel);
  sel.value = getLocale();
  sel.addEventListener('change', () => {
    changeLocale(sel.value);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setup);
} else {
  setup();
}
