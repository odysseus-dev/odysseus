import assert from 'node:assert/strict';
import { documentSource } from './helpers/document_source.mjs';
import { chromium } from 'playwright';
import { appCss } from './helpers/stylesheets.mjs';

const source = documentSource();
const start = source.indexOf('  function _showCurrentSuggestion()');
const end = source.indexOf('  /** Show inline diff by modifying', start);
assert.ok(start >= 0 && end > start);
const renderSource = source.slice(start, end);

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 700, height: 500 } });
  await page.setContent('<div class="doc-editor-pane"><div id="doc-editor-wrap"></div></div>');
  await page.addStyleTag({ content: await appCss() });
  const result = await page.evaluate(code => {
    let _activeSuggestions = [];
    let _suggestionTotal = 0, _suggestionIndex = 0;
    let accepted = [];
    const _clearSuggestionHighlight = () => {};
    const topPortalZ = () => 10031;
    const _clearInlineDiff = () => {};
    const _clearSuggestionTextSelection = () => {};
    const _esc = value => String(value || '');
    const clearAllSuggestions = () => {};
    const _applySuggestions = suggestions => { accepted = suggestions.map(s => s.id); return accepted; };
    const _animateNext = () => {};
    return eval(code + `
      const inspect = count => {
        _activeSuggestions = Array.from({length:count}, (_, i) => ({id:String(i),find:'old'+i,replace:'new'+i,reason:'Reason'}));
        _suggestionTotal = count;
        _showCurrentSuggestion();
        const card = document.getElementById('doc-suggestion-active');
        const buttons = [...card.querySelectorAll('.doc-suggestion-actions button')];
        const cardRight = card.getBoundingClientRect().right;
        return {labels:buttons.map(button => button.textContent.trim()),
          fits:buttons.every(button => button.getBoundingClientRect().right <= cardRight + 1)};
      };
      const one = inspect(1);
      const many = inspect(3);
      document.querySelector('.doc-suggestion-accept-all').click();
      ({one, many, accepted});
    `);
  }, renderSource);
  assert.deepEqual(result.one.labels, ['Accept', 'Accept All', 'Skip']);
  assert.deepEqual(result.many.labels, ['Accept', 'Accept All', 'Skip']);
  assert.equal(result.one.fits, true);
  assert.equal(result.many.fits, true);
  assert.deepEqual(result.accepted, ['0', '1', '2']);
  console.log('PASS: Accept, Accept All, and Skip stay visible and fit in one row.');
} finally {
  await browser.close();
}
