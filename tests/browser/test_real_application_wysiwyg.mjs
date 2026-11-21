import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, firefox, webkit } from 'playwright';
import { tolerateNoAuthNotificationLogs } from './realAppHelpers.mjs';

// No default: a missing origin must never fall back to a service already running on this machine.
const origin = process.env.ODYSSEUS_VALIDATION_ORIGIN;
assert.ok(origin, 'ODYSSEUS_VALIDATION_ORIGIN is required (scripts/run_real_playwright_validation.py starts an isolated server)');
const resultsDir = process.env.ODYSSEUS_VALIDATION_DIR || path.join(process.cwd(), 'artifacts', 'validation');
fs.mkdirSync(resultsDir, { recursive: true });

const suiteResults = {
  timestamp: new Date().toISOString(),
  origin,
  engineCoverage: {
    chromium: 'FULL_E2E_10_SCENARIOS',
    firefox: 'SMOKE_VISUAL_ADMISSION',
    webkit: 'SMOKE_VISUAL_ADMISSION',
  },
  scenarios: [],
  browserSmoke: {},
  consoleLogs: [],
  networkErrors: [],
  unauthorizedResponses: [],
};

function writeResults() {
  fs.writeFileSync(
    path.join(resultsDir, 'playwright-validation-results.json'),
    JSON.stringify(suiteResults, null, 2)
  );
}

function log(scenario, message) {
  const line = `[${scenario}] ${message}`;
  console.log(line);
}

// Helper to wait for editor idle state
async function waitForSaveState(page, expectedState = 'saved', timeout = 15000) {
  try {
    await page.waitForFunction(
      (st) => {
        const btn = document.getElementById('doc-footer-copy-btn');
        return btn && btn.dataset.saveState === st;
      },
      expectedState,
      { timeout }
    );
  } catch (err) {
    const info = await page.evaluate(() => {
      const btn = document.getElementById('doc-footer-copy-btn');
      return {
        saveState: btn ? btn.dataset.saveState : 'NO_BUTTON',
        btnText: btn?.textContent,
        btnTitle: btn?.title,
      };
    });
    console.error(`[waitForSaveState ERROR] Expected '${expectedState}', but button info is:`, JSON.stringify(info));
    throw err;
  }
}

// -------------------------------------------------------------
// CHROMIUM MAIN SUITE (SCENARIOS 1 - 10)
// -------------------------------------------------------------
const browser = await chromium.launch({ headless: true });
suiteResults.chromiumVersion = browser.version();

function wirePage(p) {
  p.setDefaultTimeout(60000);
  p.setDefaultNavigationTimeout(60000);
  p.on('framenavigated', (frame) => {
    if (frame === p.mainFrame()) console.log('[Navigation]', frame.url());
  });
  p.on('console', (msg) => {
    console.log(`[Browser Console ${msg.type()}]`, msg.text());
    if (msg.type() === 'error' || msg.text().includes('error')) {
      suiteResults.consoleLogs.push({ type: msg.type(), text: msg.text() });
    }
  });
  p.on('pageerror', (err) => {
    suiteResults.consoleLogs.push({ type: 'pageerror', text: err.message });
  });
  p.on('requestfailed', (req) => {
    suiteResults.networkErrors.push({ url: req.url(), error: req.failure()?.errorText });
  });
  p.on('response', (res) => {
    if (res.status() === 401) {
      console.log('[401 UNAUTHORIZED URL DETECTED]', res.url());
      suiteResults.unauthorizedResponses.push({
        url: res.url(),
        status: res.status(),
        statusText: res.statusText(),
      });
    }
  });
  p.on('dialog', async (dialog) => {
    await dialog.accept().catch(() => {});
  });
}

try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  await tolerateNoAuthNotificationLogs(context);
  let page = await context.newPage();
  wirePage(page);

  log('Setup', `Navigating to ${origin}`);
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.getElementById('app-loader'));
  await page.waitForTimeout(2000);

  // -----------------------------------------------------------
  // Test 1: Document creation
  // -----------------------------------------------------------
  log('Test 1', 'Document creation and visual mode admission');
  const t1Start = Date.now();
  await page.evaluate(async () => {
    const docMod = await import('/static/js/document.js');
    await docMod.openPanel();
    await docMod.newDocument('markdown');
  });

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible', timeout: 5000 });
  const t1State = await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const wrap = document.getElementById('doc-editor-wrap');
    return {
      richVisible: rich && getComputedStyle(rich).display !== 'none',
      richClass: rich ? rich.className : '',
      wrapVisible: wrap && getComputedStyle(wrap).display !== 'none',
      isContentEditable: rich?.isContentEditable,
    };
  });

  assert.equal(t1State.richVisible, true, 'Visual editor surface must be visible by default for new Markdown documents');
  assert.equal(t1State.wrapVisible, false, 'Source editor textarea must be hidden by default');
  assert.equal(t1State.isContentEditable, true, 'Visual editor surface must be contenteditable');

  // Interactivity: focus and type text
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Interactive document text');
  const t1Typed = await page.evaluate(() => document.getElementById('doc-email-richbody').textContent);
  assert.ok(t1Typed.includes('Interactive document text'), 'Editor surface must accept typed text');

  suiteResults.scenarios.push({
    id: 1,
    name: 'Document creation',
    status: 'PASSED',
    durationMs: Date.now() - t1Start,
    details: 'New markdown document admitted directly into interactive Visual mode.',
  });

  // -----------------------------------------------------------
  // Test 2: Visual formatting
  // -----------------------------------------------------------
  log('Test 2', 'Bold shortcut, italic toolbar, paragraph break and formatting persistence');
  const t2Start = Date.now();

  const t2Source = [
    '# Heading 1 Title',
    '',
    '* Bullet item 1',
    '* Bullet item 2',
    '',
    '1. Ordered item 1',
    '2. Ordered item 2',
    '',
    '> Blockquote quotation text',
    '',
    '~~~python',
    'def hello():',
    '    return "world"',
    '~~~',
    '',
    '| Header 1 | Header 2 |',
    '| --- | --- |',
    '| Data 1 | Data 2 |',
    '',
    'Normal text and bold and italic words.',
  ].join('\n');
  const t2DocRes = await context.request.post(`${origin}/api/document`, {
    data: { title: 'Formatting Test Document', content: t2Source, language: 'markdown' },
  });
  assert.ok(t2DocRes.ok());
  const t2Doc = await t2DocRes.json();
  const openFormattingDoc = () => page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.openPanel();
    await docMod.loadDocument(id);
  }, t2Doc.id);
  await openFormattingDoc();

  const formattingSurface = page.locator('#doc-email-richbody.markdown-visual-mode');
  await formattingSurface.waitFor({ state: 'visible' });
  const assertFormattingSync = async (expected) => {
    const state = await page.evaluate(async (id) => {
      const { docs } = await import('/static/js/document/application/documentState.js');
      const doc = docs.get(id);
      return { blocked: Boolean(doc._visualSyncBlocked), reasons: doc._visualCompatReasons, content: doc.content };
    }, t2Doc.id);
    assert.equal(state.blocked, false, JSON.stringify(state.reasons));
    assert.equal(state.content, expected, 'Visual edit synchronizes to the exact Markdown');
    assert.equal(await page.locator('#doc-visual-compat-banner').isVisible(), false);
  };
  const selectFormattingText = async (word) => {
    await formattingSurface.evaluate((rich, word) => {
      rich.focus();
      const walker = document.createTreeWalker(rich.lastElementChild, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const index = node.data.indexOf(word);
        if (index !== -1) {
          const range = document.createRange();
          range.setStart(node, index);
          range.setEnd(node, index + word.length);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          return;
        }
      }
      throw new Error(`Formatting text not found: ${word}`);
    }, word);
  };

  await selectFormattingText('bold');
  await page.keyboard.press('Control+b');
  const boldSource = t2Source.replace('and bold', 'and **bold**');
  await assertFormattingSync(boldSource);

  await selectFormattingText('italic');
  await page.locator('button[data-md="italic"]').click();
  const formattedSource = boldSource.replace('and italic', 'and *italic*');
  await assertFormattingSync(formattedSource);

  // The original recording pressed Enter at the end of the rich surface.
  // Chromium inserts an empty block with a caret placeholder before any text is typed.
  await formattingSurface.evaluate((rich) => {
    const range = document.createRange();
    range.selectNodeContents(rich);
    range.collapse(false);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await page.keyboard.press('Enter');
  assert.match(await formattingSurface.innerHTML(), /<(div|p)><br><\/\1>$/);
  await assertFormattingSync(formattedSource);
  await page.keyboard.type('A new paragraph');
  const persistedFormatting = `${formattedSource}\n\nA new paragraph`;
  await assertFormattingSync(persistedFormatting);

  const [formattingPut] = await Promise.all([
    page.waitForResponse((res) => res.request().method() === 'PUT'
      && new URL(res.url()).pathname === `/api/document/${t2Doc.id}`
      && res.request().postDataJSON()?.content === persistedFormatting),
    page.locator('#doc-footer-copy-btn').click(),
  ]);
  assert.ok(formattingPut.ok(), 'Backend acknowledges the formatted Markdown write');
  await waitForSaveState(page);
  const formattedRead = await context.request.get(`${origin}/api/document/${t2Doc.id}`);
  assert.ok(formattedRead.ok());
  assert.equal((await formattedRead.json()).current_content, persistedFormatting);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.getElementById('app-loader'));
  await openFormattingDoc();
  await formattingSurface.waitFor({ state: 'visible' });
  await assertFormattingSync(persistedFormatting);
  assert.equal(await formattingSurface.locator('b, strong').textContent(), 'bold');
  assert.equal(await formattingSurface.locator('i, em').textContent(), 'italic');

  // Verify all DOM structures present
  const t2Dom = await page.evaluate(() => {
    const r = document.getElementById('doc-email-richbody');
    return {
      hasH1: !!r.querySelector('h1'),
      hasBold: !!r.querySelector('b, strong'),
      hasItalic: !!r.querySelector('i, em'),
      hasUl: !!r.querySelector('ul > li'),
      hasOl: !!r.querySelector('ol > li'),
      hasBq: !!r.querySelector('blockquote'),
      hasCode: !!r.querySelector('pre, pre > code'),
      hasTable: !!r.querySelector('table > tbody > tr > td, table tr td'),
    };
  });

  assert.equal(t2Dom.hasH1, true, 'H1 element rendered');
  assert.equal(t2Dom.hasBold, true, 'Bold element rendered');
  assert.equal(t2Dom.hasItalic, true, 'Italic element rendered');
  assert.equal(t2Dom.hasUl, true, 'Unordered list rendered');
  assert.equal(t2Dom.hasOl, true, 'Ordered list rendered');
  assert.equal(t2Dom.hasBq, true, 'Blockquote rendered');
  assert.equal(t2Dom.hasCode, true, 'Code block rendered');
  assert.equal(t2Dom.hasTable, true, 'Table rendered');

  suiteResults.scenarios.push({
    id: 2,
    name: 'Visual formatting',
    status: 'PASSED',
    durationMs: Date.now() - t2Start,
    details: 'Bold shortcut, italic toolbar control and native Enter synchronized without blocking. A successful PUT and Saved state preceded exact API readback and reload verification. Other Markdown structures were seeded and checked after reopening.',
  });

  // -----------------------------------------------------------
  // Test 3: Source mode
  // -----------------------------------------------------------
  log('Test 3', 'Switching to Source mode and back to Visual');
  const t3Start = Date.now();

  // Switch to Source mode
  await page.locator('[data-mdview="edit"]').click();
  await page.waitForSelector('#doc-editor-wrap', { state: 'visible' });

  const t3SourceState = await page.evaluate(() => {
    const wrap = document.getElementById('doc-editor-wrap');
    const rich = document.getElementById('doc-email-richbody');
    const textarea = document.getElementById('doc-editor-textarea');
    return {
      wrapVisible: wrap && getComputedStyle(wrap).display !== 'none',
      richVisible: rich && getComputedStyle(rich).display !== 'none',
      content: textarea ? textarea.value : '',
    };
  });

  assert.equal(t3SourceState.wrapVisible, true, 'Source textarea wrapper must be visible in Source mode');
  assert.equal(t3SourceState.richVisible, false, 'Visual editor must be hidden in Source mode');
  assert.ok(t3SourceState.content.includes('# Heading 1 Title'), 'Markdown must contain heading');
  assert.ok(t3SourceState.content.includes('**bold**'), 'Markdown must contain bold syntax');
  assert.ok(t3SourceState.content.includes('*italic*') || t3SourceState.content.includes('_italic_'), 'Markdown must contain italic syntax');
  assert.ok(t3SourceState.content.includes('- Bullet item') || t3SourceState.content.includes('* Bullet item'), 'Markdown must contain bullet item');
  assert.ok(t3SourceState.content.includes('1. Ordered item'), 'Markdown must contain ordered item');
  assert.ok(t3SourceState.content.includes('> Blockquote quotation text'), 'Markdown must contain blockquote syntax');
  assert.ok(t3SourceState.content.includes('def hello()') && (t3SourceState.content.includes('```') || t3SourceState.content.includes('~~~')), 'Markdown must contain code fence syntax');
  assert.ok(t3SourceState.content.includes('| Header 1 | Header 2 |'), 'Markdown must contain table syntax');

  // Switch back to Visual mode
  await page.locator('[data-mdview="visual"]').click();
  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  const t3VisualState = await page.evaluate(() => {
    const wrap = document.getElementById('doc-editor-wrap');
    const rich = document.getElementById('doc-email-richbody');
    return {
      wrapVisible: wrap && getComputedStyle(wrap).display !== 'none',
      richVisible: rich && getComputedStyle(rich).display !== 'none',
    };
  });
  assert.equal(t3VisualState.richVisible, true, 'Visual editor returned to visible');
  assert.equal(t3VisualState.wrapVisible, false, 'Source editor returned to hidden');

  suiteResults.scenarios.push({
    id: 3,
    name: 'Source mode round-trip',
    status: 'PASSED',
    durationMs: Date.now() - t3Start,
    details: 'Switched to Source, checked Markdown syntax, then returned to Visual.',
  });

  // -----------------------------------------------------------
  // Test 4: Source fidelity
  // -----------------------------------------------------------
  log('Test 4', 'Source fidelity with noncanonical Markdown constructs');
  const t4Start = Date.now();

  const noncanonicalSource = [
    '* Asterisk item 1',
    '* Asterisk item 2',
    '',
    '|  Col A   |   Col B  |',
    '|:---|---:|',
    '| val 1 | val 2 |',
    '',
    '~~~python',
    'def compute():',
    '    return 42',
    '~~~',
    '',
    'Paragraph 1',
    '',
    '',
    'Paragraph 2',
  ].join('\n');

  // Create via API
  const t4DocRes = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Source Fidelity Noncanonical',
      content: noncanonicalSource,
      language: 'markdown',
    },
  });
  assert.equal(t4DocRes.ok(), true, 'API document creation succeeded');
  const t4Doc = await t4DocRes.json();

  // Load in UI
  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(id);
  }, t4Doc.id);

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  // Perform localized edit on Paragraph 2 in visual mode
  await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const paras = Array.from(rich.querySelectorAll('p'));
    const targetPara = paras.find((p) => p.textContent.includes('Paragraph 2'));
    if (targetPara) {
      targetPara.textContent = 'Paragraph 2 Modified';
      rich.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });

  // Save document
  await page.evaluate(async () => {
    const p = await import('/static/js/document/application/persistence.js');
    await p.saveDocument({ silent: true });
  });
  await waitForSaveState(page, 'saved');

  // Fetch updated content directly from the database via backend API
  const t4PersistedRes = await context.request.get(`${origin}/api/document/${t4Doc.id}`);
  const t4Persisted = await t4PersistedRes.json();
  const updatedContent = t4Persisted.current_content;

  // Assert exact equality between persisted Markdown and the expected source containing only the intended modification
  const expectedModifiedSource = noncanonicalSource.replace('Paragraph 2', 'Paragraph 2 Modified');
  assert.equal(
    updatedContent,
    expectedModifiedSource,
    'Persisted Markdown must be strictly byte-identical to expected source containing only the intended modification'
  );

  // Verify persistence after reopening in the user-facing editor
  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(id);
  }, t4Doc.id);
  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  const reopenedBackendRes = await context.request.get(`${origin}/api/document/${t4Doc.id}`);
  const reopenedDoc = await reopenedBackendRes.json();
  assert.equal(
    reopenedDoc.current_content,
    expectedModifiedSource,
    'Reopened document retains exact byte-for-byte source fidelity from backend'
  );

  // Verify no-op Visual/Source mode switching preserves untouched Markdown bytes
  await page.locator('[data-mdview="edit"]').click();
  await page.waitForSelector('#doc-editor-wrap', { state: 'visible' });
  await page.locator('[data-mdview="visual"]').click();
  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });
  await page.evaluate(async () => {
    const p = await import('/static/js/document/application/persistence.js');
    await p.saveDocument({ silent: true });
  });
  await waitForSaveState(page, 'saved');
  const noopSwitchRes = await context.request.get(`${origin}/api/document/${t4Doc.id}`);
  const noopSwitchDoc = await noopSwitchRes.json();
  assert.equal(
    noopSwitchDoc.current_content,
    expectedModifiedSource,
    'No-op Visual/Source switching must preserve untouched Markdown bytes'
  );

  // Verify real-backend table delimiter and literal backslash persistence
  const tableDocRes = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Table Escaping Fidelity',
      content: [
        '| Header 1 | Header 2 |',
        '| :--- | ---: |',
        '| foo \\| bar | `code | pipe` |',
        '| path | C:\\foo\\bar |',
      ].join('\n'),
      language: 'markdown',
    },
  });
  assert.equal(tableDocRes.ok(), true, 'Table document creation succeeded');
  const tableDoc = await tableDocRes.json();

  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(id);
  }, tableDoc.id);
  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  // Make an edit in the second row to trigger persistence
  await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const cells = Array.from(rich.querySelectorAll('td'));
    const targetCell = cells.find((c) => c.textContent.includes('C:\\foo\\bar'));
    if (targetCell) {
      targetCell.textContent = 'C:\\foo\\bar_updated';
      rich.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });

  await page.evaluate(async () => {
    const p = await import('/static/js/document/application/persistence.js');
    await p.saveDocument({ silent: true });
  });
  await waitForSaveState(page, 'saved');

  const tableBackendRes = await context.request.get(`${origin}/api/document/${tableDoc.id}`);
  const tableBackend = await tableBackendRes.json();
  assert.ok(
    tableBackend.current_content.includes('foo \\| bar') && tableBackend.current_content.includes('`code | pipe`') && tableBackend.current_content.includes('C:\\foo\\bar_updated'),
    'Table with pipes, code spans, and backslashes preserved through real backend save and reload'
  );

  suiteResults.scenarios.push({
    id: 4,
    name: 'Source fidelity',
    status: 'PASSED',
    durationMs: Date.now() - t4Start,
    details: 'Verified byte-identical preservation of asterisk list markers, table spacing, tildes, and blank lines.',
  });

  // -----------------------------------------------------------
  // Test 5: Persistence
  // -----------------------------------------------------------
  log('Test 5', 'Persistence (autosave, manual save, reload, navigation)');
  const t5Start = Date.now();

  const t5DocRes = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Persistence Test Doc',
      content: '# Persistence\n\nInitial body',
      language: 'markdown',
    },
  });
  const t5Doc = await t5DocRes.json();
  log('Test 5', `Created doc ${t5Doc.id}`);

  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(id);
  }, t5Doc.id);
  log('Test 5', 'Loaded initial doc');

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });
  log('Test 5', 'Visual mode confirmed');

  // Make visual edit and test autosave
  await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const p = rich.querySelector('p');
    if (p) {
      p.textContent = 'Autosaved edit body';
      rich.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  log('Test 5', 'Dispatched input event for autosave');

  // Wait for autosave (2500ms debounce in persistence engine)
  await page.waitForTimeout(3000);
  await waitForSaveState(page, 'saved', 10000);
  log('Test 5', 'Autosave wait completed');

  // Verify backend persisted
  const t5AutosavedRes = await context.request.get(`${origin}/api/document/${t5Doc.id}`);
  const t5Autosaved = await t5AutosavedRes.json();
  assert.ok(t5Autosaved.current_content.includes('Autosaved edit body'), 'Autosave successfully persisted to backend SQLite DB');
  log('Test 5', 'Autosave verification passed');

  // Test Manual Save
  await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const p = rich.querySelector('p');
    if (p) {
      p.textContent = 'Manual save body';
      rich.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });

  // Click Save button
  await page.waitForSelector('#doc-footer-copy-btn', { state: 'visible' });
  await page.locator('#doc-footer-copy-btn').click();
  await waitForSaveState(page, 'saved', 15000);
  log('Test 5', 'Manual save completed');

  const t5ManualRes = await context.request.get(`${origin}/api/document/${t5Doc.id}`);
  const t5Manual = await t5ManualRes.json();
  assert.ok(t5Manual.current_content.includes('Manual save body'), 'Manual save successfully persisted');
  log('Test 5', 'Manual save verification passed');

  // Test Browser Reload and Reopen (open fresh tab to verify persistence across sessions)
  log('Test 5', 'Opening fresh tab for reload simulation');
  const page2 = await context.newPage();
  wirePage(page2);
  log('Test 5', 'Navigating fresh page to origin');
  await page2.goto(origin, { waitUntil: 'domcontentloaded' });
  await page2.waitForFunction(() => !document.getElementById('app-loader'));
  await page2.waitForTimeout(2000);
  await page.close({ runBeforeUnload: false });
  page = page2;
  log('Test 5', 'Loading doc in fresh page');
  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.openPanel();
    await docMod.loadDocument(id);
  }, t5Doc.id);
  log('Test 5', 'Waiting for visual mode on fresh page');

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });
  log('Test 5', 'Visual mode visible on fresh page');
  const t5ReloadedText = await page.evaluate(() => document.getElementById('doc-email-richbody').textContent);
  assert.ok(t5ReloadedText.includes('Manual save body'), 'Document accurately restored after full browser reload');
  log('Test 5', 'Full reload verification passed');

  suiteResults.scenarios.push({
    id: 5,
    name: 'Persistence',
    status: 'PASSED',
    durationMs: Date.now() - t5Start,
    details: 'Verified debounced autosave, acknowledged manual save, API readback, and reopening saved content in a fresh tab.',
  });

  // -----------------------------------------------------------
  // Test 6: Rejected edits
  // -----------------------------------------------------------
  log('Test 6', 'Rejected edit handling and draft recovery');
  const t6Start = Date.now();

  const originalUncorrupted = '# Safe Title\n\n* Item 1\n* Item 2';
  const t6DocRes = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Reject Edit Doc',
      content: originalUncorrupted,
      language: 'markdown',
    },
  });
  const t6Doc = await t6DocRes.json();

  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.openPanel();
    await docMod.loadDocument(id);
  }, t6Doc.id);

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  // Monitor document PUT requests on the network layer
  const putRequests = [];
  const onPutRequest = (req) => {
    if (req.method() === 'PUT' && req.url().includes(`/api/document/${t6Doc.id}`)) {
      putRequests.push({ url: req.url(), postData: req.postData() });
    }
  };
  page.on('request', onPutRequest);

  const notificationWrites = [];
  const onNotification = req => {
    if (req.method() === 'POST' && new URL(req.url()).pathname === '/api/tasks/notification-logs') {
      notificationWrites.push(req.postDataJSON()?.body || '');
    }
  };
  page.on('request', onNotification);
  const paragraph = page.locator('#doc-email-richbody h1').last();
  const beforeWarning = await paragraph.boundingBox();
  const navigations = [];
  const onNavigation = (frame) => {
    if (frame === page.mainFrame()) navigations.push(frame.url());
  };
  page.on('framenavigated', onNavigation);

  // Inject an unsupported structure into visual DOM (e.g. merged table cells that cannot be represented in Markdown)
  await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th colspan="2">Merged Header</th></tr></thead><tbody><tr><td>Cell 1</td><td>Cell 2</td></tr></tbody>';
    rich.appendChild(table);
    rich.dispatchEvent(new Event('input', { bubbles: true }));
  });

  // Trigger sync and attempt manual save
  const rejectedSave = await page.evaluate(async (id) => {
    const sync = await import('/static/js/document/ui/richEditor/markdownVisualSync.js');
    const persistence = await import('/static/js/document/application/persistence.js');
    const rich = document.getElementById('doc-email-richbody');
    sync.syncVisualMarkdownToDoc(rich);
    // Explicit save attempt while blocked
    return persistence.saveDocument({ silent: false, docId: id });
  }, t6Doc.id);
  assert.equal(rejectedSave, false);

  assert.deepEqual(await paragraph.boundingBox(), beforeWarning, 'A blocked save must not move existing document content');
  await page.getByRole('button', { name: 'Copy draft text' }).waitFor();
  await page.getByRole('button', { name: 'Discard visual changes' }).waitFor();

  // Wait beyond the autosave debounce interval (2600ms+)
  await page.waitForTimeout(3000);

  const t6BlockedState = await page.evaluate(async (id) => {
    const { docs } = await import('/static/js/document/application/documentState.js');
    const doc = docs.get(id);
    const banner = document.getElementById('doc-visual-compat-banner');
    const toast = document.querySelector('.toast, .notification, [role="alert"]');
    const saveBtn = document.getElementById('doc-footer-copy-btn');
    return {
      blocked: !!doc?._visualSyncBlocked,
      bannerVisible: banner && getComputedStyle(banner).display !== 'none',
      bannerText: banner?.textContent || '',
      draftRetained: !!doc?._visualDraftHtml,
      saveState: saveBtn ? saveBtn.dataset.saveState : '',
      toastVisible: !!toast,
      reasons: doc?._visualCompatReasons || [],
    };
  }, t6Doc.id);

  page.off('request', onPutRequest);
  page.off('framenavigated', onNavigation);
  page.off('request', onNotification);
  assert.deepEqual(notificationWrites, [], 'Repeated blocked saves must not post global notifications');
  assert.deepEqual(navigations, [], 'A blocked save must not navigate or reload the editor');

  // Assert blocked state, visible warning, retained draft, and unsuccessful save
  assert.equal(t6BlockedState.blocked, true, 'Visual sync must be marked blocked');
  assert.equal(t6BlockedState.draftRetained, true, 'Visual draft must be retained in memory for user recovery');
  assert.ok(t6BlockedState.reasons.length > 0, 'Compatibility block reasons must be recorded on document model');
  assert.equal(t6BlockedState.bannerVisible, true, 'A visible recovery warning is required');
  assert.equal(t6BlockedState.saveState, 'blocked');
  assert.equal(putRequests.length, 0, 'Zero PUT requests must be dispatched while sync is blocked across debounce window');

  // Verify backend content was NEVER changed
  const t6BackendRes = await context.request.get(`${origin}/api/document/${t6Doc.id}`);
  const t6Backend = await t6BackendRes.json();
  assert.equal(t6Backend.current_content, originalUncorrupted, 'Backend content remains byte-identical when sync is blocked');

  suiteResults.scenarios.push({
    id: 6,
    name: 'Rejected edits safeguard',
    status: 'PASSED',
    durationMs: Date.now() - t6Start,
    details: 'An unsupported merged table blocked sync, retained the draft, and issued zero PUTs. The recovery banner did not move the editor or post a global notification.',
  });

  // -----------------------------------------------------------
  // Test 7: Concurrency & navigation while dirty
  // -----------------------------------------------------------
  log('Test 7', 'Concurrency and navigation races');
  const t7Start = Date.now();

  const docARes = await context.request.post(`${origin}/api/document`, {
    data: { title: 'Doc A', content: '# Document A', language: 'markdown' },
  });
  const docBRes = await context.request.post(`${origin}/api/document`, {
    data: { title: 'Doc B', content: '# Document B', language: 'markdown' },
  });
  const docA = await docARes.json();
  const docB = await docBRes.json();

  // Open Doc A, make rapid edit, switch to Doc B immediately before save fires
  await page.evaluate(async (idA) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(idA);
  }, docA.id);

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const p = document.createElement('p');
    p.textContent = 'Rapid edit in A';
    rich.appendChild(p);
    rich.dispatchEvent(new Event('input', { bubbles: true }));
  });

  // Switch immediately to Doc B
  await page.evaluate(async (idB) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(idB);
  }, docB.id);

  await page.waitForFunction(
    (idB) => document.getElementById('doc-editor-pane')?.dataset.activeDocumentId === idB,
    docB.id,
    { timeout: 5000 }
  );

  // Wait for any pending async saves to complete
  await page.waitForTimeout(3000);

  // Verify Doc B's content is NOT corrupted with Doc A's text
  const docBCheckRes = await context.request.get(`${origin}/api/document/${docB.id}`);
  const docBCheck = await docBCheckRes.json();
  assert.equal(docBCheck.current_content.includes('Rapid edit in A'), false, 'Doc A edits must never overwrite Doc B');

  // Verify Doc A persisted its own edits properly
  const docACheckRes = await context.request.get(`${origin}/api/document/${docA.id}`);
  const docACheck = await docACheckRes.json();
  assert.ok(docACheck.current_content.includes('Rapid edit in A'), 'Doc A edits safely persisted to Doc A');

  suiteResults.scenarios.push({
    id: 7,
    name: 'Concurrency and document switching',
    status: 'PASSED',
    durationMs: Date.now() - t7Start,
    details: 'Verified async save boundaries and document switching prevent cross-document data leakage.',
  });

  // -----------------------------------------------------------
  // Test 8: Security & sanitization
  // -----------------------------------------------------------
  log('Test 8', 'Security sanitization on active visual editor surface and paste pipeline');
  const t8Start = Date.now();

  const secDocRes = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Security Visual Surface Test',
      content: '# Visual Security Test\n\nInitial safe paragraph for visual surface sanitization testing.',
      language: 'markdown',
    },
  });
  const secDoc = await secDocRes.json();

  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(id);
  }, secDoc.id);

  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  // Initialize XSS execution detector
  await page.evaluate(() => {
    window.__xssExecuted = false;
  });

  // Exercise the paste handler with synthetic clipboard data and an active caret.
  const pasteSurface = page.locator('#doc-email-richbody.markdown-visual-mode');
  const pasteHandled = await pasteSurface.evaluate((rich) => {
    rich.focus();
    const selection = window.getSelection();
    selection.collapse(rich.querySelector('p').firstChild, 0);
    const dt = new DataTransfer();
    dt.setData('text/html', '<p>Pasted safe text</p><script>window.__xssExecuted = true;</script><img src="invalid-image" onerror="window.__xssExecuted = true;"><img src="javascript:alert(1)"><img src="data:image/svg+xml;base64,PHN2Zy8+" onerror="window.__xssExecuted = true;"><a href="javascript:alert(1)">evil js link</a><a href="vbscript:alert(2)">evil vb link</a><a href="data:text/html;base64,PHNjcmlwdD4=">evil data link</a><a href="file:///etc/passwd">evil file link</a><a href="https://example.com/safe">safe link</a><img src="/static/img/logo.png" alt="safe logo">');
    dt.setData('text/plain', 'Pasted safe text');
    const pasteEvent = new ClipboardEvent('paste', {
      bubbles: true,
      cancelable: true,
      clipboardData: dt,
    });
    return !rich.dispatchEvent(pasteEvent);
  });
  assert.equal(pasteHandled, true, 'The editor paste handler canceled the default action');
  await pasteSurface.getByText('Pasted safe text', { exact: false }).waitFor({ state: 'visible' });

  const secState = await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const xssTriggered = window.__xssExecuted === true;
    const scriptTags = rich ? rich.querySelectorAll('script').length : 0;
    const handlers = rich ? Array.from(rich.querySelectorAll('*')).filter((el) => el.getAttributeNames().some((name) => name.startsWith('on'))) : [];
    const isDisallowedUrl = (raw) => {
      const u = String(raw || '').trim().toLowerCase();
      return /^(?:javascript|vbscript|data|file):/.test(u);
    };
    const disallowedLinks = rich ? Array.from(rich.querySelectorAll('a')).filter((a) => isDisallowedUrl(a.getAttribute('href'))) : [];
    const disallowedImages = rich ? Array.from(rich.querySelectorAll('img')).filter((img) => isDisallowedUrl(img.getAttribute('src'))) : [];
    const isVisualActive = rich?.classList.contains('markdown-visual-mode') && getComputedStyle(rich).display !== 'none';
    return {
      xssTriggered,
      scriptTags,
      handlersCount: handlers.length,
      disallowedLinksCount: disallowedLinks.length,
      disallowedImagesCount: disallowedImages.length,
      isVisualActive,
    };
  });

  assert.equal(secState.isVisualActive, true, 'Visual ContentEditable surface must be active during security testing');
  assert.equal(secState.xssTriggered, false, 'No XSS payload executed on visual editor surface');
  assert.equal(secState.scriptTags, 0, 'No raw script tags admitted into visual editor DOM');
  assert.equal(secState.handlersCount, 0, 'No inline event handlers (onerror/onload) admitted');
  assert.equal(secState.disallowedLinksCount, 0, 'No disallowed scheme links (javascript, vbscript, data, file) permitted in visual editor');
  assert.equal(secState.disallowedImagesCount, 0, 'No disallowed scheme images (javascript, vbscript, data, file) permitted in visual editor');

  suiteResults.scenarios.push({
    id: 8,
    name: 'Security and sanitization',
    status: 'PASSED',
    durationMs: Date.now() - t8Start,
    details: 'Dispatched a synthetic ClipboardEvent and verified the handler inserted harmless text while stripping scripts, inline handlers and JavaScript URLs. The OS clipboard was outside scope.',
  });

  // -----------------------------------------------------------
  // Test 9: Responsive layout
  // -----------------------------------------------------------
  log('Test 9', 'Responsive layout across Desktop, Tablet, and Mobile viewports');
  const t9Start = Date.now();

  const t9DocRes = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Responsive Layout Test Doc',
      content: '# Responsive Title\n\nResponsive visual editor testing across desktop, tablet, and mobile.',
      language: 'markdown',
    },
  });
  const t9Doc = await t9DocRes.json();
  await page.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.loadDocument(id);
  }, t9Doc.id);
  await page.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible' });

  const viewports = [
    { name: 'Desktop', width: 1440, height: 900 },
    { name: 'Tablet', width: 768, height: 1024 },
    { name: 'Mobile', width: 390, height: 844 },
  ];

  for (const vp of viewports) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.waitForTimeout(300);

    const layout = await page.evaluate(() => {
      const panel = document.getElementById('doc-editor-pane');
      const rich = document.getElementById('doc-email-richbody');
      const scrollWidth = document.documentElement.scrollWidth;
      const clientWidth = document.documentElement.clientWidth;
      return {
        panelVisible: panel && getComputedStyle(panel).display !== 'none',
        richVisible: rich && getComputedStyle(rich).display !== 'none',
        hasHorizontalOverflow: scrollWidth > clientWidth + 2,
        richWidth: rich?.offsetWidth,
      };
    });

    assert.equal(layout.panelVisible, true, `Document panel visible in ${vp.name}`);
    assert.equal(layout.richVisible, true, `Visual editor visible in ${vp.name}`);
    assert.equal(layout.hasHorizontalOverflow, false, `No unexpected viewport overflow in ${vp.name}`);
  }

  // Restore desktop viewport
  await page.setViewportSize({ width: 1440, height: 900 });

  suiteResults.scenarios.push({
    id: 9,
    name: 'Responsive layout',
    status: 'PASSED',
    durationMs: Date.now() - t9Start,
    details: 'Verified clean rendering and absence of horizontal overflow across Desktop (1440x900), Tablet (768x1024), and Mobile (390x844).',
  });

  // -----------------------------------------------------------
  // Test 10: Accessibility
  // -----------------------------------------------------------
  log('Test 10', 'Accessibility attributes and keyboard navigation');
  const t10Start = Date.now();

  const a11y = await page.evaluate(() => {
    const rich = document.getElementById('doc-email-richbody');
    const saveBtn = document.getElementById('doc-footer-copy-btn');
    return {
      richRole: rich?.getAttribute('role'),
      richMultiline: rich?.getAttribute('aria-multiline'),
      saveBtnLabel: saveBtn?.getAttribute('aria-label'),
      saveBtnLive: saveBtn?.getAttribute('aria-live'),
      saveBtnTitle: saveBtn?.getAttribute('title'),
    };
  });

  assert.equal(a11y.richRole, 'textbox', 'Visual editor has role="textbox"');
  assert.equal(a11y.richMultiline, 'true', 'Visual editor has aria-multiline="true"');
  assert.ok(a11y.saveBtnLabel, 'Save button has aria-label');
  assert.equal(a11y.saveBtnLive, 'polite', 'Save button communicates state changes via aria-live="polite"');

  // Keyboard navigation: focus into rich editor
  await page.locator('#doc-email-richbody').focus();
  const isFocused = await page.evaluate(() => document.activeElement === document.getElementById('doc-email-richbody'));
  assert.equal(isFocused, true, 'Visual editor correctly receives keyboard focus');

  suiteResults.scenarios.push({
    id: 10,
    name: 'Accessibility attributes and focus',
    status: 'PASSED',
    durationMs: Date.now() - t10Start,
    details: 'Checked textbox role, multiline state, save button label and polite live region attributes, and that the visual editor receives focus.',
  });

  await context.close();
  suiteResults.chromiumStatus = 'PASSED';
} catch (err) {
  suiteResults.chromiumStatus = 'FAILED';
  suiteResults.chromiumFailure = err.message;
  writeResults();
  throw err;
} finally {
  await browser.close();
}

// -------------------------------------------------------------
// MULTI-BROWSER SMOKE VALIDATION: FIREFOX & WEBKIT
// -------------------------------------------------------------
log('Multi-Browser', 'Executing Firefox and WebKit smoke verification');

// Firefox Smoke Test
try {
  log('Firefox', 'Launching visual-admission smoke test');
  const ffBrowser = await firefox.launch({ headless: true });
  const ffContext = await ffBrowser.newContext({ viewport: { width: 1440, height: 900 } });
  await tolerateNoAuthNotificationLogs(ffContext);
  const ffPage = await ffContext.newPage();
  await ffPage.goto(origin, { waitUntil: 'domcontentloaded' });

  const ffDocRes = await ffContext.request.post(`${origin}/api/document`, {
    data: { title: 'Firefox Smoke Test', content: '* First\n* Second', language: 'markdown' },
  });
  const ffDoc = await ffDocRes.json();

  await ffPage.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.openPanel();
    await docMod.loadDocument(id);
  }, ffDoc.id);

  await ffPage.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible', timeout: 8000 });
  const ffRichVisible = await ffPage.evaluate(() => {
    const r = document.getElementById('doc-email-richbody');
    return r && getComputedStyle(r).display !== 'none';
  });
  assert.equal(ffRichVisible, true, 'Firefox renders visual mode');

  suiteResults.browserSmoke.firefox = { status: 'PASSED', engine: 'Gecko', version: ffBrowser.version() };
  await ffBrowser.close();
  log('Firefox', 'Smoke test PASSED');
} catch (err) {
  log('Firefox', `Smoke test failed: ${err.message}`);
  suiteResults.browserSmoke.firefox = { status: 'FAILED', error: err.message };
  process.exitCode = 1;
}

// WebKit Smoke Test
try {
  log('WebKit', 'Launching visual-admission smoke test');
  const wkBrowser = await webkit.launch({ headless: true });
  const wkContext = await wkBrowser.newContext({ viewport: { width: 1440, height: 900 } });
  await tolerateNoAuthNotificationLogs(wkContext);
  const wkPage = await wkContext.newPage();
  await wkPage.goto(origin, { waitUntil: 'domcontentloaded' });

  const wkDocRes = await wkContext.request.post(`${origin}/api/document`, {
    data: { title: 'WebKit Smoke Test', content: '* Alpha\n* Beta', language: 'markdown' },
  });
  const wkDoc = await wkDocRes.json();

  await wkPage.evaluate(async (id) => {
    const docMod = await import('/static/js/document.js');
    await docMod.openPanel();
    await docMod.loadDocument(id);
  }, wkDoc.id);

  await wkPage.waitForSelector('#doc-email-richbody.markdown-visual-mode', { state: 'visible', timeout: 8000 });
  const wkRichVisible = await wkPage.evaluate(() => {
    const r = document.getElementById('doc-email-richbody');
    return r && getComputedStyle(r).display !== 'none';
  });
  assert.equal(wkRichVisible, true, 'WebKit renders visual mode');

  suiteResults.browserSmoke.webkit = { status: 'PASSED', engine: 'WebKit (Safari engine)', version: wkBrowser.version() };
  await wkBrowser.close();
  log('WebKit', 'Smoke test PASSED');
} catch (err) {
  log('WebKit', `Smoke test failed: ${err.message}`);
  suiteResults.browserSmoke.webkit = { status: 'FAILED', error: err.message };
  process.exitCode = 1;
}

writeResults();

console.log('\n======================================================');
console.log('REAL APPLICATION PLAYWRIGHT VALIDATION COMPLETE');
console.log(`Passed Scenarios: ${suiteResults.scenarios.filter((s) => s.status === 'PASSED').length}/${suiteResults.scenarios.length}`);
console.log(`Firefox Smoke: ${suiteResults.browserSmoke.firefox?.status}`);
console.log(`WebKit Smoke: ${suiteResults.browserSmoke.webkit?.status}`);
console.log('======================================================\n');

if (suiteResults.scenarios.some((s) => s.status !== 'PASSED') || process.exitCode) {
  process.exit(1);
}
