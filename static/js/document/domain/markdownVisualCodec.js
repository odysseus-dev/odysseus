// frontend/src/features/documents/domain/markdownVisualCodec.js
/**
 * Pure Markdown <-> Visual DOM-descriptor codec and round-trip safety inspector.
 * Contains no DOM/browser/timer globals so it complies with Odysseus domain layer rules.
 *
 * Contract:
 * - `inspectMarkdownVisualCompatibility` rejects every construct this codec cannot
 *   represent without restructuring the document on the first visual edit.
 * - `markdownToVisualHtml` renders the supported subset into the semantic markup used
 *   by `#doc-email-richbody`. All text is HTML-escaped and URLs are allowlisted.
 * - `serializeVisualNodesToMarkdown` emits canonical Markdown. Literal text that the
 *   renderer would otherwise read as syntax is backslash-escaped, minimally, so
 *   ordinary prose is emitted unchanged. `\[` and `\(` are never emitted because the
 *   application renderer treats them as LaTeX delimiters.
 */

import { _escHtml, _htmlToPlainText } from './documentKinds.js';
import { _normalizeRichLinkUrl } from './richTextRules.js';

const FENCE_OPEN_RE = /^[ ]{0,3}(`{3,}|~{3,})([^\n]*)$/;
const FENCE_CLOSE_RE = /^[ ]{0,3}(`{3,}|~{3,})[ \t]*$/;
const ATX_HEADING_RE = /^[ ]{0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/;
const ATX_START_RE = /^[ ]{0,3}#{1,6}(?:[ \t]|$)/;
const HR_RE = /^[ ]{0,3}(?:(\*[ \t]*){3,}|(-[ \t]*){3,}|(_[ \t]*){3,})$/;
const BLOCKQUOTE_RE = /^[ ]{0,3}> ?(.*)$/;
const LIST_ITEM_RE = /^([ \t]*)([-*+]|(\d{1,9})[.)])(?:([ \t]+)(.*))?$/;
const TASK_PREFIX_RE = /^\[([ xX])\](?:[ \t]+(.*))?$/;
const SETEXT_UNDERLINE_RE = /^[ ]{0,3}(?:=+|-+)[ \t]*$/;
const REF_DEF_RE = /^[ ]{0,3}\[[^\]]+\]:[ \t]*\S/;
const FOOTNOTE_DEF_RE = /^[ ]{0,3}\[\^[^\]]+\]:/;

// ASCII punctuation that CommonMark allows to be backslash-escaped.
const ESCAPED_PUNCT_RE = /\\[!-/:-@[-`{-~]/g;
const RAW_HTML_RE = /<!--|<!\[CDATA\[|<\/?[a-zA-Z][a-zA-Z0-9:-]*(?:\s[^>]*?)?\/?>/;
const ENTITY_RE = /&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);/;
const LATEX_OPEN_RE = /\\\[|\\\(/;
const DISPLAY_MATH_RE = /\$\$/;
const INLINE_MATH_RE = /(?<![$\d\\])\$(?!\$)(?=\S)([^$\n]+?)(?<=\S)\$(?!\$|\d)/;
const FOOTNOTE_REF_RE = /\[\^[^\]]+\]/;
const REFERENCE_LINK_RE = /\[[^\]\n]+\]\[[^\]]*\]/;
const TITLED_IMAGE_RE = /!\[[^\]\n]*\]\([^)\s]+\s+["'(]/;
const TITLED_LINK_RE = /\[[^\]\n]*\]\([^)\s]+\s+["'(]/;
const INSPECT_LINK_RE = /(!?)\[([^\]\n]*)\]\(([^)\s]*)\)/g;

function _normalizeNewlines(text) {
  return String(text ?? '').replace(/\r\n?/g, '\n');
}

function _columns(whitespace) {
  let col = 0;
  for (const ch of whitespace) col = ch === '\t' ? col + 4 - (col % 4) : col + 1;
  return col;
}

/**
 * Parse one list-item line. Content offsets follow CommonMark, so nesting decisions
 * match what other Markdown renderers produce.
 * @param {string} line
 * @returns {null|{kind: 'ul'|'ol'|'check', indent: number, offset: number, gap: number,
 *   start: number|null, checked: boolean, text: string}}
 */
function _parseListItemLine(line) {
  if (HR_RE.test(line)) return null;
  const m = String(line).match(LIST_ITEM_RE);
  if (!m) return null;
  const indent = _columns(m[1]);
  const marker = m[2];
  const gap = m[4] ? _columns(m[1] + marker + m[4]) - _columns(m[1] + marker) : 0;
  let text = m[5] || '';
  let kind = m[3] ? 'ol' : 'ul';
  let checked = false;
  if (kind === 'ul') {
    const task = text.match(TASK_PREFIX_RE);
    if (task) {
      kind = 'check';
      checked = task[1] !== ' ';
      text = task[2] || '';
    }
  }
  return {
    kind,
    indent,
    offset: indent + marker.length + Math.max(1, Math.min(gap, 4)),
    gap,
    start: m[3] ? Number(m[3]) : null,
    checked,
    text,
  };
}

function _interruptsParagraph(line) {
  if (ATX_START_RE.test(line) || HR_RE.test(line) || FENCE_OPEN_RE.test(line) || BLOCKQUOTE_RE.test(line)) {
    return true;
  }
  const item = _parseListItemLine(line);
  return Boolean(item && item.text && (item.kind !== 'ol' || item.start === 1));
}

function _stripInlineCodeSpans(line) {
  let out = '';
  let i = 0;
  while (i < line.length) {
    if (line[i] === '\\' && i + 1 < line.length) {
      out += line.slice(i, i + 2);
      i += 2;
      continue;
    }
    if (line[i] === '`') {
      let tickLen = 0;
      while (i + tickLen < line.length && line[i + tickLen] === '`') tickLen++;
      const closer = '`'.repeat(tickLen);
      const end = line.indexOf(closer, i + tickLen);
      if (end !== -1 && (end + tickLen >= line.length || line[end + tickLen] !== '`')) {
        out += ' ';
        i = end + tickLen;
        continue;
      }
      out += closer;
      i += tickLen;
      continue;
    }
    out += line[i];
    i++;
  }
  return out;
}

function _splitTableCells(line) {
  let trimmed = String(line || '').trim();
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1);
  if (trimmed.endsWith('|')) {
    let slashes = 0;
    let idx = trimmed.length - 2;
    while (idx >= 0 && trimmed[idx] === '\\') {
      slashes++;
      idx--;
    }
    if (slashes % 2 === 0) {
      trimmed = trimmed.slice(0, -1);
    }
  }
  const cells = [];
  let current = '';
  let i = 0;
  while (i < trimmed.length) {
    const ch = trimmed[i];
    if (ch === '`') {
      let tickLen = 0;
      while (i + tickLen < trimmed.length && trimmed[i + tickLen] === '`') tickLen++;
      const closer = '`'.repeat(tickLen);
      const end = trimmed.indexOf(closer, i + tickLen);
      if (end !== -1 && (end + tickLen >= trimmed.length || trimmed[end + tickLen] !== '`')) {
        current += trimmed.slice(i, end + tickLen);
        i = end + tickLen;
        continue;
      }
    }
    if (ch === '\\' && i + 1 < trimmed.length) {
      current += trimmed[i + 1] === '|' ? '|' : trimmed.slice(i, i + 2);
      i += 2;
      continue;
    }
    if (ch === '|') {
      cells.push(current.trim());
      current = '';
      i++;
      continue;
    }
    current += ch;
    i++;
  }
  cells.push(current.trim());
  return cells;
}

function _isTableSeparatorLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed.includes('|') && !/^:?-{3,}:?$/.test(trimmed)) return false;
  const cells = _splitTableCells(trimmed);
  if (!cells.length) return false;
  return cells.every(cell => /^:?-+:?$/.test(cell));
}

function _parseTableAlignments(line) {
  return _splitTableCells(line).map(cell => {
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    if (left) return 'left';
    return '';
  });
}

function _normalizeVisualImageUrl(rawUrl) {
  const url = String(rawUrl || '').trim();
  if (!url || /[\u0000-\u001f\u007f-\u009f]/.test(url)) return '';
  if (/^\/api\/upload\/[A-Za-z0-9._~:/?#[\]@!$&'()*+,;=%-]+$/.test(url)) return url;
  if (/^\/static\/[A-Za-z0-9._~:/?#[\]@!$&'()*+,;=%-]+$/.test(url)) return url;
  const normalized = _normalizeRichLinkUrl(url);
  if (!normalized) return '';
  if (/^(?:https?:|\/|\.\.?\/)/i.test(normalized)) return normalized;
  return '';
}

/**
 * Return the URL only when it is allowlisted and the visual editor would keep it
 * byte-for-byte. Normalization that rewrites the target (for example a bare
 * `docs/page.md` becoming `https://docs/page.md`) is treated as unsupported.
 */
function _exactLinkUrl(url, image) {
  const safe = image ? _normalizeVisualImageUrl(url) : _normalizeRichLinkUrl(url);
  return safe && safe === url ? safe : '';
}

function _neutralizeEscapes(text) {
  return String(text).replace(ESCAPED_PUNCT_RE, '  ');
}

function _linkSyntaxReason(neutral) {
  const opens = (neutral.match(/\]\(/g) || []).length;
  if (!opens) return '';
  if (TITLED_IMAGE_RE.test(neutral)) return 'titled image';
  if (TITLED_LINK_RE.test(neutral)) return 'titled link';
  let valid = 0;
  for (const m of neutral.matchAll(INSPECT_LINK_RE)) {
    const image = m[1] === '!';
    if (!image && !m[2].trim()) return 'complex link syntax';
    if (m[3].includes('(')) return 'complex link URL';
    if (!_exactLinkUrl(m[3], image)) return image ? 'unsupported image URL' : 'unsupported link URL';
    valid++;
  }
  return valid === opens ? '' : 'complex link syntax';
}

/**
 * Add the reasons why inline text (with code spans already removed) cannot be
 * represented by the visual editor.
 * @param {string} withoutCode
 * @param {Set<string>} reasons
 */
function _collectInlineReasons(withoutCode, reasons) {
  if (LATEX_OPEN_RE.test(withoutCode)) reasons.add('math notation');
  const neutral = _neutralizeEscapes(withoutCode);
  if (RAW_HTML_RE.test(neutral)) reasons.add('raw HTML or autolink');
  if (ENTITY_RE.test(neutral)) reasons.add('HTML entity');
  if (DISPLAY_MATH_RE.test(neutral) || INLINE_MATH_RE.test(neutral)) reasons.add('math notation');
  if (FOOTNOTE_REF_RE.test(neutral)) reasons.add('footnote');
  if (REFERENCE_LINK_RE.test(neutral)) reasons.add('reference link');
  const linkReason = _linkSyntaxReason(neutral);
  if (linkReason) reasons.add(linkReason);
}

/**
 * Inspect Markdown text for constructs that cannot round-trip losslessly
 * through the Visual WYSIWYG editor.
 * @param {string} markdown
 * @returns {{ supported: boolean, reasons: string[] }}
 */
export function inspectMarkdownVisualCompatibility(markdown) {
  const src = _normalizeNewlines(markdown);
  if (!src.trim()) return { supported: true, reasons: [] };

  const reasons = new Set();
  const lines = src.split('\n');
  let startIndex = 0;

  // Front matter at document start
  if (lines[0]?.trim() === '---' && lines.length > 2) {
    for (let i = 1; i < lines.length; i++) {
      const marker = lines[i].trim();
      if (marker === '---' || marker === '...') {
        reasons.add('YAML front matter');
        startIndex = i + 1;
        break;
      }
    }
  } else if (lines[0]?.trim() === '+++' && lines.length > 2) {
    for (let i = 1; i < lines.length; i++) {
      if (lines[i].trim() === '+++') {
        reasons.add('TOML front matter');
        startIndex = i + 1;
        break;
      }
    }
  }

  let activeFence = null;
  let prevLinePlainText = false;
  let prevBlank = true;
  let prevWasListItem = false;
  let listOffsets = null;
  let inQuote = false;

  for (let i = startIndex; i < lines.length; i++) {
    const line = lines[i];

    if (activeFence) {
      const closeMatch = line.match(FENCE_CLOSE_RE);
      if (closeMatch && closeMatch[1][0] === activeFence.char && closeMatch[1].length >= activeFence.len) {
        activeFence = null;
      }
      continue;
    }

    if (!line.trim()) {
      prevLinePlainText = false;
      prevBlank = true;
      prevWasListItem = false;
      inQuote = false;
      continue;
    }

    const item = _parseListItemLine(line);
    const indented = /^[ \t]/.test(line);
    const softBreakContinuation = Boolean(listOffsets && !item && indented
      && /(?: {2,}|\\)$/.test(lines[i - 1] || '')
      && _columns(line.match(/^[ \t]*/)[0]) >= listOffsets[listOffsets.length - 1]);

    // Content that belongs to a list item beyond its first line is restructured by
    // the visual list model, so it is rejected instead of silently moved.
    if (listOffsets && !item && !softBreakContinuation) {
      if (!prevBlank && (prevWasListItem || indented) && (!_interruptsParagraph(line) || indented)) {
        reasons.add('multi-line list item');
      } else if (prevBlank && indented) {
        reasons.add('multi-paragraph list item');
      }
      listOffsets = null;
    }
    if (listOffsets && item && prevBlank && item.indent > 0) {
      // A nested item after a blank line makes the parent list loose; the visual
      // model cannot keep that spacing.
      reasons.add('multi-paragraph list item');
    }

    if (inQuote && !BLOCKQUOTE_RE.test(line)) {
      if (!prevBlank && !_interruptsParagraph(line)) reasons.add('lazy blockquote continuation');
      inQuote = false;
    }

    const fenceMatch = line.match(FENCE_OPEN_RE);
    if (fenceMatch) {
      const marker = fenceMatch[1];
      const info = (fenceMatch[2] || '').trim().toLowerCase();
      if (marker[0] === '`' && info.includes('`')) {
        // CommonMark invalid backtick info string; treat conservatively
        reasons.add('complex code fence');
      }
      const lang = info.split(/\s+/)[0] || '';
      if (lang === 'mermaid') reasons.add('Mermaid diagram');
      else if (lang === 'svg') reasons.add('SVG diagram');
      if (/^[ ]{1,3}/.test(line)) reasons.add('indented code fence');
      activeFence = { char: marker[0], len: marker.length };
      prevLinePlainText = false;
      prevBlank = false;
      prevWasListItem = false;
      listOffsets = null;
      continue;
    }

    if (prevLinePlainText && SETEXT_UNDERLINE_RE.test(line)) {
      reasons.add('Setext heading');
    }

    if (item) {
      if (!listOffsets) {
        if (item.indent >= 4) reasons.add('indented code block');
        if (prevLinePlainText && item.kind === 'ol' && item.start !== 1) reasons.add('ambiguous list start');
        listOffsets = [];
      }
      while (listOffsets.length && item.indent < listOffsets[listOffsets.length - 1]) listOffsets.pop();
      if (listOffsets.length && item.indent >= listOffsets[listOffsets.length - 1] + 4) {
        reasons.add('irregular list indentation');
      }
      if (item.gap > 4 && item.text) reasons.add('irregular list indentation');
      listOffsets.push(item.offset);
    } else if (!softBreakContinuation && /^(?: {4}|\t)\S/.test(line) && (prevBlank || !prevLinePlainText)) {
      reasons.add('indented code block');
    }

    if (/^[ ]{0,3}>\s*>/.test(line)) {
      reasons.add('nested blockquote');
    } else if (/^[ ]{0,3}>\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|```|~~~)/.test(line)) {
      reasons.add('structured blockquote');
    }

    if (REF_DEF_RE.test(line)) {
      reasons.add('reference link definition');
    }
    if (FOOTNOTE_DEF_RE.test(line)) {
      reasons.add('footnote');
    }

    // Table validation: if this line looks like a table header followed by separator
    if (line.includes('|') && i + 1 < lines.length && _isTableSeparatorLine(lines[i + 1])) {
      const headerCols = _splitTableCells(line).length;
      const sepCols = _splitTableCells(lines[i + 1]).length;
      if (headerCols !== sepCols || headerCols < 1) {
        reasons.add('complex table');
      } else {
        let j = i + 2;
        while (j < lines.length && lines[j].trim() && lines[j].includes('|')) {
          if (_splitTableCells(lines[j]).length !== headerCols) {
            reasons.add('complex table');
            break;
          }
          j++;
        }
      }
    }

    _collectInlineReasons(_stripInlineCodeSpans(line), reasons);

    const isQuote = BLOCKQUOTE_RE.test(line);
    inQuote = isQuote;
    prevWasListItem = Boolean(item) || softBreakContinuation;
    prevBlank = false;
    prevLinePlainText = !item && !softBreakContinuation
      && !ATX_START_RE.test(line)
      && !HR_RE.test(line)
      && !isQuote
      && !line.includes('|');
  }

  if (activeFence) {
    reasons.add('unclosed code block');
  }

  return {
    supported: reasons.size === 0,
    reasons: Array.from(reasons),
  };
}

function _renderInlineMarkdown(rawText) {
  const text = String(rawText || '');
  if (!text) return '';

  const tokens = [];
  const pushToken = (html) => {
    const key = `\u0000MDTOK_${tokens.length}\u0000`;
    tokens.push(html);
    return key;
  };

  // 1. Backslash escapes and inline code spans (supports multi-backtick spans).
  //    An escaped backtick cannot open a code span; backslashes inside a code span
  //    are literal.
  let s = '';
  let i = 0;
  while (i < text.length) {
    if (text[i] === '\\' && i + 1 < text.length && /[!-/:-@[-`{-~]/.test(text[i + 1])) {
      s += pushToken(_escHtml(text[i + 1]));
      i += 2;
      continue;
    }
    if (text[i] === '`') {
      let tickLen = 0;
      while (i + tickLen < text.length && text[i + tickLen] === '`') tickLen++;
      const closer = '`'.repeat(tickLen);
      let searchFrom = i + tickLen;
      let end = -1;
      while (searchFrom < text.length) {
        const candidate = text.indexOf(closer, searchFrom);
        if (candidate === -1) break;
        const beforeOk = text[candidate - 1] !== '`';
        const afterOk = candidate + tickLen >= text.length || text[candidate + tickLen] !== '`';
        if (beforeOk && afterOk) {
          end = candidate;
          break;
        }
        searchFrom = candidate + 1;
      }
      if (end !== -1) {
        let code = text.slice(i + tickLen, end);
        if (code.length >= 2 && code.startsWith(' ') && code.endsWith(' ') && code.trim()) {
          code = code.slice(1, -1);
        }
        s += pushToken(`<code>${_escHtml(code)}</code>`);
        i = end + tickLen;
        continue;
      }
      s += closer;
      i += tickLen;
      continue;
    }
    s += text[i];
    i++;
  }

  // 2. Images: ![alt](url)
  s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (match, alt, url) => {
    const safeUrl = _normalizeVisualImageUrl(url);
    if (!safeUrl) return match;
    return pushToken(`<img class="richtext-image" src="${_escHtml(safeUrl)}" alt="${_escHtml(_restoreTokens(alt, tokens.map(_htmlTokenText)))}">`);
  });

  // 3. Links: [label](url)
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    const safeUrl = _normalizeRichLinkUrl(url);
    if (!safeUrl) return label;
    return pushToken(`<a href="${_escHtml(safeUrl)}" rel="noopener noreferrer">${_renderInlineFormattingOnly(label, tokens)}</a>`);
  });

  s = _renderInlineFormattingOnly(s, tokens);
  return _restoreTokens(s, tokens);
}

function _htmlTokenText(html) {
  return _htmlToPlainText(html);
}

function _restoreTokens(text, tokens) {
  return String(text).replace(/\u0000MDTOK_(\d+)\u0000/g, (_, idx) => tokens[Number(idx)] ?? '');
}

function _isPunct(ch) {
  return Boolean(ch && /[!-/:-@[-`{-~]|\p{P}|\p{S}/u.test(ch));
}

function _isSpace(ch) {
  return Boolean(ch && /\s/.test(ch));
}

function _getCharBefore(s, idx, tokens) {
  if (idx <= 0) return null;
  if (s[idx - 1] === '\u0000') {
    const match = s.slice(0, idx).match(/\u0000MDTOK_(\d+)\u0000$/);
    if (match) {
      const plain = _htmlToPlainText(tokens[Number(match[1])] ?? '');
      return plain.length ? plain[plain.length - 1] : null;
    }
  }
  return s[idx - 1];
}

function _getCharAfter(s, idx, tokens) {
  if (idx >= s.length) return null;
  if (s[idx] === '\u0000') {
    const match = s.slice(idx).match(/^\u0000MDTOK_(\d+)\u0000/);
    if (match) {
      const plain = _htmlToPlainText(tokens[Number(match[1])] ?? '');
      return plain.length ? plain[0] : null;
    }
  }
  return s[idx];
}

function _renderInlineFormattingOnly(text, tokens) {
  const s = _escHtml(text);
  const pieces = [];
  let i = 0;
  let textBuf = '';

  const flushText = () => {
    if (textBuf) {
      pieces.push({ type: 'text', text: textBuf });
      textBuf = '';
    }
  };

  while (i < s.length) {
    if (s[i] === '\u0000') {
      const end = s.indexOf('\u0000', i + 1);
      if (end !== -1) {
        textBuf += s.slice(i, end + 1);
        i = end + 1;
        continue;
      }
    }

    const ch = s[i];
    if (ch === '*' || ch === '_' || ch === '~') {
      let runLen = 1;
      while (i + runLen < s.length && s[i + runLen] === ch) {
        runLen++;
      }
      if (ch === '~' && runLen < 2) {
        textBuf += ch;
        i++;
        continue;
      }

      const isDelimChar = (c) => c === '*' || c === '_' || c === '~';

      const immPrevChar = _getCharBefore(s, i, tokens);
      const immNextChar = _getCharAfter(s, i + runLen, tokens);

      let leftIdx = i - 1;
      while (leftIdx >= 0 && isDelimChar(s[leftIdx])) leftIdx--;
      const outerPrevChar = leftIdx >= 0 ? _getCharBefore(s, leftIdx + 1, tokens) : null;

      let rightIdx = i + runLen;
      while (rightIdx < s.length && isDelimChar(s[rightIdx])) rightIdx++;
      const outerNextChar = rightIdx < s.length ? _getCharAfter(s, rightIdx, tokens) : null;

      const prevChar = immPrevChar && isDelimChar(immPrevChar) ? outerPrevChar : immPrevChar;
      const nextChar = immNextChar && isDelimChar(immNextChar) ? outerNextChar : immNextChar;

      const prevIsSpace = !prevChar || _isSpace(prevChar);
      const prevIsPunct = Boolean(immPrevChar && _isPunct(immPrevChar));
      const nextIsSpace = !nextChar || _isSpace(nextChar);
      const nextIsPunct = Boolean(nextChar && _isPunct(nextChar));

      const leftFlanking = !nextIsSpace && (!nextIsPunct || prevIsSpace || prevIsPunct);
      const rightFlanking = !prevIsSpace && (!prevIsPunct || nextIsSpace || nextIsPunct);

      let canOpen = leftFlanking;
      let canClose = rightFlanking;

      if (ch === '_') {
        canOpen = leftFlanking && (!rightFlanking || prevIsPunct);
        canClose = rightFlanking && (!leftFlanking || nextIsPunct);
      } else {
        if (runLen >= 2) {
          if (!prevIsSpace) canClose = true;
          if (!nextIsSpace) canOpen = true;
        }
      }

      if (canOpen || canClose) {
        flushText();
        pieces.push({
          type: 'delim',
          char: ch,
          count: runLen,
          canOpen,
          canClose,
        });
        i += runLen;
        continue;
      }

      textBuf += ch.repeat(runLen);
      i += runLen;
      continue;
    }

    textBuf += ch;
    i++;
  }
  flushText();

  const stack = [];
  for (let idx = 0; idx < pieces.length; idx++) {
    const curr = pieces[idx];
    if (curr.type !== 'delim' || !curr.canClose) {
      if (curr.type === 'delim' && curr.canOpen) stack.push(idx);
      continue;
    }

    let matchIdx = -1;
    for (let st = stack.length - 1; st >= 0; st--) {
      const opener = pieces[stack[st]];
      if (opener.char === curr.char) {
        const both = (opener.canOpen && opener.canClose) || (curr.canOpen && curr.canClose);
        if (both && (opener.count + curr.count) % 3 === 0 && opener.count % 3 !== 0 && curr.count % 3 !== 0) {
          continue;
        }
        matchIdx = st;
        break;
      }
    }

    if (matchIdx === -1) {
      if (curr.canOpen) stack.push(idx);
      continue;
    }

    const openerIdx = stack[matchIdx];
    const opener = pieces[openerIdx];

    let take = 1;
    if (curr.char === '~') {
      take = Math.min(opener.count, curr.count, 2);
      if (take < 2) {
        if (curr.canOpen) stack.push(idx);
        continue;
      }
    } else if (opener.count === 3 && curr.count === 3) {
      take = 1;
    } else if (opener.count >= 2 && curr.count >= 2) {
      take = 2;
    }

    opener.count -= take;
    curr.count -= take;

    const openTag = curr.char === '~' ? '<del>' : (take === 2 ? '<strong>' : '<em>');
    const closeTag = curr.char === '~' ? '</del>' : (take === 2 ? '</strong>' : '</em>');

    opener.after = openTag + (opener.after || '');
    curr.before = (curr.before || '') + closeTag;

    stack.splice(matchIdx + 1);

    if (opener.count === 0) {
      stack.splice(matchIdx, 1);
    }

    if (curr.count > 0) {
      idx--;
    }
  }

  let out = '';
  for (const t of pieces) {
    if (t.type === 'text') {
      out += t.text;
    } else {
      if (t.before) out += t.before;
      if (t.count > 0) out += t.char.repeat(t.count);
      if (t.after) out += t.after;
    }
  }
  return _restoreTokens(out, tokens);
}

function _buildListTree(items) {
  const roots = [];
  const stack = [];
  for (const item of items) {
    while (stack.length && item.indent < stack[stack.length - 1].item.offset) stack.pop();
    const lists = stack.length ? stack[stack.length - 1].lists : roots;
    let list = lists[lists.length - 1];
    if (!list || list.kind !== item.kind) {
      list = { kind: item.kind, start: item.start, entries: [] };
      lists.push(list);
    }
    const entry = { item, lists: [] };
    list.entries.push(entry);
    stack.push(entry);
  }
  return roots;
}

function _renderListTree(list) {
  const tag = list.kind === 'ol' ? 'ol' : 'ul';
  const classAttr = list.kind === 'check' ? ' class="rich-checklist"' : '';
  const startAttr = list.kind === 'ol' && Number.isInteger(list.start) && list.start !== 1
    ? ` start="${list.start}"`
    : '';
  const items = list.entries.map(({ item, lists }) => {
    const checkedAttrs = item.kind === 'check'
      ? ` data-checked="${item.checked ? 'true' : 'false'}" aria-checked="${item.checked ? 'true' : 'false'}"`
      : '';
    const nested = lists.map(_renderListTree).join('');
    const body = item.text ? item.text.split('\n').map(line => _renderInlineMarkdown(line.replace(/(?: {2,}|\\)$/, ''))).join('<br>') : (nested ? '' : '<br>');
    return `<li${checkedAttrs}>${body}${nested}</li>`;
  }).join('');
  return `<${tag}${classAttr}${startAttr}>${items}</${tag}>`;
}

/**
 * Convert supported Markdown into semantic Visual HTML matching #doc-email-richbody.
 * @param {string} markdown
 * @returns {string}
 */
export function markdownToVisualHtml(markdown) {
  const src = _normalizeNewlines(markdown);
  if (!src.trim()) return '';

  const lines = src.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Skip blank lines
    if (!line.trim()) {
      i++;
      continue;
    }

    // Fenced code block
    const fenceMatch = line.match(FENCE_OPEN_RE);
    if (fenceMatch) {
      const marker = fenceMatch[1];
      const lang = (fenceMatch[2] || '').trim().split(/\s+/)[0] || '';
      const codeLines = [];
      i++;
      while (i < lines.length) {
        const closeMatch = lines[i].match(FENCE_CLOSE_RE);
        if (closeMatch && closeMatch[1][0] === marker[0] && closeMatch[1].length >= marker.length) {
          i++;
          break;
        }
        codeLines.push(lines[i]);
        i++;
      }
      const langAttrs = lang ? ` class="language-${_escHtml(lang)}" data-lang="${_escHtml(lang)}"` : '';
      const body = codeLines.length ? _escHtml(codeLines.join('\n')) : '<br>';
      blocks.push(`<pre><code${langAttrs}>${body}</code></pre>`);
      continue;
    }

    // ATX Heading
    const headingMatch = line.match(ATX_HEADING_RE);
    if (headingMatch && ATX_START_RE.test(line)) {
      const level = headingMatch[1].length;
      const content = (headingMatch[2] || '').replace(/(^|[ \t]+)#+[ \t]*$/, '').trim();
      blocks.push(`<h${level}>${content ? _renderInlineMarkdown(content) : '<br>'}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule
    if (HR_RE.test(line)) {
      blocks.push('<hr>');
      i++;
      continue;
    }

    // GFM pipe table
    if (line.includes('|') && i + 1 < lines.length && _isTableSeparatorLine(lines[i + 1])) {
      const headerCells = _splitTableCells(line);
      const alignments = _parseTableAlignments(lines[i + 1]);
      i += 2;
      const bodyRows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
        bodyRows.push(_splitTableCells(lines[i]));
        i++;
      }
      const renderCell = (tag, cellText, colIdx) => {
        const align = alignments[colIdx] || '';
        const styleAttr = align ? ` style="text-align: ${align};"` : '';
        const html = cellText ? _renderInlineMarkdown(cellText) : '<br>';
        return `<${tag}${styleAttr}>${html}</${tag}>`;
      };
      const thead = `<thead><tr>${headerCells.map((c, idx) => renderCell('th', c, idx)).join('')}</tr></thead>`;
      const tbodyRows = bodyRows.map(row => {
        const padded = headerCells.map((_, idx) => row[idx] || '');
        return `<tr>${padded.map((c, idx) => renderCell('td', c, idx)).join('')}</tr>`;
      }).join('');
      blocks.push(`<table>${thead}<tbody>${tbodyRows}</tbody></table>`);
      continue;
    }

    // Blockquote: consecutive quote lines form one paragraph; empty quote lines
    // separate paragraphs.
    if (BLOCKQUOTE_RE.test(line)) {
      const paragraphs = [];
      let current = [];
      while (i < lines.length) {
        const qm = lines[i].match(BLOCKQUOTE_RE);
        if (!qm) break;
        if (qm[1].trim()) {
          current.push(qm[1]);
        } else if (current.length) {
          paragraphs.push(current);
          current = [];
        }
        i++;
      }
      if (current.length) paragraphs.push(current);
      const alertMatch = paragraphs.length > 0 && paragraphs[0].length > 0
        ? paragraphs[0][0].trim().match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]$/i) : null;
      const alertType = alertMatch ? alertMatch[1].toLowerCase() : null;
      const bqClass = alertType ? ` class="markdown-alert markdown-alert-${alertType}" data-alert="${alertType.toUpperCase()}"` : '';
      if (alertType) {
        paragraphs[0].shift();
        if (!paragraphs[0].length) paragraphs.shift();
      }
      const inner = paragraphs.length
        ? `${alertType ? `<p><span class="markdown-alert-label" contenteditable="false" data-alert-label="${alertType.toUpperCase()}">${alertType[0].toUpperCase()}${alertType.slice(1)}</span></p>` : ''}${paragraphs.map(p => `<p>${p.map(_renderInlineMarkdown).join('<br>')}</p>`).join('')}`
        : '<p><br></p>';
      blocks.push(`<blockquote${bqClass}>${inner}</blockquote>`);
      continue;
    }

    // Lists (checklist, unordered, ordered)
    if (_parseListItemLine(line)) {
      const items = [];
      while (i < lines.length) {
        const item = _parseListItemLine(lines[i]);
        if (!item) {
          const previous = items[items.length - 1];
          const continuation = lines[i];
          if (previous && /(?: {2,}|\\)$/.test(previous.text)
              && _columns(continuation.match(/^[ \t]*/)[0]) >= previous.offset && continuation.trim()) {
            previous.text += '\n' + continuation.trimStart();
            i++;
            continue;
          }
          break;
        }
        items.push(item);
        i++;
      }
      blocks.push(_buildListTree(items).map(_renderListTree).join(''));
      continue;
    }

    // Paragraph (collect contiguous plain lines)
    const paraLines = [];
    while (i < lines.length) {
      const cur = lines[i];
      if (!cur.trim()) break;
      if (paraLines.length && (
        FENCE_OPEN_RE.test(cur)
          || ATX_START_RE.test(cur)
          || HR_RE.test(cur)
          || BLOCKQUOTE_RE.test(cur)
          || _parseListItemLine(cur)
          || (cur.includes('|') && i + 1 < lines.length && _isTableSeparatorLine(lines[i + 1])))) {
        break;
      }
      paraLines.push(cur.replace(/^[ \t]+/, ''));
      i++;
    }
    if (paraLines.length) {
      blocks.push(`<p>${paraLines.map(_renderInlineMarkdown).join('<br>')}</p>`);
    }
  }

  return blocks.join('');
}

function _wrapInlineMarker(text, marker) {
  if (!text) return '';
  const lead = (text.match(/^[ \t]+/) || [''])[0];
  const trail = (text.match(/[ \t]+$/) || [''])[0];
  const core = text.slice(lead.length, text.length - trail.length);
  if (!core) return text;
  return `${lead}${marker}${core}${marker}${trail}`;
}

function _formatInlineCode(raw) {
  const text = String(raw || '').replace(/​/g, '').replace(/ /g, ' ');
  if (!text) return '';
  let maxRun = 0;
  let cur = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '`') {
      cur++;
      if (cur > maxRun) maxRun = cur;
    } else {
      cur = 0;
    }
  }
  const fence = '`'.repeat(maxRun + 1);
  const needsPad = text.startsWith('`') || text.endsWith('`') || (fence.length > 1 && (text.startsWith(' ') || text.endsWith(' ')));
  return needsPad ? `${fence} ${text} ${fence}` : `${fence}${text}${fence}`;
}

function _escapeLinkLabel(text) {
  // `\[` is a LaTeX delimiter in the application renderer, so only `]` is escaped.
  return String(text || '').replace(/(?<!\\)\]/g, '\\]');
}

function _escapeLinkUrl(url) {
  return String(url || '').replace(/\)/g, '%29').replace(/\s/g, '%20');
}

function _normalizeVisualText(text) {
  return String(text || '').replace(/​/g, '').replace(/ /g, ' ');
}

/**
 * True when literal text would not survive a save/reload unchanged: the renderer
 * would read part of it as syntax, or the inspector would reject the document.
 */
function _textNeedsEscaping(text) {
  if (!text) return false;
  if (_renderInlineMarkdown(text) !== _escHtml(text)) return true;
  const reasons = new Set();
  _collectInlineReasons(_stripInlineCodeSpans(text), reasons);
  return reasons.size > 0 || /\](?=[([:])/.test(text) || /\\$/.test(text);
}

function _escapeInlineText(text) {
  return String(text)
    .replace(/\\/g, '\\\\')
    .replace(/[*_`~$]/g, '\\$&')
    .replace(/\](?=[([:])/g, '\\]')
    .replace(/\[\^/g, '[\\^')
    .replace(/<(?=[A-Za-z/!?])/g, '\\<')
    .replace(/&(?=#?[A-Za-z0-9]+;)/g, '\\&');
}

function _areMergeableFormattingElements(a, b) {
  if (!a || !b || a.type !== 'element' || b.type !== 'element') return false;
  if (['br', 'img', 'a'].includes(a.tag) || ['br', 'img', 'a'].includes(b.tag)) return false;
  if (_isInlineCodeNode(a) && _isInlineCodeNode(b)) return true;
  if (_isInlineCodeNode(a) || _isInlineCodeNode(b)) return false;
  return _isBoldNode(a) === _isBoldNode(b)
    && _isItalicNode(a) === _isItalicNode(b)
    && _isStrikeNode(a) === _isStrikeNode(b);
}

function _mergeInlineNodes(nodes) {
  const merged = [];
  for (const node of nodes || []) {
    if (!node) continue;
    const prev = merged[merged.length - 1];
    if (node.type === 'text' && prev?.type === 'text') {
      merged[merged.length - 1] = { type: 'text', text: `${prev.text}${node.text || ''}` };
    } else if (_areMergeableFormattingElements(prev, node)) {
      merged[merged.length - 1] = {
        ...prev,
        children: _mergeInlineNodes([...(prev.children || []), ...(node.children || [])]),
      };
    } else {
      const copy = node.type === 'element' && node.children
        ? { ...node, children: _mergeInlineNodes(node.children) }
        : node;
      merged.push(copy);
    }
  }
  return merged;
}

function _mergeTextNodes(nodes) {
  return _mergeInlineNodes(nodes);
}

function _nodePlainText(node) {
  if (!node) return '';
  if (node.type === 'text') return String(node.text || '').replace(/​/g, '');
  if (node.tag === 'br') return '\n';
  return (node.children || []).map(_nodePlainText).join('');
}

function _isBoldNode(node) {
  if (!node || node.type !== 'element') return false;
  if (node.tag === 'strong' || node.tag === 'b') return true;
  if (node.style?.fontWeight) {
    const w = String(node.style.fontWeight).toLowerCase();
    return w === 'bold' || w === 'bolder' || Number(w) >= 600;
  }
  return false;
}

function _isItalicNode(node) {
  if (!node || node.type !== 'element') return false;
  if (node.tag === 'em' || node.tag === 'i') return true;
  if (node.style?.fontStyle) {
    return String(node.style.fontStyle).toLowerCase() === 'italic';
  }
  return false;
}

function _isStrikeNode(node) {
  if (!node || node.type !== 'element') return false;
  if (node.tag === 'del' || node.tag === 's' || node.tag === 'strike') return true;
  if (node.style?.textDecorationLine || node.style?.textDecoration) {
    const deco = String(node.style.textDecorationLine || node.style.textDecoration).toLowerCase();
    return deco.includes('line-through');
  }
  return false;
}

function _isInlineCodeNode(node) {
  if (!node || node.type !== 'element') return false;
  if (node.tag === 'code') return true;
  if (node.tag === 'span' && String(node.style?.fontFamily || '').includes('OdysseusInlineCode')) return true;
  return false;
}

function _serializeInlineNodes(nodes, escapeAll = false) {
  const list = _mergeTextNodes(nodes);
  if (!list.length) return '';
  let out = '';
  for (let i = 0; i < list.length; i++) {
    const node = list[i];
    if (node.type === 'text') {
      const text = _normalizeVisualText(node.text);
      out += escapeAll || _textNeedsEscaping(text) ? _escapeInlineText(text) : text;
      continue;
    }
    if (node.type !== 'element') continue;
    const tag = node.tag;
    if (tag === 'br') {
      // Ignore single trailing placeholder <br> at the end of a block
      if (i === list.length - 1) continue;
      out += '\n';
      continue;
    }
    if (_isInlineCodeNode(node)) {
      out += _formatInlineCode(_nodePlainText(node));
      continue;
    }
    if (tag === 'img') {
      const safeSrc = _normalizeVisualImageUrl(node.attrs?.src || '');
      if (safeSrc) {
        const alt = String(node.attrs?.alt || '').replace(/[[\]\n\r]/g, ' ').trim();
        out += `![${alt}](${_escapeLinkUrl(safeSrc)})`;
      }
      continue;
    }
    if (tag === 'a') {
      const inner = _serializeInlineNodes(node.children || [], escapeAll);
      const safeHref = _normalizeRichLinkUrl(node.attrs?.href || '');
      if (!safeHref) {
        out += inner;
      } else {
        const label = inner || safeHref;
        out += `[${_escapeLinkLabel(label)}](${_escapeLinkUrl(safeHref)})`;
      }
      continue;
    }
    const inner = _serializeInlineNodes(node.children || [], escapeAll);
    if (!inner) continue;
    const isBold = _isBoldNode(node);
    const isItalic = _isItalicNode(node);
    const isStrike = _isStrikeNode(node);
    let formatted = inner;
    if (isBold && isItalic) formatted = _wrapInlineMarker(formatted, '***');
    else if (isBold) formatted = _wrapInlineMarker(formatted, '**');
    else if (isItalic) formatted = _wrapInlineMarker(formatted, '*');
    if (isStrike) formatted = _wrapInlineMarker(formatted, '~~');
    out += formatted;
  }
  return out;
}

function _expectedInlinePlainText(nodes) {
  const list = _mergeTextNodes(nodes);
  let out = '';
  list.forEach((node, idx) => {
    if (node.type === 'element' && node.tag === 'br' && idx === list.length - 1) return;
    if (node.type === 'element' && node.tag === 'img') return;
    out += _nodePlainText(node);
  });
  return _normalizeVisualText(out);
}

function _comparablePlainLines(text) {
  return String(text).split('\n').map(line => line.trim()).join('\n');
}

function _extractNodeRuns(nodes, current = { b: false, i: false, s: false, c: false, a: '' }) {
  const runs = [];
  for (const n of nodes || []) {
    if (!n) continue;
    if (n.type === 'text') {
      const text = _normalizeVisualText(n.text);
      if (text) runs.push({ text, ...current });
      continue;
    }
    if (n.type !== 'element') continue;
    if (n.tag === 'br') {
      runs.push({ text: '\n', ...current });
      continue;
    }
    if (n.tag === 'img') {
      runs.push({ text: '', img: n.attrs?.src || '', ...current });
      continue;
    }
    const c = _isInlineCodeNode(n) || current.c;
    const b = current.b || _isBoldNode(n);
    const i = current.i || _isItalicNode(n);
    const s = current.s || _isStrikeNode(n);
    const a = n.tag === 'a' ? (n.attrs?.href || '') : current.a;
    runs.push(..._extractNodeRuns(n.children || [], { b, i, s, c, a }));
  }
  return runs;
}

function _extractHtmlRuns(html) {
  const stack = [];
  const runs = [];
  const tagRe = /<(\/?)([a-z0-9]+)((?:\s+[a-z-]+="[^"]*")*)\s*\/?>/g;
  let last = 0;
  let match;
  while ((match = tagRe.exec(html))) {
    const text = html.slice(last, match.index);
    if (text) {
      const b = stack.some(s => s.tag === 'strong' || s.tag === 'b');
      const i = stack.some(s => s.tag === 'em' || s.tag === 'i');
      const s = stack.some(s => s.tag === 'del' || s.tag === 's');
      const c = stack.some(s => s.tag === 'code');
      const aNode = stack.find(s => s.tag === 'a');
      const a = aNode ? aNode.href : '';
      runs.push({ text: _htmlToPlainText(text), b, i, s, c, a });
    }
    last = tagRe.lastIndex;
    const [, closing, tag, rawAttrs] = match;
    if (closing) {
      if (!stack.length || stack.pop().tag !== tag) return null;
      continue;
    }
    let href = '';
    if (tag === 'a') {
      const m = rawAttrs.match(/href="([^"]*)"/);
      if (m) href = m[1];
    }
    if (tag === 'img') {
      const m = rawAttrs.match(/src="([^"]*)"/);
      const b = stack.some(s => s.tag === 'strong' || s.tag === 'b');
      const i = stack.some(s => s.tag === 'em' || s.tag === 'i');
      const s = stack.some(s => s.tag === 'del' || s.tag === 's');
      const c = stack.some(s => s.tag === 'code');
      runs.push({ text: '', img: m ? m[1] : '', b, i, s, c, a: '' });
      continue;
    }
    if (tag !== 'br') {
      stack.push({ tag, href });
    }
  }
  const trailing = html.slice(last);
  if (trailing) {
    const b = stack.some(s => s.tag === 'strong' || s.tag === 'b');
    const i = stack.some(s => s.tag === 'em' || s.tag === 'i');
    const s = stack.some(s => s.tag === 'del' || s.tag === 's');
    const c = stack.some(s => s.tag === 'code');
    const aNode = stack.find(s => s.tag === 'a');
    const a = aNode ? aNode.href : '';
    runs.push({ text: _htmlToPlainText(trailing), b, i, s, c, a });
  }
  if (stack.length > 0) return null;
  return runs;
}

function _coalesceRuns(runs) {
  if (!runs || !runs.length) return [];
  const out = [];
  for (const run of runs) {
    // The text comparison checks spaces separately. Emphasis markers move
    // adjacent spaces outside delimiters, but code and link spaces matter.
    const r = run.c || run.a || run.img ? run : { ...run, text: run.text.replace(/[ \t]/g, '') };
    if (!r.text && !r.img) continue;
    const prev = out[out.length - 1];
    if (prev
        && prev.b === r.b
        && prev.i === r.i
        && prev.s === r.s
        && prev.c === r.c
        && prev.a === r.a
        && !prev.img && !r.img) {
      prev.text += r.text;
    } else {
      out.push({ ...r });
    }
  }
  return out.filter(r => r.text || r.img);
}

function _inlineFormattingMatches(expectedNodes, renderedHtml) {
  const renderedRuns = _extractHtmlRuns(renderedHtml);
  if (!renderedRuns) return false;
  const exp = _coalesceRuns(_extractNodeRuns(expectedNodes));
  const act = _coalesceRuns(renderedRuns);
  if (exp.length !== act.length) return false;
  for (let k = 0; k < exp.length; k++) {
    const e = exp[k];
    const a = act[k];
    if (e.text !== a.text) return false;
    if (Boolean(e.b) !== Boolean(a.b)) return false;
    if (Boolean(e.i) !== Boolean(a.i)) return false;
    if (Boolean(e.s) !== Boolean(a.s)) return false;
    if (Boolean(e.c) !== Boolean(a.c)) return false;
    if ((e.a || '') !== (a.a || '')) return false;
    if ((e.img || '') !== (a.img || '')) return false;
  }
  return true;
}

/**
 * Serialize the inline content of one block. Minimal escaping is verified by
 * re-rendering; when marker placement would change the visible text or corrupt
 * formatting structure, every text run in the block is escaped instead.
 */
function _serializeInlineBlock(nodes, context = null) {
  const minimal = _serializeInlineNodes(nodes, false);
  const renderedLines = minimal.split('\n').map(line => _renderInlineMarkdown(line));
  const rendered = renderedLines.map(_htmlToPlainText).join('\n');
  if (_comparablePlainLines(rendered) === _comparablePlainLines(_expectedInlinePlainText(nodes))
      && _inlineFormattingMatches(nodes, renderedLines.join('\n'))) {
    return minimal;
  }

  const escaped = _serializeInlineNodes(nodes, true);
  const escapedRenderedLines = escaped.split('\n').map(line => _renderInlineMarkdown(line));
  const escapedRendered = escapedRenderedLines.map(_htmlToPlainText).join('\n');
  if (_comparablePlainLines(escapedRendered) === _comparablePlainLines(_expectedInlinePlainText(nodes))
      && _inlineFormattingMatches(nodes, escapedRenderedLines.join('\n'))) {
    return escaped;
  }

  if (context) {
    context.lossless = false;
    const reason = 'formatting the visual editor cannot represent in Markdown';
    if (!context.reasons.includes(reason)) context.reasons.push(reason);
  }
  return escaped;
}

/**
 * Escape a leading character that would otherwise start a Markdown block.
 * Leading whitespace is dropped: it is not rendered and could form indented code.
 */
function _escapeBlockStart(line, { bulletItem = false } = {}) {
  const s = String(line).replace(/^[ \t]+/, '');
  if (!s) return s;
  if (/^#{1,6}(?:[ \t]|$)/.test(s) || /^>/.test(s) || /^[-+*](?:[ \t]|$)/.test(s)) return `\\${s}`;
  const ordered = s.match(/^(\d{1,9})([.)])(?:[ \t]|$)/);
  if (ordered) return `${ordered[1]}\\${s.slice(ordered[1].length)}`;
  if (/^(?:`{3,}|~{3,})/.test(s)) return `\\${s}`;
  if (/^([-*_=])(?:[ \t]*\1)*[ \t]*$/.test(s) || _isTableSeparatorLine(s)) return `\\${s}`;
  if (REF_DEF_RE.test(s) || FOOTNOTE_DEF_RE.test(s)) {
    return s.replace(/(\\*)\]/g, (match, slashes) => (slashes.length % 2 === 0 ? `${slashes}\\]` : match));
  }
  if (bulletItem && /^\[[ xX]\](?:[ \t]|$)/.test(s)) {
    return s.replace(/(\\*)\]/g, (match, slashes) => (slashes.length % 2 === 0 ? `${slashes}\\]` : match));
  }
  return s;
}

function _isEmptyParagraphPlaceholder(nodes) {
  if (nodes.length !== 1 || nodes[0]?.type !== 'element') return false;
  const { tag, children = [] } = nodes[0];
  if (tag === 'br') return children.length === 0;
  // Native Enter can carry active inline marks into the new, empty paragraph.
  return ['b', 'strong', 'i', 'em', 's', 'strike', 'del'].includes(tag)
    && _isEmptyParagraphPlaceholder(children);
}

function _serializeParagraphLines(nodes, context = null) {
  if (_isEmptyParagraphPlaceholder(nodes)) return '';
  return _serializeInlineBlock(nodes, context)
    .split('\n')
    .map(line => _escapeBlockStart(line).replace(/[ \t]+$/, ''))
    .join('\n')
    .trim();
}

function _isBlockElement(node) {
  return node?.type === 'element' && [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'ul', 'ol',
    'blockquote', 'pre', 'hr', 'table', 'figure',
  ].includes(node.tag);
}

function _listStart(listNode) {
  const start = Number.parseInt(listNode.attrs?.start ?? '', 10);
  return Number.isInteger(start) && start >= 0 ? start : 1;
}

function _reportVisualStructureLoss(context, reason) {
  if (!context) return;
  context.lossless = false;
  if (!context.reasons.includes(reason)) context.reasons.push(reason);
}

function _serializeListNode(listNode, indent = '', context = null) {
  const isChecklist = listNode.tag === 'ul' && (listNode.classes || []).includes('rich-checklist');
  const isOrdered = listNode.tag === 'ol';
  const lines = [];
  let number = isOrdered ? _listStart(listNode) : 1;
  let childIndent = `${indent}  `;

  for (const child of (listNode.children || [])) {
    if (!child || child.type !== 'element') continue;
    if (child.tag === 'ul' || child.tag === 'ol') {
      const nested = _serializeListNode(child, childIndent, context);
      if (nested) lines.push(nested);
      continue;
    }
    if (child.tag !== 'li') continue;

    const inlineParts = [];
    const nestedLists = [];
    for (const liChild of (child.children || [])) {
      if (liChild?.type === 'element' && (liChild.tag === 'ul' || liChild.tag === 'ol')) {
        nestedLists.push(liChild);
      } else {
        inlineParts.push(liChild);
      }
    }
    if (inlineParts.some(_isBlockElement)) _reportVisualStructureLoss(context, 'multi-paragraph list content');
    const rawText = _serializeInlineBlock(inlineParts, context).trim();
    const text = _escapeBlockStart(rawText, { bulletItem: !isChecklist && !isOrdered });
    let prefix;
    if (isChecklist) {
      prefix = child.attrs?.['data-checked'] === 'true' ? '- [x] ' : '- [ ] ';
    } else if (isOrdered) {
      prefix = `${number++}. `;
    } else {
      prefix = '- ';
    }
    // Nested content must start at the parent's content offset (CommonMark).
    childIndent = indent + ' '.repeat(isChecklist ? 2 : prefix.length);
    const continued = text.split('\n').join(`  \n${childIndent}`);
    lines.push(text ? `${indent}${prefix}${continued}` : `${indent}${prefix.trimEnd()}`);
    for (const nested of nestedLists) {
      const nestedText = _serializeListNode(nested, childIndent, context);
      if (nestedText) lines.push(nestedText);
    }
  }

  return lines.join('\n');
}

function _escapeTableCellPipes(text) {
  const s = String(text || '').replace(/\n+/g, ' ');
  let out = '';
  let i = 0;
  while (i < s.length) {
    if (s[i] === '\\' && i + 1 < s.length) {
      out += s.slice(i, i + 2);
      i += 2;
      continue;
    }
    if (s[i] === '`') {
      let tickLen = 0;
      while (i + tickLen < s.length && s[i + tickLen] === '`') tickLen++;
      const closer = '`'.repeat(tickLen);
      const end = s.indexOf(closer, i + tickLen);
      if (end !== -1 && (end + tickLen >= s.length || s[end + tickLen] !== '`')) {
        out += s.slice(i, end + tickLen);
        i = end + tickLen;
        continue;
      }
    }
    if (s[i] === '|') {
      out += '\\|';
      i++;
      continue;
    }
    out += s[i];
    i++;
  }
  return out.trim();
}

function _serializeTableNode(tableNode, context = null) {
  const rows = [];
  const collectRows = (node) => {
    for (const child of (node.children || [])) {
      if (!child || child.type !== 'element') continue;
      if (child.tag === 'tr') rows.push(child);
      else if (['thead', 'tbody', 'tfoot'].includes(child.tag)) collectRows(child);
    }
  };
  collectRows(tableNode);
  if (!rows.length) return '';

  const parsedRows = rows.map(tr =>
    (tr.children || [])
      .filter(c => c?.type === 'element' && (c.tag === 'th' || c.tag === 'td'))
      .map(cell => {
        const children = cell.children || [];
        // A single break is the contenteditable placeholder in an empty cell.
        // Multiple breaks or text around a break are actual cell content.
        const placeholder = children.length === 1 && children[0]?.tag === 'br';
        const text = placeholder ? '' : _serializeInlineBlock(children, context);
        if (text.includes('\n') || children.some(_isBlockElement)) {
          _reportVisualStructureLoss(context, 'line breaks or paragraphs in table cells');
        }
        if (Number(cell.attrs?.colspan || 1) > 1 || Number(cell.attrs?.rowspan || 1) > 1) {
          _reportVisualStructureLoss(context, 'merged table cells');
        }
        return {
          align: String(cell.style?.textAlign || '').toLowerCase(),
          text: _escapeTableCellPipes(text),
        };
      })
  ).filter(r => r.length > 0);

  if (!parsedRows.length) return '';
  const colCount = Math.max(...parsedRows.map(r => r.length));
  const header = parsedRows[0];
  const padRow = (row) => Array.from({ length: colCount }, (_, idx) => row[idx]?.text || '');

  const sepCells = Array.from({ length: colCount }, (_, idx) => {
    const align = header[idx]?.align || '';
    if (align === 'center') return ':---:';
    if (align === 'right') return '---:';
    if (align === 'left') return ':---';
    return '---';
  });

  const mdLines = [
    `| ${padRow(header).join(' | ')} |`,
    `| ${sepCells.join(' | ')} |`,
    ...parsedRows.slice(1).map(row => `| ${padRow(row).join(' | ')} |`),
  ];
  return mdLines.join('\n');
}

function _serializeBlockNode(node, context = null) {
  if (!node || node.type !== 'element') return '';
  const tag = node.tag;

  if (/^h[1-6]$/.test(tag)) {
    const level = Number(tag.slice(1));
    const inline = _serializeInlineBlock(node.children || [], context);
    if (inline.includes('\n')) _reportVisualStructureLoss(context, 'line breaks in headings');
    let text = inline.replace(/\n+/g, ' ').trim();
    // Protect trailing hashes from being read as an ATX closing sequence.
    if (/^#+$/.test(text)) text = `\\${text}`;
    else text = text.replace(/([ \t])(#+)$/, '$1\\$2');
    return text ? `${'#'.repeat(level)} ${text}` : '';
  }

  if (tag === 'hr') return '---';

  if (tag === 'pre') {
    const codeChild = (node.children || []).find(c => c?.type === 'element' && c.tag === 'code');
    const hasFormatting = child => child?.type === 'element' && (
      !['pre', 'code', 'span', 'br'].includes(child.tag)
      || _isBoldNode(child) || _isItalicNode(child) || _isStrikeNode(child)
      || (child.children || []).some(hasFormatting)
    );
    if ((node.children || []).some(hasFormatting)) _reportVisualStructureLoss(context, 'formatting inside code blocks');
    const langFromData = codeChild?.attrs?.['data-lang'] || node.attrs?.['data-lang'] || '';
    const langFromClass = ((codeChild?.classes || node.classes || []).find(c => c.startsWith('language-')) || '').slice('language-'.length);
    const lang = (langFromData || langFromClass || '').trim();
    let raw = _nodePlainText(codeChild || node).replace(/\r\n?/g, '\n');
    if ((codeChild || node).children?.length === 1 && (codeChild || node).children[0]?.tag === 'br') raw = '';
    let maxRun = 0;
    let cur = 0;
    for (let i = 0; i < raw.length; i++) {
      if (raw[i] === '`') {
        cur++;
        if (cur > maxRun) maxRun = cur;
      } else {
        cur = 0;
      }
    }
    const fence = '`'.repeat(Math.max(3, maxRun + 1));
    return `${fence}${lang}\n${raw}\n${fence}`;
  }

  if (tag === 'blockquote') {
    const alertType = String(node.attrs?.['data-alert'] || '').toUpperCase();
    const safeAlert = ['NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION'].includes(alertType) ? alertType : '';
    const children = (node.children || []).filter(child => !(child?.type === 'element'
      && child.tag === 'p'
      && (child.children || []).some(item => item?.type === 'element' && item.attrs?.['data-alert-label'])));
    const innerBlocks = serializeVisualNodesToMarkdown(children, context);
    if (safeAlert) {
      const body = innerBlocks ? `\n${innerBlocks}` : '';
      return `> [!${safeAlert}]${body.split('\n').map((line, index) => index === 0 ? line : `\n> ${line}`).join('')}`;
    }
    if (!innerBlocks) return '>';
    return innerBlocks.split('\n').map(line => (line ? `> ${line}` : '>')).join('\n');
  }

  if (tag === 'ul' || tag === 'ol') {
    return _serializeListNode(node, '', context);
  }

  if (tag === 'table') {
    return _serializeTableNode(node, context);
  }

  if (tag === 'figure') {
    if (_nodePlainText(node).trim()) _reportVisualStructureLoss(context, 'image captions');
    const img = (node.children || []).find(c => c?.type === 'element' && c.tag === 'img');
    if (!img) return '';
    return _serializeInlineNodes([img]).trim();
  }

  if (tag === 'p' || tag === 'div') {
    const children = node.children || [];
    if (children.some(_isBlockElement)) {
      return serializeVisualNodesToMarkdown(children, context);
    }
    return _serializeParagraphLines(children, context);
  }

  return _serializeInlineBlock(node.children || [], context).trim();
}

/**
 * Serialize a normalized array of visual DOM-descriptor nodes into canonical Markdown.
 * @param {Array} nodes
 * @param {object|null} [context]
 * @returns {string}
 */
/**
 * Serialize visual descriptor nodes into an ordered array of Markdown block strings.
 * Groups consecutive inline nodes into paragraph blocks and preserves block boundaries.
 * @param {Array} nodes
 * @param {object|null} [context]
 * @returns {string[]}
 */
export function serializeVisualNodesToBlocks(nodes, context = null) {
  if (!Array.isArray(nodes) || !nodes.length) return [];
  const blocks = [];
  let inlineBuffer = [];

  const flushInline = () => {
    if (!inlineBuffer.length) return;
    const text = _serializeParagraphLines(inlineBuffer, context);
    inlineBuffer = [];
    if (text) blocks.push(text);
  };

  for (const node of nodes) {
    if (!node) continue;
    if (_isBlockElement(node)) {
      flushInline();
      if ((node.tag === 'p' || node.tag === 'div') && (node.children || []).some(_isBlockElement)) {
        blocks.push(...serializeVisualNodesToBlocks(node.children, context));
        continue;
      }
      const serialized = _serializeBlockNode(node, context);
      if (serialized) blocks.push(serialized);
    } else {
      inlineBuffer.push(node);
    }
  }
  flushInline();

  return blocks;
}

/**
 * Serialize visual descriptor nodes back into a canonical Markdown document string.
 * @param {Array} nodes
 * @param {object|null} [context]
 * @returns {string}
 */
export function serializeVisualNodesToMarkdown(nodes, context = null) {
  const blocks = serializeVisualNodesToBlocks(nodes, context);
  return blocks.join('\n\n').trim();
}

/**
 * Serialize visual nodes while reporting whether all inline formatting was preserved.
 * @param {Array} nodes
 * @returns {{ markdown: string, blocks: string[], lossless: boolean, reasons: string[] }}
 */
export function serializeVisualNodesWithFidelity(nodes) {
  const context = { lossless: true, reasons: [] };
  const blocks = serializeVisualNodesToBlocks(nodes, context);
  const markdown = blocks.join('\n\n').trim();
  return {
    markdown,
    blocks,
    lossless: context.lossless,
    reasons: context.reasons,
  };
}

/**
 * Inspect visual nodes directly for constructs or formatting combinations
 * that cannot round-trip safely into Markdown.
 * @param {Array} nodes
 * @returns {{ supported: boolean, reasons: string[] }}
 */
export function inspectVisualNodesCompatibility(nodes) {
  const { markdown, lossless, reasons } = serializeVisualNodesWithFidelity(nodes);
  if (!lossless) {
    return { supported: false, reasons };
  }
  return inspectMarkdownVisualCompatibility(markdown);
}
