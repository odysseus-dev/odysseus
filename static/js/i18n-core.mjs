export const LANGUAGE_STORAGE_KEY = 'odysseus-language';
export const I18N_SELECTOR = [
  '[data-i18n]',
  '[data-i18n-placeholder]',
  '[data-i18n-title]',
  '[data-i18n-aria-label]',
].join(',');
export const I18N_ATTRIBUTE_NAMES = [
  'data-i18n',
  'data-i18n-placeholder',
  'data-i18n-title',
  'data-i18n-aria-label',
];

export function lookup(messages = {}, msgid = '') {
  return messages[msgid] || msgid;
}

export function normalizeLocale(locale, availableLocales = []) {
  if (!locale) return 'en';
  const normalized = String(locale).replace('_', '-').toLowerCase();
  const exact = availableLocales.find((item) => item.toLowerCase() === normalized);
  if (exact) return exact;
  const primary = normalized.split('-')[0];
  const primaryMatch = availableLocales.find((item) => item.toLowerCase().split('-')[0] === primary);
  return primaryMatch || 'en';
}

export function translateElement(element, messages = {}) {
  if (element.hasAttribute('data-i18n')) {
    element.textContent = lookup(messages, element.getAttribute('data-i18n'));
  }
  for (const [dataAttr, targetAttr] of [
    ['data-i18n-placeholder', 'placeholder'],
    ['data-i18n-title', 'title'],
    ['data-i18n-aria-label', 'aria-label'],
  ]) {
    if (element.hasAttribute(dataAttr)) {
      element.setAttribute(targetAttr, lookup(messages, element.getAttribute(dataAttr)));
    }
  }
}

function hasI18nAttributes(element) {
  return Boolean(element?.hasAttribute)
    && I18N_ATTRIBUTE_NAMES.some((attribute) => element.hasAttribute(attribute));
}

export function translateRoot(root, messages = {}) {
  if (!root) return;
  if (hasI18nAttributes(root)) translateElement(root, messages);
  if (root.querySelectorAll) {
    root.querySelectorAll(I18N_SELECTOR).forEach((element) => translateElement(element, messages));
  }
}

export function createTranslationObserver(documentRef, messagesGetter) {
  const Observer = documentRef?.defaultView?.MutationObserver || globalThis.MutationObserver;
  if (!documentRef?.body || !Observer) return null;

  const observer = new Observer((mutations) => {
    const messages = messagesGetter();
    const seen = new Set();
    const translateNode = (node) => {
      if (node?.nodeType !== 1 || seen.has(node)) return;
      seen.add(node);
      translateRoot(node, messages);
    };

    mutations.forEach((mutation) => {
      if (mutation.type === 'attributes') translateNode(mutation.target);
      mutation.addedNodes?.forEach(translateNode);
    });
  });

  observer.observe(documentRef.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: I18N_ATTRIBUTE_NAMES,
  });
  return observer;
}

export async function loadCatalog(locale, fetcher = fetch) {
  if (locale === 'en') return {};
  const response = await fetcher(`/static/locales/${locale}.json`);
  if (!response.ok) throw new Error(`Unable to load locale catalog: ${locale}`);
  const catalog = await response.json();
  return catalog.messages || {};
}

export async function loadLocales(fetcher = fetch) {
  const response = await fetcher('/static/locales/index.json');
  if (!response.ok) throw new Error('Unable to load locale metadata');
  return response.json();
}

export function setDocumentLanguage(documentRef, localeInfo) {
  if (!documentRef?.documentElement || !localeInfo) return;
  documentRef.documentElement.lang = localeInfo.code || 'en';
  documentRef.documentElement.dir = localeInfo.dir || 'ltr';
}

export function installLanguageSelectors(documentRef, locales, selectedLocale, onChange) {
  if (!documentRef?.querySelectorAll) return;
  documentRef.querySelectorAll('[data-i18n-language-select]').forEach((select) => {
    select.innerHTML = '';
    locales.forEach((locale) => {
      const option = documentRef.createElement('option');
      option.value = locale.code;
      option.textContent = locale.nativeName || locale.name || locale.code;
      select.appendChild(option);
    });
    select.value = selectedLocale;
    select.onchange = () => onChange(select.value);
  });
}
