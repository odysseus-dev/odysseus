// Isolated email body rendering:
//   1. DOMPurify → DocumentFragment
//   2. Shadow DOM (#mail-body) isolates sender CSS from the app theme
//   3. Strip unknown CSS classes, block remote images by default, strip position:

import { _rewriteEmailCidUrls, _esc, _plainLooksTabular } from './utils.js';

export { _plainLooksTabular as plainLooksTabular } from './utils.js';

const ALLOWED_MAIL_CLASSES = new Set([
  'odysseus_mail_plain',
  'odysseus_mail_tabular',
  'odysseus_mail_wide_table',
  'odysseus_mail_button',
  'MsoListParagraph',
  'MsoListParagraphCxSpFirst',
  'MsoListParagraphCxSpMiddle',
  'MsoListParagraphCxSpLast',
]);

const BLOCKED_PLACEHOLDER_IMG =
  'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

/** Strip sender theme clashes — use app --mail-fg instead of black-on-white mail. */
const MAIL_STRIP_STYLE_PROPS = new Set([
  'color', '-webkit-text-fill-color', 'font-family', 'font',
  'letter-spacing', 'word-spacing', 'position', 'z-index',
  'background', 'background-color', 'background-image',
]);

let _purifyPromise = null;
let _hooksReady = false;

function loadDOMPurify() {
  if (!_purifyPromise) {
    _purifyPromise = import('https://cdn.jsdelivr.net/npm/dompurify@3.2.6/+esm')
      .then((m) => m.default);
  }
  return _purifyPromise;
}

export function emailRemoteImageProxyUrl(url) {
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  return `${origin}/api/email/remote-image?url=${encodeURIComponent(url)}`;
}

function _blockRemoteImages() {
  return localStorage.getItem('email_block_remote_images') !== '0';
}

function _stripMailInlineStyles(el) {
  if (!(el instanceof HTMLElement)) return;
  const styleAttr = el.getAttribute('style') || '';
  const isButtonLink = el.tagName === 'A' && /background(?:-color)?\s*:/i.test(styleAttr);
  if (isButtonLink) el.classList.add('odysseus_mail_button');

  const stripProps = new Set(MAIL_STRIP_STYLE_PROPS);
  if (isButtonLink) {
    stripProps.delete('background');
    stripProps.delete('background-color');
  }

  el.removeAttribute('color');
  el.removeAttribute('face');
  el.removeAttribute('bgcolor');
  for (const prop of stripProps) {
    el.style.removeProperty(prop);
  }
  const kept = styleAttr.split(';').map((s) => s.trim()).filter((decl) => {
    if (!decl) return false;
    const prop = decl.split(':', 1)[0].trim().toLowerCase();
    return !stripProps.has(prop);
  });
  if (kept.length) el.setAttribute('style', kept.join('; '));
  else el.removeAttribute('style');
}

function _isExternalResourceUrl(url) {
  const u = String(url || '').trim();
  if (!u || u.startsWith('#')) return false;
  const lower = u.toLowerCase();
  if (lower.startsWith('cid:') || lower.startsWith('data:')) return false;
  if (lower.startsWith('mailto:') || lower.startsWith('tel:')) return false;
  if (u.startsWith('/') && !u.startsWith('//')) return false;
  return /^https?:\/\//i.test(u) || lower.startsWith('//');
}

export function unblockRemoteImage(img) {
  if (!img) return;
  const raw = img.getAttribute('data-blocked-src');
  if (!raw) return;
  img.src = emailRemoteImageProxyUrl(raw);
  img.removeAttribute('data-blocked-src');
  img.classList.remove('email-remote-img-blocked');
  img.removeAttribute('title');
}

function _installPurifyHooks(purify) {
  if (_hooksReady) return;
  _hooksReady = true;

  purify.addHook('afterSanitizeAttributes', (node) => {
    if (!(node instanceof Element)) return;

    if (node.classList?.length) {
      for (const cls of [...node.classList]) {
        if (!ALLOWED_MAIL_CLASSES.has(cls)) node.classList.remove(cls);
      }
    }

    if (node.tagName === 'A') {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
      const linkStyle = node.getAttribute('style') || '';
      const cell = node.closest('td, th');
      const cellStyle = cell?.getAttribute('style') || '';
      const cellBg = cell?.getAttribute('bgcolor') || '';
      if (/background(?:-color)?\s*:/i.test(linkStyle)
          || /background(?:-color)?\s*:/i.test(cellStyle)
          || (cellBg && !/transparent/i.test(cellBg))) {
        node.classList.add('odysseus_mail_button');
      }
    }

    if (node.tagName === 'IMG') {
      node.style.maxWidth = '100%';
      node.style.height = 'auto';
      const src = node.getAttribute('src') || '';
      if (_blockRemoteImages() && _isExternalResourceUrl(src)) {
        node.setAttribute('data-blocked-src', src);
        node.setAttribute('src', BLOCKED_PLACEHOLDER_IMG);
        node.setAttribute('title', 'Remote image blocked — click to load');
        node.classList.add('email-remote-img-blocked');
      }
    }

    if (node.tagName === 'TABLE') {
      const firstRow = node.querySelector('tr');
      const colCount = firstRow
        ? firstRow.querySelectorAll(':scope > th, :scope > td').length
        : 0;
      if (colCount >= 7) node.classList.add('odysseus_mail_wide_table');
    }

    _stripMailInlineStyles(node);
  });
}

function _extractBodyHtml(raw) {
  const trimmed = String(raw || '').trim();
  if (!/^\s*<!doctype/i.test(trimmed) && !/^\s*<html[\s>]/i.test(trimmed)) return trimmed;
  const doc = new DOMParser().parseFromString(trimmed, 'text/html');
  return doc.body?.innerHTML || trimmed;
}

function _prepareMailHtml(raw, ctx) {
  let html = String(raw || '');
  html = html.replace(/[\u200B\u200C\u200D\uFEFF\u00AD]/g, '');
  html = html.replace(/>(\s|&nbsp;|&#160;|&#x0*a0;)+</gi, '><');
  if (ctx) html = _rewriteEmailCidUrls(html, ctx);
  return _extractBodyHtml(html);
}

export function plainTextToMailHtml(text) {
  const normalized = String(text || '').replace(/\r\n/g, '\n');
  if (_plainLooksTabular(normalized)) {
    return `<pre class="odysseus_mail_plain odysseus_mail_tabular">${_esc(normalized)}</pre>`;
  }
  const body = _esc(normalized).split('\n').join('<br>');
  return `<div class="odysseus_mail_plain">${body}</div>`;
}

export async function sanitizeMailFragment(html, ctx = null) {
  const purify = await loadDOMPurify();
  _installPurifyHooks(purify);
  const prepared = _prepareMailHtml(html, ctx);
  const fragment = purify.sanitize(prepared, {
    ADD_ATTR: ['target', 'data-blocked-src'],
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'link', 'base', 'meta', 'style', 'svg', 'math'],
    RETURN_DOM_FRAGMENT: true,
  });
  if (fragment instanceof DocumentFragment) {
    for (const child of [...fragment.children]) child.removeAttribute('align');
  }
  return fragment;
}

const SHADOW_MAIL_STYLES = `
:host {
  display: block;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  color: var(--mail-fg, inherit);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 12px;
  line-height: 1.45;
  font-variant-emoji: text;
  letter-spacing: normal;
  word-spacing: normal;
  overflow-wrap: break-word;
}
.shadow-mail-body {
  min-width: 0;
  box-sizing: border-box;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  overflow-wrap: break-word;
  word-break: normal;
  letter-spacing: normal;
  word-spacing: normal;
  white-space: normal;
  font-variant-emoji: text;
  font-feature-settings: normal;
  color: var(--mail-fg) !important;
  -webkit-text-fill-color: currentColor !important;
  background: transparent;
}
.shadow-mail-body :where(
  p, div, span, td, th, li, font, h1, h2, h3, h4, h5, h6,
  b, i, strong, em, label, small, center
) {
  color: inherit !important;
  -webkit-text-fill-color: currentColor !important;
}
.shadow-mail-body > table:not(.odysseus_mail_wide_table) {
  width: 100%;
  max-width: 100%;
}
.odysseus_mail_plain { white-space: pre-wrap; }
.odysseus_mail_tabular,
.shadow-mail-body pre.odysseus_mail_tabular {
  white-space: pre;
  overflow-x: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.35;
  margin: 0;
  font-variant-emoji: text;
}
.shadow-mail-body img { max-width: 100%; height: auto; display: inline-block; }
.shadow-mail-body span {
  display: inline !important;
  width: auto !important;
  min-width: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
}
.shadow-mail-body table {
  border-collapse: collapse;
  width: auto;
  max-width: none;
  font-size: inherit;
  background: transparent;
}
.shadow-mail-body td, .shadow-mail-body th {
  vertical-align: top;
  padding: 0;
  background: transparent;
}
.shadow-mail-body table.odysseus_mail_wide_table {
  font-size: 11px;
}
.shadow-mail-body table.odysseus_mail_wide_table td,
.shadow-mail-body table.odysseus_mail_wide_table th {
  padding: 2px 6px;
  white-space: nowrap;
}
.shadow-mail-body pre {
  white-space: pre-wrap;
  overflow-x: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.4;
}
.shadow-mail-body a[href]:not(.odysseus_mail_button) {
  color: var(--mail-link, #6ea8fe) !important;
  -webkit-text-fill-color: currentColor !important;
  text-decoration: underline;
}
.shadow-mail-body a.odysseus_mail_button {
  color: #fff !important;
  -webkit-text-fill-color: #fff !important;
  text-decoration: none !important;
  display: inline-block;
  border-radius: 4px;
  padding: 10px 18px;
  line-height: 1.3;
}
.shadow-mail-body blockquote {
  margin: 0.5em 0;
  padding-left: 10px;
  border-left: 3px solid color-mix(in srgb, currentColor 28%, transparent);
}
.shadow-mail-body img.email-remote-img-blocked {
  cursor: pointer;
  opacity: 0.45;
  min-width: 48px;
  min-height: 32px;
  background: color-mix(in srgb, currentColor 8%, transparent);
}
`;

export function countBlockedRemoteImages(hostEl) {
  if (!hostEl) return 0;
  let n = hostEl.querySelectorAll('img.email-remote-img-blocked').length;
  if (hostEl.shadowRoot) {
    n += hostEl.shadowRoot.querySelectorAll('img.email-remote-img-blocked').length;
  }
  return n;
}

export function loadAllRemoteImages(hostEl) {
  if (!hostEl) return 0;
  const imgs = [];
  hostEl.querySelectorAll('img.email-remote-img-blocked').forEach((img) => imgs.push(img));
  hostEl.shadowRoot?.querySelectorAll('img.email-remote-img-blocked').forEach((img) => imgs.push(img));
  for (const img of imgs) unblockRemoteImage(img);
  return imgs.length;
}

export function mountShadowMailBody(hostEl, fragment) {
  if (!hostEl || !fragment) return;

  let shadow = hostEl._odysseusMailShadow;
  if (!shadow) {
    shadow = hostEl.attachShadow({ mode: 'open' });
    hostEl._odysseusMailShadow = shadow;
    const styleEl = document.createElement('style');
    styleEl.textContent = SHADOW_MAIL_STYLES;
    shadow.appendChild(styleEl);
    shadow.addEventListener('click', (ev) => {
      const img = ev.target?.closest?.('img.email-remote-img-blocked');
      if (!img) return;
      ev.preventDefault();
      unblockRemoteImage(img);
    });
  }

  const styleEl = shadow.firstChild;
  if (styleEl?.tagName === 'STYLE') styleEl.textContent = SHADOW_MAIL_STYLES;
  while (shadow.lastChild && shadow.lastChild !== styleEl) {
    shadow.lastChild.remove();
  }

  const rootCs = getComputedStyle(document.documentElement);
  const hostCs = getComputedStyle(hostEl);
  const fg = hostCs.color || rootCs.getPropertyValue('--fg').trim() || '#e8e8e8';
  const link = rootCs.getPropertyValue('--hl-function').trim()
    || rootCs.getPropertyValue('--accent-primary').trim()
    || '#6ea8fe';
  hostEl.style.setProperty('--mail-fg', fg);
  hostEl.style.setProperty('--mail-link', link || '#6ea8fe');

  const wrap = document.createElement('div');
  wrap.className = 'shadow-mail-body';
  wrap.appendChild(fragment.cloneNode(true));
  shadow.appendChild(wrap);
}

/**
 * Render email body: sanitize HTML/plain → shadow root.
 * @param {HTMLElement} hostEl  .email-reader-body container
 * @param {{ plain?: string, html?: string, ctx?: object }} opts
 */
export async function renderMailBody(hostEl, { plain = '', html = '', ctx = null } = {}) {
  const hasHtml = !!String(html || '').trim();
  const mailHtml = hasHtml ? html : plainTextToMailHtml(plain);
  const fragment = await sanitizeMailFragment(mailHtml, ctx);
  mountShadowMailBody(hostEl, fragment);
}
