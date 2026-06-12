// static/js/i18n.js — Client-side UI localization (en default, pt-BR optional)

export const LOCALE_KEY = 'odysseus-locale';
export const DEFAULT_LOCALE = 'en';
const SUPPORTED = new Set(['en', 'pt-BR']);

let _locale = DEFAULT_LOCALE;
let _dict = {};
let _enDict = {};
let _ready = null;

function _normalizeLocale(raw) {
  if (!raw || typeof raw !== 'string') return DEFAULT_LOCALE;
  const v = raw.trim();
  if (SUPPORTED.has(v)) return v;
  if (v.toLowerCase() === 'pt-br' || v.toLowerCase() === 'pt_br') return 'pt-BR';
  return DEFAULT_LOCALE;
}

function _readStoredLocale() {
  try {
    return _normalizeLocale(localStorage.getItem(LOCALE_KEY));
  } catch (_) {
    return DEFAULT_LOCALE;
  }
}

async function _loadDict(locale) {
  const res = await fetch(`/static/i18n/${locale}.json`, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`i18n load failed: ${locale}`);
  return res.json();
}

/**
 * Translate a key. Missing keys fall back to English, then to `fallback`, then to `key`.
 */
export function t(key, fallback) {
  if (!key) return fallback ?? '';
  const val = _dict[key] ?? _enDict[key];
  if (val != null && val !== '') return val;
  if (fallback != null && fallback !== '') return fallback;
  return key;
}

export function getLocale() {
  return _locale;
}

function _setDocumentLang(locale) {
  document.documentElement.lang = locale === 'pt-BR' ? 'pt-BR' : 'en';
}

function _applyAttr(root, attr, dataAttr) {
  root.querySelectorAll(`[${dataAttr}]`).forEach((el) => {
    const key = el.getAttribute(dataAttr);
    if (!key) return;
    const fb = el.getAttribute('data-i18n-fallback') || el.getAttribute(attr) || '';
    el.setAttribute(attr, t(key, fb));
  });
}

/**
 * Apply translations to elements marked with data-i18n* attributes under `root`.
 */
export function applyI18n(root = document) {
  if (!root || !root.querySelectorAll) return;

  root.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    const fb = el.getAttribute('data-i18n-fallback') ?? el.textContent;
    const val = t(key, fb);
    if (el.hasAttribute('data-i18n-html')) {
      el.innerHTML = val;
    } else {
      el.textContent = val;
    }
  });

  _applyAttr(root, 'placeholder', 'data-i18n-placeholder');
  _applyAttr(root, 'title', 'data-i18n-title');
  _applyAttr(root, 'aria-label', 'data-i18n-aria-label');

  const localeSel = root.querySelector ? root.querySelector('#settings-locale-select') : null;
  if (localeSel) {
    const enOpt = localeSel.querySelector('option[value="en"]');
    const ptOpt = localeSel.querySelector('option[value="pt-BR"]');
    if (enOpt) enOpt.textContent = t('settings.language.en', 'English');
    if (ptOpt) ptOpt.textContent = t('settings.language.ptBR', 'Português (Brasil)');
  }

  refreshWelcomeI18n();
}

export function refreshWelcomeI18n() {
  const tip = document.getElementById('welcome-tip');
  if (tip) tip.textContent = t('welcome.tip', tip.textContent);

  const sub = document.getElementById('welcome-sub');
  if (!sub) return;
  const link = sub.querySelector('.setup-trigger-link');
  if (link) {
    link.textContent = t('welcome.setupLink', link.textContent);
    link.title = t('welcome.setupTitle', link.title || 'Click to launch setup');
  }
  const parts = [
    t('welcome.greeting', 'Welcome,'),
    link ? link.outerHTML : t('welcome.setupLink', 'type /setup'),
    t('welcome.getStarted', ' to get started.'),
  ];
  sub.innerHTML = parts.join(' ');
}

export async function setLocale(locale, { persist = true, apply = true } = {}) {
  const next = _normalizeLocale(locale);
  if (next === _locale && Object.keys(_dict).length) {
    if (apply) applyI18n();
    return;
  }
  _locale = next;
  if (persist) {
    try {
      if (_locale === DEFAULT_LOCALE) localStorage.removeItem(LOCALE_KEY);
      else localStorage.setItem(LOCALE_KEY, _locale);
    } catch (_) {}
  }
  _setDocumentLang(_locale);
  if (_locale === DEFAULT_LOCALE) {
    _dict = { ..._enDict };
  } else {
    try {
      _dict = await _loadDict(_locale);
    } catch (e) {
      console.warn('[i18n] locale load failed, falling back to English', e);
      _locale = DEFAULT_LOCALE;
      _dict = { ..._enDict };
      if (persist) {
        try { localStorage.removeItem(LOCALE_KEY); } catch (_) {}
      }
    }
  }
  if (apply) applyI18n();
  try {
    window.dispatchEvent(new CustomEvent('odysseus-locale-change', { detail: { locale: _locale } }));
  } catch (_) {}
}

export function initI18n() {
  if (_ready) return _ready;
  _locale = _readStoredLocale();
  _setDocumentLang(_locale);
  _ready = (async () => {
    _enDict = await _loadDict(DEFAULT_LOCALE);
    if (_locale === DEFAULT_LOCALE) {
      _dict = { ..._enDict };
    } else {
      try {
        _dict = await _loadDict(_locale);
      } catch (_) {
        _locale = DEFAULT_LOCALE;
        _dict = { ..._enDict };
      }
    }
    applyI18n();
    return _locale;
  })();
  return _ready;
}

/** Early head bootstrap — sets html[lang] before first paint */
export function bootstrapLocaleFromStorage() {
  _setDocumentLang(_readStoredLocale());
}

const i18nModule = { t, getLocale, setLocale, initI18n, applyI18n, refreshWelcomeI18n, LOCALE_KEY, DEFAULT_LOCALE, bootstrapLocaleFromStorage };
export default i18nModule;

if (typeof window !== 'undefined') {
  window.t = t;
  window.i18nModule = i18nModule;
}
