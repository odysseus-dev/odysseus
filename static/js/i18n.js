const STORAGE_KEY = 'odysseus.locale';
const RESOURCE_ROOT = '/static/i18n';
const TRANSLATABLE_ATTRIBUTES = ['title', 'placeholder', 'aria-label', 'aria-description', 'alt'];

// Never run legacy string matching over user-authored or model-authored text.
const USER_CONTENT_SELECTOR = [
  '[data-user-content]',
  '.msg .body',
  '.document-content',
  '.document-title',
  '.note-editor',
  '.note-content-preview',
  '.note-title',
  '.memory-item-content',
  '.session-title',
  '.email-reader-body',
  '.email-subject',
  '.email-sender',
  '.research-job-report-body',
  '.task-log-row-body',
].join(',');
const SKIP_SELECTOR = [
  'script',
  'style',
  'code',
  'pre',
  'textarea',
  '[contenteditable]',
  '[data-i18n-skip]',
  USER_CONTENT_SELECTOR,
].join(',');
const SEMANTIC_SELECTOR = [
  '[data-i18n]',
  ...TRANSLATABLE_ATTRIBUTES.map(attribute => `[data-i18n-${attribute}]`),
].join(',');
const USER_DIRECTION_SELECTOR = [
  'input',
  'textarea',
  '[contenteditable]',
  USER_CONTENT_SELECTOR,
].join(',');

const CSS_MESSAGES = {
  '--i18n-css-copy': 'Copy',
  '--i18n-css-copied': '✓ Copied',
  '--i18n-css-edit': 'Edit',
  '--i18n-css-editing': 'EDITING',
  '--i18n-css-save': 'Save',
  '--i18n-css-run': 'Run',
  '--i18n-css-enabled': 'Enabled',
  '--i18n-css-disabled': 'Disabled',
  '--i18n-css-show-more': 'Show more',
  '--i18n-css-show-less': 'Show less',
  '--i18n-css-archive': 'Archive',
  '--i18n-css-no-todos': 'No todos',
  '--i18n-css-drop-to-attach': 'Drop to attach',
  '--i18n-css-write-email': 'Write your email…',
  '--i18n-css-planning-goal': 'AI is planning your goal…',
  '--i18n-css-no-title': 'No title',
};

let registry = null;
let english = {};
let catalog = {};
let locale = 'en';
let exactTranslations = new Map();
let templateTranslations = [];
let observer = null;
let applying = false;
let localeRequest = 0;
const catalogRequests = new Map();
const nativeDialogs = typeof window === 'undefined' ? null : {
  alert: window.alert.bind(window),
  confirm: window.confirm.bind(window),
  prompt: window.prompt.bind(window),
};

const enrolledText = new Set();
const textState = new WeakMap();
const enrolledAttributeElements = new Set();
const attributeState = new WeakMap();

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage may be blocked by browser privacy settings; the active locale
    // still applies for this page.
  }
}

async function fetchJson(name) {
  const response = await fetch(`${RESOURCE_ROOT}/${name}.json`, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`Unable to load language resource: ${name}`);
  return response.json();
}

function fetchCatalog(name) {
  if (!catalogRequests.has(name)) {
    catalogRequests.set(
      name,
      fetchJson(name).catch(error => {
        catalogRequests.delete(name);
        throw error;
      }),
    );
  }
  return catalogRequests.get(name);
}

export function interpolate(value, parameters = {}) {
  return String(value).replace(
    /\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}/g,
    (placeholder, name) => (
      Object.hasOwn(parameters, name) ? String(parameters[name]) : placeholder
    ),
  );
}

export function matchLocale(requestedLocales, localeRegistry) {
  const aliases = localeRegistry?.aliases || {};
  const locales = localeRegistry?.locales || {};
  for (const requested of requestedLocales || []) {
    if (typeof requested !== 'string' || !requested) continue;
    const tag = requested;
    const lower = tag.toLowerCase();
    const exact = Object.keys(locales).find(id => id.toLowerCase() === lower);
    if (exact) return exact;
    const alias = Object.entries(aliases).find(([id]) => id.toLowerCase() === lower);
    if (alias && Object.hasOwn(locales, alias[1])) return alias[1];
    if (lower.startsWith('zh-hant') && Object.hasOwn(locales, 'zh-TW')) return 'zh-TW';
    if (lower.startsWith('zh-hans') && Object.hasOwn(locales, 'zh-CN')) return 'zh-CN';
    const base = lower.split('-')[0];
    const baseLocale = Object.keys(locales).find(id => id.toLowerCase() === base);
    if (baseLocale) return baseLocale;
    const baseAlias = Object.entries(aliases).find(([id]) => id.toLowerCase() === base);
    if (baseAlias && Object.hasOwn(locales, baseAlias[1])) return baseAlias[1];
  }
  const fallback = localeRegistry?.default_locale;
  if (typeof fallback === 'string' && Object.hasOwn(locales, fallback)) return fallback;
  return Object.hasOwn(locales, 'en') ? 'en' : (Object.keys(locales)[0] || 'en');
}

function lookupTranslation(key) {
  if (Object.hasOwn(catalog, key)) return catalog[key];
  if (Object.hasOwn(english, key)) return english[key];
  return key;
}

function localeMetadata(id) {
  const locales = registry?.locales || {};
  return Object.hasOwn(locales, id) ? locales[id] : null;
}

function rebuildLegacyIndex() {
  exactTranslations = new Map();
  templateTranslations = [];
  for (const [key, source] of Object.entries(english)) {
    const target = Object.hasOwn(catalog, key) ? catalog[key] : source;
    exactTranslations.set(source, target);
    const parameters = [];
    let cursor = 0;
    let pattern = '';
    for (const match of source.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}/g)) {
      pattern += source
        .slice(cursor, match.index)
        .replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      pattern += '([\\s\\S]*?)';
      parameters.push(match[1]);
      cursor = match.index + match[0].length;
    }
    const literalLength = source.length - parameters.reduce(
      (total, name) => total + name.length + 2,
      0,
    );
    if (parameters.length && literalLength >= 8 && /[A-Za-z]{4}/u.test(source)) {
      pattern += source.slice(cursor).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      templateTranslations.push({
        pattern: new RegExp(`^${pattern}$`, 'u'),
        parameters,
        target,
        literalLength,
      });
    }
  }
  templateTranslations.sort((left, right) => right.literalLength - left.literalLength);
}

function translateLegacy(value) {
  if (locale === 'en') return value;
  return exactTranslations.has(value) ? exactTranslations.get(value) : value;
}

function translateMessage(value) {
  if (locale === 'en' || exactTranslations.has(value)) return translateLegacy(value);
  for (const template of templateTranslations) {
    const match = String(value).match(template.pattern);
    if (!match) continue;
    const parameters = Object.create(null);
    template.parameters.forEach((name, index) => {
      parameters[name] = match[index + 1];
    });
    return interpolate(template.target, parameters);
  }
  return value;
}

function shouldSkipLegacy(node) {
  const parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  return !parent || Boolean(parent.closest(SKIP_SELECTOR));
}

function semanticParameters(element) {
  const parameters = Object.create(null);
  for (const attribute of element.attributes) {
    if (!attribute.name.startsWith('data-i18n-param-')) continue;
    parameters[attribute.name.slice('data-i18n-param-'.length)] = attribute.value;
  }
  return parameters;
}

function applySemantic(element) {
  if (element.closest('[data-i18n-skip]')) return;
  const parameters = semanticParameters(element);
  const textKey = element.getAttribute('data-i18n');
  if (textKey) {
    const translated = interpolate(lookupTranslation(textKey), parameters);
    if (element.textContent !== translated) element.textContent = translated;
  }
  for (const attribute of TRANSLATABLE_ATTRIBUTES) {
    const key = element.getAttribute(`data-i18n-${attribute}`);
    if (!key) continue;
    const translated = interpolate(lookupTranslation(key), parameters);
    if (element.getAttribute(attribute) !== translated) {
      element.setAttribute(attribute, translated);
    }
  }
}

function captureTextNode(node) {
  if (
    shouldSkipLegacy(node)
    || node.parentElement.closest('[data-i18n]')
    || !node.nodeValue.trim()
  ) return;
  enrolledText.add(node);
  textState.set(node, { source: node.nodeValue, lastRendered: node.nodeValue });
}

function captureElementAttributes(element) {
  if (shouldSkipLegacy(element)) return;
  const attributes = new Map();
  for (const attribute of TRANSLATABLE_ATTRIBUTES) {
    if (
      !element.hasAttribute(attribute)
      || element.hasAttribute(`data-i18n-${attribute}`)
    ) continue;
    const source = element.getAttribute(attribute);
    attributes.set(attribute, { source, lastRendered: source });
  }
  if (!attributes.size) return;
  enrolledAttributeElements.add(element);
  attributeState.set(element, attributes);
}

function captureStaticTree(root = document.documentElement) {
  if (root.nodeType === Node.ELEMENT_NODE) captureElementAttributes(root);
  if (root.nodeType === Node.TEXT_NODE) captureTextNode(root);
  const walker = document.createTreeWalker(
    root,
    NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
    {
      acceptNode(node) {
        if (node.nodeType === Node.ELEMENT_NODE && node.matches(SKIP_SELECTOR)) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    },
  );
  while (walker.nextNode()) {
    if (walker.currentNode.nodeType === Node.TEXT_NODE) {
      captureTextNode(walker.currentNode);
    } else {
      captureElementAttributes(walker.currentNode);
    }
  }
}

function translatedStaticText(source) {
  const leading = source.match(/^\s*/u)?.[0] || '';
  const trailing = source.match(/\s*$/u)?.[0] || '';
  const core = source.slice(leading.length, source.length - trailing.length);
  const translated = translateLegacy(core);
  return translated === core ? source : `${leading}${translated}${trailing}`;
}

function translateEnrolled() {
  for (const node of enrolledText) {
    const state = textState.get(node);
    if (!node.isConnected || !state) {
      enrolledText.delete(node);
      continue;
    }
    const current = node.nodeValue;
    if (current !== state.source && current !== state.lastRendered) {
      enrolledText.delete(node);
      textState.delete(node);
      continue;
    }
    const translated = translatedStaticText(state.source);
    if (translated !== current) node.nodeValue = translated;
    state.lastRendered = translated;
  }

  for (const element of enrolledAttributeElements) {
    const states = attributeState.get(element);
    if (!element.isConnected || !states) {
      enrolledAttributeElements.delete(element);
      continue;
    }
    for (const [attribute, state] of states) {
      const current = element.getAttribute(attribute);
      if (
        element.hasAttribute(`data-i18n-${attribute}`)
        || (current !== state.source && current !== state.lastRendered)
      ) {
        states.delete(attribute);
        continue;
      }
      const translated = translateLegacy(state.source);
      if (translated !== current) element.setAttribute(attribute, translated);
      state.lastRendered = translated;
    }
    if (!states.size) {
      enrolledAttributeElements.delete(element);
      attributeState.delete(element);
    }
  }
}

function markUserDirections(root) {
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  const elements = root.matches(USER_DIRECTION_SELECTOR)
    ? [root, ...root.querySelectorAll(USER_DIRECTION_SELECTOR)]
    : [...root.querySelectorAll(USER_DIRECTION_SELECTOR)];
  for (const element of elements) {
    if (!element.hasAttribute('dir')) element.setAttribute('dir', 'auto');
  }
}

function applySemanticTree(root) {
  if (root.nodeType === Node.TEXT_NODE) {
    const owner = root.parentElement?.closest(SEMANTIC_SELECTOR);
    if (owner) applySemantic(owner);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  if (root.matches(SEMANTIC_SELECTOR)) applySemantic(root);
  for (const element of root.querySelectorAll(SEMANTIC_SELECTOR)) applySemantic(element);
}

function renderDocument() {
  applying = true;
  try {
    translateEnrolled();
    applySemanticTree(document.documentElement);
    markUserDirections(document.documentElement);
  } finally {
    queueMicrotask(() => {
      applying = false;
    });
  }
}

function hydrateLanguageControls() {
  for (const select of document.querySelectorAll('[data-language-select]')) {
    const priorValue = select.value;
    select.replaceChildren();
    for (const [id, meta] of Object.entries(registry.locales)) {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = meta.name;
      option.lang = id;
      option.dir = meta.dir;
      option.setAttribute('data-i18n-skip', '');
      select.appendChild(option);
    }
    select.value = Object.hasOwn(registry.locales, locale) ? locale : priorValue;
  }
}

function syncCssMessages() {
  for (const [property, source] of Object.entries(CSS_MESSAGES)) {
    document.documentElement.style.setProperty(property, JSON.stringify(translateLegacy(source)));
  }
}

function announceLanguageChange() {
  let status = document.getElementById('i18n-language-status');
  if (!status) {
    status = document.createElement('div');
    status.id = 'i18n-language-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.style.cssText = 'position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0';
    document.body.appendChild(status);
  }
  status.lang = locale;
  status.dir = localeMetadata(locale)?.dir || 'ltr';
  status.textContent = translateLegacy('Language changed.');
}

async function setLocale(nextLocale, { persist = true, announce = persist } = {}) {
  const request = ++localeRequest;
  const locales = registry?.locales || {};
  const configuredFallback = registry?.default_locale;
  const fallback = (
    typeof configuredFallback === 'string'
    && Object.hasOwn(locales, configuredFallback)
  )
    ? configuredFallback
    : (Object.hasOwn(locales, 'en') ? 'en' : (Object.keys(locales)[0] || 'en'));
  if (typeof nextLocale !== 'string' || !Object.hasOwn(locales, nextLocale)) {
    nextLocale = fallback;
  }
  const nextCatalog = nextLocale === 'en' ? english : await fetchCatalog(nextLocale);
  if (request !== localeRequest) return locale;
  locale = nextLocale;
  catalog = nextCatalog;
  rebuildLegacyIndex();
  document.documentElement.lang = locale;
  document.documentElement.dir = localeMetadata(locale)?.dir || 'ltr';
  const manifest = document.querySelector('link[rel="manifest"]');
  if (typeof window.__odysseusUpdateRouteManifest === 'function') {
    window.__odysseusUpdateRouteManifest(locale, translateLegacy);
  } else if (manifest) {
    manifest.href = `/static/manifest.${locale}.json`;
  }
  if (persist) safeStorageSet(STORAGE_KEY, locale);
  renderDocument();
  hydrateLanguageControls();
  syncCssMessages();
  if (announce) announceLanguageChange();
  document.dispatchEvent(new CustomEvent('odysseus:languagechange', {
    detail: { locale },
  }));
  return locale;
}

async function init() {
  captureStaticTree();
  markUserDirections(document.documentElement);
  [registry, english] = await Promise.all([fetchJson('registry'), fetchJson('en')]);
  const saved = safeStorageGet(STORAGE_KEY);
  const requested = saved
    ? matchLocale([saved], registry)
    : matchLocale([registry.default_locale], registry);
  try {
    await setLocale(requested, { persist: false, announce: false });
  } catch (error) {
    if (requested === registry.default_locale) throw error;
    console.warn(`[i18n] unable to load ${requested}; using ${registry.default_locale}`, error);
    await setLocale(registry.default_locale, { persist: false, announce: false });
  }
  // Existing modules still have a few native-dialog fallbacks. Translation is
  // catalog-gated: unknown text is returned byte-for-byte, and placeholders
  // preserve dynamic values.
  window.alert = message => nativeDialogs.alert(translateMessage(message));
  window.confirm = message => nativeDialogs.confirm(translateMessage(message));
  window.prompt = (message, defaultValue) => (
    nativeDialogs.prompt(translateMessage(message), defaultValue)
  );

  document.addEventListener('change', event => {
    if (!(event.target instanceof Element) || !event.target.matches('[data-language-select]')) return;
    setLocale(event.target.value).catch(error => {
      console.error('[i18n]', error);
      hydrateLanguageControls();
    });
  });

  observer = new MutationObserver(records => {
    if (applying) return;
    applying = true;
    try {
      for (const record of records) {
        if (record.type === 'characterData') {
          applySemanticTree(record.target);
          continue;
        }
        if (record.type === 'attributes') {
          if (
            record.attributeName === 'data-i18n'
            || TRANSLATABLE_ATTRIBUTES.some(
              attribute => record.attributeName === `data-i18n-${attribute}`,
            )
            || record.attributeName.startsWith('data-i18n-param-')
          ) {
            applySemantic(record.target);
          }
          if (
            record.attributeName === 'contenteditable'
            || record.attributeName === 'data-user-content'
            || record.attributeName === 'class'
          ) {
            markUserDirections(record.target);
          }
          continue;
        }
        for (const node of record.addedNodes) {
          applySemanticTree(node);
          markUserDirections(node);
        }
      }
    } finally {
      queueMicrotask(() => {
        applying = false;
      });
    }
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
  });
}

function applyStoredDocumentMetadata() {
  const saved = safeStorageGet(STORAGE_KEY);
  if (!saved || !/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/u.test(saved)) return;
  document.documentElement.lang = saved;
  document.documentElement.dir = saved === 'ar' ? 'rtl' : 'ltr';
}

if (typeof document !== 'undefined') applyStoredDocumentMetadata();

const ready = typeof document === 'undefined'
  ? Promise.resolve()
  : init().catch(error => console.error('[i18n]', error));

if (typeof window !== 'undefined') {
  window.odysseusI18n = {
    ready,
    get locale() {
      return locale;
    },
    get locales() {
      return registry?.locales || {};
    },
    setLocale,
    t(key, parameters) {
      return interpolate(lookupTranslation(key), parameters);
    },
    translateLegacy,
    translateMessage,
    plural(count, forms) {
      const category = new Intl.PluralRules(locale).select(count);
      return forms[category] ?? forms.other;
    },
    formatNumber(value, options) {
      return new Intl.NumberFormat(locale, options).format(value);
    },
    formatDate(value, options) {
      return new Intl.DateTimeFormat(locale, options).format(value);
    },
    formatRelative(value, unit, options) {
      return new Intl.RelativeTimeFormat(locale, options).format(value, unit);
    },
    formatList(values, options) {
      return new Intl.ListFormat(locale, options).format(values);
    },
    compare(left, right, options) {
      return new Intl.Collator(locale, options).compare(left, right);
    },
  };
}
