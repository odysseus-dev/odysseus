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
  blocked: { label: 'Save blocked', title: 'Resolve the visual draft to resume saving' },
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

export const HTML_TAGS = new Set([
  'a', 'abbr', 'address', 'area', 'article', 'aside', 'audio', 'b', 'base', 'bdi',
  'bdo', 'blockquote', 'body', 'br', 'button', 'canvas', 'caption', 'cite', 'code',
  'col', 'colgroup', 'data', 'datalist', 'dd', 'del', 'details', 'dfn', 'dialog',
  'div', 'dl', 'dt', 'em', 'embed', 'fieldset', 'figcaption', 'figure', 'footer',
  'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'header', 'hgroup', 'hr',
  'html', 'i', 'iframe', 'img', 'input', 'ins', 'kbd', 'label', 'legend', 'li',
  'link', 'main', 'map', 'mark', 'menu', 'meta', 'meter', 'nav', 'noscript',
  'object', 'ol', 'optgroup', 'option', 'output', 'p', 'picture', 'pre', 'progress',
  'q', 'rp', 'rt', 'ruby', 's', 'samp', 'script', 'section', 'select', 'slot',
  'small', 'source', 'span', 'strong', 'style', 'sub', 'summary', 'sup', 'table',
  'tbody', 'td', 'template', 'textarea', 'tfoot', 'th', 'thead', 'time', 'title',
  'tr', 'track', 'u', 'ul', 'var', 'video', 'wbr',
  'font', 'center', 'strike', 'big', 'tt'
]);

export function _looksLikeHtml(text) {
  if (!text || typeof text !== 'string' || !text.includes('<')) return false;
  if (/<!(?:doctype|--)/i.test(text)) return true;
  const tagMatch = text.match(/<\/?([a-zA-Z][a-zA-Z0-9:-]*)\b([^>]*)>/);
  if (!tagMatch) return false;
  const tagName = tagMatch[1].toLowerCase();
  return HTML_TAGS.has(tagName);
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
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0',
    ndash: '\u2013', mdash: '\u2014', hellip: '\u2026', lsquo: '\u2018', rsquo: '\u2019',
    sbquo: '\u201a', ldquo: '\u201c', rdquo: '\u201d', bdquo: '\u201e', dagger: '\u2020',
    Dagger: '\u2021', permil: '\u2030', lsaquo: '\u2039', rsaquo: '\u203a', bull: '\u2022',
    prime: '\u2032', Prime: '\u2033', oline: '\u203e', frasl: '\u2044',
    euro: '\u20ac', cent: '\u00a2', pound: '\u00a3', curren: '\u00a4', yen: '\u00a5',
    copy: '\u00a9', reg: '\u00ae', trade: '\u2122', sect: '\u00a7', deg: '\u00b0',
    plusmn: '\u00b1', sup1: '\u00b9', sup2: '\u00b2', sup3: '\u00b3', acute: '\u00b4',
    micro: '\u00b5', para: '\u00b6', middot: '\u00b7', cedil: '\u00b8', ordm: '\u00ba',
    ordf: '\u00aa', laquo: '\u00ab', raquo: '\u00bb', not: '\u00ac', shy: '\u00ad',
    macr: '\u00af', brvbar: '\u00a6', uml: '\u00a8', iquest: '\u00bf', iexcl: '\u00a1',
    times: '\u00d7', divide: '\u00f7', frac14: '\u00bc', frac12: '\u00bd', frac34: '\u00be',
    Agrave: '\u00c0', Aacute: '\u00c1', Acirc: '\u00c2', Atilde: '\u00c3', Auml: '\u00c4',
    Aring: '\u00c5', AElig: '\u00c6', Ccedil: '\u00c7', Egrave: '\u00c8', Eacute: '\u00c9',
    Ecirc: '\u00ca', Euml: '\u00cb', Igrave: '\u00cc', Iacute: '\u00cd', Icirc: '\u00ce',
    Iuml: '\u00cf', ETH: '\u00d0', Ntilde: '\u00d1', Ograve: '\u00d2', Oacute: '\u00d3',
    Ocirc: '\u00d4', Otilde: '\u00d5', Ouml: '\u00d6', Oslash: '\u00d8', Ugrave: '\u00d9',
    Uacute: '\u00da', Ucirc: '\u00db', Uuml: '\u00dc', Yacute: '\u00dd', THORN: '\u00de',
    szlig: '\u00df', agrave: '\u00e0', aacute: '\u00e1', acirc: '\u00e2', atilde: '\u00e3',
    auml: '\u00e4', aring: '\u00e5', aelig: '\u00e6', ccedil: '\u00e7', egrave: '\u00e8',
    eacute: '\u00e9', ecirc: '\u00ea', euml: '\u00eb', igrave: '\u00ec', iacute: '\u00ed',
    icirc: '\u00ee', iuml: '\u00ef', eth: '\u00f0', ntilde: '\u00f1', ograve: '\u00f2',
    oacute: '\u00f3', ocirc: '\u00f4', otilde: '\u00f5', ouml: '\u00f6', oslash: '\u00f8',
    ugrave: '\u00f9', uacute: '\u00fa', ucirc: '\u00fb', uuml: '\u00fc', yacute: '\u00fd',
    thorn: '\u00fe', yuml: '\u00ff',
    larr: '\u2190', uarr: '\u2191', rarr: '\u2192', darr: '\u2193', harr: '\u2194',
    crarr: '\u21b5', lArr: '\u21d0', uArr: '\u21d1', rArr: '\u21d2', dArr: '\u21d3',
    hArr: '\u21d4', forall: '\u2200', part: '\u2202', exist: '\u2203', empty: '\u2205',
    nabla: '\u2207', isin: '\u2208', notin: '\u2209', ni: '\u220b', prod: '\u220f',
    sum: '\u2211', minus: '\u2212', lowast: '\u2217', radic: '\u221a', prop: '\u221d',
    infin: '\u221e', ang: '\u2220', and: '\u2227', or: '\u2228', cap: '\u2229',
    cup: '\u222a', int: '\u222b', there4: '\u2234', sim: '\u223c', cong: '\u2245',
    asymp: '\u2248', ne: '\u2260', equiv: '\u2261', le: '\u2264', ge: '\u2265',
    sub: '\u2282', sup: '\u2283', nsub: '\u2284', sube: '\u2286', supe: '\u2287',
    oplus: '\u2295', otimes: '\u2297', perp: '\u22a5', sdot: '\u22c5',
  };

  let _domParser = null;
  function decodeNativeEntity(reference) {
    if (typeof DOMParser !== 'undefined') {
      try {
        if (!_domParser) _domParser = new DOMParser();
        // Only the matched entity reference reaches the parser, never markup.
        const doc = _domParser.parseFromString(reference, 'text/html');
        const val = doc.body.textContent;
        if (val && val !== reference) return val;
      } catch (_) {}
    }
    return null;
  }

  function decodeEntityText(chunk) {
    if (!chunk || !chunk.includes('&')) return chunk;
    return chunk.replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z][a-z0-9]*)(;?)/gi, (match, body, semicolon) => {
      if (semicolon && Object.prototype.hasOwnProperty.call(namedEntities, body)) {
        return namedEntities[body];
      }
      const native = decodeNativeEntity(match);
      if (native !== null) return native;
      if (body.startsWith('#x') || body.startsWith('#X')) {
        const cp = Number.parseInt(body.slice(2), 16);
        if (!Number.isFinite(cp) || cp <= 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
          return '\ufffd';
        }
        return String.fromCodePoint(cp);
      }
      if (body.startsWith('#')) {
        const cp = Number.parseInt(body.slice(1), 10);
        if (!Number.isFinite(cp) || cp <= 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
          return '\ufffd';
        }
        return String.fromCodePoint(cp);
      }
      return match;
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

  function pushBreak(newlines = 1) {
    if (chunks.length === 0) return;
    let trailing = 0;
    for (let k = chunks.length - 1; k >= 0; k--) {
      const c = chunks[k];
      for (let j = c.length - 1; j >= 0; j--) {
        if (c[j] === '\n') trailing += 1;
        else if (c[j] === ' ' || c[j] === '\t' || c[j] === '\r') continue;
        else { k = -1; break; }
      }
    }
    const needed = Math.min(2, newlines) - trailing;
    if (needed === 1) chunks.push('\n');
    else if (needed >= 2) chunks.push('\n\n');
  }

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
        if (endComment === -1) {
          chunks.push('<');
          i += 1;
          continue;
        }
        i = endComment + 3;
        continue;
      }
      const endDecl = source.indexOf('>', i + 2);
      if (endDecl === -1) {
        chunks.push('<');
        i += 1;
        continue;
      }
      i = endDecl + 1;
      continue;
    }

    if (next === '?') {
      const endPi = source.indexOf('>', i + 2);
      if (endPi === -1) {
        chunks.push('<');
        i += 1;
        continue;
      }
      i = endPi + 1;
      continue;
    }

    if (next === '/') {
      if (i + 2 < len && source[i + 2] === '>') {
        i += 3;
        continue;
      }
      if (i + 2 < len && /[A-Za-z]/.test(source[i + 2])) {
        let closeNameEnd = i + 2;
        while (closeNameEnd < len && /[A-Za-z0-9:-]/.test(source[closeNameEnd])) {
          closeNameEnd += 1;
        }
        const closeTagName = source.slice(i + 2, closeNameEnd).toLowerCase();
        const endTag = findQuotedTagEnd(closeNameEnd, len);
        if (endTag >= len) {
          chunks.push('<');
          i += 1;
          continue;
        }
        if (closeTagName === 'p' || closeTagName === 'blockquote' || /^h[1-6]$/.test(closeTagName)) {
          pushBreak(2);
        } else if (closeTagName === 'div' || closeTagName === 'li' || closeTagName === 'tr') {
          pushBreak(1);
        }
        i = endTag + 1;
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
      chunks.push('<');
      i += 1;
      continue;
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

    if (tagName === 'br') {
      chunks.push('\n');
    } else if (tagName === 'p' || tagName === 'blockquote' || tagName === 'hr' || /^h[1-6]$/.test(tagName)) {
      pushBreak(2);
    } else if (tagName === 'div' || tagName === 'li' || tagName === 'tr') {
      pushBreak(1);
    } else if (tagName === 'td' || tagName === 'th') {
      if (chunks.length > 0 && !/\s$/.test(chunks[chunks.length - 1])) {
        chunks.push(' ');
      }
    }

    i = endTag + 1;
  }

  return {
    text: decodeEntityText(chunks.join('')).replace(/^\n+|\n+$/g, ''),
    hasRichElement,
  };
}

export function _htmlToPlainText(input) {
  return _inspectHtmlText(input).text;
}

export function _hasRichHtmlElement(input) {
  return _inspectHtmlText(input).hasRichElement;
}

/**
 * Options every Markdown document surface passes to the shared renderer. Documents
 * keep `:shortcode:` text literal and parse CommonMark (backslash escapes, fences and lists), the same
 * escapes the Visual editor writes when it serializes literal Markdown characters.
 * Chat does not pass these options and keeps its historical rendering.
 */
export const MARKDOWN_DOCUMENT_RENDER_OPTIONS = Object.freeze({ shortcodes: false, commonmark: true });
