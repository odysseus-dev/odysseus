import {
  createTranslationObserver,
  LANGUAGE_STORAGE_KEY,
  installLanguageSelectors,
  loadCatalog,
  loadLocales,
  lookup,
  normalizeLocale,
  setDocumentLanguage,
  translateRoot,
} from './i18n-core.mjs';

const state = {
  locales: [],
  locale: 'en',
  messages: {},
};
let translationObserver = null;

function storedLocale() {
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch (_) {
    return null;
  }
}

function saveLocale(locale) {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
  } catch (_) {}
}

function browserLocale() {
  return navigator.languages?.[0] || navigator.language || 'en';
}

function translatePage() {
  translateRoot(document, state.messages);
}

function translateNode(node) {
  translateRoot(node, state.messages);
}

function installLiveTranslations() {
  translationObserver?.disconnect?.();
  translationObserver = createTranslationObserver(document, () => state.messages);
}

async function applyLocale(locale) {
  const available = state.locales.map((item) => item.code);
  state.locale = normalizeLocale(locale, available);
  const localeInfo = state.locales.find((item) => item.code === state.locale);
  state.messages = await loadCatalog(state.locale);
  saveLocale(state.locale);
  setDocumentLanguage(document, localeInfo);
  translatePage();
  installLanguageSelectors(document, state.locales, state.locale, applyLocale);
}

export async function initI18n() {
  try {
    state.locales = await loadLocales();
    const initial = storedLocale() || browserLocale();
    await applyLocale(initial);
    installLiveTranslations();
  } catch (err) {
    console.warn('[i18n] Falling back to English source strings', err);
  }
}

window.OdysseusI18n = {
  init: initI18n,
  setLocale: applyLocale,
  t: (msgid) => lookup(state.messages, msgid),
  translateNode,
  translatePage,
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initI18n, { once: true });
} else {
  initI18n();
}
