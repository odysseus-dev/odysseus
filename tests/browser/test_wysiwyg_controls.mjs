import assert from 'node:assert/strict';
import { tolerateNoAuthNotificationLogs } from './realAppHelpers.mjs';
import { chromium } from 'playwright';

// No default: a missing origin must never fall back to a service already running on this machine.
const origin = process.env.ODYSSEUS_VALIDATION_ORIGIN;
assert.ok(origin, 'ODYSSEUS_VALIDATION_ORIGIN is required (scripts/run_real_playwright_validation.py starts an isolated server)');
const browser = await chromium.launch({ headless: true });

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await tolerateNoAuthNotificationLogs(context);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.getElementById('app-loader'));

  const source = [
    '# Keep this heading',
    '',
    '> [!NOTE]',
    '> Alert body',
    '',
    '```',
    'print("hello")',
    '```',
    '',
    '| Name | Age |',
    '| --- | --- |',
    '| Ada | 37 |',
  ].join('\n');
  const create = await context.request.post(`${origin}/api/document`, {
    data: { title: 'WYSIWYG controls', content: source, language: 'markdown' },
  });
  assert.ok(create.ok(), `document creation returned ${create.status()}`);
  const doc = await create.json();
  await page.evaluate(async id => {
    const app = await import('/static/js/document.js');
    await app.openPanel();
    await app.loadDocument(id);
  }, doc.id);

  const rich = page.locator('#doc-email-richbody.markdown-visual-mode');
  await rich.waitFor({ state: 'visible' });
  const selectText = async (text) => rich.evaluate((root, requested) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const start = node.data.indexOf(requested);
      if (start < 0) continue;
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, start + requested.length);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      root.focus();
      return;
    }
    throw new Error(`Text not found: ${requested}`);
  }, text);
  const openMenu = async (kind) => {
    await page.locator(`#doc-md-toolbar [data-dd="${kind}"]`).click();
    await page.locator('#doc-md-dd-menu').waitFor({ state: 'visible' });
  };
  const choose = async (label) => {
    await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: label }).click();
    await page.waitForTimeout(100);
  };
  const readMarkdown = () => page.evaluate(async id => {
    const { docs } = await import('/static/js/document/application/documentState.js');
    return docs.get(id)?.content;
  }, doc.id);

  await selectText('Alert body');
  await openMenu('alert');
  await choose('Warning');
  let content = await readMarkdown();
  assert.match(content, /> \[!WARNING\]/, 'alert control changes the Markdown type');
  assert.match(content, /> Alert body/, 'alert body remains intact');

  await selectText('Ada');
  await openMenu('table');
  await choose('Column: align center');
  content = await readMarkdown();
  assert.match(content, /\| :---: \| --- \|/, 'column alignment is serialized as a centered Markdown column');

  await selectText('print("hello")');
  await openMenu('code');
  // Tab strip scrolling also occurs when autosave refreshes a tab title.
  await page.evaluate(() => document.getElementById('doc-tab-scroll').dispatchEvent(new Event('scroll')));
  await choose('Language: Python');
  content = await readMarkdown();
  assert.match(content, /```python\nprint\("hello"\)/, 'language picker updates the code fence');
  assert.match(content, /# Keep this heading/, 'unrelated heading survives all control changes');

  await page.locator('#doc-footer-copy-btn').click();
  await page.waitForFunction(() => document.getElementById('doc-footer-copy-btn')?.dataset.saveState === 'saved');
  const read = await context.request.get(`${origin}/api/document/${doc.id}`);
  assert.ok(read.ok(), `document read returned ${read.status()}`);
  assert.equal((await read.json()).current_content, content, 'all control changes persist through the backend');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.getElementById('app-loader'));
  await page.evaluate(async id => {
    const app = await import('/static/js/document.js');
    await app.openPanel();
    await app.loadDocument(id);
  }, doc.id);
  await rich.waitFor({ state: 'visible' });
  assert.equal(await rich.locator('blockquote.markdown-alert').getAttribute('data-alert'), 'WARNING');
  assert.equal(await rich.locator('pre code').getAttribute('data-lang'), 'python');
  assert.equal(await rich.locator('table th').first().evaluate(cell => cell.style.textAlign), 'center');
  assert.equal(await rich.locator('h1').textContent(), 'Keep this heading');
  console.log('Alert switching, table column alignment, code language, and backend persistence passed.');
  await context.close();
} finally {
  await browser.close();
}
