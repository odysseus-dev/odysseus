import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const markdownPath = path.join(__dirname, '..', 'static', 'js', 'markdown.js');
let src = fs.readFileSync(markdownPath, 'utf8');

src = src.replace(
  /import uiModule from '\.\/ui\.js';/,
  'const uiModule = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\\"/g, "&quot;") };'
);
src = src.replace(
  /import \{ splitTableRow \} from '\.\/markdown\/tableRow\.js';/,
  'const splitTableRow = (row) => row.split("|").filter((cell) => cell.trim() !== "");'
);
src = src.replace(
  /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from '\.\/emojiShortcodes\.js';/,
  'const hasEmojiShortcode = (t) => !!t && t.indexOf(":") !== -1 && /:[a-z0-9_+-]{1,40}:/i.test(t); const replaceEmojiShortcodes = (t) => t;'
);
src = src.replace(/export function /g, 'function ');
src = src.replace(/export const /g, 'const ');
src = src.replace(/export default markdownModule;?/g, '');
src += '\nthis.__mdToHtml = mdToHtml;';

class MutationObserver {
  observe() {}
  disconnect() {}
}

const sandbox = {
  console,
  URL,
  MutationObserver,
  localStorage: { getItem() { return '[]'; }, setItem() {} },
  document: {
    body: { classList: { contains() { return true; } } },
    addEventListener() {},
    querySelectorAll() { return []; },
    getElementById() { return null; },
    contains() { return true; },
  },
  window: {
    location: { origin: 'http://localhost' },
    katex: null,
    mermaid: null,
  },
};

vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: markdownPath });

const input = [
  '> ```html',
  '> <script>',
  '>   newWindow.addEventListener(\'click\', () => {',
  '>     desktop.appendChild(newWindow);',
  '>   });',
  '> </script>',
  '> ```',
].join('\n');

const html = sandbox.__mdToHtml(input);
assert.equal(html.includes('___ALLOWED_HTML_'), false, html);
assert.equal(html.includes('appendChild'), true, html);

// Restoring a block with String.replace and a *string* replacement treats
// `$&`, `` $` ``, `$'`, `$$`, `$1` as special patterns, corrupting any code
// sample that contains them. Restore must use a function replacer so the
// content is inserted verbatim. Each fenced code block below carries a `$`
// sequence next to a unique marker word; assert the marker survives intact
// and no placeholder leaks.
const dollarCases = [
  ['AMPCASE $& END', 'ampersand'],
  ["BTCASE $` END", 'backtick'],
  ["APOSCASE $' END", 'apostrophe'],
  ['DOUBLECASE $$ END', 'dollar-dollar'],
  ['GROUPCASE $1 END', 'group-ref'],
];
for (const [code, name] of dollarCases) {
  const out = sandbox.__mdToHtml(['```sh', code, '```'].join('\n'));
  const marker = code.split(' ')[0];  // e.g. "AMPCASE"
  assert.equal(out.includes('___CODE_BLOCK_'), false,
    `${name}: placeholder leaked -> ${out}`);
  assert.ok(out.includes(marker), `${name}: marker ${marker} missing -> ${out}`);
  assert.ok(out.includes('END'), `${name}: trailing text lost -> ${out}`);
}

console.log('ok');
