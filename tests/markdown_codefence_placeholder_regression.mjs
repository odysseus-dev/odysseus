import assert from 'node:assert/strict';
import { loadMarkdown } from './streaming/markdownHarness.mjs';

// Use the same ES-module loader as the streaming renderer tests. It keeps the
// production exports intact and handles the browser's versioned sibling imports.
const { mdToHtml } = await loadMarkdown();

const input = [
  '> ```html',
  '> <script>',
  '>   newWindow.addEventListener(\'click\', () => {',
  '>     desktop.appendChild(newWindow);',
  '>   });',
  '> </script>',
  '> ```',
].join('\n');

const html = mdToHtml(input);
assert.equal(html.includes('___ALLOWED_HTML_'), false, html);
assert.equal(html.includes('appendChild'), true, html);

console.log('ok');
