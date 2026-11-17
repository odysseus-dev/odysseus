import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

test('Gallery and Quick Edit Model Picker: identity, privacy, and synchronization', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();

    const mockModels = [
      {
        id: 'sdxl',
        label: 'Stable Diffusion XL (Primary)',
        kind: 'edit',
        provider: 'Cluster A',
        endpoint_id: 101,
        // Non-admin view: base_url omitted
      },
      {
        id: 'sdxl',
        label: 'Stable Diffusion XL (Secondary)',
        kind: 'edit',
        provider: 'Cluster B',
        endpoint_id: 202,
        // Non-admin view: base_url omitted
      },
      {
        id: 'qwen-image',
        label: 'Qwen Image Edit',
        kind: 'edit',
        provider: 'Local Seneca',
        endpoint_id: 303,
      },
    ];

    await page.route('http://gallery-sync.local/**', async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;

      if (path === '/') {
        return route.fulfill({
          contentType: 'text/html',
          body: `<!DOCTYPE html>
          <html>
            <body>
              <div id="gallery-settings-container">
                <select id="gallery-image-model"></select>
              </div>
              <div id="editor-container">
                <select class="ge-ai-command-model" id="ge-ai-command-model"></select>
                <div id="ge-status-model"></div>
              </div>
            </body>
          </html>`,
        });
      }

      if (path === '/api/editor/image-models') {
        return route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ models: mockModels }),
        });
      }

      if (path === '/api/editor/agent-models') {
        return route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ models: [] }),
        });
      }

      try {
        const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
        return route.fulfill({ contentType: 'text/javascript', body: source });
      } catch (err) {
        return route.abort();
      }
    });

    await page.goto('http://gallery-sync.local/');

    // --- Test 1: Option value encoding and distinct endpoint identities ---
    const evalResult1 = await page.evaluate(async () => {
      // Set legacy localStorage value that contained a private base URL
      localStorage.setItem('ge-ai-command-model', 'http://private-cluster.internal/v1::qwen-image');

      const { wireAIModelSelectors } = await import('/static/js/editor/ai-models.js');
      wireAIModelSelectors({ container: document.body, apiBase: '' });

      // Run gallery initialization
      const { openGallery } = await import('/static/js/gallery.js');
      try { openGallery(); } catch (_) {}

      // Wait a moment for network fetch
      await new Promise((r) => setTimeout(r, 80));

      const gallerySel = document.getElementById('gallery-image-model');
      const qeSel = document.getElementById('ge-ai-command-model');

      const galleryOpts = Array.from(gallerySel.options).map((o) => ({ value: o.value, text: o.textContent }));
      const qeOpts = Array.from(qeSel.options).map((o) => ({ value: o.value, text: o.textContent }));

      const migratedKey = localStorage.getItem('ge-ai-command-model');

      return {
        galleryOpts,
        qeOpts,
        galleryValue: gallerySel.value,
        qeValue: qeSel.value,
        migratedKey,
      };
    });

    // Check duplicate model id 'sdxl' produces distinguishable values with endpoint_id
    const sdxlGalleryOpts = evalResult1.galleryOpts.filter((o) => o.value.endsWith('::sdxl'));
    assert.equal(sdxlGalleryOpts.length, 2, 'Two endpoints offering sdxl produce two distinct options');
    assert.equal(sdxlGalleryOpts[0].value, '101::sdxl');
    assert.equal(sdxlGalleryOpts[1].value, '202::sdxl');

    // Check no options contain undefined or empty prefix
    for (const opt of evalResult1.galleryOpts) {
      assert.equal(opt.value.startsWith('::'), false, 'Option value must not start with empty ::');
      assert.equal(opt.value.includes('undefined'), false, 'Option value must not include undefined');
      assert.equal(opt.value.includes('http://'), false, 'Option value must not expose raw http URL');
    }

    // Check legacy migration: 'http://private-cluster.internal/v1::qwen-image' was migrated to '303::qwen-image'
    assert.equal(evalResult1.migratedKey, '303::qwen-image', 'Legacy key with URL migrated cleanly');
    assert.equal(evalResult1.galleryValue, '303::qwen-image', 'Gallery picker selected migrated model');
    assert.equal(evalResult1.qeValue, '303::qwen-image', 'Quick Edit picker selected migrated model');

    // --- Test 2: Same-tab synchronization (Gallery -> Quick Edit) ---
    const evalResult2 = await page.evaluate(async () => {
      const gallerySel = document.getElementById('gallery-image-model');
      gallerySel.value = '101::sdxl';
      gallerySel.dispatchEvent(new Event('change'));

      await new Promise((r) => setTimeout(r, 40));

      const qeSel = document.getElementById('ge-ai-command-model');
      const statusEl = document.getElementById('ge-status-model');
      const savedKey = localStorage.getItem('ge-ai-command-model');

      return {
        qeValue: qeSel.value,
        statusText: statusEl ? statusEl.textContent : '',
        savedKey,
      };
    });

    assert.equal(evalResult2.qeValue, '101::sdxl', 'Changing gallery picker updates quick-edit picker in same tab');
    assert.equal(evalResult2.savedKey, '101::sdxl', 'Changing gallery picker updates localStorage');

    // --- Test 3: Same-tab synchronization (Quick Edit -> Gallery) ---
    const evalResult3 = await page.evaluate(async () => {
      const qeSel = document.getElementById('ge-ai-command-model');
      qeSel.value = '202::sdxl';
      qeSel.dispatchEvent(new Event('change'));

      await new Promise((r) => setTimeout(r, 40));

      const gallerySel = document.getElementById('gallery-image-model');
      const savedKey = localStorage.getItem('ge-ai-command-model');

      return {
        galleryValue: gallerySel.value,
        savedKey,
      };
    });

    assert.equal(evalResult3.galleryValue, '202::sdxl', 'Changing quick-edit picker updates gallery picker in same tab');
    assert.equal(evalResult3.savedKey, '202::sdxl', 'Changing quick-edit picker updates localStorage');

    // --- Test 4: Cross-tab synchronization via storage event ---
    const evalResult4 = await page.evaluate(async () => {
      // Simulate storage event from another tab
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'ge-ai-command-model',
        newValue: '101::sdxl',
      }));

      await new Promise((r) => setTimeout(r, 40));

      const gallerySel = document.getElementById('gallery-image-model');
      const qeSel = document.getElementById('ge-ai-command-model');

      return {
        galleryValue: gallerySel.value,
        qeValue: qeSel.value,
      };
    });

    assert.equal(evalResult4.galleryValue, '101::sdxl', 'Storage event updates gallery picker');
    assert.equal(evalResult4.qeValue, '101::sdxl', 'Storage event updates quick-edit picker');
  } finally {
    await browser.close();
  }
});

test('picker reconstruction owns listeners and clears invalid persisted identities', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    let imageRequests = 0;
    await context.route('http://picker-lifecycle.local/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<div id="ge-status-model"></div><div id="editor-container"><select id="ge-ai-command-model"></select><select id="ge-ai-inpaint"></select></div>' });
      if (path.startsWith('/api/')) {
        if (path.endsWith('/image-models')) imageRequests++;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ models: path.endsWith('/image-models') ? [{ id: 'qwen-image', endpoint_id: 'alice', kind: 'edit', provider: 'A' }] : [], items: [], albums: [] }) });
      }
      try { return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('..' + path, import.meta.url), 'utf8') }); }
      catch { return route.fulfill({ status: 404, body: '' }); }
    });
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.modelListeners = {};
      const add = window.addEventListener.bind(window);
      window.addEventListener = (type, ...args) => { if (['storage', 'ge-ai-command-model-change', 'ge:model-endpoints-updated'].includes(type)) window.modelListeners[type] = (window.modelListeners[type] || 0) + 1; return add(type, ...args); };
      window.modelEvents = 0;
      add('ge-ai-command-model-change', () => window.modelEvents++);
    });
    const init = async p => {
      await p.goto('http://picker-lifecycle.local/');
      await p.evaluate(async () => {
        window.ai = await import('/static/js/editor/ai-models.js');
        window.gallery = await import('/static/js/gallery.js');
        window.ai.wireAIModelSelectors({ container: document.getElementById('editor-container') });
        window.gallery.openGallery();
      });
      await p.waitForFunction(() => document.getElementById('gallery-image-model')?.options.length === 2 && document.getElementById('ge-ai-command-model')?.options.length > 2);
    };
    await init(page);
    const initial = await page.evaluate(() => ({ ...window.modelListeners }));
    for (let i = 0; i < 3; i++) {
      await page.evaluate(() => { window.gallery.closeGallery(); window.oldSelect = document.getElementById('ge-ai-command-model'); window.oldValue = window.oldSelect.value; document.getElementById('editor-container').replaceChildren(); });
      await page.waitForFunction(() => !document.getElementById('gallery-modal'));
      await page.evaluate(() => {
        document.getElementById('editor-container').innerHTML = '<select id="ge-ai-command-model"></select><select id="ge-ai-inpaint"></select>';
        window.ai.wireAIModelSelectors({ container: document.getElementById('editor-container') });
        window.gallery.openGallery();
      });
      await page.waitForFunction(() => document.getElementById('gallery-image-model')?.options.length === 2 && document.getElementById('ge-ai-command-model')?.options.length > 2);
    }
    assert.deepEqual(await page.evaluate(() => window.modelListeners), initial, 'no new global listeners after reopen/reconstruction');
    const before = imageRequests;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated')));
    await page.waitForTimeout(100);
    assert.equal(imageRequests - before, 1, 'only live editor refreshes; no detached control handlers');
    const p2 = await context.newPage(); await init(p2);
    await page.evaluate(() => { window.modelEvents = 0; window.footerUpdates = 0; new MutationObserver(records => window.footerUpdates += records.length).observe(document.getElementById('ge-status-model'), { childList: true }); const s = document.getElementById('ge-ai-command-model'); s.value = 'alice::qwen-image'; s.dispatchEvent(new Event('change')); });
    await p2.waitForFunction(() => document.getElementById('gallery-image-model').value === 'alice::qwen-image');
    assert.equal(await page.evaluate(() => window.modelEvents), 1);
    assert.equal(await page.evaluate(() => window.footerUpdates), 1, 'exactly one footer update');
    assert.match(await page.locator('#ge-status-model').textContent(), /Edit:.*qwen-image/);
    assert.equal(await page.evaluate(() => window.oldSelect.value), await page.evaluate(() => window.oldValue), 'global events never update detached controls');
    await init(p2);
    assert.equal(await p2.locator('#gallery-image-model').inputValue(), 'alice::qwen-image');
    for (const invalid of ['deleted::qwen-image', 'disabled::qwen-image', 'https://private.invalid/v1::missing']) {
      await page.evaluate(value => localStorage.setItem('ge-ai-command-model', value), invalid);
      await init(page);
      assert.equal(await page.evaluate(() => localStorage.getItem('ge-ai-command-model')), null, invalid);
      assert.equal(await page.locator('#gallery-image-model').inputValue(), '');
      assert.equal(await page.locator('#ge-ai-command-model').inputValue(), '');
    }
  } finally { await browser.close(); }
});
