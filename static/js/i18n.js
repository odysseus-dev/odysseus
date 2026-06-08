// ============================================================
// i18n — lightweight internationalisation module
// Usage:  import i18n from './i18n.js';
//         await i18n.init();          // call once in app.js
//         i18n.t('key')               // translate a string
//         i18n.t('key', {n: 3})       // with variable substitution
//         i18n.setLang('zh')          // switch language at runtime
// HTML:   <span data-i18n="key"></span>          ← textContent
//         <input data-i18n-placeholder="key">    ← placeholder
//         <button data-i18n-title="key">         ← title attr
//         <el data-i18n-aria-label="key">        ← aria-label
// ============================================================

const STORAGE_KEY = 'odysseus-lang';
const SUPPORTED    = ['en', 'zh'];
const DEFAULT_LANG = 'en';

let _lang  = localStorage.getItem(STORAGE_KEY) || DEFAULT_LANG;
let _dict  = {};                // current language dictionary
let _fallback = {};             // English fallback (always loaded)
let _listeners = [];            // callbacks fired on language change

// ── internal helpers ─────────────────────────────────────────

async function _loadJson(lang) {
  try {
    const res = await fetch(`/static/locales/${lang}.json?v=${Date.now()}`);
    if (!res.ok) return {};
    return await res.json();
  } catch {
    return {};
  }
}

function _apply(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  root.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = t(el.getAttribute('data-i18n-html'));
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
  });
  root.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.getAttribute('data-i18n-title'));
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria-label')));
  });
}

// ── public API ────────────────────────────────────────────────

/** Translate a key with optional {variable} substitution */
function t(key, vars = {}) {
  if (!key) return '';
  let str = _dict[key] ?? _fallback[key] ?? key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replaceAll(`{${k}}`, String(v));
  }
  return str;
}

/** Load translations and apply data-i18n attributes on the whole document */
async function init() {
  // Always keep English as fallback
  _fallback = await _loadJson('en');
  if (_lang !== 'en') {
    _dict = await _loadJson(_lang);
  } else {
    _dict = _fallback;
  }
  // Set html[lang]
  document.documentElement.lang = _lang === 'zh' ? 'zh-CN' : 'en';
  _apply();
}

/** Switch language, re-apply all data-i18n bindings, fire listeners */
async function setLang(lang) {
  if (!SUPPORTED.includes(lang)) return;
  _lang = lang;
  localStorage.setItem(STORAGE_KEY, lang);
  if (lang !== 'en') {
    _dict = await _loadJson(lang);
  } else {
    _dict = _fallback;
  }
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  _apply();
  _listeners.forEach(fn => fn(lang));
}

function getLang() { return _lang; }

/** Register a callback for language changes */
function onLangChange(fn) { _listeners.push(fn); }

/** Manually apply translations to a newly-injected subtree */
function applyTo(root) { _apply(root); }

export default { t, init, setLang, getLang, onLangChange, applyTo, SUPPORTED };
