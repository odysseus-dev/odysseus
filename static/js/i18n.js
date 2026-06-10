// static/js/i18n.js
// Lightweight runtime i18n for the Odysseus SPA.
//
// Strings live in /static/locales/<locale>.json as flat key -> text maps.
// HTML marks translatable nodes declaratively:
//   <span data-i18n="memories.title">Memories</span>
//   <input data-i18n-placeholder="memory.add_placeholder">
//   <button data-i18n-title="common.delete" data-i18n-aria-label="common.delete">
//
// Locale resolution order (most to least preferred):
//   1. explicit choice persisted in the `locale` user pref / localStorage
//   2. navigator.language (mapped to a supported locale)
//   3. DEFAULT_LOCALE
//
// On the authenticated app the choice is mirrored into the per-user pref store
// (/api/prefs/locale) so it follows the user across devices. login.html is
// pre-auth, so there we rely on localStorage only and sync to the pref after
// a successful login (see init.js / login flow).

export const SUPPORTED_LOCALES = ['en-US', 'pt-BR', 'es-ES'];
export const DEFAULT_LOCALE = 'en-US';
const STORAGE_KEY = 'odysseus.locale';

// Human-readable names shown in the language selector (in their own language).
export const LOCALE_NAMES = {
  'en-US': 'English',
  'pt-BR': 'Português (Brasil)',
  'es-ES': 'Español (España)',
};

let _locale = DEFAULT_LOCALE;
let _strings = {};
let _ready = null; // Promise resolved once initial locale is loaded.

/** Map an arbitrary BCP-47 tag to one of our supported locales, or null. */
function resolveSupported(tag) {
  if (!tag) return null;
  const lower = String(tag).toLowerCase();
  // Exact match first.
  for (const loc of SUPPORTED_LOCALES) {
    if (loc.toLowerCase() === lower) return loc;
  }
  // Match on the primary subtag (e.g. "pt", "es", "en", "pt-PT" -> pt-BR).
  const primary = lower.split('-')[0];
  const byPrimary = {
    en: 'en-US',
    pt: 'pt-BR',
    es: 'es-ES',
  };
  return byPrimary[primary] || null;
}

/** Best-effort detection from storage + browser, without contacting backend. */
export function detectLocale() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    const fromStore = resolveSupported(stored);
    if (fromStore) return fromStore;
  } catch (_) { /* localStorage may be unavailable */ }

  const navLangs = navigator.languages && navigator.languages.length
    ? navigator.languages
    : [navigator.language];
  for (const l of navLangs) {
    const r = resolveSupported(l);
    if (r) return r;
  }
  return DEFAULT_LOCALE;
}

async function loadStrings(locale) {
  const res = await fetch(`/static/locales/${locale}.json`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`locale ${locale} -> HTTP ${res.status}`);
  return res.json();
}

/**
 * Translate a key. Falls back to the provided `fallback` (usually the original
 * English text shipped in the HTML), then to the key itself. Supports simple
 * {name} placeholder interpolation via `vars`.
 */
export function t(key, vars, fallback) {
  let str = (key in _strings) ? _strings[key] : (fallback != null ? fallback : key);
  if (vars && typeof str === 'string') {
    str = str.replace(/\{(\w+)\}/g, (m, name) =>
      (name in vars ? String(vars[name]) : m));
  }
  return str;
}

export function getLocale() { return _locale; }

/**
 * Apply translations to every marked node under `root` (default: document).
 * - [data-i18n]             -> textContent
 * - [data-i18n-placeholder] -> placeholder attr
 * - [data-i18n-title]       -> title attr
 * - [data-i18n-aria-label]  -> aria-label attr
 * - [data-i18n-value]       -> value attr (buttons/inputs)
 * The element's existing content is passed as the fallback, so untranslated
 * keys keep the shipped English text instead of showing raw keys.
 */
export function applyTranslations(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    // Many tagged elements carry an inline icon (<svg>, <span>…) next to the
    // label, so we must swap only the label's text node — setting textContent
    // on the element would destroy those children.
    let labelNode = null;
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE && child.nodeValue.trim()) {
        labelNode = child; // last non-empty text node wins (icons come first)
      }
    }
    // The shipped English is the fallback, but after the first translation the
    // DOM no longer holds it — so capture it once. Without this, switching
    // back to en-US (which loads no JSON) would "fall back" to whatever
    // language was rendered last.
    if (labelNode) {
      if (!('i18nOrig' in el.dataset)) el.dataset.i18nOrig = labelNode.nodeValue.trim();
      labelNode.nodeValue = t(key, null, el.dataset.i18nOrig);
    } else if (!el.childElementCount) {
      if (!('i18nOrig' in el.dataset)) el.dataset.i18nOrig = el.textContent.trim();
      el.textContent = t(key, null, el.dataset.i18nOrig);
    }
  });
  const attrMap = {
    'data-i18n-placeholder': 'placeholder',
    'data-i18n-title': 'title',
    'data-i18n-aria-label': 'aria-label',
    'data-i18n-value': 'value',
  };
  for (const [dataAttr, realAttr] of Object.entries(attrMap)) {
    root.querySelectorAll(`[${dataAttr}]`).forEach((el) => {
      const key = el.getAttribute(dataAttr);
      const origAttr = `${dataAttr}-orig`; // same capture-once trick as above
      if (!el.hasAttribute(origAttr)) el.setAttribute(origAttr, el.getAttribute(realAttr) || '');
      el.setAttribute(realAttr, t(key, null, el.getAttribute(origAttr)));
    });
  }
}

/** Load (or reload) a locale and apply it to the live DOM. */
export async function setLocaleStrings(locale) {
  const target = resolveSupported(locale) || DEFAULT_LOCALE;
  if (target !== DEFAULT_LOCALE) {
    try {
      _strings = await loadStrings(target);
    } catch (e) {
      console.warn('[i18n] failed to load', target, e);
      _strings = {};
    }
  } else {
    // en-US is the source language baked into the HTML; no JSON needed.
    _strings = {};
  }
  _locale = target;
  document.documentElement.setAttribute('lang', target.split('-')[0]);
  applyTranslations(document);
  return target;
}

/**
 * Change locale at user request: persist (localStorage + backend pref when
 * available) and re-render. `persistBackend` is false on the pre-auth login
 * page where the pref endpoint isn't reachable yet.
 */
export async function changeLocale(locale, { persistBackend = true } = {}) {
  const target = await setLocaleStrings(locale);
  try { localStorage.setItem(STORAGE_KEY, target); } catch (_) {}
  if (persistBackend) {
    try {
      await fetch('/api/prefs/locale', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: target }),
      });
    } catch (e) { console.warn('[i18n] could not persist locale pref', e); }
  }
  return target;
}

/**
 * Initialise i18n. Resolves the locale (optionally consulting the backend
 * pref for the authenticated app) and applies it. Idempotent — repeated calls
 * return the same in-flight/settled promise.
 *
 * @param {object}  opts
 * @param {boolean} opts.consultBackend  fetch /api/prefs/locale to override the
 *                                        local guess (default true).
 */
export function initI18n({ consultBackend = true } = {}) {
  if (_ready) return _ready;
  _ready = (async () => {
    let locale = detectLocale();
    if (consultBackend) {
      try {
        const res = await fetch('/api/prefs/locale', { credentials: 'same-origin' });
        if (res.ok) {
          const data = await res.json();
          const fromPref = resolveSupported(data && data.value);
          if (fromPref) {
            locale = fromPref;
            try { localStorage.setItem(STORAGE_KEY, fromPref); } catch (_) {}
          }
        }
      } catch (_) { /* offline / unauthenticated — keep local guess */ }
    }
    await setLocaleStrings(locale);
    return locale;
  })();
  return _ready;
}

// Expose a tiny global so non-module inline scripts and other bundles can
// translate dynamically-built strings without importing the module.
if (typeof window !== 'undefined') {
  window.i18n = { t, getLocale, changeLocale, applyTranslations, initI18n,
    SUPPORTED_LOCALES, LOCALE_NAMES, DEFAULT_LOCALE };
}
