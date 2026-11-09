import assert from 'node:assert/strict';
import { documentSource } from './helpers/document_source.mjs';
import { chromium } from 'playwright';

const source = documentSource();
const start = source.indexOf('  function _applySuggestions(');
const end = source.indexOf('  /** Animate transition to next suggestion */', start);
assert.ok(start >= 0 && end > start);
const applySource = source.slice(start, end);

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.setContent('<textarea id="doc-editor-textarea"></textarea><div id="doc-email-richbody"></div><iframe id="doc-html-preview"></iframe>');
  const results = await page.evaluate((fn) => {
    const textarea = document.getElementById('doc-editor-textarea');
    const rich = document.getElementById('doc-email-richbody');
    const docs = new Map();
    const activeDocId = 'test-doc';
    let saves = 0;
    const errors = [];
    const uiModule = { showError: message => errors.push(message) };
    const saveCurrentToMap = () => {
      const doc = docs.get(activeDocId);
      doc.content = doc.language === 'richtext' ? rich.innerHTML :
        doc.language === 'email' ? `To: person@example.com\n\n${rich.innerHTML}` : textarea.value;
    };
    const _isRichTextLang = lang => lang === 'richtext';
    const _showRichTextEditor = doc => { rich.innerHTML = doc.content; textarea.value = doc.content; };
    const _showEmailFields = doc => { rich.innerHTML = doc.content.split('\n\n').slice(1).join('\n\n'); };
    const _refreshMarkdownPreviewIfVisible = () => {};
    const _htmlPreviewActive = true;
    const _isRenderLang = lang => lang === 'svg';
    const _themedRenderSrcdoc = content => content;
    const syncHighlighting = () => {};
    const saveDocument = () => { saveCurrentToMap(); saves++; };
    return eval(fn + `
      const outcome = {};
      for (const language of ['richtext', 'email', 'markdown', 'javascript', 'svg']) {
        const original = language === 'email' ? 'To: person@example.com\\n\\n<p>old phrase</p>' :
          language === 'richtext' ? '<p>old phrase</p>' : 'old phrase';
        docs.set(activeDocId, { id: activeDocId, language, content: original });
        if (language === 'email' || language === 'richtext') rich.innerHTML = '<p>old phrase</p>';
        else textarea.value = original;
        const applied = _applySuggestions([{ id: 'one', find: 'old phrase', replace: 'new phrase' }]);
        outcome[language] = {
          applied, content: docs.get(activeDocId).content,
          preview: document.getElementById('doc-html-preview').srcdoc,
          visible: language === 'email' || language === 'richtext' ? rich.innerHTML : textarea.value,
        };
      }
      const before = saves;
      const unmatched = _applySuggestions([{ id: 'missing', find: 'absent phrase', replace: 'x' }]);
      ({ outcome, saves, before, unmatched, errors });
    `);
  }, applySource);
  for (const language of ['richtext', 'email', 'markdown', 'javascript', 'svg']) {
    assert.deepEqual(results.outcome[language].applied, ['one']);
    assert.match(results.outcome[language].content, /new phrase/);
    assert.match(results.outcome[language].visible, /new phrase/);
  }
  assert.match(results.outcome.svg.preview, /new phrase/);
  assert.equal(results.saves, 5);
  assert.equal(results.before, 5);
  assert.deepEqual(results.unmatched, []);
  assert.equal(results.errors.length, 1);
  console.log('PASS: accepting suggestions updates and saves rich text, email, markdown, and code documents.');
  console.log('PASS: stale suggestions remain pending and do not save an unchanged document.');
} finally {
  await browser.close();
}
