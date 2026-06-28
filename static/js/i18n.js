// ============================================
// Odysseus i18n — lightweight gettext-style localization
// ============================================
// The ENGLISH source string is the translation KEY. `t('Save')` returns the
// translation for the active language when one exists, otherwise it returns the
// key (English) unchanged. This means:
//   - There is exactly ONE dictionary to maintain per language (e.g. pt-BR).
//   - English is the automatic fallback for any missing key.
//   - Retrofitting is just "wrap the literal": `'Save'` -> `t('Save')`.
//
// Per-module dictionaries live in `static/js/i18n/<module>.pt-BR.js` and call
// `registerMessages('pt-BR', { 'English': 'Português', ... })` at import time.
// Each feature module imports its own dictionary file (side-effect import).
//
// Active language is resolved from `window.__ODY_LANG` (set by the inline boot
// script in index.html) and persisted in localStorage + `/api/prefs/language`.

const DEFAULT_LANG = 'pt-BR';
const LS_KEY = 'odysseus-language';

// { 'pt-BR': { 'English': 'Português', ... }, ... }
const dicts = Object.create(null);

function activeLang() {
  if (typeof window !== 'undefined' && window.__ODY_LANG) return window.__ODY_LANG;
  try {
    const v = localStorage.getItem(LS_KEY);
    if (v) return v;
  } catch (_) {}
  return DEFAULT_LANG;
}

/** Merge a batch of `{ key: translation }` entries into a language dictionary. */
export function registerMessages(lang, obj) {
  if (!lang || !obj) return;
  const d = dicts[lang] || (dicts[lang] = Object.create(null));
  for (const k in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, k)) d[k] = obj[k];
  }
}

/** Replace `{name}` placeholders in `str` using `vars`. */
function interpolate(str, vars) {
  if (!vars) return str;
  return str.replace(/\{(\w+)\}/g, (m, k) =>
    (vars[k] !== undefined && vars[k] !== null) ? String(vars[k]) : m);
}

/**
 * Translate `key` (an English source string) into the active language.
 * Returns the key itself when the language is English or no translation exists.
 * `vars` interpolates `{placeholders}`: t('Deleted {n} item(s)', { n: 3 }).
 */
export function t(key, vars) {
  if (key == null) return key;
  const lang = activeLang();
  let out = key;
  if (lang !== 'en') {
    const d = dicts[lang];
    if (d && typeof d[key] === 'string') out = d[key];
  }
  return vars ? interpolate(out, vars) : out;
}

/** Current active language code (e.g. 'pt-BR' or 'en'). */
export function getLang() {
  return activeLang();
}

/**
 * Persist a new language choice and reload so the whole UI re-renders in it.
 * Reload is the simplest correct "apply" — translateDOM() re-sweeps the static
 * DOM and every module's t() calls pick up the new language on next render.
 */
export async function setLang(lang) {
  if (!lang) return;
  try { localStorage.setItem(LS_KEY, lang); } catch (_) {}
  try { window.__ODY_LANG = lang; } catch (_) {}
  try {
    await fetch('/api/prefs/language', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: lang }),
    });
  } catch (_) {}
  try { location.reload(); } catch (_) {}
}

// Tags whose text/attributes must never be auto-translated by translateDOM.
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'NOSCRIPT']);
// Attributes worth translating on the static DOM. Deliberately conservative —
// `value` is excluded so we never rewrite form data.
const ATTRS = ['placeholder', 'title', 'aria-label', 'aria-placeholder'];

/** True if `node` or an ancestor opted out of translation via data-i18n-skip. */
function isSkipped(node) {
  let el = node.nodeType === 1 ? node : node.parentNode;
  while (el && el.nodeType === 1) {
    if (SKIP_TAGS.has(el.nodeName)) return true;
    if (el.hasAttribute && el.hasAttribute('data-i18n-skip')) return true;
    el = el.parentNode;
  }
  return false;
}

/**
 * Sweep the static DOM under `root` and translate any text node or attribute
 * whose (trimmed) value is a known dictionary key for the active language.
 *
 * Content-based by design: there is NO need to mark elements — only strings
 * present in the dictionary are touched, everything else is left verbatim.
 * No-op in English (English is the source). Dynamic strings rendered by JS
 * after load are handled by `t()` at render time, not here.
 */
export function translateDOM(root) {
  root = root || (typeof document !== 'undefined' ? document : null);
  if (!root) return;
  const lang = activeLang();
  if (lang === 'en') return;
  const d = dicts[lang];
  if (!d) return;

  // 1) Text nodes
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const raw = node.nodeValue;
      if (!raw || !raw.trim()) return NodeFilter.FILTER_REJECT;
      if (d[raw.trim()] === undefined) return NodeFilter.FILTER_REJECT;
      if (isSkipped(node)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) nodes.push(n);
  for (const node of nodes) {
    const raw = node.nodeValue;
    const key = raw.trim();
    const translated = d[key];
    if (typeof translated === 'string' && translated !== '') {
      // Preserve the original surrounding whitespace. Function-form replace so
      // `$` sequences in the translation are not treated as match references.
      node.nodeValue = raw.replace(key, () => translated);
    }
  }

  // 2) Attributes
  const els = root.nodeType === 1
    ? [root, ...root.querySelectorAll('*')]
    : [...root.querySelectorAll('*')];
  for (const el of els) {
    if (SKIP_TAGS.has(el.nodeName)) continue;
    if (el.hasAttribute && el.hasAttribute('data-i18n-skip')) continue;
    for (const attr of ATTRS) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (!v) continue;
      const key = v.trim();
      const translated = d[key];
      if (typeof translated === 'string' && translated !== '') {
        el.setAttribute(attr, v.replace(key, () => translated));
      }
    }
  }
}

// Expose globals so deeply-nested module files (e.g. editor/tools/*.js) can call
// `t()` without worrying about relative import depth. Modules MAY also import
// { t } from the correct relative path to this file — both work.
try {
  window.t = t;
  window.i18n = { t, registerMessages, getLang, setLang, translateDOM };
  if (!window.__ODY_LANG) window.__ODY_LANG = activeLang();
} catch (_) {}

export default { t, registerMessages, getLang, setLang, translateDOM };
