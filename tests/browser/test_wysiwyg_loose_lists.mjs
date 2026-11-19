// Loose lists through the real application and backend persistence: edit, save,
// reopen and restore an earlier version without touching untouched source bytes.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import {
  createMarkdownDocument, openApp, openDocument, readDocument, reopenApp, saveAndWait, origin, tolerateNoAuthNotificationLogs,
} from './realAppHelpers.mjs';

const browser = await chromium.launch({ headless: true });
const results = [];
const record = (name) => { results.push(name); console.log(`[PASS] ${name}`); };

const extendText = (page, needle, suffix) => page.evaluate(({ needle, suffix }) => {
  const rich = document.getElementById('doc-email-richbody');
  const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
  let node;
  let match = null;
  while ((node = walker.nextNode())) if (node.data === needle) match = node;
  if (!match) throw new Error(`text not found: ${needle}`);
  rich.focus();
  const range = document.createRange();
  range.setStart(match, match.data.length);
  range.collapse(true);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}, { needle, suffix }).then(() => page.keyboard.type(suffix));

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await tolerateNoAuthNotificationLogs(context);
  const configure = (target) => {
    target.setDefaultTimeout(30000);
    target.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  };
  let page = await context.newPage();
  configure(page);

  const original = 'para\n\n- a\n\n- b\n\n1. one\n\n2. two\n\n- [ ] todo\n\n- [x] done\n\ntail';
  const doc = await createMarkdownDocument(context, 'Loose lists', original);
  await openApp(page);
  await openDocument(page, doc.id);
  assert.equal(await page.locator('#doc-visual-compat-banner').isVisible(), false, 'a loose list document is admitted to Visual mode');

  // A no-op visit changes nothing.
  assert.equal((await readDocument(context, doc.id)).current_content, original);

  // Edit the paragraph before the lists, a loose item, and the paragraph after.
  await extendText(page, 'para', ' one');
  await extendText(page, 'b', 'X');
  await extendText(page, 'tail', '!');
  const blocked = await page.evaluate(async (id) => {
    const { docs } = await import('/static/js/document/application/documentState.js');
    const d = docs.get(id);
    return { blocked: Boolean(d._visualSyncBlocked), reasons: d._visualCompatReasons, content: d.content };
  }, doc.id);
  const expected = 'para one\n\n- a\n\n- bX\n\n1. one\n\n2. two\n\n- [ ] todo\n\n- [x] done\n\ntail!';
  assert.equal(blocked.blocked, false, JSON.stringify(blocked.reasons));
  assert.equal(blocked.content, expected, 'only the typed characters changed');
  record('edits before, inside and after loose lists synchronize exact Markdown');

  await saveAndWait(page, doc.id);
  assert.equal((await readDocument(context, doc.id)).current_content, expected, 'backend stores the exact Markdown');
  record('save persists exact source through the real backend');

  // Reopen from the backend: still Visual, still editable, blank lines intact.
  page = await reopenApp(context, page, configure);
  await openDocument(page, doc.id);
  await extendText(page, 'a', 'Y');
  await saveAndWait(page, doc.id);
  const reopened = 'para one\n\n- aY\n\n- bX\n\n1. one\n\n2. two\n\n- [ ] todo\n\n- [x] done\n\ntail!';
  assert.equal((await readDocument(context, doc.id)).current_content, reopened);
  record('reopened loose-list document stays editable');

  // Restore the first saved version through the real history panel.
  await page.locator('.doc-tab-version').first().click();
  await page.waitForSelector('#doc-version-panel:not(.hidden) .doc-version-restore');
  const restores = page.locator('#doc-version-panel .doc-version-restore');
  await restores.last().click();
  await page.waitForFunction(async ([id, text]) => {
    const response = await fetch(`/api/document/${id}`);
    return (await response.json()).current_content === text;
  }, [doc.id, original]);
  page = await reopenApp(context, page, configure);
  await openDocument(page, doc.id);
  await extendText(page, 'b', 'Z');
  await saveAndWait(page, doc.id);
  assert.equal((await readDocument(context, doc.id)).current_content, original.replace('- b', '- bZ'));
  record('restored version opens in Visual and edits cleanly');
} catch (error) {
  console.error('[FAIL]', error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
console.log(`Passed ${results.length} loose-list checks against ${origin}`);
if (process.exitCode) process.exit(process.exitCode);
