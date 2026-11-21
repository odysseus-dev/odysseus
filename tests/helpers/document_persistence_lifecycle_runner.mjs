/**
 * Shared Chromium runner and scenario implementations for document persistence lifecycle tests.
 *
 * Provides isolated browser contexts for each scenario while avoiding the overhead
 * of repeated Node and Chromium process launches.
 */

import readline from 'node:readline';
import { chromium } from 'playwright';
import { stylesheetLinkTags } from './stylesheets.mjs';

const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

export async function launchBrowser() {
  const launchOptions = {
    headless: true,
  };
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  }
  return await chromium.launch(launchOptions);
}

export async function createScenarioContext(browser, isPdf = false) {
  const viewport = isPdf ? { width: 1000, height: 750 } : { width: 900, height: 700 };
  return await browser.newContext({ viewport });
}

export async function prepareScenarioPage(page, origin, stylesheets) {
  await page.goto(`${origin}/static/js/documentStats.js`);
  await page.setContent(`${stylesheets}<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>`);
}

export const SCENARIOS = {
  async scenario_1(page, origin, stylesheets) {
    const puts = [];
    await page.route('**/api/document/**', async route => {
      if (route.request().method() === 'PUT') {
        puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'doc-a', title: 'Doc A', language: 'markdown', current_content: 'A initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.injectFreshDoc({ id: 'doc-b', title: 'Doc B', language: 'markdown', current_content: 'B initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.loadDocument('doc-a');
      await new Promise(r => setTimeout(r, 60));
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'A modified';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      await mod.loadDocument('doc-b');
      await new Promise(r => setTimeout(r, 120));
    });
    const activeValue = await page.locator('#doc-editor-textarea').inputValue();
    return { puts, activeValue };
  },

  async scenario_2(page, origin, stylesheets) {
    const puts = [];
    await page.route('**/api/document/**', async route => {
      if (route.request().method() === 'PUT') {
        puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'doc-a', title: 'Doc A', language: 'markdown', current_content: 'A initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.injectFreshDoc({ id: 'doc-b', title: 'Doc B', language: 'markdown', current_content: 'B initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.loadDocument('doc-a');
      await new Promise(r => setTimeout(r, 60));
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'A closing edit';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      const closeBtn = document.querySelector('.doc-tab[data-doc-id="doc-a"] .doc-tab-close');
      closeBtn.click();
      await new Promise(r => setTimeout(r, 120));
    });
    const activeValue = await page.locator('#doc-editor-textarea').inputValue();
    const toast = await page.locator('#toast').textContent();
    return { puts, activeValue, toast };
  },

  async scenario_3(page, origin, stylesheets) {
    const puts = [];
    await page.route('**/api/document/**', async route => {
      if (route.request().method() === 'PUT') {
        puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'doc-a', title: 'Doc A', language: 'markdown', current_content: 'A initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.injectFreshDoc({ id: 'doc-b', title: 'Doc B', language: 'markdown', current_content: 'B initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.loadDocument('doc-a');
      await new Promise(r => setTimeout(r, 60));
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'A pending edit';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      const closeBtnB = document.querySelector('.doc-tab[data-doc-id="doc-b"] .doc-tab-close');
      closeBtnB.click();
      await new Promise(r => setTimeout(r, 60));
    });
    const activeAfterClose = await page.evaluate(() => window.__doc.getChatDocumentId());
    const saveStateAfterClose = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.waitForTimeout(2200);
    const saveStateAfterAutoSave = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    return { puts, activeAfterClose, saveStateAfterClose, saveStateAfterAutoSave };
  },

  async scenario_4(page, origin, stylesheets) {
    const puts = [];
    await page.route('**/api/document?session_id=session-2', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'doc-s2', session_id: 'session-2', title: 'Doc S2', content: 'S2 content', language: 'markdown', version_count: 1 },
        ]),
      });
    });
    await page.route('**/api/document/**', async route => {
      if (route.request().method() === 'PUT') {
        puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'doc-s1', session_id: 'session-1', title: 'Doc S1', language: 'markdown', current_content: 'S1 initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'S1 edited content';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      await mod.loadSessionDocs('session-2');
      await new Promise(r => setTimeout(r, 150));
    });
    const activeDoc = await page.evaluate(() => window.__doc.getChatDocumentId());
    return { puts, activeDoc };
  },

  async scenario_5(page, origin, stylesheets) {
    await page.route('**/api/document/slow-doc', async route => {
      await new Promise(r => setTimeout(r, 150));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 3 }) });
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'slow-doc', title: 'Slow', language: 'markdown', current_content: 'initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
    });
    await page.evaluate(() => {
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'slow edited';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      window.__savePromise = window.__doc.saveDocument({ silent: true });
    });
    await page.waitForTimeout(30);
    const stateDuringFlight = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(() => window.__savePromise);
    const stateAfterFlight = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    const badgeText = await page.locator('#doc-version-badge').textContent();
    return { stateDuringFlight, stateAfterFlight, badgeText };
  },

  async scenario_6(page, origin, stylesheets) {
    let attempts = 0;
    await page.route('**/api/document/fail-doc', async route => {
      attempts += 1;
      if (attempts === 1) {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Server exploded' }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'fail-doc', title: 'Fail', language: 'markdown', current_content: 'initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
    });
    await page.evaluate(async () => {
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'attempt 1';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      await window.__doc.saveDocument({ silent: true });
    });
    const stateAfterFailure = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(async () => {
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'attempt 2 retry';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const stateAfterEdit = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(async () => {
      await window.__doc.saveDocument({ silent: true });
    });
    const stateAfterSuccess = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    return { stateAfterFailure, stateAfterEdit, stateAfterSuccess, attempts };
  },

  async scenario_7(page, origin, stylesheets) {
    const savedBodies = [];
    await page.route('**/api/document/rev-doc', async route => {
      savedBodies.push(route.request().postDataJSON().content);
      await new Promise(r => setTimeout(r, 120));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: savedBodies.length }) });
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'rev-doc', title: 'Rev', language: 'markdown', current_content: 'initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
    });
    await page.evaluate(() => {
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'first in-flight edit';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      window.__p1 = window.__doc.saveDocument({ silent: true });
      setTimeout(() => {
        ta.value = 'second concurrent edit';
        ta.dispatchEvent(new Event('input', { bubbles: true }));
      }, 30);
    });
    await page.evaluate(() => window.__p1);
    const stateAfterFirstCompleted = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(async () => {
      await window.__doc.saveDocument({ silent: true });
    });
    const stateAfterSecondCompleted = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    return { savedBodies, stateAfterFirstCompleted, stateAfterSecondCompleted };
  },

  async scenario_8(page, origin, stylesheets) {
    page.on('dialog', async dialog => {
      await dialog.accept();
    });
    const puts = [];
    let deleteReceived = false;
    await page.route('**/api/document/del-doc', async route => {
      if (route.request().method() === 'DELETE') {
        deleteReceived = true;
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      } else if (route.request().method() === 'PUT') {
        puts.push(route.request().postDataJSON());
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      window.confirm = () => true;
      const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
      if (uiMod.default) {
        uiMod.default.styledConfirm = () => Promise.resolve(true);
      }
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'del-doc', title: 'Del Doc', language: 'markdown', current_content: 'initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 100));
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'typed before delete';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      ta.blur();
      const btn = document.getElementById('doc-footer-copy-btn');
      btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
    });
    await page.waitForTimeout(2200);
    return { deleteReceived, putsCount: puts.length };
  },

  async scenario_9(page, origin, stylesheets) {
    await page.route('**/api/document/trans-doc', async route => {
      await new Promise(r => setTimeout(r, 80));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'trans-doc', title: 'Trans', language: 'markdown', current_content: 'initial', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
    });
    const sInitial = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(() => {
      const ta = document.querySelector('#doc-editor-textarea');
      ta.value = 'mod 1';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const sDirty = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(() => {
      window.__saveP = window.__doc.saveDocument({ silent: true });
    });
    await page.waitForTimeout(20);
    const sSaving = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    await page.evaluate(() => window.__saveP);
    const sSaved = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
    return { sInitial, sDirty, sSaving, sSaved };
  },

  async scenario_10(page, origin, stylesheets) {
    const puts = [];
    await page.route('**/api/document/**', async route => {
      if (route.request().method() === 'PUT') {
        puts.push({ url: route.request().url(), body: route.request().postDataJSON() });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=' + Date.now());
      mod.init('/api');
      window.__doc = mod;
      await mod.injectFreshDoc({ id: 'target-a', title: 'Target A', language: 'markdown', current_content: 'Doc A Text In Textarea', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.injectFreshDoc({ id: 'target-b', title: 'Target B', language: 'markdown', current_content: 'Doc B Stored Content', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.loadDocument('target-a');
      await new Promise(r => setTimeout(r, 60));
      await window.__doc.saveDocument({ silent: true, docId: 'target-b' });
    });
    return { puts };
  },

  async deletion_http_500_then_save_and_retry(page, origin, stylesheets) {
    page.on('dialog', async dialog => { await dialog.accept(); });
    const puts = [];
    let deleteAttempts = 0;
    await page.route('**/api/document/retry-doc', async route => {
      if (route.request().method() === 'DELETE') {
        deleteAttempts += 1;
        if (deleteAttempts === 1) {
          await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        }
      } else if (route.request().method() === 'PUT') {
        puts.push(route.request().postDataJSON());
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: puts.length + 1 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    const res = await page.evaluate(async () => {
      window.confirm = () => true;
      const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
      if (uiMod.default) uiMod.default.styledConfirm = () => Promise.resolve(true);
      const mod = await import('/static/js/document.js?v=del-500-' + Date.now());
      mod.init('/api');
      await mod.injectFreshDoc({ id: 'retry-doc', title: 'Retry Doc', language: 'markdown', current_content: 'start', version_count: 1 });
      await new Promise(r => setTimeout(r, 80));

      // 1. Trigger failed DELETE (HTTP 500)
      const triggerDelete = () => {
        const btn = document.getElementById('doc-footer-copy-btn');
        btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
      };
      triggerDelete();
      await new Promise(r => setTimeout(r, 150));

      const stillOpenAfter500 = mod.isPanelOpen() && mod.getCurrentDocId() === 'retry-doc';
      const tabExistsAfter500 = Boolean(document.querySelector('.doc-tab[data-doc-id="retry-doc"]'));

      // 2. Edit & manually save after failed deletion
      const ta = document.getElementById('doc-editor-textarea');
      ta.value = 'saved after failed delete';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      const manualSaveOk = await mod.saveDocument({ silent: true, docId: 'retry-doc' });

      // 3. Edit & wait for debounced autosave (2000ms debounce) after failed deletion
      ta.value = 'autosaved after failed delete';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise(r => setTimeout(r, 2300));

      // 4. Retry deletion -> backend now returns 200
      ta.blur();
      triggerDelete();
      await new Promise(r => setTimeout(r, 150));

      const tabExistsAfterSuccess = Boolean(document.querySelector('.doc-tab[data-doc-id="retry-doc"]'));
      const saveAfterSuccess = await mod.saveDocument({ silent: true, docId: 'retry-doc' });

      return {
        stillOpenAfter500,
        tabExistsAfter500,
        manualSaveOk,
        tabExistsAfterSuccess,
        saveAfterSuccess,
        activeDocAfterSuccess: mod.getCurrentDocId(),
      };
    });
    return { ...res, deleteAttempts, puts };
  },

  async deletion_network_failure_preserves_doc_and_saves(page, origin, stylesheets) {
    const puts = [];
    let deleteAborted = false;
    await page.route('**/api/document/net-doc', async route => {
      if (route.request().method() === 'DELETE') {
        deleteAborted = true;
        await route.abort('failed');
      } else if (route.request().method() === 'PUT') {
        puts.push(route.request().postDataJSON());
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    const res = await page.evaluate(async () => {
      window.confirm = () => true;
      const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
      if (uiMod.default) uiMod.default.styledConfirm = () => Promise.resolve(true);
      const mod = await import('/static/js/document.js?v=del-net-' + Date.now());
      mod.init('/api');
      await mod.injectFreshDoc({ id: 'net-doc', title: 'Net Doc', language: 'markdown', current_content: 'start', version_count: 1 });
      await new Promise(r => setTimeout(r, 80));

      const btn = document.getElementById('doc-footer-copy-btn');
      btn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
      await new Promise(r => setTimeout(r, 150));

      const ta = document.getElementById('doc-editor-textarea');
      ta.value = 'saved after network abort';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      const savedAfterAbort = await mod.saveDocument({ silent: true, docId: 'net-doc' });
      return {
        savedAfterAbort,
        docIdStillPresent: mod.getCurrentDocId() === 'net-doc',
      };
    });
    return { ...res, deleteAborted, puts };
  },

  async pending_save_during_deletion_outcomes(page, origin, stylesheets) {
    const puts = [];
    await page.route('**/api/document/**', async route => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === 'DELETE' && url.endsWith('/del-ok-doc')) {
        await new Promise(r => setTimeout(r, 120));
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      } else if (method === 'DELETE' && url.endsWith('/del-fail-doc')) {
        await new Promise(r => setTimeout(r, 120));
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'fail' }) });
      } else if (method === 'PUT') {
        puts.push({ url, content: route.request().postDataJSON()?.content });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: 2 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    const res = await page.evaluate(async () => {
      window.confirm = () => true;
      const uiMod = await import('/static/js/ui.js?v=20260916largetoolscroll1');
      if (uiMod.default) uiMod.default.styledConfirm = () => Promise.resolve(true);
      const mod = await import('/static/js/document.js?v=del-pending-' + Date.now());
      mod.init('/api');

      // Subcase A: pending autosave + in-flight save call during successful DELETE
      await mod.injectFreshDoc({ id: 'del-ok-doc', title: 'Del Ok', language: 'markdown', current_content: 'init', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      const ta = document.getElementById('doc-editor-textarea');
      ta.value = 'pending edit before success delete';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('doc-footer-copy-btn').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
      const saveDuringOkDelete = await mod.saveDocument({ silent: true, docId: 'del-ok-doc' });
      await new Promise(r => setTimeout(r, 950));

      // Subcase B: pending autosave during failing DELETE must be preserved and saved after failure
      await mod.injectFreshDoc({ id: 'del-fail-doc', title: 'Del Fail', language: 'markdown', current_content: 'init', version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      const ta2 = document.getElementById('doc-editor-textarea');
      ta2.value = 'pending edit preserved on fail';
      ta2.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('doc-footer-copy-btn').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }));
      await new Promise(r => setTimeout(r, 1200));

      return { saveDuringOkDelete };
    });
    return { ...res, puts };
  },

  async pdf_pane_delayed_autosave_targets_originating_doc_after_tab_switch(page, origin, stylesheets) {
    const puts = [];
    const storedContent = {
      'pdf-a': '<!-- pdf_form_source upload_id="up-a" -->\n- **FullName:** _(empty)_ <!-- field=FullName type=text -->\n',
      'pdf-b': '<!-- pdf_form_source upload_id="up-b" -->\n- **FullName:** Bob Original <!-- field=FullName type=text -->\n',
    };
    await page.route('**/api/document/**', async route => {
      const url = route.request().url();
      const method = route.request().method();
      const docId = url.includes('/pdf-b') ? 'pdf-b' : 'pdf-a';
      if (url.endsWith('/render-pages')) {
        const md = storedContent[docId] || '';
        const m = md.match(/\*\*FullName:\*\*[ ]*(.*?) <!-- field=FullName/);
        const val = (m && m[1] !== '_(empty)_') ? m[1] : '';
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pages: [{
              page: 1,
              width: 400,
              height: 500,
              fields: [{ name: 'FullName', label: 'Full Name', type: 'text', value: val, rect_px: [20, 40, 220, 70] }],
            }],
          }),
        });
      } else if (url.endsWith('/page/1.png')) {
        await route.fulfill({
          status: 200,
          contentType: 'image/png',
          body: Buffer.from(TINY_PNG_BASE64, 'base64'),
        });
      } else if (method === 'PUT') {
        const body = route.request().postDataJSON();
        storedContent[docId] = body.content;
        puts.push({ url, body });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: puts.length + 1 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    const res = await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=pdf-tab-' + Date.now());
      const pdfTools = await import('/static/js/document/ui/pdfTools.js');
      mod.init('/api');
      const pdfContentA = '<!-- pdf_form_source upload_id="up-a" -->\n- **FullName:** _(empty)_ <!-- field=FullName type=text -->\n';
      const pdfContentB = '<!-- pdf_form_source upload_id="up-b" -->\n- **FullName:** Bob Original <!-- field=FullName type=text -->\n';
      await mod.injectFreshDoc({ id: 'pdf-a', title: 'PDF A', language: 'markdown', current_content: pdfContentA, version_count: 1 });
      await new Promise(r => setTimeout(r, 60));
      await mod.injectFreshDoc({ id: 'pdf-b', title: 'PDF B', language: 'markdown', current_content: pdfContentB, version_count: 1 });
      await new Promise(r => setTimeout(r, 60));

      await mod.loadDocument('pdf-a');
      await new Promise(r => setTimeout(r, 150));

      // 1. Edit PDF A field, then switch immediately to PDF B before 600ms debounce expires
      const inputA = document.querySelector('#doc-pdf-view input[data-field-name="FullName"]');
      inputA.value = 'Alice FormValue';
      inputA.dispatchEvent(new Event('input', { bubbles: true }));
      await mod.loadDocument('pdf-b');
      await new Promise(r => setTimeout(r, 150));

      const renderedDocWhenOnB = document.getElementById('doc-pdf-view')?.dataset?.renderedDocId || null;
      const inputB = document.querySelector('#doc-pdf-view input[data-field-name="FullName"]');
      const fieldBValue = inputB ? inputB.value : null;

      // A detached control is no longer authoritative. Late callbacks must
      // neither revive its refs nor overwrite the captured originating draft.
      const retainedDetachedRefs = pdfTools._pdfPaneFieldsByDoc.has('pdf-a');
      inputA.value = 'Alice DelayedTimer';
      pdfTools._schedulePdfPaneSave('pdf-a');
      await new Promise(r => setTimeout(r, 850));

      // Verify PDF B's textarea & rendered value were never overwritten by PDF A's save
      const textareaB = document.getElementById('doc-editor-textarea')?.value || '';
      await mod.loadDocument('pdf-a');
      await new Promise(r => setTimeout(r, 150));
      const inputAAfterReturn = document.querySelector('#doc-pdf-view input[data-field-name="FullName"]')?.value || '';

      return {
        renderedDocWhenOnB,
        fieldBValue,
        textareaB,
        inputAAfterReturn,
        retainedDetachedRefs,
      };
    });
    return { ...res, puts };
  },

  async pdf_pane_autosave_across_session_switch_and_revision_serialization(page, origin, stylesheets) {
    const putEvents = [];
    let putSeq = 0;
    await page.route('**/api/documents/**', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'doc-sess-2', title: 'Session 2 Doc', language: 'markdown', current_content: 'Session Two Clean', version_count: 1, is_active: true },
        ]),
      });
    });
    await page.route('**/api/document/**', async route => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.endsWith('/render-pages')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pages: [{
              page: 1,
              width: 400,
              height: 500,
              fields: [{ name: 'Signer', label: 'Signer', type: 'text', value: '', rect_px: [20, 40, 220, 70] }],
            }],
          }),
        });
      } else if (url.endsWith('/page/1.png')) {
        await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(TINY_PNG_BASE64, 'base64') });
      } else if (method === 'PUT') {
        const seq = ++putSeq;
        const body = route.request().postDataJSON();
        putEvents.push({ phase: 'start', seq, url, content: body.content });
        await new Promise(r => setTimeout(r, seq === 1 ? 150 : 20));
        putEvents.push({ phase: 'end', seq, url });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: seq + 1 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    const res = await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=pdf-sess-' + Date.now());
      mod.init('/api');
      const pdfMd = '<!-- pdf_form_source upload_id="up-s1" -->\n- **Signer:** _(empty)_ <!-- field=Signer type=text -->\n';
      await mod.injectFreshDoc({ id: 'pdf-s1', session_id: 'sess-1', title: 'PDF S1', language: 'markdown', current_content: pdfMd, version_count: 1 });
      await new Promise(r => setTimeout(r, 150));

      // 1. Concurrent serialized edits on pdf-s1 before switching session
      const inp = document.querySelector('#doc-pdf-view input[data-field-name="Signer"]');
      inp.value = 'First Draft';
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      const p1 = mod.saveDocument({ silent: true, docId: 'pdf-s1' });
      await new Promise(r => setTimeout(r, 20));
      inp.value = 'Session One Final';
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      // Switch chat session while debounce timer is pending and first PUT is in flight
      await mod.loadSessionDocs('sess-2');
      await p1;
      await new Promise(r => setTimeout(r, 800));

      const activeTextareaAfterSwitch = document.getElementById('doc-editor-textarea')?.value || '';
      return {
        activeDocAfterSwitch: mod.getCurrentDocId(),
        activeTextareaAfterSwitch,
      };
    });
    return { ...res, putEvents };
  },

  async pdf_pane_annotation_undo_and_export_preserved(page, origin, stylesheets) {
    const requests = [];
    await page.route('**/api/document/**', async route => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.endsWith('/render-pages')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ pages: [{ page: 1, width: 400, height: 500, fields: [] }] }),
        });
      } else if (url.endsWith('/page/1.png')) {
        await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(TINY_PNG_BASE64, 'base64') });
      } else if (url.endsWith('/export-pdf')) {
        requests.push({ type: 'EXPORT' });
        await route.fulfill({
          status: 200,
          contentType: 'application/pdf',
          headers: { 'Content-Disposition': 'attachment; filename="filled.pdf"' },
          body: '%PDF-1.4\n',
        });
      } else if (method === 'PUT') {
        requests.push({ type: 'PUT', content: route.request().postDataJSON()?.content || '' });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version_count: requests.length + 1 }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });
    await prepareScenarioPage(page, origin, stylesheets);
    await page.evaluate(async () => {
      const mod = await import('/static/js/document.js?v=pdf-undo-exp-' + Date.now());
      mod.init('/api');
      const pdfMd = '<!-- pdf_source upload_id="up-ann" -->\n';
      await mod.injectFreshDoc({ id: 'pdf-ops', title: 'PDF Ops', language: 'markdown', current_content: pdfMd, version_count: 1 });
      await new Promise(r => setTimeout(r, 150));

      // Add text annotation via toolbar + blank page click
      document.getElementById('doc-pdf-add-text-btn').click();
      const pageImg = document.querySelector('#doc-pdf-view img');
      const r = pageImg.getBoundingClientRect();
      pageImg.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: r.left + 40, clientY: r.top + 40 }));
      pageImg.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.left + 40, clientY: r.top + 40 }));
      const annTa = document.querySelector('#doc-pdf-view .pdf-annotation-wrap textarea');
      annTa.value = 'Note One';
      annTa.dispatchEvent(new Event('input', { bubbles: true }));

      // Click export immediately before 600ms timer expires -> must flush PUT before EXPORT
      document.getElementById('doc-export-pdf-btn').click();
      await new Promise(r => setTimeout(r, 300));
    });

    // Undo the annotation edit / creation via header Undo button (#doc-undo-btn)
    const beforeUndoCount = await page.locator('#doc-pdf-view .pdf-annotation-wrap').count();
    await page.locator('#doc-undo-btn').click();
    await page.waitForTimeout(250);
    const afterUndoText = await page.evaluate(() => {
      const ta = document.querySelector('#doc-pdf-view .pdf-annotation-wrap textarea');
      return ta ? ta.value : null;
    });
    await page.locator('#doc-undo-btn').click();
    await page.waitForTimeout(250);
    const afterSecondUndoCount = await page.locator('#doc-pdf-view .pdf-annotation-wrap').count();

    return { requests, beforeUndoCount, afterUndoText, afterSecondUndoCount };
  },
};

export async function executeScenario(browser, name, origin, stylesheets) {
  const scenarioFn = SCENARIOS[name];
  if (!scenarioFn) {
    throw new Error(`Unknown scenario: ${name}`);
  }
  const isPdf = name.startsWith('pdf_');
  const context = await createScenarioContext(browser, isPdf);
  try {
    const page = await context.newPage();
    return await scenarioFn(page, origin, stylesheets);
  } finally {
    await context.close();
  }
}

export function createWorkerShutdown({ browser, rl, getRl, onExit = code => process.exit(code) } = {}) {
  let shutdownPromise = null;
  const shutdown = (exitCode = 0) => {
    if (!shutdownPromise) {
      const doShutdown = async () => {
        const activeRl = typeof getRl === 'function' ? getRl() : (rl || null);
        if (activeRl) {
          try {
            activeRl.close();
          } catch (_) {}
        }
        try {
          if (browser && typeof browser.close === 'function') {
            await browser.close();
          }
        } catch (_) {}
        if (typeof onExit === 'function') {
          onExit(exitCode);
        }
      };
      shutdownPromise = Promise.resolve().then(doShutdown);
    }
    return shutdownPromise;
  };
  return shutdown;
}

async function runWorker() {
  const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
  if (!origin) {
    process.stderr.write('Missing ODYSSEUS_TEST_STATIC_ORIGIN\n');
    process.exit(1);
  }
  const stylesheets = await stylesheetLinkTags();
  const browser = await launchBrowser();

  let rl;
  const shutdown = createWorkerShutdown({
    browser,
    getRl: () => rl,
    onExit: code => process.exit(code),
  });

  process.on('SIGTERM', () => { shutdown(0); });
  process.on('SIGINT', () => { shutdown(0); });

  rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false,
  });

  process.stdout.write(JSON.stringify({ status: 'ready', pid: process.pid }) + '\n');

  rl.on('line', async line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      const msg = JSON.parse(trimmed);
      if (msg.action === 'close') {
        await shutdown(0);
        return;
      }
      if (msg.action === 'run' || msg.scenario) {
        const scenarioName = msg.scenario;
        const data = await executeScenario(browser, scenarioName, origin, stylesheets);
        process.stdout.write(JSON.stringify({ ok: true, data }) + '\n');
      } else {
        process.stdout.write(JSON.stringify({ ok: false, error: `Unrecognized action: ${msg.action}` }) + '\n');
      }
    } catch (err) {
      process.stdout.write(JSON.stringify({ ok: false, error: err?.stack || String(err) }) + '\n');
    }
  });

  rl.on('close', () => {
    shutdown(0);
  });
}

async function main() {
  const arg = process.argv[2];
  if (arg === '--worker') {
    await runWorker();
    return;
  }

  const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
  if (!origin) {
    process.stderr.write('Missing ODYSSEUS_TEST_STATIC_ORIGIN\n');
    process.exit(1);
  }
  const stylesheets = await stylesheetLinkTags();
  const browser = await launchBrowser();

  try {
    if (arg === '--all') {
      const results = {};
      for (const [name] of Object.entries(SCENARIOS)) {
        results[name] = await executeScenario(browser, name, origin, stylesheets);
      }
      process.stdout.write(JSON.stringify(results) + '\n');
    } else if (arg && SCENARIOS[arg]) {
      const result = await executeScenario(browser, arg, origin, stylesheets);
      process.stdout.write(JSON.stringify(result) + '\n');
    } else {
      process.stderr.write(`Invalid scenario: ${arg}\nAvailable: ${Object.keys(SCENARIOS).join(', ')}\n`);
      process.exit(1);
    }
  } finally {
    await browser.close();
  }
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/'))) {
  main().catch(err => {
    process.stderr.write((err?.stack || String(err)) + '\n');
    process.exit(1);
  });
}
