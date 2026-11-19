// Visual, saved source, application Preview and a reload must agree on what the
// user typed, including Markdown-significant characters that the serializer escapes.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import {
  createMarkdownDocument, openApp, openDocument, placeCaretAtEnd, readDocument, reopenApp, saveAndWait, semanticSummary, tolerateNoAuthNotificationLogs,
} from './realAppHelpers.mjs';

const browser = await chromium.launch({ headless: true });
const results = [];
const record = (name) => { results.push(name); console.log(`[PASS] ${name}`); };

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await tolerateNoAuthNotificationLogs(context);
  const configure = (target) => {
    target.setDefaultTimeout(30000);
    target.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  };
  let page = await context.newPage();
  configure(page);

  const doc = await createMarkdownDocument(context, 'Escapes', 'Intro\n\n| A | B |\n| --- | --- |\n| x | 2 |');
  await openApp(page);
  await openDocument(page, doc.id);

  // Type literal Markdown characters, one block each.
  await placeCaretAtEnd(page);
  const typed = ['2*3*4', 'C:\\temp and \\\\server\\share', 'snake_case_name [not a link] 1. no list', 'a | b and ~~plain~~ `tick`'];
  for (const line of typed) {
    await page.keyboard.press('Enter');
    await page.keyboard.insertText(line);
  }
  // Type a pipe into a table cell.
  const cell = page.locator('#doc-email-richbody td').first();
  await cell.click();
  await page.keyboard.press('End');
  await page.keyboard.insertText('|y');

  await saveAndWait(page, doc.id);
  const saved = (await readDocument(context, doc.id)).current_content;
  assert.ok(saved.includes('2\\*3\\*4'), `asterisks are escaped in the source: ${saved}`);
  assert.ok(saved.includes('x\\|y'), `cell pipe is escaped in the source: ${saved}`);
  record('visual edit saves escaped Markdown');

  const visual = await semanticSummary(page, '#doc-email-richbody');
  assert.deepEqual(visual.blocks.filter((b) => b.table)[0].table, [['A', 'B'], ['x|y', '2']]);
  assert.ok(visual.blocks.some((b) => b.p === '2*3*4'));
  assert.ok(visual.blocks.some((b) => b.p === 'C:\\temp and \\\\server\\share'));
  assert.ok(visual.blocks.some((b) => b.p === 'a | b and ~~plain~~ `tick`'));

  // The application's real Preview.
  await page.locator('#doc-header-preview-btn').click();
  await page.waitForSelector('#doc-md-preview', { state: 'visible' });
  const preview = await semanticSummary(page, '#doc-md-preview');
  assert.deepEqual(preview.blocks, visual.blocks, 'Preview blocks match the Visual editor');
  assert.deepEqual(preview.strong, visual.strong);
  assert.deepEqual(preview.em, visual.em);
  assert.deepEqual(preview.code, visual.code);
  assert.deepEqual(preview.links, visual.links);
  const previewText = await page.locator('#doc-md-preview').innerText();
  assert.ok(!previewText.includes('2\\*'), 'Preview shows no stray backslash before asterisks');
  assert.equal(await page.locator('#doc-md-preview table td').count(), 2, 'the escaped pipe adds no column');
  record('Preview agrees with Visual on text, formatting and table shape');

  // Back to editing: Source shows the escapes, Visual reopens the same content.
  await page.locator('#doc-header-preview-btn').click();
  await page.locator('[data-mdview="edit"]').click();
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), saved);
  await page.locator('[data-mdview="visual"]').click();
  assert.deepEqual((await semanticSummary(page, '#doc-email-richbody')).blocks, visual.blocks);
  record('Source and reopened Visual match');

  // Load again from the backend.
  page = await reopenApp(context, page, configure);
  await openDocument(page, doc.id);
  assert.deepEqual((await semanticSummary(page, '#doc-email-richbody')).blocks, visual.blocks);
  assert.equal((await readDocument(context, doc.id)).current_content, saved, 'no-op reopen leaves the source byte for byte');
  record('reload keeps source and Visual identical');

  const literals = await createMarkdownDocument(context, 'Literal markers', 'Record ___ESC_42___ and \\*\n\n`___ESC_42___`\n\n~~~text\n___ESC_42___\n~~~');
  await openDocument(page, literals.id, { visual: false });
  await page.locator('#doc-header-preview-btn').click();
  const literalPreview = page.locator('#doc-md-preview');
  await literalPreview.waitFor({ state: 'visible' });
  assert.ok((await literalPreview.innerText()).includes('Record ___ESC_42___ and *'));
  assert.deepEqual(await literalPreview.locator('code').allTextContents(), ['___ESC_42___', '___ESC_42___\n']);
  record('Preview preserves literal marker text in prose and code');

  const crlf = await createMarkdownDocument(context, 'CRLF source', 'First\r\n\r\nLast\r\n');
  await openDocument(page, crlf.id);
  await page.locator('[data-mdview="edit"]').click();
  const source = page.locator('#doc-editor-textarea');
  assert.equal(await source.inputValue(), 'First\n\nLast\n', 'textarea displays normalized line endings');
  await source.press('Control+End');
  await page.keyboard.insertText('Added\n\nAnother\n');
  await saveAndWait(page, crlf.id);
  assert.equal((await readDocument(context, crlf.id)).current_content, 'First\r\n\r\nLast\r\nAdded\r\n\r\nAnother\r\n');
  record('Source edits preserve a CRLF document’s line endings');
} catch (error) {
  console.error('[FAIL]', error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
console.log(`Passed ${results.length} preview parity checks`);
if (process.exitCode) process.exit(process.exitCode);
