// static/js/emailLibrary/signatureFold.js
//
// Heuristics that turn raw HTML email bodies into folded structures —
// "Earlier reply" details collapsing the quoted history, and "Signature"
// details collapsing the trailing corporate disclaimer / boilerplate.
//
// All pure functions of HTML strings (and one DOM-mutating exception:
// `_harvestAttribution` peels nodes off a container). No module state,
// no fetch, no globals. The icons (`_SIG_ICON`, `_QUOTE_ICON`) live here
// since `_foldSummary` is the only caller and other modules pass them in
// via that helper.

import {
  _TALON_WROTE, _TALON_FROM, _TALON_SENT, _TALON_ORIG_RE,
  _SIG_BLOAT_MIN_CHARS,
} from './utils.js';

// No leading icon on the signature fold — the user explicitly does not
// want a star/emoji-style glyph in this header.
export const _SIG_ICON = '';
export const _QUOTE_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>';

// HTML-escape used by `_extractQuoteMeta`. Inlined here (rather than
// imported from utils) so this module remains free of cross-file links.
function _esc(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

// Looks like a signature / corporate disclaimer rather than a quoted email.
// Heuristic: scores known "this is a disclaimer" tells against
// "this is a real email" tells. 3+ disclaimer hits with ≤1 conversational
// hit → signature.
export function _looksLikeSignature(html) {
  if (!html) return false;
  const txt = String(html).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  if (!txt) return false;
  let score = 0;
  const SIG_TELLS = [
    /\bregistered\s+in\b/i,
    /\blimited\s+liability\s+partnership\b/i,
    /\b(Pte\.?\s*Ltd|GmbH|S\.A\.|S\.A\.S|LLC|LLP|Inc\.?)\b/,
    /\bintended\s+solely\s+for\b/i,
    /\bconfidential(?:ity)?\s+(?:notice|information)\b/i,
    /\b(?:disclaimer|please\s+(?:notify|delete))\b/i,
    /\bunsubscribe\b/i,
    /\bUEN\b\s*\w/i,
    /\b\+\d[\d\s().-]{6,}\b/, // phone number
  ];
  for (const re of SIG_TELLS) if (re.test(txt)) score++;
  const PRIOR_TELLS = [
    /\bHi\s+[A-Z][a-z]+\b/,
    /\bDear\s+[A-Z][a-z]+\b/,
    /\bRegards\b/i,
    /\?\s*$/,
  ];
  let priorScore = 0;
  for (const re of PRIOR_TELLS) if (re.test(txt)) priorScore++;
  return score >= 3 && priorScore <= 1;
}

// Look for an "On <date>, <addr> wrote:" line at the END of a fragment
// and remove it (returning the captured meta string, or null). Also
// handles Outlook-style "From: ... Sent: ... Subject: ..." blocks.
export function _harvestAttribution(container) {
  const text = container.textContent || '';
  const wroteLineRe = new RegExp(`${_TALON_WROTE}\\s*:\\s*$|${_TALON_WROTE}\\s*:\\s*<`, 'i');
  const lastLines = text.trim().split('\n').slice(-3).join('\n');
  if (!wroteLineRe.test(lastLines)) {
    const outlookHeadRe = new RegExp(`${_TALON_FROM}\\s*:.*?${_TALON_SENT}\\s*:`, 'is');
    if (!outlookHeadRe.test(text.split('\n').slice(-12).join('\n'))) {
      if (!_TALON_ORIG_RE.test(text)) return null;
    }
  }
  const trailing = [];
  for (let i = container.childNodes.length - 1; i >= 0; i--) {
    const node = container.childNodes[i];
    const t = (node.textContent || '').trim();
    if (!t) { trailing.unshift(node); continue; }
    trailing.unshift(node);
    if (trailing.map(n => n.textContent || '').join('\n').length > 600) break;
  }
  const meta = _extractQuoteMeta(trailing.map(n => n.outerHTML || n.textContent || '').join(''));
  for (const n of trailing) {
    try { container.removeChild(n); } catch {}
  }
  return meta || null;
}

export function _extractTurnMetaFromBlockquote(bq) {
  const html = bq.innerHTML.slice(0, 2000);
  const meta = _extractQuoteMeta(html);
  return meta || null;
}

// "Earlier reply" / "Signature" summary header — caller supplies the
// label string + icon SVG. `meta`, when present, is split on " · " to
// promote the sender's name to the headline.
export function _foldSummary(label, iconSvg, meta) {
  let primary = label;
  let subMeta = meta || '';
  if (meta) {
    const idx = meta.indexOf(' · ');
    if (idx > 0) {
      primary = meta.slice(0, idx);
      subMeta = meta.slice(idx + 3);
    } else if (meta.length <= 80 && !/^\d/.test(meta)) {
      primary = meta;
      subMeta = '';
    }
  }
  // `meta` is derived from _extractQuoteMeta, which strips tags but then
  // un-escapes entities (to recover `<foo@bar.com>` for bubble alignment) —
  // so it can carry attacker-controlled angle brackets from a quoted block.
  // This summary is built into innerHTML, so escape both parts to stop a
  // crafted quote (e.g. `From: <img src=x onerror=...>`) from running script.
  const metaSpan = subMeta
    ? `<span class="email-fold-summary-meta">${_esc(subMeta)}</span>`
    : '';
  return (
    '<summary class="email-fold-summary">'
    + iconSvg
    + `<span class="email-fold-summary-name">${_esc(primary)}</span>`
    + metaSpan
    + '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    + '</summary>'
  );
}

// Extract sender + date from a quoted email block. Tries Outlook-style
// "From: X · Sent: Y" header first, falls back to Gmail-style
// "On <date>, <addr> wrote:". Returns a display string like
// "Jane Doe · Mon, Apr 18, 2026 at 9:31 AM" or `''`.
export function _extractQuoteMeta(html) {
  if (!html) return '';
  const txt = html
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/\s+/g, ' ')
    .slice(0, 1500);

  const FROM = '(?:From|Från|Von|De|Da|От|Od|Van)';
  const SENT = '(?:Sent|Skickat|Gesendet|Envoyé|Inviato|Enviado|Verzonden|Отправлено|Wysłane|Date)';
  const STOP = `(?=\\s+(?:To|Cc|Bcc|Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat|${SENT})\\s*:)`;
  const fromMatch = txt.match(new RegExp(`${FROM}\\s*:\\s*(.+?)${STOP}`, 'i'));
  const sentMatch = txt.match(new RegExp(`${SENT}\\s*:\\s*([^\\n]+?)(?=\\s+(?:To|Cc|Bcc|Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat)\\s*:)`, 'i'));
  let from = fromMatch ? fromMatch[1].trim() : '';
  let date = sentMatch ? sentMatch[1].trim() : '';

  if (!from && !date) {
    const gmail = txt.match(/On\s+([^,]+?,[^,]+?\d{4}[^,]*),?\s+(.+?)\s+wrote\s*:/i);
    if (gmail) { date = gmail[1].trim(); from = gmail[2].trim(); }
  }

  from = from.replace(/[<>]/g, '').replace(/\s+/g, ' ').trim();
  date = date.replace(/\s+/g, ' ').trim();
  if (from.length > 60) from = from.slice(0, 57) + '…';
  if (date.length > 28) date = date.slice(0, 25) + '…';

  // Return the raw sender/date text; `_foldSummary` is the single sink that
  // builds these into HTML, so it owns escaping. Escaping here too would
  // double-encode (e.g. "Ben & Jerry" -> "Ben &amp;amp; Jerry").
  if (from && date) return `${from} · ${date}`;
  if (from) return from;
  if (date) return date;
  return '';
}

// Peel the first non-empty line off the signature tail. That line is
// usually the signer's name — keep it inline so "Kind regards, / Bob"
// reads naturally. Returns `{ preBloat, bloat }` — `bloat` is what
// should go into the fold; `preBloat` stays visible above it.
export function _peelSigNameLine(htmlAfterClosing) {
  if (!htmlAfterClosing) return { preBloat: '', bloat: '' };
  const breakRe = /<br\s*\/?>|<\/p>|<\/div>|\n/gi;
  let cursor = 0;
  let nameConsumed = false;
  let mm;
  while ((mm = breakRe.exec(htmlAfterClosing)) !== null) {
    const seg = htmlAfterClosing.slice(cursor, mm.index)
      .replace(/<[^>]+>/g, '').replace(/&nbsp;/gi, ' ').trim();
    if (seg.length > 0) {
      const looksBloat = /[@]|tel\.?:|mobile:|phone:|www\.|https?:\/\/|sent from|^\+?\d[\d \-().]{6,}$/i.test(seg);
      if (looksBloat) {
        return {
          preBloat: htmlAfterClosing.slice(0, cursor),
          bloat: htmlAfterClosing.slice(cursor),
        };
      }
      if (!nameConsumed) {
        nameConsumed = true;
        const off = mm.index + mm[0].length;
        return {
          preBloat: htmlAfterClosing.slice(0, off),
          bloat: htmlAfterClosing.slice(off),
        };
      }
    }
    cursor = mm.index + mm[0].length;
  }
  return { preBloat: htmlAfterClosing, bloat: '' };
}

export function _isBloatedSig(htmlFragment) {
  if (!htmlFragment) return false;
  const plain = htmlFragment
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
  return plain.length >= _SIG_BLOAT_MIN_CHARS;
}

// Try folding using a per-sender cached signature (built by the
// `learn_sender_signatures` action). When the cached text is found near
// the end of `html`, slice there and wrap the tail in a details fold.
// Returns the wrapped HTML or null when the hint doesn't apply.
export function _tryFoldHintSig(html, hintSig) {
  if (!html || !hintSig || typeof hintSig !== 'string') return null;
  if (hintSig.length < 20) return null;
  const lines = hintSig.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const closingsRe = /^(?:Best regards|Best wishes|Kind regards|Yours (?:truly|sincerely|faithfully)|Sincerely|Cheers|Thanks|Thank you|Regards|Warm regards|Many thanks|Take care)[,!.\s]*$/i;
  const anchor = (lines.find(l => l.length >= 8 && !closingsRe.test(l)) || lines[0] || '').trim();
  if (anchor.length < 8) return null;
  const plain = [];
  const map = [];
  let i = 0;
  while (i < html.length) {
    if (html[i] === '<') {
      if (/^<br\s*\/?\s*>/i.test(html.slice(i, i + 6))) {
        plain.push('\n'); map.push(i);
        const e = html.indexOf('>', i);
        i = e + 1;
        continue;
      }
      const e = html.indexOf('>', i);
      if (e < 0) break;
      i = e + 1;
      continue;
    }
    if (html[i] === '&') {
      const semi = html.indexOf(';', i);
      if (semi > 0 && semi - i < 8) {
        const ent = html.slice(i + 1, semi);
        const dec = ({nbsp: ' ', amp: '&', lt: '<', gt: '>', quot: '"', apos: "'"})[ent];
        if (dec !== undefined) {
          plain.push(dec); map.push(i);
          i = semi + 1;
          continue;
        }
      }
    }
    plain.push(html[i]); map.push(i);
    i++;
  }
  const plainStr = plain.join('');
  const idx = plainStr.lastIndexOf(anchor);
  if (idx < 0) return null;
  const htmlStart = map[idx];
  if (htmlStart == null) return null;
  const before = html.slice(0, htmlStart);
  const sigSection = html.slice(htmlStart);
  if (!_isBloatedSig(sigSection)) return null;
  return before + '<details class="email-sig-fold">'
    + _foldSummary('Signature', _SIG_ICON)
    + sigSection + '</details>';
}

// Top-level signature fold — runs through several detection strategies
// in priority order. Returns the original html unchanged when no
// strategy fires.
export function _foldSignature(html, hintSig) {
  if (!html || typeof html !== 'string') return html;
  if (html.length > 80000) return html;
  if (hintSig) {
    const wrapped = _tryFoldHintSig(html, hintSig);
    if (wrapped !== null) return wrapped;
  }
  const wrap = (before, marker, rest) => {
    if (!_isBloatedSig(rest)) return html;
    return before + (marker || '') + '<details class="email-sig-fold">'
      + _foldSummary('Signature', _SIG_ICON) + rest + '</details>';
  };

  let m = html.match(/<div[^>]*class=["'][^"']*\bgmail_signature\b[^"']*["'][\s\S]*$/i);
  if (m) return wrap(html.slice(0, html.length - m[0].length), '', m[0]);
  m = html.match(/<div[^>]*data-smartmail=["']gmail_signature["'][\s\S]*$/i);
  if (m) return wrap(html.slice(0, html.length - m[0].length), '', m[0]);
  m = html.match(/<div[^>]*id=["'](?:Signature|signature|divRplyFwdMsg)["'][\s\S]*$/i);
  if (m) return wrap(html.slice(0, html.length - m[0].length), '', m[0]);

  m = html.match(/(<br>|\n)\s*--\s*(<br>|\n)([\s\S]*)$/i);
  if (m) {
    const idx = html.lastIndexOf(m[0]);
    return wrap(html.slice(0, idx), m[1], m[3]);
  }

  const blockBoundary = '(?:<br\\s*/?>|<\\/p>|<\\/div>|<\\/li>|<p[^>]*>|<div[^>]*>|<span[^>]*>|\\n)';
  const closings = '(?:Best regards|Best wishes|Kind regards|Yours truly|Yours sincerely|Yours faithfully|Best,|Best\\s|Cheers,|Cheers\\s|Thanks,|Thanks\\s|Thank you,|Regards,|Regards\\s|Sincerely[, ]|Warm regards|Many thanks|Talk soon|Take care)';
  m = html.match(new RegExp(`(${blockBoundary})\\s*(${closings})([\\s\\S]+)$`, 'i'));
  if (m) {
    const idx = html.lastIndexOf(m[0]);
    const boundary = m[1];
    const closing = m[2];
    const after = m[3];
    const { preBloat, bloat } = _peelSigNameLine(after);
    if (!_isBloatedSig(bloat)) return html;
    return html.slice(0, idx) + boundary + closing + preBloat
      + '<details class="email-sig-fold">' + _foldSummary('Signature', _SIG_ICON)
      + bloat + '</details>';
  }

  m = html.match(new RegExp(`(${blockBoundary})\\s*((?:Sent from my (?:iPhone|iPad|Android|Galaxy|Pixel|phone|mobile)|Get Outlook for (?:iOS|Android))[\\s\\S]*)$`, 'i'));
  if (m) {
    const idx = html.lastIndexOf(m[0]);
    return wrap(html.slice(0, idx), m[1], m[2]);
  }

  m = html.match(new RegExp(`(${blockBoundary})\\s*((?:CONFIDENTIALITY NOTICE|DISCLAIMER|This e-?mail (?:is confidential|may contain confidential)|The information (?:contained )?in this e-?mail|This message and any attachments)[\\s\\S]*)$`, 'i'));
  if (m) {
    const idx = html.lastIndexOf(m[0]);
    return wrap(html.slice(0, idx), m[1], m[2]);
  }

  return html;
}
