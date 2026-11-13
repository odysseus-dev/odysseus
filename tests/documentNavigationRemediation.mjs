import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
const scenario = process.argv[2];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const pending = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');
const row = id => ({ id, title: id, language: 'markdown', current_content: `Content ${id}`, version_count: 2, is_active: true });
try {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  if (process.env.ODYSSEUS_DOCUMENT_CONTROL_REVISION) {
    for (const asset of ['document.js', 'document/ui/suggestions.js']) {
      const source = execFileSync('git', ['show', `${process.env.ODYSSEUS_DOCUMENT_CONTROL_REVISION}:static/js/${asset}`], { encoding: 'utf8' });
      await page.route(`**/static/js/${asset}*`, route => route.fulfill({ contentType: 'application/javascript', body: source }));
    }
  }
  await page.goto(`${origin}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar" class="sidebar"></aside><main id="chat-container" class="chat-container"></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js?v=20260916docctx2');
    window.suggestions = await import('/static/js/document/ui/suggestions.js');
    editor.init('');
  });
  const inject = async id => page.evaluate(row => editor.injectFreshDoc(row), row(id));
  await inject('a');
  if (scenario === 'sessions' || scenario === 'session-error') {
    const gate = pending();
    await page.route('**/api/documents/*', async route => {
      const session = route.request().url().split('/').pop();
      if (session === 's1') await gate.promise;
      await route.fulfill({ status: session === 's1' && scenario === 'session-error' ? 500 : 200, json: [{ ...row(`doc-${session}`), session_id: session }] });
    });
    const request = page.waitForRequest('**/api/documents/s1');
    await page.evaluate(() => { window.loading = editor.loadSessionDocs('s1'); }); await request;
    await page.evaluate(() => editor.loadSessionDocs('s2'));
    gate.release(); await page.evaluate(() => window.loading);
    assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'doc-s2');
    assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'Content doc-s2');
  } else if (scenario === 'open' || scenario === 'create') {
    const gate = pending();
    await inject('b'); await page.evaluate(() => editor.loadDocument('a'));
    const endpoint = scenario === 'create' ? '**/api/document' : '**/api/document/c';
    await page.route(endpoint, async route => { await gate.promise; await route.fulfill({ json: row('c') }); });
    const request = page.waitForRequest(endpoint);
    await page.evaluate(scenario => { window.loading = scenario === 'create' ? editor.createDocument('s1') : editor.loadDocument('c'); }, scenario); await request;
    await page.evaluate(() => editor.loadDocument('b'));
    gate.release(); await page.evaluate(() => window.loading);
    assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'b');
    assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'Content b');
  } else {
    await inject('b'); await page.evaluate(() => editor.loadDocument('a'));
    const target = scenario === 'suggestions-active' ? 'a' : scenario === 'suggestions-loaded' ? 'b' : 'c';
    const gate = pending();
    const held = ['suggestions-away', 'suggestions-session'].includes(scenario);
    await page.route('**/api/document/c', async route => {
      if (held) await gate.promise;
      await route.fulfill({ status: scenario === 'suggestions-failure' ? 500 : 200, json: row('c') });
    });
    const batch = { doc_id: target, suggestions: [{ id: 'suggestion', find: `Content ${target}`, replace: 'Improved', reason: 'fixture' }] };
    if (held) {
      const request = page.waitForRequest('**/api/document/c');
      await page.evaluate(batch => { window.loading = editor.handleDocSuggestions(batch); }, batch); await request;
      if (scenario === 'suggestions-session') {
        await page.route('**/api/documents/s2', route => route.fulfill({ json: [{ ...row('new-session'), session_id: 's2' }] }));
        await page.evaluate(() => editor.loadSessionDocs('s2'));
      } else await page.evaluate(() => editor.loadDocument('b'));
      gate.release(); await page.evaluate(() => window.loading);
      assert.equal(await page.evaluate(() => suggestions.getActiveSuggestions().length), 0);
      assert.equal(await page.evaluate(() => editor.getCurrentDocId()), scenario === 'suggestions-session' ? 'new-session' : 'b');
    } else {
      await page.evaluate(batch => editor.handleDocSuggestions(batch), batch);
      if (scenario === 'suggestions-failure') {
        assert.equal(await page.evaluate(() => suggestions.getActiveSuggestions().length), 0);
        assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'a');
      } else {
        await page.evaluate(batch => editor.handleDocSuggestions(batch), batch);
        assert.equal(await page.evaluate(() => editor.getCurrentDocId()), target);
        assert.equal(await page.evaluate(() => suggestions.getActiveSuggestions().length), 1, 'repeated batches deduplicate');
        await page.evaluate(() => editor.loadDocument('b'));
        if (target !== 'b') assert.equal(await page.evaluate(() => suggestions.getActiveSuggestions().length), 0, 'queue belongs to its original document');
        await page.evaluate(target => editor.loadDocument(target), target);
        assert.equal(await page.evaluate(() => suggestions.getActiveSuggestions().length), 1, 'queue returns with its document');
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ scenario, passed: true }));
} finally { await browser.close(); }
