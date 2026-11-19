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

  // Create Doc A (Markdown) and Doc B (Markdown)
  const createA = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Selection Doc A',
      content: '# Document A\n\nFirst paragraph for selection context testing.\n\nSecond paragraph for AI editing.',
      language: 'markdown',
    },
  });
  assert.ok(createA.ok(), `create Doc A returned ${createA.status()}`);
  const docA = await createA.json();

  const createB = await context.request.post(`${origin}/api/document`, {
    data: {
      title: 'Selection Doc B',
      content: '# Document B\n\nTarget document for cross-document safety verification.',
      language: 'markdown',
    },
  });
  assert.ok(createB.ok(), `create Doc B returned ${createB.status()}`);
  const docB = await createB.json();

  // Load Doc A
  await page.evaluate(async id => {
    const app = await import('/static/js/document.js');
    await app.openPanel();
    await app.loadDocument(id);
  }, docA.id);

  const rich = page.locator('#doc-email-richbody.markdown-visual-mode');
  await rich.waitFor({ state: 'visible' });

  const selectText = async (targetText) => {
    await rich.evaluate((root, requested) => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const start = node.data.indexOf(requested);
        if (start < 0) continue;
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, start + requested.length);
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        root.focus();
        root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: 20, clientY: 20 }));
        return;
      }
      throw new Error(`Text not found: ${requested}`);
    }, targetText);
    await page.waitForTimeout(50);
  };

  // 1. Verify selection UI cleanliness in Markdown Visual mode
  await selectText('First paragraph for selection context');

  const uiState = await page.evaluate(() => {
    const floatingToolbar = document.getElementById('doc-rich-selection-toolbar');
    const badge = document.getElementById('doc-selection-badge');
    const clearButtons = document.querySelectorAll('.doc-selection-rich-clear');
    return {
      hasFloatingToolbar: !!floatingToolbar && floatingToolbar.style.display !== 'none',
      badgeVisible: !!badge && badge.style.display !== 'none' && !badge.hidden,
      clearButtonsCount: clearButtons.length,
    };
  });
  assert.equal(uiState.hasFloatingToolbar, false, 'no floating toolbar in Markdown Visual mode');
  assert.equal(uiState.badgeVisible, false, 'no selection badge in Markdown Visual mode');
  assert.equal(uiState.clearButtonsCount, 0, 'no floating clear buttons in Markdown Visual mode');

  // 2. Verify selection context retrieval for chat AI
  const selContext1 = await page.evaluate(async () => {
    const app = await import('/static/js/document.js');
    return app.getSelectionContext();
  });
  assert.ok(selContext1, 'getSelectionContext returned context');
  assert.equal(selContext1.kind, 'rich');
  assert.equal(selContext1.text, 'First paragraph for selection context');
  assert.equal(selContext1.docId, docA.id);
  assert.ok(selContext1.startLine >= 1, 'startLine is at least 1');
  assert.ok(selContext1.endLine >= selContext1.startLine, 'endLine is at least startLine');

  // Calling getSelectionContext again must return null (consumed)
  const selContextConsumed = await page.evaluate(async () => {
    const app = await import('/static/js/document.js');
    return app.getSelectionContext();
  });
  assert.equal(selContextConsumed, null, 'getSelectionContext consumed previous selection');

  // 3. Verify cross-document safety: selection from Doc A is not reused in Doc B
  await selectText('Second paragraph');
  // Confirm selection is captured
  const capturedForDocA = await page.evaluate(async () => {
    const { _selections } = await import('/static/js/document/ui/selection/selectionHighlights.js');
    return _selections.length > 0;
  });
  assert.ok(capturedForDocA, 'selection captured in Doc A');

  // Switch to Doc B
  await page.evaluate(async id => {
    const app = await import('/static/js/document.js');
    await app.loadDocument(id);
  }, docB.id);
  await rich.waitFor({ state: 'visible' });

  // In Doc B, getSelectionContext() without new selection must return null
  const staleContext = await page.evaluate(async () => {
    const app = await import('/static/js/document.js');
    return app.getSelectionContext();
  });
  assert.equal(staleContext, null, 'stale selection from Doc A dropped on document switch');

  // 4. Verify selection invalidation on edit
  await selectText('Target document');
  // Type an edit into rich editor
  await page.keyboard.press('End');
  await page.keyboard.type(' edited');
  await page.waitForTimeout(100);

  const contextAfterEdit = await page.evaluate(async () => {
    const app = await import('/static/js/document.js');
    return app.getSelectionContext();
  });
  // Since caret collapsed at End and text was typed, selection context is null
  assert.equal(contextAfterEdit, null, 'selection context null after caret collapse/typing');

  // 5. Verify Escape layering in Markdown Visual mode
  // Select text again
  await selectText('cross-document safety');
  const selBeforeEscape = await page.evaluate(() => window.getSelection()?.toString());
  assert.equal(selBeforeEscape, 'cross-document safety');

  // Open Heading dropdown
  await page.locator('#doc-md-toolbar [data-dd="heading"]').click();
  const ddMenu = page.locator('#doc-md-dd-menu');
  await ddMenu.waitFor({ state: 'visible' });

  // First Escape: must close dropdown menu, preserving selection
  await page.keyboard.press('Escape');
  await page.waitForTimeout(50);
  const ddMenuVisible = await ddMenu.isVisible();
  assert.equal(ddMenuVisible, false, 'first Escape closes dropdown menu');
  const selAfterDropdownEscape = await page.evaluate(() => window.getSelection()?.toString());
  assert.equal(selAfterDropdownEscape, 'cross-document safety', 'selection preserved after closing dropdown');

  // Second Escape: must collapse selection since no layered UI is open
  await page.keyboard.press('Escape');
  await page.waitForTimeout(50);
  const selAfterSecondEscape = await page.evaluate(() => {
    const sel = window.getSelection();
    return sel ? sel.isCollapsed : true;
  });
  assert.equal(selAfterSecondEscape, true, 'second Escape collapses selection');

  console.log('test_markdown_selection_context passed successfully');
} finally {
  await browser.close();
}
