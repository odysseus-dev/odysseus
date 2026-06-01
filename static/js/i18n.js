// i18n.js — lightweight client-side localization runtime.
//
// Foundation only: provides the machinery (catalog loading, lookup with
// fallback, DOM application, locale switching + persistence). UI strings are
// migrated into the catalogs incrementally — see docs/localization.md.
//
// Design notes:
//   • Catalogs are plain JSON under /static/locales/<code>.json, namespaced by
//     dotted keys (e.g. "settings.nav.appearance").
//   • The base locale (en) is ALWAYS loaded so any key missing from the active
//     locale falls back to English, then to the raw key as a last resort —
//     nothing ever renders blank.
//   • Markup opts in declaratively:
//       <span data-i18n="settings.nav.appearance">Appearance</span>
//       <input data-i18n-attr="placeholder:common.search">
//       <div  data-i18n-html="some.rich.key"></div>   (catalog-trusted HTML)
//     The English text left in the DOM is the design-time default; it is what
//     a fresh extraction tool would pull, and it shows if the catalog 404s.
//   • ES module + a window.i18n mirror so inline / non-module scripts can call t().
//
// ES6 module.

const LS_KEY = 'odysseus-locale';
const LOCALES_BASE = '/static/locales';
const BASE_LOCALE = 'en';

const state = {
  active: BASE_LOCALE,
  registry: null,            // { default, fallback, locales:[{code,name,nativeName,dir}] }
  catalogs: Object.create(null), // code -> flattened {dottedKey: string|pluralObj}
};

// ---- catalog helpers ------------------------------------------------------

// Flatten a nested catalog object into dotted keys. Leaf values that are
// strings become entries; objects whose keys are CLDR plural categories
// (one/other/…) are kept whole so t() can plural-select them. Keys starting
// with "_" (e.g. "_meta") are ignored.
const PLURAL_CATS = new Set(['zero', 'one', 'two', 'few', 'many', 'other']);
function isPluralObj(v) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
  const keys = Object.keys(v);
  return keys.length > 0 && keys.every(k => PLURAL_CATS.has(k));
}
function flatten(obj, prefix, out) {
  for (const [k, v] of Object.entries(obj)) {
    if (k.startsWith('_')) continue;
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string' || isPluralObj(v)) {
      out[key] = v;
    } else if (v && typeof v === 'object') {
      flatten(v, key, out);
    }
  }
  return out;
}

async function fetchCatalog(code) {
  if (state.catalogs[code]) return state.catalogs[code];
  try {
    const res = await fetch(`${LOCALES_BASE}/${code}.json`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const raw = await res.json();
    state.catalogs[code] = flatten(raw, '', Object.create(null));
  } catch (_) {
    // Missing/invalid catalog must never break the page — cache an empty map
    // so we fall through to the base locale / raw key.
    state.catalogs[code] = Object.create(null);
  }
  return state.catalogs[code];
}

async function fetchRegistry() {
  if (state.registry) return state.registry;
  try {
    const res = await fetch(`${LOCALES_BASE}/index.json`, { credentials: 'same-origin' });
    if (res.ok) state.registry = await res.json();
  } catch (_) { /* fall through to default */ }
  if (!state.registry) {
    state.registry = {
      default: BASE_LOCALE, fallback: BASE_LOCALE,
      locales: [{ code: 'en', name: 'English', nativeName: 'English', dir: 'ltr' }],
    };
  }
  return state.registry;
}

// ---- string interpolation + lookup ----------------------------------------

// "Hi {name}" + {name:'Ada'} -> "Hi Ada". Missing params are left as-is so the
// placeholder is visible rather than silently dropped.
function interpolate(str, params) {
  if (!params || typeof str !== 'string') return str;
  return str.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

function rawLookup(code, key) {
  const cat = state.catalogs[code];
  return cat ? cat[key] : undefined;
}

/**
 * Translate a dotted key.
 *   t('settings.nav.appearance')
 *   t('chat.message_count', { count: n })   // plural-aware when value is {one,other,…}
 * Resolution order: active locale → base locale → the key itself.
 */
function t(key, params) {
  let val = rawLookup(state.active, key);
  if (val === undefined) val = rawLookup(BASE_LOCALE, key);
  if (val === undefined) return key; // last resort: surfaces missing keys in-place

  if (isPluralObj(val)) {
    const count = params && typeof params.count === 'number' ? params.count : 0;
    let cat = 'other';
    try { cat = new Intl.PluralRules(state.active).select(count); } catch (_) {}
    val = val[cat] ?? val.other ?? Object.values(val)[0];
  }
  return interpolate(val, params);
}

// ---- DOM application ------------------------------------------------------

function applyTranslations(root) {
  const scope = root || document;

  scope.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });

  scope.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = t(el.getAttribute('data-i18n-html'));
  });

  // data-i18n-attr="placeholder:common.search, title:common.close"
  scope.querySelectorAll('[data-i18n-attr]').forEach(el => {
    el.getAttribute('data-i18n-attr').split(',').forEach(pair => {
      const [attr, k] = pair.split(':').map(s => s && s.trim());
      if (attr && k) el.setAttribute(attr, t(k));
    });
  });
}

// ---- locale selection -----------------------------------------------------

function localeDir(code) {
  const reg = state.registry;
  const entry = reg && reg.locales.find(l => l.code === code);
  return (entry && entry.dir) || 'ltr';
}

function availableCodes() {
  const reg = state.registry;
  return reg ? reg.locales.map(l => l.code) : [BASE_LOCALE];
}

// Pick the best available locale for a BCP-47-ish tag list, matching the
// primary subtag when an exact match isn't available (en-US -> en).
function matchAvailable(tags) {
  const avail = availableCodes();
  for (const tag of tags) {
    if (!tag) continue;
    const lc = tag.toLowerCase();
    if (avail.includes(lc)) return lc;
    const primary = lc.split('-')[0];
    const hit = avail.find(c => c.toLowerCase() === primary || c.toLowerCase().split('-')[0] === primary);
    if (hit) return hit;
  }
  return null;
}

async function setLocale(code, { persist = true } = {}) {
  await fetchRegistry();
  if (!availableCodes().includes(code)) code = state.registry.default || BASE_LOCALE;

  // Base locale underpins fallback; load it once alongside the target.
  await Promise.all([fetchCatalog(BASE_LOCALE), fetchCatalog(code)]);
  state.active = code;

  const html = document.documentElement;
  html.setAttribute('lang', code);
  html.setAttribute('dir', localeDir(code));

  applyTranslations(document);

  if (persist) {
    try { localStorage.setItem(LS_KEY, code); } catch (_) {}
    // Best-effort cross-device persistence; ignore failures (anon mode, offline).
    fetch('/api/prefs/locale', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ value: code }),
    }).catch(() => {});
  }

  document.dispatchEvent(new CustomEvent('i18n:change', { detail: { locale: code } }));
  return code;
}

// Resolve which locale to start in: explicit local choice wins; otherwise the
// stored server pref; otherwise the browser's languages; otherwise default.
async function resolveInitial() {
  await fetchRegistry();
  let stored = null;
  try { stored = localStorage.getItem(LS_KEY); } catch (_) {}
  if (stored && availableCodes().includes(stored)) return stored;

  // No local override — consult the per-user server pref (cross-device).
  try {
    const res = await fetch('/api/prefs/locale', { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      if (data && data.value && availableCodes().includes(data.value)) return data.value;
    }
  } catch (_) {}

  const nav = (navigator.languages && navigator.languages.length)
    ? navigator.languages
    : [navigator.language].filter(Boolean);
  return matchAvailable(nav) || state.registry.default || BASE_LOCALE;
}

let _initPromise = null;
function init() {
  if (_initPromise) return _initPromise;
  _initPromise = (async () => {
    const code = await resolveInitial();
    // Don't re-persist on boot — the user hasn't actively chosen this.
    await setLocale(code, { persist: false });
  })();
  return _initPromise;
}

const i18n = {
  t,
  init,
  setLocale,
  applyTranslations,
  getLocale: () => state.active,
  getRegistry: fetchRegistry,
  availableCodes,
};

// Expose for inline / non-module callers (CSP nonce'd scripts in index.html).
if (typeof window !== 'undefined') window.i18n = i18n;

// Auto-initialize. setLocale() touches <html> + does a DOM pass, so wait for
// the document to be parsed first.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}

export default i18n;
export { t, init, setLocale, applyTranslations };
