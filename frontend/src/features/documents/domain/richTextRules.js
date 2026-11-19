export const _RICH_SLASH_COMMANDS = [
  { action: 'paragraph', label: 'Text', icon: 'P', keywords: 'paragraph normal body' },
  { action: 'h1', label: 'Heading 1', icon: 'H1', keywords: 'title large' },
  { action: 'h2', label: 'Heading 2', icon: 'H2', keywords: 'subtitle medium' },
  { action: 'h3', label: 'Heading 3', icon: 'H3', keywords: 'section small' },
  { action: 'h4', label: 'Heading 4', icon: 'H4', keywords: 'subsection small' },
  { action: 'h5', label: 'Heading 5', icon: 'H5', keywords: 'subsection minor' },
  { action: 'h6', label: 'Heading 6', icon: 'H6', keywords: 'subsection minor' },
  { action: 'ul', label: 'Bullet list', icon: '•', keywords: 'unordered bullets' },
  { action: 'ol', label: 'Numbered list', icon: '1.', keywords: 'ordered numbers' },
  { action: 'check', label: 'Checklist', icon: '✓', keywords: 'todo task checkbox' },
  { action: 'quote', label: 'Quote', icon: '“', keywords: 'blockquote citation' },
  { action: 'codeblock', label: 'Code block', icon: '</>', keywords: 'preformatted snippet' },
  { action: 'hr', label: 'Divider', icon: '—', keywords: 'rule separator line' },
  { action: 'pagebreak', label: 'Page break', icon: '↵', keywords: 'new page print break export' },
  { action: 'table:insert:3:3', label: 'Table', icon: '▦', keywords: 'grid rows columns' },
  { action: 'image', label: 'Image', icon: '▧', keywords: 'photo picture upload' },
];

export const _RICH_BLOCK_INPUT_RULES = new Map([
  ['#', { action: 'h1' }],
  ['##', { action: 'h2' }],
  ['###', { action: 'h3' }],
  ['####', { action: 'h4' }],
  ['#####', { action: 'h5' }],
  ['######', { action: 'h6' }],
  ['-', { action: 'ul' }],
  ['*', { action: 'ul' }],
  ['1.', { action: 'ol' }],
  ['>', { action: 'quote' }],
  ['```', { action: 'codeblock' }],
  ['[]', { action: 'check' }],
  ['- []', { action: 'check' }],
  ['- [ ]', { action: 'check' }],
  ['- [x]', { action: 'check', checked: true }],
  ['- [X]', { action: 'check', checked: true }],
]);

export function _isRichInlineCodeMarker(element) {
  return element?.tagName === 'SPAN'
    && String(element.style?.fontFamily || '').includes('OdysseusInlineCode');
}

export function _normalizeRichLinkUrl(rawUrl) {
  let url = String(rawUrl || '').trim();
  if (!url || /[\u0000-\u001f\u007f-\u009f]/.test(url)) return '';
  if (/^\/{2,}(?:$|[\s/?#\\])/.test(url) || /^\/\\/.test(url)) return '';
  if (/^(?:#|\/|\.\/|\.\.\/)/.test(url)) return url;
  if (url.startsWith('//')) url = `https:${url}`;
  if (/^(?:javascript|vbscript|data|file):/i.test(url.replace(/\s+/g, ''))) return '';
  if (!/^[a-z][a-z0-9+.-]*:/i.test(url)) url = `https://${url}`;
  if (!/^(?:https?:|mailto:|tel:)/i.test(url)) return '';
  if (/^https?:/i.test(url)) {
    try {
      const parsed = new URL(url);
      if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || !parsed.hostname) return '';
    } catch (_) {
      return '';
    }
  } else if (/^mailto:/i.test(url)) {
    const recipient = url.slice('mailto:'.length).split('?')[0].trim();
    if (!recipient) return '';
    const addrs = recipient.split(',').map(s => s.trim()).filter(Boolean);
    if (addrs.length === 0 || !addrs.every(addr => /^[^\s@,]+@[^\s@,]+$/.test(addr))) return '';
  } else if (/^tel:/i.test(url)) {
    const phone = url.slice('tel:'.length).trim();
    if (!phone) return '';
    const parts = phone.split(';');
    const number = parts[0].trim();
    if (!number || !/^[+\d().\-\s]+$/.test(number) || !/\d/.test(number)) return '';
    for (let k = 1; k < parts.length; k++) {
      const param = parts[k].trim();
      if (!param || !/^[a-zA-Z0-9\-]+(?:=[a-zA-Z0-9\-+.]*)?$/.test(param)) return '';
    }
  }
  return url;
}

export const _RICH_FONT_SIZE_PX = Object.freeze({
  1: 10,
  2: 13,
  3: 16,
  4: 18,
  5: 24,
  6: 32,
  7: 48,
});

export const _RICH_COLOR_PALETTE = Object.freeze({
  dark: Object.freeze([
    ['#111827', 'Ink'],
    ['#7f1d1d', 'Crimson'],
    ['#7c2d12', 'Copper'],
    ['#713f12', 'Gold'],
    ['#14532d', 'Forest'],
    ['#0c4a6e', 'Ocean'],
    ['#4c1d95', 'Plum'],
  ]),
  light: Object.freeze([
    ['#ffffff', 'White'],
    ['#fecdd3', 'Rose'],
    ['#fed7aa', 'Peach'],
    ['#fef3c7', 'Lemon'],
    ['#bbf7d0', 'Mint'],
    ['#bae6fd', 'Sky'],
    ['#ddd6fe', 'Lavender'],
  ]),
});
