import assert from 'node:assert/strict';
import { documentSource } from './helpers/document_source.mjs';
import { chromium } from 'playwright';
const source = documentSource();
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  const rest = source.slice(start + 2);
  const next = rest.slice(10).search(/\n  (?:export )?(?:async )?function /);
  return rest.slice(0, next + 10);
}
const functions = ['_showRichTextEditor', '_syncEmailRichbody'].map(extract).join('\n');
const browser = await chromium.launch({headless:true});
try {
 const page = await browser.newPage();
 await page.setContent('<div class="doc-editor-pane"><div id="doc-editor-wrap"></div><textarea id="doc-editor-textarea"></textarea><div id="doc-email-richbody" contenteditable="true"></div></div>');
 const result = await page.evaluate(async (functions) => {
  const docs = new Map(); const activeDocId = 'fixture';
  let _richInlineCodeTypingArmed = false;
  const _clearRichImageSelection = () => {};
  const _normalizeRichTextImages = () => {};
  const _normalizeRichChecklists = () => {};
  const _normalizeRichInlineCode = () => {};
  const _wireEmailRichbody = () => {};
  const _syncRichEmptyImport = () => {};
  const _isRichTextLang = lang => lang === 'richtext';
  const _sanitizedRichTextHtml = rich => rich.innerHTML;
  const _richTextContentToHtml = content => content;
  const _emailRichbodyActive = () => document.getElementById('doc-email-richbody');
  const newContent = '<p>This sentence needs work.</p><p><strong>Keep this.</strong></p>';
  const doc = {id:activeDocId, language:'richtext',content:newContent}; docs.set(activeDocId,doc);
  eval(functions + '\n_showRichTextEditor(doc);');
  return {overlayGone:!document.querySelector('.doc-rich-diff-overlay'),rich:_emailRichbodyActive().innerHTML,mirror:document.getElementById('doc-editor-textarea').value,content:doc.content,newContent};
 }, functions);
 assert.equal(result.overlayGone, true);
 assert.equal(result.rich,result.newContent); assert.equal(result.mirror,result.newContent); assert.equal(result.content,result.newContent);
 const discardSource = source.slice(source.indexOf('  function exitDiffMode('), source.indexOf('  function isDiffModeActive('));
 const staleSaveCount = await page.evaluate(discardSource => {
  let _diffModeActive = true, _diffChunks = [], _diffOldContent = 'stale text', _diffNewContent = 'saved text', _diffUnresolvedCount = 1;
  let saves = 0;
  const saveDocument = () => { saves++; };
  const syncHighlighting = () => {};
  const updateLineNumbers = () => {};
  eval(discardSource + '\nexitDiffMode(true, { persist: false });');
  return saves;
 }, discardSource);
 assert.equal(staleSaveCount, 0);
 console.log('PASS: saved rich-text changes appear immediately with no transient diff overlay.');
 console.log('PASS: clearing a stale review diff cannot overwrite a newer saved edit.');
} finally {await browser.close();}
