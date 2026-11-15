import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');

async function setup(t, routeHandler) {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], 'integrated module graph must not throw'));
  const requests = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    requests.push({ path: new URL(request.url()).pathname, method: request.method(), body: request.postDataJSON() });
    if (await routeHandler?.(route, requests.at(-1))) return;
    await route.fulfill({ json: { version_count: 2 } });
  });
  await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"><textarea id="message"></textarea></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js');
    editor.init('');
    window.state = await import('/static/js/document/application/documentState.js');
    window.preview = await import('/static/js/document/ui/previewModes.js');
  });
  return { page, requests };
}

async function inject(page, id, content, language = 'markdown', title = `${id} title`) {
  await page.evaluate(async data => editor.injectFreshDoc(data), {
    id, title, language, current_content: content, version_count: 2,
  });
}

async function edit(page, content) {
  await page.evaluate(value => {
    preview._setMarkdownPreviewActive(false);
    const ta = document.getElementById('doc-editor-textarea');
    ta.value = value;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }, content);
}

test('ownership: rapid minimize/reopen preserves cached snapshots and outgoing PUT', async t => {
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method !== 'PUT') return false;
    await held;
    await route.fulfill({ json: { version_count: 3 } });
    return true;
  });
  await inject(page, 'a', 'A initial');
  await inject(page, 'b', 'B original');
  for (let iteration = 0; iteration < 4; iteration++) {
    await page.evaluate(() => editor.loadDocument('a'));
    await edit(page, `A latest ${iteration}`);
    await page.evaluate(async () => { editor.closePanel('down'); await editor.loadDocument('b'); });
    const snapshot = await page.evaluate(() => ({
      a: { content: state.docs.get('a').content, title: state.docs.get('a').title, language: state.docs.get('a').language },
      b: state.docs.get('b').content,
    }));
    assert.deepEqual(snapshot, { a: { content: `A latest ${iteration}`, title: 'a title', language: 'markdown' }, b: 'B original' });
  }
  assert(requests.some(request => request.method === 'PUT'));
  release();
  await page.waitForFunction(() => state.docs.get('a')._saveState === 'saved');
  const puts = requests.filter(request => request.method === 'PUT');
  assert(puts.length > 0);
  assert(puts.every(request => request.path === '/api/document/a' && request.body.content.startsWith('A latest ')));
  assert.equal(puts.at(-1).body.content, 'A latest 3');
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'B original');
});

