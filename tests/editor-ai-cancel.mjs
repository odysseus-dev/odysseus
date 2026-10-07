import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.route('http://editor.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<button id="run">Generate</button>' });
    const body = await readFile(new URL('../static/js/editor/' + path.slice(1), import.meta.url), 'utf8');
    return route.fulfill({ contentType: 'text/javascript', body });
  });
  await page.goto('http://editor.test/');
  const result = await page.evaluate(async () => {
    const { createApplyImageTool } = await import('/ai-tool-runner.js');
    const { decodeAIImage } = await import('/ai-operation.js');
    const { state } = await import('/state.js');
    state.editorOpen = true;
    let requests = 0;
    let layers = 0;
    const messages = [];
    window.fetch = (_url, { signal }) => {
      requests++;
      return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }));
    };
    const run = createApplyImageTool({
      flatten: () => document.createElement('canvas'), saveState() {},
      createLayer() { layers++; }, composite() {}, renderLayerPanel() {},
      deriveBusyLabel: () => 'Generating', getSelectedAIEndpoint: () => ({}),
      spinnerModule: {}, uiModule: { showToast: message => messages.push(message) },
    });
    const button = document.querySelector('button');
    let pending;
    button.addEventListener('click', () => { pending = run('/test', {}, 'Test', button); });
    button.click();
    const cancellable = !button.disabled && button.getAttribute('aria-busy') === 'true';
    button.click();
    await pending;
    const restored = button.textContent === 'Generate' && !button.hasAttribute('aria-busy');
    button.click();
    button.click();
    await pending;
    const controller = new AbortController();
    controller.abort();
    let decodeCancelled = false;
    try { await decodeAIImage('invalid', controller.signal); }
    catch (error) { decodeCancelled = error.name === 'AbortError'; }
    return { requests, layers, messages, cancellable, restored, decodeCancelled };
  });
  assert.equal(result.requests, 2);
  assert.equal(result.layers, 0);
  assert.deepEqual(result.messages, ['Cancelled', 'Cancelled']);
  assert.equal(result.cancellable, true);
  assert.equal(result.restored, true);
  assert.equal(result.decodeCancelled, true);
  console.log('AI cancel: repeated click, request abort, retry, UI restoration, and decode guard passed');
} finally {
  await browser.close();
}
