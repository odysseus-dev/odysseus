// Existing behavior must not change: chat Markdown rendering, HTML (rich text)
// documents and email composition, exercised in the real application.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { openApp, origin, tolerateNoAuthNotificationLogs } from './realAppHelpers.mjs';

const browser = await chromium.launch({ headless: true });
const results = [];
const record = (name) => { results.push(name); console.log(`[PASS] ${name}`); };

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await tolerateNoAuthNotificationLogs(context);
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  await openApp(page);

  // --- Chat rendering: the shared renderer without the document option --------
  const chat = await page.evaluate(async () => {
    const { mdToHtml } = await import('/static/js/markdown.js');
    const host = document.createElement('div');
    const render = (source) => { host.innerHTML = mdToHtml(source); return host; };
    const out = {};
    let el = render('1. Step\n    ```bash\n    ls -la\n    ```\n2. Next');
    out.indentedFence = { pre: el.querySelectorAll('pre code.language-bash').length, text: el.querySelector('pre code')?.textContent };
    el = render('Intro\n```py\nx=1\n``` then done.\nafter text');
    out.closingText = { codeText: el.querySelector('pre code')?.textContent, after: el.textContent.includes('then done.') };
    el = render('> note\n> ```js\n> x()\n> ```\n\nafter');
    out.quoteFence = el.querySelectorAll('pre code.language-js').length;
    el = render('```js\nlet a = 1;\n```');
    out.copyPayload = el.querySelector('.copy-code')?.getAttribute('data-code');
    el = render('- a\n  - b');
    out.nested = { items: el.querySelectorAll('li').length, text: el.textContent };
    el = render('+ a\n+ b');
    out.plus = { items: el.querySelectorAll('li').length };
    el = render('| a | b |\n| --- | --- |\n| x\\|y | 2 |');
    out.pipeCells = el.querySelectorAll('td').length;
    el = render('Literal \\(x\\) and C:\\temp');
    out.backslashes = el.textContent;
    el = render('> [!NOTE]\n> A note');
    out.alert = el.querySelectorAll('.markdown-alert').length;
    return out;
  });
  assert.deepEqual(chat.indentedFence, { pre: 1, text: '    ls -la' }, 'indented fence under a numbered step renders as code');
  assert.equal(chat.closingText.codeText, 'x=1');
  assert.equal(chat.closingText.after, true, 'text after a closing fence stays outside the code block');
  assert.equal(chat.quoteFence, 1);
  assert.equal(chat.copyPayload, 'let a = 1;', 'Copy payload has no trailing newline');
  assert.equal(chat.nested.items, 1, 'chat keeps its historical nested-list rendering');
  assert.equal(chat.plus.items, 0, 'chat keeps its historical + bullet rendering');
  assert.equal(chat.pipeCells, 3, 'chat table splitting is unchanged');
  assert.ok(chat.backslashes.includes('C:\\temp'));
  assert.equal(chat.alert, 0, 'chat does not render alerts');
  record('chat rendering matches its historical behavior in the browser');

  // --- HTML (rich text) document ------------------------------------------------
  const rtRes = await context.request.post(`${origin}/api/document`, { data: { title: 'HTML doc', content: '<p>Hello world</p>', language: 'richtext' } });
  const rt = await rtRes.json();
  await page.evaluate(async (id) => {
    const mod = await import('/static/js/document.js');
    await mod.openPanel();
    await mod.loadDocument(id);
  }, rt.id);
  await page.waitForSelector('#doc-email-richbody.richtext-mode', { state: 'visible' });
  const rich = page.locator('#doc-email-richbody');
  const selectAll = () => rich.evaluate((el) => {
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el.firstElementChild);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  await selectAll();
  await page.keyboard.press('Control+u');
  assert.ok(await rich.evaluate((el) => Boolean(el.querySelector('u, [style*="underline"]'))), 'Ctrl+U underlines HTML documents');
  await selectAll();
  await page.waitForSelector('#doc-rich-selection-toolbar', { state: 'visible' });
  const toolbar = await page.evaluate(() => [...document.querySelectorAll('#doc-rich-selection-toolbar [data-rich-selection-action]')].map((b) => b.dataset.richSelectionAction));
  assert.deepEqual(toolbar, ['bold', 'italic', 'underline', 'strike', 'link', 'highlight:#fef08a', 'insert-image', 'removeformat']);
  await rich.evaluate((el) => {
    el.focus();
    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    el.firstElementChild.textContent = 'Hello world composing';
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertCompositionText', isComposing: true }));
  });
  assert.ok((await page.locator('#doc-editor-textarea').inputValue()).includes('composing'), 'HTML documents sync during composition as before');
  await rich.evaluate((el) => el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })));
  // Paste sanitization in HTML documents.
  await rich.evaluate((el) => {
    el.focus();
    const range = document.createRange(); range.selectNodeContents(el); range.collapse(false);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    const data = new DataTransfer();
    data.setData('text/html', '<p>ok<script>window.__xss=1<\/script><img src=x onerror="window.__xss=2"></p>');
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  });
  assert.equal(await page.evaluate(() => window.__xss), undefined, 'pasted script never runs');
  assert.equal(await rich.evaluate((el) => el.querySelectorAll('script, [onerror]').length), 0);
  record('HTML document: underline, floating toolbar, composition and paste sanitization unchanged');

  // --- Email composition ----------------------------------------------------------
  const emailRes = await context.request.post(`${origin}/api/document`, {
    data: { title: 'Mail', content: 'To: someone@example.com\nSubject: Hello\n\nBody text', language: 'email' },
  });
  const email = await emailRes.json();
  await page.evaluate(async (id) => {
    const mod = await import('/static/js/document.js');
    await mod.openPanel();
    await mod.loadDocument(id);
  }, email.id);
  await page.waitForSelector('#doc-email-richbody', { state: 'visible' });
  assert.equal(await page.locator('#doc-email-richbody.markdown-visual-mode').count(), 0, 'email never uses Markdown Visual mode');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type(' typed');
  assert.ok((await page.locator('#doc-editor-textarea').inputValue()).includes('typed'));
  await page.keyboard.press('Control+u');
  record('email composition keeps its rich editor');
} catch (error) {
  console.error('[FAIL]', error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
console.log(`Passed ${results.length} compatibility checks`);
if (process.exitCode) process.exit(process.exitCode);
