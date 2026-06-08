// Loads the real browser markdown renderer (static/js/markdown.js) under Node by
// mocking the minimal browser globals it touches and stubbing its sibling imports.
// This mirrors the loader in tests/test_markdown_rendering_js.py so the streaming
// tests exercise the exact same renderer the browser runs.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

export async function loadMarkdown() {
  globalThis.window = { location: { origin: 'http://localhost' }, katex: null };
  globalThis.document = {
    readyState: 'loading',
    addEventListener() {},
    createElement(tag) {
      if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
      return {
        _html: '',
        content: { querySelectorAll() { return []; } },
        set innerHTML(v) { this._html = v; },
        get innerHTML() { return this._html; },
      };
    },
  };
  globalThis.MutationObserver = class { observe() {} };

  let src = fs.readFileSync(path.join(REPO, 'static/js/markdown.js'), 'utf8');
  src = src.replace(/import uiModule from ['"]\.\/ui\.js['"];/, '');
  src = src.replace(
    /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
    () => `function splitTableRow(row){return (row||'').replace(/^\\s*\\|/,'').replace(/\\|\\s*$/,'').split('|').map((c)=>c.trim());}`,
  );
  const emoji = fs
    .readFileSync(path.join(REPO, 'static/js/emojiShortcodes.js'), 'utf8')
    .replace(/^export default .*$/m, '')
    .replace(/export const /g, 'const ')
    .replace(/export function /g, 'function ');
  src = src.replace(
    /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
    () => emoji,
  );
  src = src.replace(
    /var escapeHtml = uiModule\.esc;/,
    () =>
      `var escapeHtml = (v) => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');`,
  );
  const url = 'data:text/javascript;base64,' + Buffer.from(src).toString('base64');
  return import(url);
}

// Canonicalize rendered HTML so two renders that produce the SAME DOM compare
// equal. Collapses only newline-bearing whitespace BETWEEN tags (`>\n\n<` ->
// `><`): it is insignificant in rendered HTML, and incremental finalization
// legitimately emits `\n\n` between two blocks where a single full render emits
// `\n`. Code whitespace is safe because code is HTML-escaped, so significant
// newlines live inside <code> as text (never between a `>` and a `<`). Inline
// single spaces between tags are left alone. Structural differences (two <ul> vs
// one, <ol> vs <ul>) survive normalization and still fail, as they must.
// Mermaid ids embed Date.now(), so they are normalized too.
export function normalizeRender(html) {
  return String(html)
    .replace(/>\s*\n\s*</g, '><')
    .trim()
    .replace(/(mermaid|thinking)-\d+-\d+/g, '$1-X');
}
