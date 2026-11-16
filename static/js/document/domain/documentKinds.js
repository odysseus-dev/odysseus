export const AUTO_DETECT_DELAY = 500;
export const AUTO_DETECT_MIN_CHARS = 30;
export const AUTO_DETECT_MIN_RELEVANCE = 8;
export const AUTO_DETECT_SAMPLE_SIZE = 2000;

export const HLJS_TO_DROPDOWN = {
  python: 'python', javascript: 'javascript', typescript: 'typescript',
  xml: 'html', html: 'html', css: 'css', markdown: 'markdown',
  json: 'json', yaml: 'yaml', bash: 'bash', shell: 'bash',
  sql: 'sql', rust: 'rust', go: 'go', java: 'java', c: 'c', cpp: 'cpp',
  csv: 'csv',
};

export const _DOC_SAVE_STATE = {
  dirty: { label: 'Unsaved', title: 'Changes are waiting to be saved' },
  saving: { label: 'Saving', title: 'Saving document' },
  saved: { label: 'Saved', title: 'All changes saved' },
  error: { label: 'Save failed', title: 'Document could not be saved' },
};

export function _escHtml(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function _esc(s) {
  return _escHtml(s);
}

export function _buildDiffSummary(oldText, newText) {
  if (!oldText && !newText) return '';
  const oldLines = (oldText || '').split('\n');
  const newLines = (newText || '').split('\n');
  const added = [], removed = [];
  // Simple line diff - collect changed lines
  const maxCheck = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < maxCheck; i++) {
    const ol = oldLines[i], nl = newLines[i];
    if (ol === nl) continue;
    if (ol !== undefined && (nl === undefined || ol !== nl)) removed.push(ol.trim());
    if (nl !== undefined && (ol === undefined || ol !== nl)) added.push(nl.trim());
  }
  // Show up to 3 changes
  const parts = [];
  for (const line of removed.slice(0, 2)) {
    if (line) parts.push(`<span class="diff-del">${_escHtml(line.slice(0, 60))}</span>`);
  }
  for (const line of added.slice(0, 2)) {
    if (line) parts.push(`<span class="diff-add">${_escHtml(line.slice(0, 60))}</span>`);
  }
  const extra = (added.length + removed.length) - 4;
  if (extra > 0) parts.push(`<span>+${extra} more changes</span>`);
  return parts.join('<br>');
}

export const _isRenderLang = (l) => ['html', 'svg', 'xml'].includes((l || '').toLowerCase());

export const _isRichTextLang = (l) => ['richtext', 'rich-text'].includes((l || '').toLowerCase());

export const _isDocxLang = (l) => (l || '').toLowerCase() === 'docx';

export const _hasViewToggle = (l) => {
  const lang = (l || '').toLowerCase();
  return [
    'csv', 'python', 'javascript', 'typescript', 'bash', 'sh', 'shell',
    'php', 'ruby', 'sql', 'java', 'go', 'rust',
    'c', 'cpp', 'c++', 'csharp', 'c#',
    'yaml', 'json', 'css',
    'ini', 'toml',
  ].includes(lang) || _isRenderLang(lang) || _isDocxLang(lang) || _isRichTextLang(lang);
};

export function _isMarkdownImageFile(file) {
  if (!file) return false;
  if ((file.type || '').toLowerCase().startsWith('image/')) return true;
  return /\.(avif|bmp|gif|jpe?g|png|svg|webp)$/i.test(file.name || '');
}

export function _markdownImageAlt(name) {
  const base = String(name || 'image').replace(/\.[^.]+$/, '').trim() || 'image';
  return base.replace(/[\[\]\n\r]/g, ' ').replace(/\s+/g, ' ').trim() || 'image';
}

export function _looksLikeMarkdown(text) {
  const lines = text.slice(0, 2000).split('\n');
  let score = 0;
  for (const line of lines) {
    if (/^#{1,6}\s/.test(line)) score += 3;         // headings
    else if (/^\s*[-*+]\s/.test(line)) score += 1;  // list items
    else if (/^\s*\d+\.\s/.test(line)) score += 1;  // ordered list
    else if (/^\s*>/.test(line)) score += 1;         // blockquote
    else if (/\[.+\]\(.+\)/.test(line)) score += 2; // links
    else if (/^```/.test(line)) score += 2;          // fenced code
    else if (/\*\*.+\*\*/.test(line)) score += 1;   // bold
    else if (/^---\s*$/.test(line)) score += 1;      // horizontal rule
  }
  return score >= 3;
}

function _inspectHtmlText(input) {
  if (input == null) return { text: '', hasRichElement: false };
  const source = typeof input === 'string'
    ? input
    : (typeof input === 'number' || typeof input === 'boolean' ? String(input) : '');
  if (!source) return { text: '', hasRichElement: false };

  const richTags = new Set(['img', 'video', 'audio', 'iframe', 'table']);
  const rawTextTags = new Set(['script', 'style', 'noscript']);
  const namedEntities = {
    amp: '&',
    lt: '<',
    gt: '>',
    quot: '"',
    apos: "'",
    nbsp: '\u00a0',
    ndash: '\u2013',
    mdash: '\u2014',
    hellip: '\u2026',
    lsquo: '\u2018',
    rsquo: '\u2019',
    ldquo: '\u201c',
    rdquo: '\u201d',
    copy: '\u00a9',
    reg: '\u00ae',
    trade: '\u2122',
  };

  function decodeEntityText(chunk) {
    if (!chunk || !chunk.includes('&')) return chunk;
    return chunk.replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z][a-z0-9]*);/gi, (match, body) => {
      const lower = body.toLowerCase();
      if (lower.startsWith('#x')) {
        const cp = Number.parseInt(lower.slice(2), 16);
        if (!Number.isFinite(cp) || cp <= 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
          return '\ufffd';
        }
        return String.fromCodePoint(cp);
      }
      if (lower.startsWith('#')) {
        const cp = Number.parseInt(lower.slice(1), 10);
        if (!Number.isFinite(cp) || cp <= 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
          return '\ufffd';
        }
        return String.fromCodePoint(cp);
      }
      return Object.prototype.hasOwnProperty.call(namedEntities, lower) ? namedEntities[lower] : match;
    });
  }

  function findQuotedTagEnd(startIdx, len) {
    let quote = '';
    let idx = startIdx;
    while (idx < len) {
      const ch = source[idx];
      if (quote) {
        if (ch === quote) quote = '';
      } else if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === '>') {
        return idx;
      }
      idx += 1;
    }
    return len;
  }

  const len = source.length;
  const chunks = [];
  let hasRichElement = false;
  let i = 0;

  while (i < len) {
    const nextLt = source.indexOf('<', i);
    if (nextLt === -1) {
      chunks.push(source.slice(i));
      break;
    }
    if (nextLt > i) {
      chunks.push(source.slice(i, nextLt));
      i = nextLt;
    }
    if (i + 1 >= len) {
      chunks.push('<');
      break;
    }

    const next = source[i + 1];
    if (next === '!') {
      if (source.startsWith('<!--', i)) {
        if (source.startsWith('<!-->', i)) { i += 5; continue; }
        if (source.startsWith('<!--->', i)) { i += 6; continue; }
        const endComment = source.indexOf('-->', i + 4);
        i = endComment === -1 ? len : endComment + 3;
        continue;
      }
      const endDecl = source.indexOf('>', i + 2);
      i = endDecl === -1 ? len : endDecl + 1;
      continue;
    }

    if (next === '?') {
      const endPi = source.indexOf('>', i + 2);
      i = endPi === -1 ? len : endPi + 1;
      continue;
    }

    if (next === '/') {
      if (i + 2 < len && source[i + 2] === '>') {
        i += 3;
        continue;
      }
      if (i + 2 < len && /[A-Za-z]/.test(source[i + 2])) {
        const endTag = findQuotedTagEnd(i + 2, len);
        i = endTag >= len ? len : endTag + 1;
        continue;
      }
      chunks.push('<');
      i += 1;
      continue;
    }

    if (!/[A-Za-z]/.test(next)) {
      chunks.push('<');
      i += 1;
      continue;
    }

    let nameEnd = i + 2;
    while (nameEnd < len && /[A-Za-z0-9:-]/.test(source[nameEnd])) {
      nameEnd += 1;
    }
    const tagName = source.slice(i + 1, nameEnd).toLowerCase();
    const endTag = findQuotedTagEnd(nameEnd, len);
    if (endTag >= len) {
      i = len;
      break;
    }

    if (richTags.has(tagName)) {
      hasRichElement = true;
    }

    if (rawTextTags.has(tagName)) {
      const closeLen = 2 + tagName.length;
      let searchPos = endTag + 1;
      let closed = false;
      while (searchPos + closeLen <= len) {
        const closeIdx = source.indexOf('</', searchPos);
        if (closeIdx === -1 || closeIdx + closeLen > len) break;
        if (source.slice(closeIdx + 2, closeIdx + closeLen).toLowerCase() === tagName) {
          const afterPos = closeIdx + closeLen;
          if (afterPos === len) {
            i = len;
            closed = true;
            break;
          }
          const afterCh = source[afterPos];
          if (
            afterCh === '>'
            || afterCh === '/'
            || afterCh === ' '
            || afterCh === '\t'
            || afterCh === '\n'
            || afterCh === '\r'
            || afterCh === '\f'
          ) {
            const closeEnd = source.indexOf('>', afterPos);
            i = closeEnd === -1 ? len : closeEnd + 1;
            closed = true;
            break;
          }
        }
        searchPos = closeIdx + 2;
      }
      if (!closed) i = len;
      continue;
    }

    i = endTag + 1;
  }

  return {
    text: decodeEntityText(chunks.join('')),
    hasRichElement,
  };
}

export function _htmlToPlainText(input) {
  return _inspectHtmlText(input).text;
}

export function _hasRichHtmlElement(input) {
  return _inspectHtmlText(input).hasRichElement;
}
