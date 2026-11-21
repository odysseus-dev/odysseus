// Feature coverage through the real application and backend: real keyboard and
// pointer events, persistence read back over HTTP, and the real Preview.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import {
  createMarkdownDocument, openApp, openDocument, placeCaretAtEnd, readDocument, reopenApp,
  saveAndWait, semanticSummary, tolerateNoAuthNotificationLogs,
} from './realAppHelpers.mjs';

const browser = await chromium.launch({ headless: true });
const results = [];
const record = (name) => { results.push(name); console.log(`[PASS] ${name}`); };

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await tolerateNoAuthNotificationLogs(context);
  const configure = (target) => {
    target.setDefaultTimeout(30000);
    target.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  };
  let page = await context.newPage();
  configure(page);
  await openApp(page);

  const content = (id) => page.evaluate(async (docId) => {
    const { docs } = await import('/static/js/document/application/documentState.js');
    return docs.get(docId)?.content;
  }, id);
  const selectText = (needle) => page.evaluate((target) => {
    const rich = document.getElementById('doc-email-richbody');
    const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const at = node.data.indexOf(target);
      if (at !== -1) {
        rich.focus();
        const range = document.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + target.length);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        return;
      }
    }
    throw new Error(`text not found: ${target}`);
  }, needle);
  const caretAfter = (needle) => page.evaluate((target) => {
    const rich = document.getElementById('doc-email-richbody');
    const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
    let node; let found = null;
    while ((node = walker.nextNode())) if (node.data.includes(target)) found = node;
    if (!found) throw new Error(`text not found: ${target}`);
    rich.focus();
    const range = document.createRange();
    range.setStart(found, found.data.indexOf(target) + target.length);
    range.collapse(true);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  }, needle);

  // --- Formatting, undo and redo with real keys -------------------------------
  const base = await createMarkdownDocument(context, 'Formatting', '# Title\n\nPlain words here.\n\n- one\n- two\n\n- [ ] task a\n- [x] task b');
  await openDocument(page, base.id);
  await selectText('words');
  await page.keyboard.press('Control+b');
  assert.equal(await content(base.id), '# Title\n\nPlain **words** here.\n\n- one\n- two\n\n- [ ] task a\n- [x] task b');
  await page.keyboard.press('Control+z');
  assert.equal(await content(base.id), '# Title\n\nPlain words here.\n\n- one\n- two\n\n- [ ] task a\n- [x] task b', 'undo restores the source');
  await page.keyboard.press('Control+Shift+z');
  assert.ok((await content(base.id)).includes('**words**'), 'redo reapplies bold');
  record('bold, undo and redo with real shortcuts');

  await selectText('Title');
  await page.keyboard.press('Control+Alt+2');
  assert.ok((await content(base.id)).startsWith('## Title'), 'heading level shortcut');
  await selectText('here');
  await page.keyboard.press('Control+i');
  await selectText('one');
  await page.keyboard.press('Control+`');
  const formatted = await content(base.id);
  assert.ok(formatted.includes('*here*') && formatted.includes('`one`'), formatted);
  record('heading, italic and inline code shortcuts');

  await caretAfter('task a');
  await page.keyboard.press('Control+Enter');
  assert.ok((await content(base.id)).includes('- [x] task a'), 'task list toggles with the keyboard');
  record('task list toggle');

  await saveAndWait(page, base.id);
  assert.equal((await readDocument(context, base.id)).current_content, await content(base.id));
  record('formatted document persists exactly');

  // --- Table column alignment and the alert menu ------------------------------
  const rich = await createMarkdownDocument(context, 'Table and alert', '| A | B |\n| --- | --- |\n| x | `y` |\n\n> [!NOTE]\n> About this.\n\ntail');
  await openDocument(page, rich.id);
  await page.locator('#doc-email-richbody td').first().click();
  await page.locator('[data-dd="table"]').click();
  await page.locator('#doc-md-dd-menu .doc-overflow-item', { hasText: 'Column: align center' }).click();
  assert.match(await content(rich.id), /\| :?-+:? \| --- \|\n/, 'column alignment writes a delimiter row');
  assert.ok((await content(rich.id)).includes(':-') && (await content(rich.id)).includes('-:'), await content(rich.id));
  await selectText('About this.');
  await page.locator('[data-dd="alert"]').click();
  await page.locator('#doc-md-dd-menu .doc-overflow-item', { hasText: 'Warning' }).click();
  assert.ok((await content(rich.id)).includes('> [!WARNING]'), await content(rich.id));
  record('table column alignment and alert type through the real menus');

  await saveAndWait(page, rich.id);
  const visual = await semanticSummary(page, '#doc-email-richbody');
  await page.locator('#doc-header-preview-btn').click();
  await page.waitForSelector('#doc-md-preview', { state: 'visible' });
  assert.equal(await page.locator('#doc-md-preview blockquote.markdown-alert-warning').count(), 1, 'Preview renders the alert');
  const preview = await semanticSummary(page, '#doc-md-preview');
  assert.deepEqual(preview.blocks.filter((b) => b.table), visual.blocks.filter((b) => b.table));
  assert.deepEqual(preview.code, visual.code);
  await page.locator('#doc-header-preview-btn').click();
  record('Preview matches Visual for the aligned table and the alert');

  // --- Links and images survive a localized edit ------------------------------
  const rich2 = await createMarkdownDocument(context, 'Links', 'See [docs](https://example.com/a_b) and ![logo](https://example.com/logo.png).\n\nSecond paragraph.');
  await openDocument(page, rich2.id);
  await placeCaretAtEnd(page);
  await page.keyboard.type(' More.');
  assert.equal(await content(rich2.id), 'See [docs](https://example.com/a_b) and ![logo](https://example.com/logo.png).\n\nSecond paragraph. More.');
  record('links and images stay byte for byte through a neighbouring edit');

  // --- Noncanonical LF and CRLF documents -------------------------------------
  const lf = 'Edit me\n\n* star item\n* second   item\n\n| Name  | Value |\n|:------|------:|\n| A     |   001 |\n\n~~~text\nfence\n~~~';
  const lfDoc = await createMarkdownDocument(context, 'Noncanonical LF', lf);
  await openDocument(page, lfDoc.id);
  await placeCaretAtEnd(page);
  await selectText('Edit me');
  await page.keyboard.press('End');
  await page.keyboard.type('!');
  assert.equal(await content(lfDoc.id), lf.replace('Edit me', 'Edit me!'));
  await saveAndWait(page, lfDoc.id);
  assert.equal((await readDocument(context, lfDoc.id)).current_content, lf.replace('Edit me', 'Edit me!'));
  record('LF document: only the edited text changes, untouched bytes preserved');

  const crlf = 'Edit me\r\n\r\n- a\r\n- b\r\n\r\n| A | B |\r\n| --- | --- |\r\n| 1 | 2 |\r\n';
  const crDoc = await createMarkdownDocument(context, 'CRLF', crlf);
  await openDocument(page, crDoc.id);
  await selectText('Edit me');
  await page.keyboard.press('End');
  await page.keyboard.type('?');
  await caretAfter('b');
  await page.keyboard.press('Enter');
  await page.keyboard.type('c');
  const crSaved = await content(crDoc.id);
  assert.ok(!/(?<!\r)\n/.test(crSaved), `no bare LF in a CRLF document: ${JSON.stringify(crSaved)}`);
  assert.ok(crSaved.includes('- b\r\n- c'));
  await saveAndWait(page, crDoc.id);
  assert.equal((await readDocument(context, crDoc.id)).current_content, crSaved);
  record('CRLF document stays CRLF after paragraph and list edits');

  // No-op mode switching leaves every byte alone.
  await page.locator('[data-mdview="edit"]').click();
  await page.locator('[data-mdview="visual"]').click();
  assert.equal(await content(crDoc.id), crSaved);
  record('no-op Visual/Source switching');

  // --- Unsupported construct: warning, Source and Preview ----------------------
  const unsupported = 'Energy is $E=mc^2$ here.\n\nSecond paragraph.';
  const unDoc = await createMarkdownDocument(context, 'Unsupported', unsupported);
  await openDocument(page, unDoc.id, { visual: false });
  await page.waitForSelector('#doc-visual-compat-banner', { state: 'visible' });
  const banner = await page.locator('#doc-visual-compat-banner').innerText();
  assert.match(banner, /Source mode/);
  assert.match(banner, /has not been changed/);
  assert.equal(await page.locator('#doc-email-richbody.markdown-visual-mode').isVisible().catch(() => false), false);
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), unsupported);
  await page.locator('#doc-editor-textarea').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type(' Edited in Source.');
  await saveAndWait(page, unDoc.id);
  assert.equal((await readDocument(context, unDoc.id)).current_content, `${unsupported} Edited in Source.`);
  await page.locator('#doc-header-preview-btn').click();
  await page.waitForSelector('#doc-md-preview', { state: 'visible' });
  assert.ok((await page.locator('#doc-md-preview').innerText()).includes('Edited in Source.'));
  await page.locator('#doc-header-preview-btn').click();
  record('unsupported construct: warning, Source editing and Preview, no Visual corruption');

  // --- Document switching and a fresh load ------------------------------------
  await openDocument(page, base.id);
  const switched = await content(base.id);
  await openDocument(page, rich2.id);
  assert.equal(await content(base.id), switched, 'switching away does not alter the first document');
  page = await reopenApp(context, page, configure);
  await openDocument(page, base.id);
  assert.equal(await content(base.id), (await readDocument(context, base.id)).current_content);
  record('document switching and reopen show the persisted content');
} catch (error) {
  console.error('[FAIL]', error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
console.log(`Passed ${results.length} feature coverage checks`);
if (process.exitCode) process.exit(process.exitCode);
