// Remote content must not load on render, neither from an email body nor from
// chat markdown.
//
// - Email: _sanitizeHtml output is inserted into the main document, where the
//   email CSS only forces `background-image: none`. Inline url()-bearing
//   declarations and absolute `background` / `poster` attributes would still
//   fetch on open, bypassing click-to-load. Runs in Chromium (Playwright),
//   because the sanitizer needs a real DOMParser.
// - Chat: mdToHtml must not turn a remote `![x](https://...)` into an <img>
//   (a prompt-injected reply could exfiltrate data in the URL); same-origin
//   images still render inline. Runs in plain Node, like
//   tests/test_markdown_rendering_js.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);

let chromium = null;
try {
  ({ chromium } = require('playwright'));
} catch (_) {
  chromium = null;
}

const EMAIL_BODY = `
<div style="background-image:url(https://t.test/bg.png) !important; padding: 4px">a</div>
<ul style="list-style-image:url('https://t.test/li.png')"><li>b</li></ul>
<div style="border-image:url(https://t.test/border.png) 30 round; border: 1px solid">c</div>
<div style="cursor:url(https://t.test/cur.png), auto">d</div>
<div style="background-image:u\\72l(https://t.test/escaped.png)">e</div>
<div style="background-image:image-set('https://t.test/set.png' 1x)">f</div>
<div style="background-image:ur/**/l(https://t.test/comment.png)">g</div>
<table background="https://t.test/table.png"><tr><td background="//t.test/td.png">h</td></tr></table>
<table background="http:t.test/noslash.png"><tr><td>i</td></tr></table>
<table background="images/relative.png"><tr><td>j</td></tr></table>
<video poster="https://t.test/poster.png"></video>
`;

test('email sanitizer drops url()-bearing styles and remote background/poster attributes',
  { skip: chromium ? false : 'playwright is not installed' }, async () => {
    const browser = await chromium.launch({ args: ['--no-sandbox'] });
    try {
      const page = await browser.newPage();
      const remoteRequests = [];
      await page.route('https://t.test/**', (route) => {
        remoteRequests.push(route.request().url());
        return route.fulfill({ status: 204, body: '' });
      });
      await page.route('http://odysseus.test/**', (route) => {
        const { pathname } = new URL(route.request().url());
        if (pathname === '/') {
          return route.fulfill({ contentType: 'text/html', body: '<!doctype html><div id="reader"></div>' });
        }
        const file = path.join(REPO, pathname);
        if (!file.startsWith(path.join(REPO, 'static') + path.sep) || !fs.existsSync(file)) {
          return route.fulfill({ status: 404, body: '' });
        }
        return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
      });
      await page.goto('http://odysseus.test/');
      const out = await page.evaluate(async (body) => {
        const mod = await import('/static/js/emailLibrary/utils.js');
        const html = mod._sanitizeHtml(body);
        document.getElementById('reader').innerHTML = html;
        await new Promise((resolve) => setTimeout(resolve, 300));
        return html;
      }, EMAIL_BODY);

      assert.doesNotMatch(out, /url\s*\(/i);
      assert.doesNotMatch(out, /image-set/i);
      assert.doesNotMatch(out, /t\.test/, out);
      assert.doesNotMatch(out, /\sposter=/i);
      // Harmless declarations and relative backgrounds survive.
      assert.match(out, /padding: 4px/);
      assert.match(out, /border: 1px solid/);
      assert.match(out, /background="images\/relative\.png"/);
      assert.deepEqual(remoteRequests, []);
    } finally {
      await browser.close();
    }
  });
