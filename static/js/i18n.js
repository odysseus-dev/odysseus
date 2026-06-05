import {
  DEFAULT_LANGUAGE,
  DICTIONARY,
  LANGUAGE_STORAGE_KEY,
  SUPPORTED_LANGUAGES,
} from './i18n-dictionary.js';

const SKIP_SELECTOR = [
  'textarea',
  'input',
  'code',
  'pre',
  '[contenteditable="true"]',
  '.msg',
  '.message',
  '.markdown-body',
  '.chat-message',
  '#chat-history',
  '#messages',
  '#document-editor',
  '.doc-editor',
  '.doc-editor-highlight',
  '.cm-editor',
  '.hljs',
  '[data-language-toggle]',
  '[data-i18n-skip]',
].join(',');

const USER_CONTENT_SKIP_SELECTOR = [
  '[contenteditable="true"]',
  '.msg',
  '.message',
  '.markdown-body',
  '.chat-message',
  '#chat-history',
  '#messages',
  '#document-editor',
  '.doc-editor',
  '.doc-editor-highlight',
  '.cm-editor',
  '.hljs',
  '[data-i18n-skip]',
].join(',');

const TRANSLATABLE_ATTRIBUTES = ['title', 'aria-label', 'placeholder', 'alt', 'value'];
const ORIGINAL_TEXT = new WeakMap();
const ORIGINAL_ATTR_PREFIX = 'i18nOriginal';

let currentLanguage = DEFAULT_LANGUAGE;
let observer = null;
let applying = false;
let pendingFlush = false;
const pendingRoots = new Set();

export const MUTATION_OBSERVER_OPTIONS = Object.freeze({
  childList: true,
  subtree: true,
});

export function normalizeLanguage(language) {
  const lang = String(language || '').toLowerCase();
  if (lang.startsWith('zh')) return 'zh';
  if (Object.prototype.hasOwnProperty.call(SUPPORTED_LANGUAGES, lang)) return lang;
  return DEFAULT_LANGUAGE;
}

export function detectInitialLanguage() {
  if (typeof localStorage !== 'undefined') {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored) return normalizeLanguage(stored);
  }
  if (typeof navigator !== 'undefined') {
    const languages = navigator.languages && navigator.languages.length
      ? navigator.languages
      : [navigator.language];
    if (languages.some((lang) => String(lang || '').toLowerCase().startsWith('zh'))) {
      return 'zh';
    }
  }
  return DEFAULT_LANGUAGE;
}

export function translate(language, key, vars = {}, dictionary = DICTIONARY) {
  const lang = normalizeLanguage(language);
  const source = String(key ?? '');
  const template = dictionary[lang]?.[source] || translatePattern(lang, source) || source;
  return String(template).replace(/\{(\w+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
  ));
}

function translatePattern(language, source) {
  if (language !== 'zh') return null;
  const text = String(source || '').trim();
  if (text.includes(' · ')) {
    return text.split(/\s+·\s+/).map((part) => translate(language, part)).join(' · ');
  }
  const categoryLabels = {
    all: '全部',
    calendar: '日历',
    email: '邮件',
    chats: '聊天',
    documents: '文档',
    memory: '记忆',
    research: '研究',
    skills: '技能',
    assistant: '助手',
    system: '系统',
    cookbook: '模型库',
    reminders: '提醒',
    'check-in': '检查',
    other: '其他',
    errors: '错误',
    notifications: '通知',
  };
  let match = text.match(/^(\d+)\s+research$/i);
  if (match) return `${match[1]} 项研究`;
  match = text.match(/^(\d+)\s+Selected$/i);
  if (match) return `已选择 ${match[1]} 项`;
  match = text.match(/^(\d+)\s+selected$/i);
  if (match) return `已选择 ${match[1]} 项`;
  match = text.match(/^(\d+)\s+tasks?$/i);
  if (match) return `${match[1]} 个任务`;
  match = text.match(/^(\d+)\s+documents?$/i);
  if (match) return `${match[1]} 个文档`;
  match = text.match(/^(\d+)\s+runs?$/i);
  if (match) return `${match[1]} 次运行`;
  match = text.match(/^(all|calendar|email|chats|documents|memory|research|skills|assistant|system|cookbook|reminders|check-in|other|errors|notifications)\s+\((\d+)\)$/i);
  if (match) return `${categoryLabels[match[1].toLowerCase()] || match[1]}（${match[2]}）`;
  match = text.match(/^notifications\s+(\d+)$/i);
  if (match) return `通知 ${match[1]}`;
  match = text.match(/^(Next|Last):\s+(.+)$/i);
  if (match) return `${match[1].toLowerCase() === 'next' ? '下次' : '上次'}：${translate(language, match[2])}`;
  match = text.match(/^model:\s+(.+)$/i);
  if (match) return `模型：${match[1]}`;
  match = text.match(/^Daily at\s+(.+)$/i);
  if (match) return `每天 ${match[1]}`;
  match = text.match(/^Weekly on\s+(.+)\s+at\s+(.+)$/i);
  if (match) return `每周${translate(language, match[1])} ${match[2]}`;
  match = text.match(/^Monthly on\s+(\d+)(st|nd|rd|th)\s+at\s+(.+)$/i);
  if (match) return `每月 ${match[1]} 日 ${match[3]}`;
  match = text.match(/^Cron:\s+(.+)$/i);
  if (match) return `Cron：${match[1]}`;
  match = text.match(/^Once on\s+(.+)\s+at\s+(.+)$/i);
  if (match) return `一次：${match[1]} ${match[2]}`;
  match = text.match(/^Every\s+(\d+)\s+(.+)$/i);
  if (match) return `每 ${match[1]} 次 ${translate(language, match[2].replace(/s$/, ''))}`;
  match = text.match(/^Limit:\s+(\d+)\s+tool calls per message$/i);
  if (match) return `限制：每条消息 ${match[1]} 次工具调用`;
  match = text.match(/^(.+?)\s+([▲▼])$/);
  if (match) return `${translate(language, match[1])} ${match[2]}`;
  match = text.match(/^in\s+(\d+)([mhd])$/i);
  if (match) return `${match[1]}${{ m: '分钟', h: '小时', d: '天' }[match[2].toLowerCase()]}后`;
  match = text.match(/^(\d+)([mhd])\s+ago$/i);
  if (match) return `${match[1]}${{ m: '分钟前', h: '小时前', d: '天前' }[match[2].toLowerCase()]}`;
  return null;
}

export function t(key, vars = {}) {
  return translate(currentLanguage, key, vars);
}

export function getLanguage() {
  return currentLanguage;
}

function hasDOM() {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

export function shouldSkipElementForI18n(element) {
  if (!element || element.nodeType !== 1) return false;
  return Boolean(element.closest(SKIP_SELECTOR));
}

export function shouldSkipAttributesForI18n(element) {
  if (!element || element.nodeType !== 1) return false;
  if (element.closest(USER_CONTENT_SKIP_SELECTOR)) return true;
  return element.matches('script, style, noscript, svg, path, code, pre');
}

function shouldTranslateTextNode(node) {
  if (!node || node.nodeType !== Node.TEXT_NODE) return false;
  const text = node.nodeValue || '';
  if (!text.trim()) return false;
  const parent = node.parentElement;
  if (!parent || shouldSkipElementForI18n(parent)) return false;
  if (parent.matches('script, style, noscript, svg, path')) return false;
  return true;
}

function applyWhitespace(original, translated) {
  const leading = original.match(/^\s*/)?.[0] || '';
  const trailing = original.match(/\s*$/)?.[0] || '';
  return `${leading}${translated}${trailing}`;
}

function setElementText(element, text) {
  if (element.textContent === text) return;
  element.textContent = text;
}

function translateExplicitElement(element) {
  const key = element.getAttribute('data-i18n');
  if (!key) return;
  setElementText(element, translate(currentLanguage, key));
}

function originalAttrDatasetName(attribute) {
  return ORIGINAL_ATTR_PREFIX + attribute
    .replace(/^aria-/, 'Aria-')
    .replace(/-([a-z])/g, (_, chr) => chr.toUpperCase())
    .replace(/-/g, '');
}

function translateAttributes(element) {
  const attrKeys = element.getAttribute('data-i18n-attrs');
  const explicit = attrKeys
    ? attrKeys.split(',').map((item) => item.trim()).filter(Boolean)
    : [];
  const attrs = new Set([...TRANSLATABLE_ATTRIBUTES, ...explicit]);

  attrs.forEach((attr) => {
    if (!element.hasAttribute(attr)) return;
    if (attr === 'value' && !element.matches('button, input[type="button"], input[type="submit"]')) return;
    const originalKey = originalAttrDatasetName(attr);
    if (!Object.prototype.hasOwnProperty.call(element.dataset, originalKey)) {
      element.dataset[originalKey] = element.getAttribute(attr) || '';
    }
    const original = element.dataset[originalKey] || '';
    if (!original.trim()) return;
    const translated = translate(currentLanguage, original);
    if (element.getAttribute(attr) !== translated) {
      element.setAttribute(attr, translated);
    }
  });
}

function translateTextNode(node) {
  if (!shouldTranslateTextNode(node)) return;
  if (!ORIGINAL_TEXT.has(node)) ORIGINAL_TEXT.set(node, node.nodeValue || '');
  const original = ORIGINAL_TEXT.get(node) || '';
  const trimmed = original.trim();
  if (!trimmed) return;
  const translated = translate(currentLanguage, trimmed);
  const nextValue = applyWhitespace(original, translated);
  if (node.nodeValue !== nextValue) node.nodeValue = nextValue;
}

function walkTextNodes(root) {
  if (!hasDOM()) return;
  if (!root) return;
  if (root.nodeType === Node.TEXT_NODE) {
    translateTextNode(root);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
  if (root.nodeType === Node.ELEMENT_NODE && shouldSkipElementForI18n(root)) return;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return shouldTranslateTextNode(node)
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(translateTextNode);
}

function translateElementTree(root) {
  if (!hasDOM()) return;
  if (!root) return;

  const elements = [];
  if (root.nodeType === Node.ELEMENT_NODE) elements.push(root);
  if (root.querySelectorAll) {
    elements.push(...root.querySelectorAll('[data-i18n], [title], [aria-label], [placeholder], [alt], button[value], input[type="button"][value], input[type="submit"][value]'));
  }
  elements.forEach((element) => {
    if (!shouldSkipElementForI18n(element)) translateExplicitElement(element);
    if (shouldSkipAttributesForI18n(element)) return;
    translateAttributes(element);
  });
  walkTextNodes(root);
}

export function applyTranslations(root = document) {
  if (!hasDOM() || applying) return;
  applying = true;
  try {
    document.documentElement.lang = SUPPORTED_LANGUAGES[currentLanguage]?.htmlLang || 'en';
    translateElementTree(root);
    updateLanguageControls();
  } finally {
    applying = false;
  }
}

function updateLanguageControls() {
  if (!hasDOM()) return;
  document.querySelectorAll('[data-language-toggle]').forEach((button) => {
    const next = currentLanguage === 'zh' ? 'en' : 'zh';
    const label = currentLanguage === 'zh'
      ? SUPPORTED_LANGUAGES.en.nativeLabel
      : SUPPORTED_LANGUAGES.zh.nativeLabel;
    const ariaLabel = translate(currentLanguage, `language.switchTo${next === 'zh' ? 'Chinese' : 'English'}`);
    const title = translate(currentLanguage, currentLanguage === 'zh' ? 'language.currentChinese' : 'language.currentEnglish');
    const pressed = currentLanguage === 'zh' ? 'true' : 'false';
    if (button.textContent !== label) button.textContent = label;
    if (button.getAttribute('aria-label') !== ariaLabel) button.setAttribute('aria-label', ariaLabel);
    if (button.getAttribute('title') !== title) button.setAttribute('title', title);
    if (button.getAttribute('aria-pressed') !== pressed) button.setAttribute('aria-pressed', pressed);
  });
}

export function setLanguage(language) {
  currentLanguage = normalizeLanguage(language);
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, currentLanguage);
  }
  applyTranslations();
  if (hasDOM()) {
    window.dispatchEvent(new CustomEvent('odysseus:languagechange', { detail: { language: currentLanguage } }));
  }
  return currentLanguage;
}

export function initLanguageControls() {
  if (!hasDOM()) return;
  document.addEventListener('click', (event) => {
    const button = event.target?.closest?.('[data-language-toggle]');
    if (!button) return;
    event.preventDefault();
    setLanguage(currentLanguage === 'zh' ? 'en' : 'zh');
  });
  updateLanguageControls();
}

function initMutationObserver() {
  if (!hasDOM() || observer || typeof MutationObserver === 'undefined') return;
  observer = new MutationObserver((mutations) => {
    if (applying) return;
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => scheduleTranslations(node));
    });
  });
  observer.observe(document.body, MUTATION_OBSERVER_OPTIONS);
}

function scheduleTranslations(root) {
  if (!root || applying) return;
  pendingRoots.add(root);
  if (pendingFlush) return;
  pendingFlush = true;
  const schedule = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : (callback) => setTimeout(callback, 16);
  schedule(flushPendingTranslations);
}

function flushPendingTranslations() {
  pendingFlush = false;
  if (!pendingRoots.size) return;
  const roots = Array.from(pendingRoots);
  pendingRoots.clear();
  roots.forEach((root) => applyTranslations(root));
}

export function initI18n() {
  if (!hasDOM()) return;
  currentLanguage = detectInitialLanguage();
  initLanguageControls();
  applyTranslations();
  initMutationObserver();
}

if (hasDOM()) {
  window.odysseusI18n = {
    applyTranslations,
    getLanguage,
    setLanguage,
    t,
    translate,
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initI18n, { once: true });
  } else {
    initI18n();
  }
}
