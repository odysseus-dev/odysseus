const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English', nativeLabel: 'English' },
  { code: 'zh-CN', label: 'Simplified Chinese', nativeLabel: '简体中文' },
];

const STORAGE_KEY = 'odysseus-language';
const PREF_KEY = 'language';

function mapStorage(storage) {
  if (!storage) return null;
  if (typeof storage.getItem === 'function') return storage;
  if (typeof storage.get === 'function') {
    return {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    };
  }
  return null;
}

function getPath(obj, key) {
  if (!obj || !key) return undefined;
  return key.split('.').reduce((cur, part) => (
    cur && Object.prototype.hasOwnProperty.call(cur, part) ? cur[part] : undefined
  ), obj);
}

function interpolate(value, params) {
  if (typeof value !== 'string') return value == null ? '' : String(value);
  if (!params) return value;
  return value.replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (match, name) => {
    const replacement = params[name];
    return replacement == null ? '' : String(replacement);
  });
}

function normalizeLanguage(code) {
  if (!code) return 'en';
  const exact = SUPPORTED_LANGUAGES.find(lang => lang.code === code);
  if (exact) return exact.code;
  const lower = String(code).toLowerCase();
  if (lower === 'zh' || lower === 'zh-cn' || lower === 'zh-hans') return 'zh-CN';
  return 'en';
}

export function createI18nRuntime(options = {}) {
  const win = options.windowRef || (typeof window !== 'undefined' ? window : null);
  const doc = options.documentRef || (typeof document !== 'undefined' ? document : null);
  const storage = mapStorage(options.storage || win?.localStorage);
  const navigatorLanguages = options.navigatorLanguages || win?.navigator?.languages || [win?.navigator?.language].filter(Boolean);
  const fetchCatalog = options.fetchCatalog || (async (lang) => {
    const response = await fetch(`/static/i18n/${lang}.json`);
    if (!response.ok) throw new Error(`Unable to load locale ${lang}`);
    return response.json();
  });

  let language = normalizeLanguage(
    storage?.getItem(STORAGE_KEY)
      || storage?.getItem(`odysseus-pref-${PREF_KEY}`)
      || navigatorLanguages?.[0]
  );
  const catalogs = {};

  async function loadCatalog(lang) {
    if (catalogs[lang]) return catalogs[lang];
    catalogs[lang] = await fetchCatalog(lang);
    return catalogs[lang];
  }

  async function loadLanguage(lang) {
    const next = normalizeLanguage(lang);
    await loadCatalog('en');
    if (next !== 'en') {
      try {
        await loadCatalog(next);
      } catch (err) {
        console.warn('[i18n] failed to load locale, falling back to English:', next, err);
        language = 'en';
        return language;
      }
    }
    language = next;
    return language;
  }

  function t(key, params) {
    const active = getPath(catalogs[language], key);
    const fallback = getPath(catalogs.en, key);
    return interpolate(active ?? fallback ?? key, params);
  }

  function setNodeAttr(node, attr, value) {
    if (attr === 'text') {
      node.textContent = value;
    } else if (typeof node.setAttribute === 'function') {
      node.setAttribute(attr, value);
    } else {
      node[attr] = value;
    }
  }

  function applyToDocument(documentRef = doc) {
    if (!documentRef) return;
    if (documentRef.documentElement?.setAttribute) documentRef.documentElement.setAttribute('lang', language);
    const nodes = documentRef.querySelectorAll?.(
      '[data-i18n], [data-i18n-placeholder], [data-i18n-title], [data-i18n-aria-label], [data-i18n-value]'
    );
    if (!nodes) return;
    nodes.forEach(node => {
      const data = node.dataset || {};
      if (data.i18n) setNodeAttr(node, 'text', t(data.i18n));
      if (data.i18nPlaceholder) setNodeAttr(node, 'placeholder', t(data.i18nPlaceholder));
      if (data.i18nTitle) setNodeAttr(node, 'title', t(data.i18nTitle));
      if (data.i18nAriaLabel) setNodeAttr(node, 'aria-label', t(data.i18nAriaLabel));
      if (data.i18nValue) setNodeAttr(node, 'value', t(data.i18nValue));
    });
  }

  async function setLanguage(lang) {
    const next = await loadLanguage(lang);
    storage?.setItem(STORAGE_KEY, next);
    storage?.setItem(`odysseus-pref-${PREF_KEY}`, next);
    applyToDocument();
    if (doc && typeof doc.dispatchEvent === 'function' && typeof CustomEvent !== 'undefined') {
      doc.dispatchEvent(new CustomEvent('i18n:changed', { detail: { language: next } }));
    }
    return next;
  }

  const ready = loadLanguage(language).then(() => {
    applyToDocument();
    return language;
  });

  return {
    ready,
    t,
    setLanguage,
    getLanguage: () => language,
    getSupportedLanguages: () => SUPPORTED_LANGUAGES.slice(),
    applyToDocument,
  };
}

const runtime = typeof window !== 'undefined' ? createI18nRuntime() : null;

if (typeof window !== 'undefined') {
  window.i18n = runtime;
  window.t = runtime.t;
}

export default runtime;
