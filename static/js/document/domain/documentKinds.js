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
