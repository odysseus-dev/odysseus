// static/js/emailLibrary/utils.js
//
// Pure helpers extracted from emailLibrary.js. No DOM state, no fetch,
// no shared mutable references — safe to import anywhere.

// ── Talon-inspired multilingual quote-detection regexes ───────────
// Borrowed (loosely) from Mailgun's `talon` library. These are partial
// regex source strings — combined with surrounding patterns by callers.
// Multilingual on purpose: a typed "wrote:" line is locale-bound, and
// people forward / reply across language settings all the time.

export const _TALON_WROTE = '(?:wrote|écrit|escribió|scrisse|schrieb|skrev|schreef|napisał|написал|napsal|написа|έγραψε|katselivat|napisao|написав|napisała|napisali|hat geschrieben|kirjoitti|написала|escreveu|napisao|написа|написала)';

export const _TALON_FROM = '(?:From|Från|Von|De|Da|От|Od|Van|差出人|发件人|寄件人|Ut|Frá|Lähettäjä|Avsender|Pošiljatelj|Од|Від|Posiljatelj|Frå)';
export const _TALON_SENT = '(?:Sent|Skickat|Gesendet|Envoy[ée]|Inviato|Enviado|Verzonden|Отправлено|Wysłane|Date|送信日時|发送时间|寄件日期|Sendt|Lähetetty|Tarih|Datum|Data|Datum)';
export const _TALON_SUBJ = '(?:Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat|件名|主题|主旨|Emne|Aihe|Onderwerp|Konu)';
export const _TALON_TO   = '(?:To|Till|An|À|A|Voor|Para|Naar|Кому|Do|宛先|收件人|Emri|Komu)';
export const _TALON_ORIG_RE = /(?:^|\n)[\s>]*[-_=]{3,}\s*(?:Original\s+Message|Forwarded\s+message|Ursprüngliche\s+Nachricht|Mensaje\s+original|Messaggio\s+originale|Message\s+d['’]origine|Oorspronkelijk\s+bericht|Original\s+meddelande|Vor[ ]asal[a]\s+meddelande|原文|原始邮件|転送)\s*[-_=]{3,}/i;

// Minimum plain-text length of a "signature" before we bother folding it.
// Short closings ("Cheers, John") stay inline — folding them would add
// a click for two bytes of saving.
export const _SIG_BLOAT_MIN_CHARS = 200;

// HTML-escape a string by round-tripping through a detached div. Cheap
// and correct (handles all the entities that matter for innerHTML).
export function _esc(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

function _attrEsc(text) {
  return String(text ?? '')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/`/g, '&#96;');
}

function _compactUrlSchemeValue(value) {
  return String(value || '').replace(/[\u0000-\u0020\u007f-\u009f]+/g, '').toLowerCase();
}

function _isDangerousUrl(value) {
  const compact = _compactUrlSchemeValue(value);
  return compact.startsWith('javascript:') || compact.startsWith('vbscript:') || compact.startsWith('data:');
}

function _isDangerousSrcset(value) {
  return String(value || '').split(',').some(candidate => _isDangerousUrl(candidate));
}

// Escape + linkify URLs and email addresses. Returns innerHTML-safe markup.
export function _escLinkify(text) {
  const escaped = _esc(text);
  // URLs: http(s)://... or www....
  const urlRe = /\b((?:https?:\/\/|www\.)[^\s<>"']+[^\s<>"'.,;:!?)\]])/g;
  const mailRe = /\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b/g;
  return escaped
    .replace(urlRe, (m) => {
      const href = m.startsWith('www.') ? `https://${m}` : m;
      return `<a href="${_attrEsc(href)}" target="_blank" rel="noopener noreferrer">${m}</a>`;
    })
    .replace(mailRe, (m) => `<a href="${_attrEsc(`mailto:${m}`)}">${m}</a>`);
}

// Pull display name out of "Name <email@x>"; fallback to local-part of
// the email; final fallback to the input string.
export function _extractName(addr) {
  const m = addr.match(/^"?([^"<]+?)"?\s*<([^>]+)>\s*$/);
  if (m) return m[1].trim();
  const localPart = addr.split('@')[0];
  return localPart || addr;
}

// Parse the "Author <email> · Date" metadata string emitted by the
// server-side thread parser.
export function _parseTurnMeta(meta) {
  if (!meta) return { author: '', email: '', date: '' };
  const m = String(meta);
  const eMatch = m.match(/<([^<>\s]+@[^<>\s]+)>/) ||
                 m.match(/\b([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})\b/);
  const email = eMatch ? eMatch[1].toLowerCase().trim() : '';
  const parts = m.split(/\s+[·•]\s+/);
  let author = '', date = '';
  if (parts.length >= 2) {
    author = parts[0].replace(/<[^>]+>/g, '').trim();
    date = parts.slice(1).join(' · ').trim();
  } else {
    author = m.replace(/<[^>]+>/g, '').trim();
  }
  return { author, email, date };
}

// Short, locale-aware display string for a chat-bubble timestamp.
// Returns '' for invalid / empty input.
export function _formatBubbleDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (!d || isNaN(d.getTime())) return '';
  try {
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) { return ''; }
}

// Format a raw "to" address string ("Foo <foo@x.com>, bar@y.com") into a
// short, readable list — display names when present, just the local part
// of the email otherwise, and ", +N" once there are more than 2 recipients.
export function _formatRecipients(raw) {
  if (!raw) return '';
  const addrs = String(raw).split(',').map(s => s.trim()).filter(Boolean);
  if (!addrs.length) return '';
  const friendly = addrs.map(a => {
    const m = a.match(/^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$/);
    if (m && m[1].trim()) return m[1].trim();
    const em = a.replace(/[<>]/g, '').trim();
    return em.split('@')[0] || em;
  });
  if (friendly.length === 1) return friendly[0];
  if (friendly.length === 2) return friendly.join(', ');
  return friendly.slice(0, 2).join(', ') + ' +' + (friendly.length - 2);
}

// Deterministic per-sender colour. Same hashing as
// emailInbox.js#_senderColor so a sender's avatar / name colour matches
// across the list view and the bubble reader.
export function _senderColor(name) {
  if (!name) return 'hsl(220, 55%, 65%)';
  const key = String(name).toLowerCase();
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0;
  }
  const hue = ((hash % 360) + 360) % 360;
  return `hsl(${hue}, 55%, 65%)`;
}

// 1- or 2-letter initials for an avatar bubble. Unicode-friendly.
export function _initials(s) {
  if (!s) return '?';
  const clean = String(s).replace(/<[^>]+>/g, '').replace(/[^\p{L}\s]/gu, ' ').trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  const first = parts[0][0] || '';
  const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
  return (first + last).toUpperCase();
}

function _sanitizeStyleContent(css) {
  return String(css || '')
    .replace(/@import\b[^;]+;?/gi, '')
    .replace(/expression\s*\(/gi, '')
    .replace(/javascript\s*:/gi, '')
    .replace(/-moz-binding\s*:/gi, '')
    .replace(/behavior\s*:/gi, '');
}

function _normalizeCidKey(cid) {
  return decodeURIComponent(String(cid || '')).replace(/^<|>$/g, '').trim().toLowerCase();
}

/** True when plain text has per-digit spacing (anti-scrape), not normal phone groups. */
export function _plainHasPerDigitSpacing(plain) {
  const s = String(plain || '');
  const runRe = /(?:\d[\s\u00a0]+){3,}\d/g;
  let m;
  while ((m = runRe.exec(s)) !== null) {
    const parts = m[0].trim().split(/[\s\u00a0]+/).filter(Boolean);
    if (parts.length >= 4 && parts.every((p) => /^\d$/.test(p))) return true;
  }
  return false;
}

/** @deprecated use _plainHasPerDigitSpacing */
export function _plainLooksObfuscated(plain) {
  return _plainHasPerDigitSpacing(plain);
}

export function _htmlLooksObfuscated(html) {
  if (!html) return false;
  if (/font-size\s*:\s*0/i.test(html) || /visibility\s*:\s*hidden/i.test(html)) return true;
  const stripped = String(html).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  return _plainHasPerDigitSpacing(stripped);
}

/** True when HTML has a real `<table>` tag (not CSS `table {` / `.table` inside `<style>`). */
export function _htmlHasRealTable(html) {
  const stripped = String(html || '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '');
  return /<table[\s/>]/i.test(stripped);
}

/** Fixed-column plain-text reports (monitoring digests, etc.). */
export function _plainLooksTabular(text) {
  const lines = String(text || '').split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 4) return false;
  let aligned = 0;
  for (const line of lines) {
    if (/ {2,}|\t/.test(line)) aligned++;
  }
  return aligned >= Math.min(4, Math.ceil(lines.length * 0.2));
}

/** Spark-style: use clean text/plain when HTML is obfuscated or plain is enough. */
export function _shouldPreferPlainBody(plain, html) {
  if (!plain || !String(plain).trim()) return false;
  const hasHtml = !!(html && String(html).trim());
  if (!hasHtml) return !_plainHasPerDigitSpacing(plain);
  if (_htmlLooksObfuscated(html)) return true;
  if (_plainHasPerDigitSpacing(plain)) return false;
  if (_htmlHasRealTable(html) || /<img\b/i.test(html)) return false;
  if (_plainLooksTabular(plain) && !_htmlHasRealTable(html)) return true;
  return true;
}

/** Collapse only single-digit-separated runs (2 0 2 6 → 2026), not +370 5 2222444. */
export function _collapseObfuscatedDigits(text) {
  let s = String(text || '').replace(/[\u200B\u200C\u200D\uFEFF\u00AD]/g, '');
  s = s.replace(/(?:\d[\s\u00a0]+)+/g, (run) => {
    const parts = run.trim().split(/[\s\u00a0]+/).filter(Boolean);
    if (parts.length >= 2 && parts.every((p) => /^\d$/.test(p))) {
      return parts.join('');
    }
    return run;
  });
  return s;
}

function _stripObfuscationStyles(doc) {
  doc.querySelectorAll('[style]').forEach((el) => {
    const style = el.getAttribute('style') || '';
    const lower = style.toLowerCase();
    const obfuscated = /font-size\s*:\s*0(?:\D|$)/.test(lower)
      || /line-height\s*:\s*0/.test(lower)
      || /opacity\s*:\s*0/.test(lower)
      || /visibility\s*:\s*hidden/.test(lower)
      || /(?:^|;)\s*display\s*:\s*none/.test(lower)
      || /max-height\s*:\s*0(?:px)?(?:\D|$)/.test(lower);
    if (!obfuscated) return;
    const kept = style.split(';').map((d) => d.trim()).filter((decl) => {
      if (!decl) return false;
      const d = decl.toLowerCase();
      if (/^font-size\s*:\s*0/.test(d)) return false;
      if (/^line-height\s*:\s*0/.test(d)) return false;
      if (/^opacity\s*:\s*0/.test(d)) return false;
      if (/^visibility\s*:\s*hidden/.test(d)) return false;
      if (/^display\s*:\s*none/.test(d)) return false;
      if (/^max-height\s*:\s*0/.test(d)) return false;
      if (/^width\s*:\s*0/.test(d)) return false;
      if (/^height\s*:\s*0/.test(d)) return false;
      return true;
    });
    if (kept.length) el.setAttribute('style', kept.join('; '));
    else el.removeAttribute('style');
  });
}

function _flattenTrackingElements(doc) {
  const tags = ['SPAN', 'FONT', 'B', 'I', 'EM', 'STRONG', 'U', 'SMALL'];
  let changed = true;
  while (changed) {
    changed = false;
    for (const el of [...doc.body.querySelectorAll(tags.join(','))]) {
      if (el.children.length > 0) continue;
      const text = el.textContent || '';
      if (text.length > 4) continue;
      el.replaceWith(doc.createTextNode(text));
      changed = true;
    }
  }
}

function _normalizeEmailTextNodes(doc) {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const next = _collapseObfuscatedDigits(node.textContent || '');
    if (next !== node.textContent) node.textContent = next;
  }
}

/** Rewrite cid: image refs to the attachment download route before sanitize. */
export function _rewriteEmailCidUrls(html, { uid, folder, accountId, attachments } = {}) {
  if (!html || !uid || !attachments?.length) return String(html || '');
  const byCid = new Map();
  for (const att of attachments) {
    const cid = att?.content_id || att?.contentId;
    if (cid) byCid.set(_normalizeCidKey(cid), att.index);
  }
  if (!byCid.size) return String(html || '');
  const acctQs = accountId ? `&account_id=${encodeURIComponent(accountId)}` : '';
  const folderQs = encodeURIComponent(folder || 'INBOX');
  const base = `/api/email/attachment/${encodeURIComponent(uid)}`;
  return String(html).replace(
    /\bcid:([^"'\s>)]+)/gi,
    (match, rawCid) => {
      const idx = byCid.get(_normalizeCidKey(rawCid));
      if (idx == null) return match;
      return `${base}/${idx}?folder=${folderQs}${acctQs}`;
    },
  );
}

/** Sanitize then prepare HTML mail for in-app rendering. */
export function _prepareEmailHtml(html, ctx) {
  let out = String(html || '');
  // Anti-scrape invisible separators and whitespace between per-char tags.
  out = out.replace(/[\u200B\u200C\u200D\uFEFF\u00AD]/g, '');
  out = out.replace(/>(\s|&nbsp;|&#160;|&#x0*a0;)+</gi, '><');
  out = out.replace(/(<br\s*\/?>[\s\u00a0]*){3,}/gi, '<br><br>');
  out = _rewriteEmailCidUrls(out, ctx);
  return _sanitizeHtml(out);
}

// HTML sanitizer for rendering remote email bodies. Strips script/iframe/
// form/etc., sanitizes embedded <style> (layout CSS from newsletters),
// kills `on*` handlers, blocks dangerous URL schemes, scrubs inline colour/
// font/position/letter-spacing styles so the theme can take over, and wraps
// highlight-bearing inline tags in <mark> so they render legibly across themes.
function _sanitizeHtmlOnce(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll(
    'script, iframe, object, embed, form, link, ' +
    'svg, math, base, meta, noscript, frame, frameset, applet, portal'
  ).forEach(el => el.remove());
  doc.querySelectorAll('style').forEach((el) => {
    el.textContent = _sanitizeStyleContent(el.textContent || '');
  });

  const URL_ATTRS = ['href', 'src', 'xlink:href', 'srcset', 'action', 'formaction', 'background', 'poster', 'data'];

  const STRIP_CSS_PROPS = ['color', 'background', 'background-color',
                           'font-family', 'font', '-webkit-text-fill-color',
                           'position', 'z-index', 'letter-spacing', 'word-spacing'];
  const HIGHLIGHT_INLINE_TAGS = new Set(['SPAN', 'FONT', 'EM', 'B', 'I',
                                         'STRONG', 'SMALL', 'U']);
  const HAS_BG_COLOR = /background(?:-color)?\s*:\s*(?!\s*(?:transparent|none|inherit|initial)\b)[^;]+/i;
  const _markedForHighlight = [];

  const blockRemote = localStorage.getItem('email_block_remote_images') !== '0';

  doc.querySelectorAll('*').forEach(el => {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) { el.removeAttribute(attr.name); continue; }
      if (name === 'srcdoc') { el.removeAttribute(attr.name); continue; }
      if (blockRemote && el.tagName === 'IMG' && name === 'src') {
        const src = String(attr.value || '').trim();
        if (src && !src.startsWith('cid:') && !src.startsWith('data:')) {
          el.setAttribute('data-blocked-src', src);
          el.setAttribute('src', 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7');
          el.setAttribute('title', 'Remote image blocked — click to load');
          el.classList.add('email-remote-img-blocked');
          continue;
        }
      }
      if (URL_ATTRS.includes(name) && (name === 'srcset' ? _isDangerousSrcset(attr.value) : _isDangerousUrl(attr.value))) {
        el.removeAttribute(attr.name);
        continue;
      }
    }
    el.removeAttribute('color');
    const bgcolor = el.getAttribute('bgcolor');
    el.removeAttribute('bgcolor');
    el.removeAttribute('face');
    const style = el.getAttribute('style');
    const hadHighlight =
      HIGHLIGHT_INLINE_TAGS.has(el.tagName) &&
      ((style && HAS_BG_COLOR.test(style)) || (bgcolor && bgcolor !== 'transparent'));
    if (hadHighlight) _markedForHighlight.push(el);
    if (style) {
      const kept = style.split(';').map(s => s.trim()).filter(decl => {
        if (!decl) return false;
        const lower = _compactUrlSchemeValue(decl);
        if (lower.includes('javascript:') || lower.includes('vbscript:') || lower.includes('data:') || lower.includes('expression(')) return false;
        const prop = decl.split(':', 1)[0].trim().toLowerCase();
        return !STRIP_CSS_PROPS.includes(prop);
      });
      if (kept.length) el.setAttribute('style', kept.join('; '));
      else el.removeAttribute('style');
    }
    if (el.tagName === 'A') {
      el.setAttribute('target', '_blank');
      el.setAttribute('rel', 'noopener noreferrer');
    }
  });

  _markedForHighlight.forEach(el => {
    if (el.tagName === 'MARK' || !el.firstChild) return;
    const mark = doc.createElement('mark');
    while (el.firstChild) mark.appendChild(el.firstChild);
    el.appendChild(mark);
  });

  _stripObfuscationStyles(doc);
  _flattenTrackingElements(doc);
  _normalizeEmailTextNodes(doc);

  return doc.body.innerHTML;
}

export function _sanitizeHtml(html) {
  let out = String(html ?? '');
  for (let i = 0; i < 4; i++) {
    const next = _sanitizeHtmlOnce(out);
    if (next === out) break;
    out = next;
  }
  return out;
}

/** plain = textContent; iframe = sandboxed HTML; inline = legacy sanitized innerHTML fallback. */
export function _emailBodyRenderMode(plain, html) {
  const p = String(plain || '').trim();
  const h = String(html || '').trim();
  if (!h) return 'plain';
  if (p && _htmlLooksObfuscated(h)) return 'plain';
  return 'iframe';
}

function _linkPatternRe() {
  return /\b((?:https?:\/\/|www\.)[^\s<>"']+[^\s<>"'.,;:!?)\]]|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b/g;
}

/** Linkify URLs and mailto addresses inside a plain-text container (DOM only). */
export function _linkifyPlainContainer(root) {
  if (!root) return;
  const re = _linkPatternRe();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.textContent || '';
    re.lastIndex = 0;
    if (!re.test(text)) continue;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const token = m[1];
      const a = document.createElement('a');
      if (token.includes('@') && !token.startsWith('www.')) {
        a.href = `mailto:${token}`;
      } else {
        a.href = token.startsWith('www.') ? `https://${token}` : token;
      }
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = token;
      frag.appendChild(a);
      last = m.index + token.length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode?.replaceChild(frag, node);
  }
}

/** Minimal sanitization for sandboxed iframe — keep sender layout/CSS, drop scripts only. */
export function _buildEmailIframeDoc(html, ctx = {}) {
  let raw = String(html || '');
  raw = raw.replace(/[\u200B\u200C\u200D\uFEFF\u00AD]/g, '');
  raw = raw.replace(/>(\s|&nbsp;|&#160;|&#x0*a0;)+</gi, '><');
  raw = _rewriteEmailCidUrls(raw, ctx);

  const trimmed = raw.trim();
  const isFullDoc = /^\s*<!doctype/i.test(trimmed) || /^\s*<html[\s>]/i.test(trimmed);
  const doc = new DOMParser().parseFromString(
    isFullDoc
      ? trimmed
      : `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>${trimmed}</body></html>`,
    'text/html',
  );
  const blockRemote = localStorage.getItem('email_block_remote_images') !== '0';
  const URL_ATTRS = ['href', 'src', 'xlink:href', 'srcset', 'action', 'formaction', 'background', 'poster', 'data'];

  doc.querySelectorAll(
    'script, iframe, object, embed, form, link, base, meta, noscript, frame, frameset, applet, portal, svg, math'
  ).forEach((el) => el.remove());

  doc.querySelectorAll('*').forEach((el) => {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on') || name === 'srcdoc') {
        el.removeAttribute(attr.name);
        continue;
      }
      if (blockRemote && el.tagName === 'IMG' && name === 'src') {
        const src = String(attr.value || '').trim();
        if (src && !src.startsWith('cid:') && !src.startsWith('data:')) {
          el.setAttribute('data-blocked-src', src);
          el.setAttribute('src', 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7');
          el.setAttribute('title', 'Remote image blocked — click to load');
          continue;
        }
      }
      if (URL_ATTRS.includes(name) && (name === 'srcset' ? _isDangerousSrcset(attr.value) : _isDangerousUrl(attr.value))) {
        el.removeAttribute(attr.name);
      }
    }
    if (el.tagName === 'A') {
      el.setAttribute('target', '_blank');
      el.setAttribute('rel', 'noopener noreferrer');
    }
  });

  const head = doc.head || doc.createElement('head');
  if (!doc.head) doc.documentElement.prepend(head);
  if (!head.querySelector('meta[charset]')) {
    const meta = doc.createElement('meta');
    meta.setAttribute('charset', 'utf-8');
    head.prepend(meta);
  }
  if (!head.querySelector('meta[name="referrer"]')) {
    const ref = doc.createElement('meta');
    ref.setAttribute('name', 'referrer');
    ref.setAttribute('content', 'no-referrer');
    head.appendChild(ref);
  }
  if (!head.querySelector('base')) {
    const base = doc.createElement('base');
    base.setAttribute('target', '_blank');
    base.setAttribute('rel', 'noopener noreferrer');
    head.prepend(base);
  }
  if (!isFullDoc) {
    const extra = doc.createElement('style');
    extra.textContent = (
      'html,body{margin:0;padding:8px 10px;font-family:system-ui,-apple-system,sans-serif;'
      + 'line-height:1.45;overflow-wrap:break-word;word-break:normal}'
      + 'img{max-width:100%;height:auto}table{border-collapse:collapse}'
    );
    head.appendChild(extra);
  }
  return '<!DOCTYPE html>' + doc.documentElement.outerHTML;
}
