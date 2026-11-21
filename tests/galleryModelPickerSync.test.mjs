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

test('Quick Edit preference update with Gallery closed, cross-tab broadcast, and backend failure recovery', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    let backendSavedModel = '';
    let shouldFailBackendSave = false;

    const mockModels = [
      { id: 'sdxl', label: 'SDXL Primary', kind: 'edit', provider: 'Cluster A', endpoint_id: 101 },
      { id: 'sdxl', label: 'SDXL Secondary', kind: 'edit', provider: 'Cluster B', endpoint_id: 202 },
      { id: 'qwen-image', label: 'Qwen Image', kind: 'edit', provider: 'Seneca', endpoint_id: 303 },
    ];

    await context.route('http://preference-recovery.local/**', async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;

      if (path === '/') {
        return route.fulfill({
          contentType: 'text/html',
          body: `<!DOCTYPE html>
          <html>
            <body>
              <div id="gallery-settings-container"></div>
              <div id="editor-container">
                <div id="ge-status-model"></div>
              </div>
            </body>
          </html>`,
        });
      }

      if (path === '/api/auth/settings') {
        if (route.request().method() === 'POST') {
          if (shouldFailBackendSave) {
            return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Backend save simulated failure' }) });
          }
          const body = JSON.parse(route.request().postData() || '{}');
          if ('image_edit_model' in body) backendSavedModel = body.image_edit_model || '';
          return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ image_model: '', image_edit_model: backendSavedModel }) });
        }
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ image_model: '', image_edit_model: backendSavedModel }) });
      }

      if (path.startsWith('/api/prefs')) {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ image_edit_model: backendSavedModel }) });
      }

      if (path === '/api/editor/image-models') {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ models: mockModels }) });
      }

      if (path === '/api/editor/agent-models') {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ models: [] }) });
      }

      try {
        const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
        return route.fulfill({ contentType: 'text/javascript', body: source });
      } catch (err) {
        return route.abort();
      }
    });

    const page1 = await context.newPage();
    await page1.goto('http://preference-recovery.local/');

    // Initialize editor and AI Settings with Gallery CLOSED (no gallery-image-model in DOM)
    await page1.evaluate(async () => {
      const { createAiSettings } = await import('/static/js/editor/ai-settings.js');
      const { wireAIModelSelectors } = await import('/static/js/editor/ai-models.js');
      window.aiSettings = createAiSettings({ container: document.getElementById('editor-container') });
      wireAIModelSelectors({ container: document.getElementById('editor-container'), apiBase: '' });
      await new Promise(r => setTimeout(r, 60));
    });

    // 1. Select a model in Quick Edit while Gallery is closed
    const syncResult1 = await page1.evaluate(async () => {
      const qeSelect = document.getElementById('ge-ai-command-model');
      qeSelect.value = '101::sdxl';
      qeSelect.dispatchEvent(new Event('change'));
      await new Promise(r => setTimeout(r, 60));
      return {
        qeValue: qeSelect.value,
        savedKey: localStorage.getItem('ge-ai-command-model'),
        galleryPresent: Boolean(document.getElementById('gallery-image-model')),
      };
    });

    assert.equal(syncResult1.galleryPresent, false, 'Gallery must not be in DOM initially');
    assert.equal(syncResult1.qeValue, '101::sdxl', 'Quick Edit selector adopted new model');
    assert.equal(syncResult1.savedKey, '101::sdxl', 'Storage key synchronized while Gallery was closed');
    assert.equal(backendSavedModel, 'sdxl', 'Backend preference store received persisted model');

    // 1b. Backend failure recovery while Gallery is closed:
    // Attempt change with backend returning HTTP 500, verify rollback in Quick Edit and storage,
    // and verify that the unpersisted selection does NOT leak into Gallery when opened later.
    shouldFailBackendSave = true;
    const failureResultClosed = await page1.evaluate(async () => {
      const qeSelect = document.getElementById('ge-ai-command-model');
      const statusEl = document.getElementById('ge-ai-settings-save-status');

      qeSelect.value = '303::qwen-image';
      qeSelect.dispatchEvent(new Event('change'));
      await new Promise(r => setTimeout(r, 120));

      return {
        qeValue: qeSelect.value,
        savedKey: localStorage.getItem('ge-ai-command-model'),
        statusText: statusEl ? statusEl.textContent : '',
        galleryPresent: Boolean(document.getElementById('gallery-image-model')),
      };
    });

    assert.equal(failureResultClosed.galleryPresent, false, 'Gallery remains closed');
    assert.match(failureResultClosed.statusText, /Backend save simulated failure|Could not save/i, 'Reports failure');
    assert.equal(failureResultClosed.qeValue, '101::sdxl', 'Quick Edit rolled back to 101::sdxl with Gallery closed');
    assert.equal(failureResultClosed.savedKey, '101::sdxl', 'Storage key rolled back to 101::sdxl with Gallery closed');
    assert.notEqual(failureResultClosed.qeValue, '303::qwen-image', 'Negative control: rejected model not in Quick Edit');
    assert.notEqual(failureResultClosed.savedKey, '303::qwen-image', 'Negative control: rejected model not in localStorage');
    assert.equal(backendSavedModel, 'sdxl', 'Backend model remains unchanged');
    shouldFailBackendSave = false;

    // 2. Open Gallery afterward and verify correct selection was restored from storage
    const galleryResult = await page1.evaluate(async () => {
      const { openGallery } = await import('/static/js/gallery.js');
      openGallery();
      await new Promise(r => setTimeout(r, 80));
      const gallerySelect = document.getElementById('gallery-image-model');
      return {
        galleryValue: gallerySelect ? gallerySelect.value : '',
      };
    });
    assert.equal(galleryResult.galleryValue, '101::sdxl', 'Opening Gallery after change reflects synced model');
    assert.notEqual(galleryResult.galleryValue, '303::qwen-image', 'Negative control: Gallery does not adopt failed selection');

    // 3. Select a different model with Gallery already open
    const syncResult2 = await page1.evaluate(async () => {
      const qeSelect = document.getElementById('ge-ai-command-model');
      qeSelect.value = '202::sdxl';
      qeSelect.dispatchEvent(new Event('change'));
      await new Promise(r => setTimeout(r, 60));
      const gallerySelect = document.getElementById('gallery-image-model');
      return {
        qeValue: qeSelect.value,
        galleryValue: gallerySelect.value,
        savedKey: localStorage.getItem('ge-ai-command-model'),
      };
    });
    assert.equal(syncResult2.qeValue, '202::sdxl', 'Quick edit updated to 202::sdxl');
    assert.equal(syncResult2.galleryValue, '202::sdxl', 'Gallery updated synchronously in same tab');
    assert.equal(syncResult2.savedKey, '202::sdxl', 'Storage key updated');
    assert.equal(backendSavedModel, 'sdxl', 'Backend saved model updated');

    // 4. Cross-tab synchronization: open page2 and verify selection
    const page2 = await context.newPage();
    await page2.goto('http://preference-recovery.local/');
    await page2.evaluate(async () => {
      const { createAiSettings } = await import('/static/js/editor/ai-settings.js');
      const { wireAIModelSelectors } = await import('/static/js/editor/ai-models.js');
      const { openGallery } = await import('/static/js/gallery.js');
      window.aiSettings = createAiSettings({ container: document.getElementById('editor-container') });
      wireAIModelSelectors({ container: document.getElementById('editor-container'), apiBase: '' });
      openGallery();
      await new Promise(r => setTimeout(r, 80));
    });

    const page2Model = await page2.locator('#gallery-image-model').inputValue();
    assert.equal(page2Model, '202::sdxl', 'Cross-tab page picked up synchronized storage key');

    // 5. Backend failure recovery: inject 500 error
    shouldFailBackendSave = true;
    const failureResult = await page1.evaluate(async () => {
      const qeSelect = document.getElementById('ge-ai-command-model');
      const statusEl = document.getElementById('ge-ai-settings-save-status');

      // Attempt to switch to Seneca Qwen model
      qeSelect.value = '303::qwen-image';
      qeSelect.dispatchEvent(new Event('change'));

      // Wait for async save failure and rollback
      await new Promise(r => setTimeout(r, 120));

      const gallerySelect = document.getElementById('gallery-image-model');
      return {
        qeValue: qeSelect.value,
        galleryValue: gallerySelect.value,
        savedKey: localStorage.getItem('ge-ai-command-model'),
        statusText: statusEl ? statusEl.textContent : '',
      };
    });

    // Verification of failure recovery:
    assert.match(failureResult.statusText, /Backend save simulated failure|Could not save/i, 'UI reports the save failure');
    assert.equal(failureResult.qeValue, '202::sdxl', 'Quick Edit rolled back to previously persisted selection');
    assert.equal(failureResult.galleryValue, '202::sdxl', 'Gallery rolled back to previously persisted selection');
    assert.equal(failureResult.savedKey, '202::sdxl', 'Storage rolled back, preventing dirty state');

    // Negative control: explicit check that rejected model was NOT retained
    assert.notEqual(failureResult.qeValue, '303::qwen-image', 'Negative control: Quick Edit must not retain rejected model');
    assert.notEqual(failureResult.galleryValue, '303::qwen-image', 'Negative control: Gallery must not retain rejected model');
    assert.notEqual(failureResult.savedKey, '303::qwen-image', 'Negative control: Storage must not retain rejected model');

    // 6. Subsequent successful save recovery
    shouldFailBackendSave = false;
    const recoveryResult = await page1.evaluate(async () => {
      const qeSelect = document.getElementById('ge-ai-command-model');
      const statusEl = document.getElementById('ge-ai-settings-save-status');

      qeSelect.value = '303::qwen-image';
      qeSelect.dispatchEvent(new Event('change'));
      await new Promise(r => setTimeout(r, 80));

      const gallerySelect = document.getElementById('gallery-image-model');
      return {
        qeValue: qeSelect.value,
        galleryValue: gallerySelect.value,
        savedKey: localStorage.getItem('ge-ai-command-model'),
        statusText: statusEl ? statusEl.textContent : '',
      };
    });

    assert.equal(recoveryResult.statusText, 'Saved', 'Subsequent save reports Saved');
    assert.equal(recoveryResult.qeValue, '303::qwen-image', 'Quick Edit accepted valid save');
    assert.equal(recoveryResult.galleryValue, '303::qwen-image', 'Gallery updated after successful save');
    assert.equal(recoveryResult.savedKey, '303::qwen-image', 'Storage updated after successful save');
    assert.equal(backendSavedModel, 'qwen-image', 'Backend received final accepted model');
  } finally {
    await browser.close();
  }
});
