// static/js/i18n.js -- lightweight runtime i18n for the static UI.

const DEFAULT_LOCALE = 'en';
const LOCALE_PREF_KEY = 'locale';
const LOCAL_STORAGE_KEY = 'odysseus-locale';
const SKIP_SELECTOR = [
  'script',
  'style',
  'code',
  'pre',
  'textarea',
  'input',
  '[contenteditable="true"]',
  '[data-i18n-skip]',
  '#chat-history',
  '.msg',
  '.body',
  '.markdown-body',
  '.cm-editor'
].join(',');
const ATTR_SKIP_SELECTOR = [
  'script',
  'style',
  'code',
  'pre',
  'textarea',
  '[contenteditable="true"]',
  '[data-i18n-skip]',
  '#chat-history',
  '.msg',
  '.body',
  '.markdown-body',
  '.cm-editor'
].join(',');

let currentLocale = DEFAULT_LOCALE;
let messages = {};
let fallbackMessages = {};
let locales = [];
let observerStarted = false;
let applying = false;
let applyTimer = null;

const textOriginals = new WeakMap();
const attrOriginals = new WeakMap();

function getByPath(obj, path) {
  if (!obj || !path) return undefined;
  return String(path).split('.').reduce((cur, part) => {
    if (cur && Object.prototype.hasOwnProperty.call(cur, part)) return cur[part];
    return undefined;
  }, obj);
}

function interpolate(value, params) {
  if (!params || typeof value !== 'string') return value;
  return value.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key) => {
    return Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : match;
  });
}

export function t(key, fallback, params) {
  const translated = getByPath(messages, key);
  if (typeof translated === 'string') return interpolate(translated, params);
  const fallbackTranslated = getByPath(fallbackMessages, key);
  if (typeof fallbackTranslated === 'string') return interpolate(fallbackTranslated, params);
  return interpolate(fallback !== undefined ? String(fallback) : String(key), params);
}

function legacyStrings() {
  const strings = messages && messages.strings;
  return strings && typeof strings === 'object' ? strings : {};
}

function translateRaw(raw) {
  const key = String(raw || '').trim();
  if (!key) return null;
  const strings = legacyStrings();
  if (Object.prototype.hasOwnProperty.call(strings, key)) return String(strings[key]);
  return null;
}

function withOriginalWhitespace(original, translated) {
  const prefix = (original.match(/^\s*/) || [''])[0];
  const suffix = (original.match(/\s*$/) || [''])[0];
  return prefix + translated + suffix;
}

function shouldSkipElement(el) {
  return !el || !el.closest || !!el.closest(SKIP_SELECTOR);
}

function shouldSkipAttrElement(el) {
  return !el || !el.closest || !!el.closest(ATTR_SKIP_SELECTOR);
}

function applyDataKeys(root) {
  const scope = root && root.querySelectorAll ? root : document;
  const direct = root && root.nodeType === Node.ELEMENT_NODE ? [root] : [];
  const keyed = direct.concat(Array.from(scope.querySelectorAll('[data-i18n]')));
  keyed.forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (key) el.textContent = t(key, el.textContent || '');
  });

  [
    ['data-i18n-title', 'title'],
    ['data-i18n-placeholder', 'placeholder'],
    ['data-i18n-aria-label', 'aria-label'],
    ['data-i18n-value', 'value']
  ].forEach(([dataAttr, targetAttr]) => {
    const attrKeyed = direct.concat(Array.from(scope.querySelectorAll(`[${dataAttr}]`)));
    attrKeyed.forEach(el => {
      const key = el.getAttribute(dataAttr);
      if (!key) return;
      const translated = t(key, el.getAttribute(targetAttr) || '');
      if (targetAttr === 'value' && 'value' in el) el.value = translated;
      else el.setAttribute(targetAttr, translated);
    });
  });
}

function applyTextNode(node) {
  if (!node || node.nodeType !== Node.TEXT_NODE || !node.nodeValue || !node.nodeValue.trim()) return;
  const parent = node.parentElement;
  if (shouldSkipElement(parent)) return;
  const original = textOriginals.get(node) || node.nodeValue;
  textOriginals.set(node, original);
  const translated = translateRaw(original);
  if (translated !== null) node.nodeValue = withOriginalWhitespace(original, translated);
}

function applyLegacyText(root) {
  const walkerRoot = root && root.nodeType === Node.ELEMENT_NODE ? root : document.body;
  if (!walkerRoot) return;
  if (walkerRoot.nodeType === Node.TEXT_NODE) {
    applyTextNode(walkerRoot);
    return;
  }
  const walker = document.createTreeWalker(walkerRoot, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      return shouldSkipElement(node.parentElement) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    }
  });
  let node = walker.nextNode();
  while (node) {
    applyTextNode(node);
    node = walker.nextNode();
  }
}

function originalAttrsFor(el) {
  let map = attrOriginals.get(el);
  if (!map) {
    map = {};
    attrOriginals.set(el, map);
  }
  return map;
}

function applyLegacyAttrs(root) {
  const scope = root && root.querySelectorAll ? root : document;
  const direct = root && root.nodeType === Node.ELEMENT_NODE ? [root] : [];
  const attrElements = direct.concat(Array.from(scope.querySelectorAll('[title], [placeholder], [aria-label]')));
  attrElements.forEach(el => {
    if (shouldSkipAttrElement(el)) return;
    const originals = originalAttrsFor(el);
    ['title', 'placeholder', 'aria-label'].forEach(attr => {
      if (!el.hasAttribute(attr)) return;
      if (!Object.prototype.hasOwnProperty.call(originals, attr)) {
        originals[attr] = el.getAttribute(attr) || '';
      }
      const translated = translateRaw(originals[attr]);
      if (translated !== null) el.setAttribute(attr, translated);
    });
  });
}

export function applyTranslations(root) {
  if (applying) return;
  applying = true;
  try {
    if (document.documentElement) document.documentElement.lang = currentLocale;
    applyDataKeys(root || document);
    applyLegacyText(root || document.body);
    applyLegacyAttrs(root || document);
  } finally {
    applying = false;
  }
}

function scheduleApply(root) {
  if (applying) return;
  clearTimeout(applyTimer);
  applyTimer = setTimeout(() => applyTranslations(root || document.body), 30);
}

function startObserver() {
  if (observerStarted || !document.body || typeof MutationObserver === 'undefined') return;
  observerStarted = true;
  const observer = new MutationObserver(mutations => {
    if (applying) return;
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        for (const node of mutation.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE || node.nodeType === Node.TEXT_NODE) {
            scheduleApply(node);
            return;
          }
        }
      } else if (mutation.type === 'attributes') {
        scheduleApply(mutation.target);
        return;
      }
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['title', 'placeholder', 'aria-label', 'data-i18n', 'data-i18n-title', 'data-i18n-placeholder', 'data-i18n-aria-label']
  });
}

async function fetchJson(url) {
  const res = await fetch(url, { credentials: 'same-origin', cache: 'no-cache' });
  if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
  return res.json();
}

async function loadLocaleMessages(locale) {
  const code = normalizeLocale(locale);
  return fetchJson(`/static/i18n/${encodeURIComponent(code)}.json?v=${Date.now()}`);
}

function normalizeLocale(locale) {
  return String(locale || DEFAULT_LOCALE).trim().replace(/_/g, '-').toLowerCase() || DEFAULT_LOCALE;
}

async function loadPreferredLocale() {
  try {
    const res = await fetch(`/api/prefs/${encodeURIComponent(LOCALE_PREF_KEY)}`, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      if (data && data.value) return normalizeLocale(data.value);
    }
  } catch (_) {}
  try {
    const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (stored) return normalizeLocale(stored);
  } catch (_) {}
  return DEFAULT_LOCALE;
}

async function savePreferredLocale(locale) {
  try { localStorage.setItem(LOCAL_STORAGE_KEY, locale); } catch (_) {}
  try {
    await fetch(`/api/prefs/${encodeURIComponent(LOCALE_PREF_KEY)}`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: locale })
    });
  } catch (e) {
    console.warn('[i18n] failed to save locale preference', e);
  }
}

export async function refreshLocales() {
  try {
    const data = await fetchJson('/api/i18n/locales');
    locales = Array.isArray(data.locales) ? data.locales : [];
  } catch (e) {
    console.warn('[i18n] failed to list locales', e);
    locales = [{ code: DEFAULT_LOCALE, name: 'English', nativeName: 'English', url: '/static/i18n/en.json' }];
  }
  return locales.slice();
}

export function getLocales() {
  return locales.slice();
}

export function getLocale() {
  return currentLocale;
}

export async function setLocale(locale) {
  const code = normalizeLocale(locale);
  let nextMessages;
  try {
    nextMessages = await loadLocaleMessages(code);
  } catch (e) {
    if (code !== DEFAULT_LOCALE) console.warn(`[i18n] failed to load ${code}, falling back to ${DEFAULT_LOCALE}`, e);
    nextMessages = fallbackMessages;
  }
  currentLocale = code;
  messages = nextMessages || fallbackMessages || {};
  await savePreferredLocale(currentLocale);
  applyTranslations(document.body);
  window.dispatchEvent(new CustomEvent('odysseus:i18n-changed', { detail: { locale: currentLocale } }));
  return currentLocale;
}

async function init() {
  try {
    fallbackMessages = await loadLocaleMessages(DEFAULT_LOCALE);
  } catch (e) {
    console.warn('[i18n] failed to load English fallback', e);
    fallbackMessages = {};
  }
  await refreshLocales();
  const preferred = await loadPreferredLocale();
  try {
    messages = await loadLocaleMessages(preferred);
    currentLocale = preferred;
  } catch (e) {
    messages = fallbackMessages;
    currentLocale = DEFAULT_LOCALE;
  }
  applyTranslations(document.body || document);
  startObserver();
  window.dispatchEvent(new CustomEvent('odysseus:i18n-ready', { detail: { locale: currentLocale } }));
}

const ready = init();

const i18n = {
  ready,
  t,
  getLocale,
  getLocales,
  setLocale,
  refreshLocales,
  applyTranslations
};

window.i18n = i18n;

export default i18n;
