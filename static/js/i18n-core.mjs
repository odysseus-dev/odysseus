export const LANGUAGE_STORAGE_KEY = 'odysseus-language';

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

export function translateRoot(root, messages = {}) {
  if (!root?.querySelectorAll) return;
  const selector = [
    '[data-i18n]',
    '[data-i18n-placeholder]',
    '[data-i18n-title]',
    '[data-i18n-aria-label]',
  ].join(',');
  root.querySelectorAll(selector).forEach((element) => translateElement(element, messages));
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
