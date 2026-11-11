import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const source = readFileSync(new URL('../static/js/document.js', import.meta.url), 'utf8');
const build = source.slice(source.indexOf('  function _buildAnnotation('), source.indexOf('  // Prompt user for an instruction'));
const state = source.slice(source.indexOf('  let _pdfAnnotationDragging'), source.indexOf('  // Per-doc last-used line spacing'));
const placement = source.slice(source.indexOf('      const isBlankPageTarget'), source.indexOf('      pane.appendChild(pageWrap);', source.indexOf('      const isBlankPageTarget')));
const browser = await chromium.launch({ headless: true });
try {
  for (const touch of [true, false]) {
    const page = await browser.newPage({ viewport: { width: 600, height: 800 }, hasTouch: touch });
    await page.setContent('<div id="page" style="position:relative;width:500px;height:700px;background:white"></div>');
    await page.evaluate(({ build, state, placement }) => {
      new Function(`
        const activeDocId = 'test', docId = activeDocId;
        const _pdfPaneAnnotationsByDoc = new Map([['test', []]]);
        const annotationRefs = _pdfPaneAnnotationsByDoc.get('test');
        const _pdfLastLineHeight = new Map(), _pdfLastFontSize = new Map();
        let _pdfDropMode = 'check', serial = 0;
        const _newAnnotationId = () => 'new-' + (++serial);
        const _pushPdfUndoSnapshot = () => {};
        const _schedulePdfPaneSave = () => {};
        const pageWrap = document.getElementById('page');
        const page = {page: 1};
        ${state}
        ${build}
        ${placement}
        for (let i = 0; i < 2; i++) {
          const {ref} = _buildAnnotation(pageWrap, {id:'a'+i, page:1, x:20+i*35, y:30, w:5, h:5, kind:'check'});
          annotationRefs.push(ref);
          ref._setHandlesVisible(true);
        }
        window.refs = annotationRefs;
      `)();
    }, { build, state, placement });
    const grip = page.locator('[data-ann-id="a0"] [title="Drag to move"]');
    const box = await grip.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(300, 220);
    assert(await page.evaluate(() => refs.every(ref => [...ref.wrap.querySelectorAll('[title]')].every(el => el.style.display === 'none'))));
    // Proximity updates must not bring controls back while dragging.
    await page.evaluate(() => refs.forEach(ref => ref._setHandlesVisible(true)));
    assert(await page.locator('[data-ann-id="a1"] [title="Delete annotation"]').isHidden());
    await page.mouse.up();
    assert.equal(await page.locator('.pdf-annotation-wrap').count(), 2);
    assert(await page.locator('[data-ann-id="a1"] [title="Delete annotation"]').isVisible());
    await page.locator('[data-ann-id="a1"] [title="Delete annotation"]').click();
    assert.equal(await page.locator('.pdf-annotation-wrap').count(), 1);
    await page.locator('#page').click({ position: { x: 100, y: 450 } });
    assert.equal(await page.locator('.pdf-annotation-wrap').count(), 2);
    console.log(`PASS ${touch ? 'touch' : 'mouse'}: drag controls hidden, delete only deletes, blank page still places`);
    await page.close();
  }
} finally {
  await browser.close();
}
