import assert from 'node:assert/strict';
import { documentSource } from './helpers/document_source.mjs';
import { chromium } from 'playwright';

const source = documentSource();
const start = source.indexOf('  function clearSelection(');
const cleanup = source.slice(start, source.indexOf('  function clearSelectionAt(', start));
assert.equal((source.match(/if \(_selections.length\) clearSelection\(\{ preserveCaret: true \}\);/g) || []).length, 2);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.setContent('<div id="rich" contenteditable="true">before SELECT after</div><span id="doc-selection-badge"></span>');
  await page.evaluate(cleanup => {
    let _selections = [{ kind: 'rich' }];
    const _richSelectionHighlightName = 'test-selection';
    const rich = document.getElementById('rich');
    const _emailRichbodyActive = () => rich;
    const _scheduleDocumentStats = () => {};
    eval(cleanup + '\nwindow.clearPinned = clearSelection;');
    rich.addEventListener('input', () => {
      if (_selections.length) window.clearPinned({ preserveCaret: true });
    });
    rich.focus();
    const range = document.createRange();
    range.setStart(rich.firstChild, 7);
    range.setEnd(rich.firstChild, 13);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
  }, cleanup);
  await page.keyboard.type('new');
  const result = await page.evaluate(() => ({
    text: document.getElementById('rich').textContent,
    offset: window.getSelection().anchorOffset,
    collapsed: window.getSelection().isCollapsed,
    ranges: window.getSelection().rangeCount,
    badge: document.getElementById('doc-selection-badge').style.display,
  }));
  assert.equal(result.text, 'before new after');
  assert.equal(result.offset, 10);
  assert.equal(result.collapsed, true);
  assert.equal(result.ranges, 1);
  assert.equal(result.badge, 'none');
  await page.evaluate(() => window.clearPinned());
  assert.equal(await page.evaluate(() => window.getSelection().rangeCount), 0);
  console.log('PASS: replacement typing preserves caret; explicit clear still removes selection.');
} finally {
  await browser.close();
}
