import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const scenario = process.argv[2];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');
const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
const pending = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
await page.route('**/api/**', route => route.fulfill({ json: {} }));
try {
  await page.goto(`${origin}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar" class="sidebar"></aside><main id="chat-container" class="chat-container"></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js?v=20260916docctx2');
    editor.init('');
  });
  const inject = async (id, content = `${id} latest`) => {
    await page.evaluate(async ({ id, content }) => {
      await editor.injectFreshDoc({ id, title: id, language: 'markdown', current_content: content, version_count: 2 });
    }, { id, content });
  };
  const edit = async content => page.evaluate(content => {
    const textarea = document.getElementById('doc-editor-textarea');
    textarea.value = content;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }, content);
  const startSave = async () => page.evaluate(() => { window.saving = editor.saveDocument({ silent: true }); });
  const openHistory = async () => {
    await page.locator('.doc-tab.active .doc-tab-version').dispatchEvent('click');
    await page.locator('.doc-version-item').first().waitFor();
  };
  await inject('a');
  if (scenario === 'minimized-save') {
    const gate = pending(); const bodies = [];
    await page.route('**/api/document/a', async route => {
      bodies.push(route.request().postDataJSON().content);
      if (bodies.length === 1) await gate.promise;
      await route.fulfill({ json: { version_count: 3 } });
    });
    await edit('first snapshot');
    const request = page.waitForRequest(r => r.method() === 'PUT');
    await startSave(); await request;
    await edit('newer draft');
    await page.evaluate(() => editor.closePanel('down'));
    await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
    gate.release(); await page.evaluate(() => window.saving);
    // Save explicitly while unmounted, before restoring the DOM.
    await page.evaluate(() => editor.saveDocument({ silent: true, docId: 'a' }));
    await page.evaluate(() => editor.loadDocument('a'));
    assert.deepEqual(bodies, ['first snapshot', 'newer draft']);
    assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'newer draft');
    assert.equal(await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state'), 'saved');
  } else if (scenario.startsWith('closed-')) {
    await inject('b'); await page.evaluate(() => editor.loadDocument('a'));
    let fail = true; const bodies = [];
    await page.route('**/api/document/a', async route => {
      if (route.request().method() === 'PUT') {
        bodies.push(route.request().postDataJSON().content);
        if (fail && scenario === 'closed-network') return route.abort('failed');
        await route.fulfill({ status: fail ? (scenario === 'closed-missing' ? 404 : 500) : 200, json: { version_count: 3 } });
      } else await route.fulfill({ json: { id: 'a', title: 'a', language: 'markdown', current_content: 'stale server', version_count: 2 } });
    });
    await edit('recover this detached draft');
    const response = scenario === 'closed-network' ? page.waitForEvent('requestfailed', { predicate: request => request.method() === 'PUT' }) : page.waitForResponse(r => r.request().method() === 'PUT');
    await page.locator('.doc-tab[data-doc-id="a"] .doc-tab-close').dispatchEvent('click'); await response;
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 0)));
    await page.evaluate(() => editor.loadDocument('a'));
    assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'recover this detached draft');
    fail = false; await page.evaluate(() => editor.saveDocument({ silent: true }));
    assert.equal(bodies.at(-1), 'recover this detached draft');
  } else {
    await inject('b'); await page.evaluate(() => editor.loadDocument('a'));
    const versions = [{ version_number: 2, content: 'a latest', source: 'user', summary: 'latest' },
      { version_number: 1, content: 'a old', source: '<img id="hostile-source">', summary: '<b id="hostile-summary">markup</b>' }];
    await page.route('**/api/document/*/versions', route => route.fulfill({ json: versions }));
    const gate = pending(); let hold = scenario === 'history-preview' || scenario === 'history-restore';
    await page.route('**/api/document/*/version/1', async route => { if (hold) await gate.promise; await route.fulfill({ json: { content: 'a old' } }); });
    await page.route('**/api/document/*/restore/1', async route => { if (hold) await gate.promise; await route.fulfill({ json: { id: 'a', title: 'a', language: 'markdown', current_content: 'a restored', version_count: 3 } }); });
    await openHistory();
    if (scenario === 'history-lifecycle') {
      await page.locator('.doc-version-item[data-version="1"]').dispatchEvent('click');
      await page.waitForFunction(() => document.getElementById('doc-editor-textarea').value === 'a old');
      const bodies = [];
      await page.route('**/api/document/a', async route => {
        bodies.push(route.request().postDataJSON().content);
        await route.fulfill({ json: { version_count: 2 } });
      });
      await page.evaluate(() => editor.saveDocument({ silent: true }));
      assert.deepEqual(bodies, ['a latest'], 'preview text is never a draft');
      await page.evaluate(() => editor.closePanel('down'));
      await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
      assert.equal(await page.locator('#doc-version-panel').count(), 0);
      await page.evaluate(() => editor.loadDocument('a'));
      assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'a latest');
      assert.equal(await page.locator('#doc-editor-textarea').getAttribute('readonly'), null);
      await openHistory();
      await page.locator('#doc-version-close').dispatchEvent('click');
      await openHistory();
      assert.equal(await page.locator('#doc-version-panel').count(), 1);
    } else if (scenario === 'history-failure') {
      await page.route('**/api/document/*/version/1', route => route.fulfill({ status: 500, json: {} }));
      await page.route('**/api/document/*/restore/1', route => route.fulfill({ status: 500, json: {} }));
      const preview = page.waitForResponse('**/api/document/a/version/1');
      await page.locator('.doc-version-item[data-version="1"]').dispatchEvent('click'); await preview;
      assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'a latest');
      const restore = page.waitForResponse('**/api/document/a/restore/1');
      await page.locator('.doc-version-restore').dispatchEvent('click'); await restore;
      await page.locator('#doc-version-close').dispatchEvent('click');
      await page.route('**/api/document/*/versions', route => route.fulfill({ status: 500, json: {} }));
      await page.locator('.doc-tab.active .doc-tab-version').dispatchEvent('click');
      await page.locator('#doc-version-list').getByText('Failed to load versions').waitFor();
      await page.evaluate(() => editor.loadDocument('b'));
      assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'b latest');
    } else if (scenario === 'history-rapid') {
      await page.locator('#doc-version-close').dispatchEvent('click');
      const listGate = pending();
      await page.route('**/api/document/*/versions', async route => { await listGate.promise; await route.fulfill({ json: versions }); });
      await page.locator('.doc-tab.active .doc-tab-version').dispatchEvent('click');
      await page.locator('#doc-version-close').dispatchEvent('click');
      await page.evaluate(() => editor.loadDocument('b'));
      listGate.release();
      await page.waitForTimeout(50);
      await page.locator('#doc-editor-textarea').dispatchEvent('click');
      assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'b latest');
      assert.equal(await page.locator('#doc-version-panel').getAttribute('class'), 'doc-version-panel hidden');
    } else if (scenario === 'history-metadata') {
      assert.equal(await page.locator('#hostile-source, #hostile-summary').count(), 0);
      assert.equal(await page.locator('.doc-version-summary').last().textContent(), versions[1].summary);
    } else {
      if (scenario === 'history-preview') {
        const request = page.waitForRequest('**/api/document/a/version/1');
        await page.locator('.doc-version-item[data-version="1"]').dispatchEvent('click'); await request;
      } else if (scenario === 'history-restore') {
        const request = page.waitForRequest('**/api/document/a/restore/1');
        await page.locator('.doc-version-restore').dispatchEvent('click'); await request;
      }
      if (scenario === 'history-ai') await page.evaluate(() => editor.streamDocOpen('AI new document', 'markdown'));
      else await page.evaluate(() => editor.loadDocument('b'));
      const expected = scenario === 'history-ai' ? '' : 'b latest';
      gate.release(); hold = false;
      await page.waitForTimeout(60);
      await page.locator('#doc-editor-textarea').dispatchEvent('click');
      assert.equal(await page.locator('#doc-editor-textarea').inputValue(), expected);
      if (scenario !== 'history-ai') assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'b');
    }
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ scenario, passed: true }));
} finally { await browser.close(); }
