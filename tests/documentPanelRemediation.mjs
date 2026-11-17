import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';
const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');
try {
  const deletes = [];
  await page.route('**/api/**', route => {
    if (route.request().method() === 'DELETE') deletes.push(route.request().url());
    return route.fulfill({ json: { version_count: 2 } });
  });
  await page.route('**/api/document/*/versions', route => route.fulfill({ json: [
    { version_number: 2, content: 'latest', source: 'user' },
    { version_number: 1, content: 'old', source: 'user' },
  ] }));
  await page.route('**/api/document/*/version/2', route => route.fulfill({ json: { content: 'latest' } }));
  await page.goto(`${origin}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar" class="sidebar"></aside><main id="chat-container" class="chat-container"><textarea id="message"></textarea></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js?v=20260916docctx2');
    editor.init('');
    for (const id of ['a', 'b', 'c']) await editor.injectFreshDoc({ id, title: `Document ${id}`, language: 'markdown', current_content: 'latest', version_count: 2 });
  });
  const tab = id => page.locator(`.doc-tab[data-doc-id="${id}"] .doc-tab-select`);
  await tab('c').focus();
  await page.keyboard.press('Home');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'a');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'doc-tab-a');
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'b');
  await page.keyboard.press('End');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'c');
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'a', 'arrows wrap');
  assert.equal(await page.locator('[role="tab"][tabindex="0"]').count(), 1);
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('Delete');
  assert.equal(await page.locator('.doc-tab[data-doc-id="c"]').count(), 0);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'doc-tab-a');
  assert.deepEqual(deletes, [], 'keyboard closing retains the library document');
  await tab('b').click();
  await page.waitForFunction(() => editor.getCurrentDocId() === 'b');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'b');
  // Space/Enter use the native tab selector button activation.
  await tab('a').focus(); await page.keyboard.press('Space');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'a');
  await tab('b').focus(); await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => editor.getCurrentDocId()), 'b');
  const history = page.locator('.doc-tab.active .doc-tab-version');
  await history.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.activeElement.matches('.doc-version-preview'));
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.getElementById('doc-editor-textarea').readOnly);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#doc-editor-textarea').getAttribute('readonly'), null);
  assert.equal(await page.evaluate(() => document.activeElement.matches('.doc-tab.active .doc-tab-version')), true);
  await page.locator('.doc-tab.active .doc-tab-title').dblclick();
  await page.locator('.doc-tab-title-input').fill('Renamed document');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('.doc-tab.active .doc-tab-select').getAttribute('aria-label'), 'Renamed document');
  assert.equal(await page.locator('button input, button button').count(), 0);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'doc-tab-b');

  const cdp = await page.context().newCDPSession(page);
  const counts = async () => {
    const response = await cdp.send('Runtime.evaluate', { expression: 'Object.fromEntries(["mousemove", "mouseup", "keydown", "click", "selectionchange"].map(type => [type, (getEventListeners(document)[type] || []).length]))', includeCommandLineAPI: true, returnByValue: true });
    return response.result.value;
  };
  const before = await counts();
  for (let i = 0; i < 4; i++) {
    await page.evaluate(() => { editor.swapSide(); editor.swapSide(); editor.closePanel('down'); });
    await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
    await page.evaluate(() => editor.loadDocument('b'));
    await page.waitForTimeout(50);
  }
  const after = await counts();
  assert.deepEqual(after, before, 'pane listeners remain stable across side swap and remount');
  // Verify actual drag and fullscreen toggling still work after rebinding.
  const divider = page.locator('#doc-divider');
  const bounds = await divider.boundingBox();
  await page.mouse.move(bounds.x + 1, bounds.y + 50); await page.mouse.down();
  await page.mouse.move(bounds.x - 80, bounds.y + 50); await page.mouse.up();
  assert.equal(await page.locator('#doc-editor-pane').evaluate(pane => pane.style.flex), '0 0 auto');
  await page.locator('.doc-divider-collapse').evaluate(button => { button.dataset.mode = 'fullscreen'; button.click(); });
  assert.equal(await page.locator('#doc-editor-pane').evaluate(pane => pane.classList.contains('doc-fullscreen')), true);
  await page.locator('.doc-divider-collapse').evaluate(button => { button.dataset.mode = 'unfullscreen'; button.click(); });
  assert.equal(await page.locator('#doc-editor-pane').evaluate(pane => pane.classList.contains('doc-fullscreen')), false);
  if (process.env.ODYSSEUS_REMEDIATION_SCREENSHOTS) {
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `${process.env.ODYSSEUS_REMEDIATION_SCREENSHOTS}/tabs-desktop-after.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(1000);
    const activeClose = await page.locator('.doc-tab.active .doc-tab-close').boundingBox();
    assert(activeClose.x >= 0 && activeClose.x + activeClose.width <= 390, 'selected tab close control stays visible after narrow resize');
    await fs.writeFile(`${process.env.ODYSSEUS_REMEDIATION_SCREENSHOTS}/panel-results.json`, JSON.stringify({ before, after, activeClose }, null, 2));
    await page.screenshot({ path: `${process.env.ODYSSEUS_REMEDIATION_SCREENSHOTS}/tabs-mobile-after.png`, fullPage: true });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, before, after }));
} finally { await browser.close(); }
