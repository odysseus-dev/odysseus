// static/js/i18n.js — UI locale switching (English default + Spanish)
import en from './locales/en.js';
import es from './locales/es.js';

export const STORAGE_KEY = 'odysseus-locale';
export const DEFAULT_LOCALE = 'en';
export const SUPPORTED_LOCALES = ['en', 'es'];

const LOCALES = { en, es };

let currentLocale = DEFAULT_LOCALE;
const listeners = new Set();

/** Tracks #current-meta so locale changes re-apply translated labels. */
const _chatMeta = { mode: 'default', custom: '' };

function renderChatMeta() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('current-meta');
  if (!el) return;
  const { mode, custom } = _chatMeta;
  if (mode === 'new') el.textContent = t('chat.meta.newChat');
  else if (mode === 'default') el.textContent = t('chat.meta.title');
  else el.textContent = custom;
}

/** @param {'default'|'new'|'custom'} mode */
export function setChatMeta(mode, custom = '') {
  _chatMeta.mode = mode;
  _chatMeta.custom = custom;
  renderChatMeta();
}

function resolve(key) {
  const dict = LOCALES[currentLocale] || LOCALES[DEFAULT_LOCALE];
  if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
  return LOCALES[DEFAULT_LOCALE][key] ?? key;
}

export function getLocale() {
  return currentLocale;
}

export function t(key, vars = {}) {
  let str = resolve(key);
  for (const [name, value] of Object.entries(vars)) {
    str = str.replaceAll(`{${name}}`, String(value));
  }
  return str;
}

export function onLocaleChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

listeners.add(() => renderChatMeta());

function applyAttr(root, attr, dataAttr) {
  root.querySelectorAll(`[${dataAttr}]`).forEach((node) => {
    const key = node.getAttribute(dataAttr);
    if (!key) return;
    node.setAttribute(attr, t(key));
  });
}

export function applySelectOptions(root = document) {
  if (typeof document === 'undefined' || !root) return;
  root.querySelectorAll('optgroup[data-i18n-label]').forEach((node) => {
    const key = node.getAttribute('data-i18n-label');
    if (key) node.label = t(key);
  });
  root.querySelectorAll('option[data-i18n]').forEach((node) => {
    const key = node.getAttribute('data-i18n');
    if (key) node.textContent = t(key);
  });
}

export function applyTranslations(root = document) {
  if (typeof document === 'undefined' || !root) return;
  root.querySelectorAll('[data-i18n]').forEach((node) => {
    const key = node.getAttribute('data-i18n');
    if (key) node.textContent = t(key);
  });
  applySelectOptions(root);
  applyAttr(root, 'title', 'data-i18n-title');
  applyAttr(root, 'placeholder', 'data-i18n-placeholder');
  applyAttr(root, 'aria-label', 'data-i18n-aria');
}

function syncLocaleSelector() {
  if (typeof document === 'undefined') return;
  const seg = document.getElementById('locale-seg');
  if (!seg) return;
  seg.classList.toggle('is-es', currentLocale === 'es');
  seg.querySelectorAll('[data-locale]').forEach((btn) => {
    const active = btn.dataset.locale === currentLocale;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function initLocaleSelector() {
  if (typeof document === 'undefined') return;
  const seg = document.getElementById('locale-seg');
  if (!seg || seg.dataset.i18nBound === '1') return;
  seg.dataset.i18nBound = '1';
  syncLocaleSelector();
  seg.querySelectorAll('[data-locale]').forEach((btn) => {
    btn.addEventListener('click', () => {
      void setLocale(btn.dataset.locale);
    });
  });
}

async function persistLocale(locale) {
  try {
    await fetch('/api/prefs/ui_locale', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: locale }),
    });
  } catch (_) {
    /* offline / auth disabled — localStorage is enough */
  }
}

export async function setLocale(locale) {
  const next = SUPPORTED_LOCALES.includes(locale) ? locale : DEFAULT_LOCALE;
  if (next === currentLocale) {
    syncLocaleSelector();
    return;
  }
  currentLocale = next;
  try { localStorage.setItem(STORAGE_KEY, currentLocale); } catch (_) {}
  if (typeof document !== 'undefined') {
    document.documentElement.lang = currentLocale;
    applyTranslations();
  }
  syncLocaleSelector();
  listeners.forEach((fn) => {
    try { fn(currentLocale); } catch (_) {}
  });
  await persistLocale(currentLocale);
}

export async function initI18n() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && SUPPORTED_LOCALES.includes(saved)) currentLocale = saved;
  } catch (_) {}

  try {
    const res = await fetch('/api/prefs/ui_locale', { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      if (data?.value && SUPPORTED_LOCALES.includes(data.value)) {
        currentLocale = data.value;
        try { localStorage.setItem(STORAGE_KEY, currentLocale); } catch (_) {}
      }
    }
  } catch (_) {}

  if (typeof document !== 'undefined') {
    document.documentElement.lang = currentLocale;
    applyTranslations();
    initLocaleSelector();
  }
}

export default { t, getLocale, setLocale, initI18n, applyTranslations, applySelectOptions, onLocaleChange, setChatMeta };
