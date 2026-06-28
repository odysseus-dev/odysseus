// static/js/i18n.js — Lightweight i18n module for Odysseus UI
// ES6 module — provides t() for translations and language switching

import Storage from './storage.js';

const STORAGE_KEY = 'odysseus-lang';
const DEFAULT_LANG = 'en';
const SUPPORTED_LANGS = { en: 'English', ru: 'Русский' };

let _currentLang = null;
let _translations = {};
let _fallback = {};

async function _loadLocale(lang) {
  try {
    const mod = await import(`/static/locales/${lang}.js`);
    return mod.default || mod;
  } catch (_) {
    return {};
  }
}

async function _ensureLoaded(lang) {
  if (_translations[lang]) return;
  _translations[lang] = await _loadLocale(lang);
  if (lang !== DEFAULT_LANG && !_fallback[DEFAULT_LANG]) {
    _fallback[DEFAULT_LANG] = await _loadLocale(DEFAULT_LANG);
  }
}

function _detectLang() {
  const stored = Storage.get(STORAGE_KEY);
  if (stored && SUPPORTED_LANGS[stored]) return stored;
  const nav = (navigator.language || '').toLowerCase();
  if (nav.startsWith('ru')) return 'ru';
  return DEFAULT_LANG;
}

export async function initI18n() {
  _currentLang = _detectLang();
  await Promise.all([
    _ensureLoaded(_currentLang),
    _ensureLoaded(DEFAULT_LANG),
  ]);
  document.documentElement.lang = _currentLang === 'ru' ? 'ru' : 'en';
}

export function t(key, vars) {
  const lang = _currentLang || DEFAULT_LANG;
  let val = (_translations[lang] && _translations[lang][key])
    || (_fallback[DEFAULT_LANG] && _fallback[DEFAULT_LANG][key])
    || key;
  if (vars && typeof val === 'string') {
    for (const [k, v] of Object.entries(vars)) {
      val = val.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
    }
  }
  return val;
}

export function getLang() {
  return _currentLang || DEFAULT_LANG;
}

export async function setLang(lang) {
  if (!SUPPORTED_LANGS[lang]) return;
  _currentLang = lang;
  Storage.set(STORAGE_KEY, lang);
  await _ensureLoaded(lang);
  document.documentElement.lang = lang === 'ru' ? 'ru' : 'en';
  document.dispatchEvent(new CustomEvent('i18n:changed', { detail: { lang } }));
}

export function getSupportedLangs() {
  return { ...SUPPORTED_LANGS };
}

export function applyTranslations(root) {
  root = root || document;
  root.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (!key) return;
    const val = t(key);
    if (val === key) return;
    // Find the first text node child, or append one — never destroy child elements
    const children = Array.from(el.childNodes);
    const firstText = children.find(n => n.nodeType === 3);
    if (firstText) {
      firstText.textContent = val;
    } else {
      el.appendChild(document.createTextNode(val));
    }
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (key) el.placeholder = t(key);
  });
  root.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.getAttribute('data-i18n-title');
    if (key) el.title = t(key);
  });
  root.querySelectorAll('[data-i18n-aria]').forEach(el => {
    const key = el.getAttribute('data-i18n-aria');
    if (key) el.setAttribute('aria-label', t(key));
  });
}

// Make t() available globally for inline scripts and non-module code
window.__t = t;
window.__getLang = getLang;
window.__setLang = async (lang) => {
  await setLang(lang);
  // Reload the page so all JS-generated content picks up the new language
  // Guard against infinite reload loops
  if (!window.__i18nReloading) {
    window.__i18nReloading = true;
    setTimeout(() => { window.location.reload(); }, 50);
  }
};
window.__applyTranslations = applyTranslations;

export default { initI18n, t, getLang, setLang, getSupportedLangs, applyTranslations };
