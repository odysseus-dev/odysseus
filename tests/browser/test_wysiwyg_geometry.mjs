import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { tolerateNoAuthNotificationLogs } from './realAppHelpers.mjs';
import { chromium } from 'playwright';

// No default: a missing origin must never fall back to a service already running on this machine.
const origin = process.env.ODYSSEUS_VALIDATION_ORIGIN;
assert.ok(origin, 'ODYSSEUS_VALIDATION_ORIGIN is required (scripts/run_real_playwright_validation.py starts an isolated server)');
const resultsDir = process.env.ODYSSEUS_VALIDATION_DIR || path.join(process.cwd(), 'artifacts', 'validation');
fs.mkdirSync(resultsDir, { recursive: true });
const report = { origin, profiles: [], failures: [] };
const browser = await chromium.launch({ headless: true });

function writeReport() {
  fs.writeFileSync(path.join(resultsDir, 'wysiwyg-geometry-results.json'), JSON.stringify(report, null, 2));
}

try {
  for (const profile of [
    { name: 'desktop', viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'no-preference' },
    { name: 'tablet', viewport: { width: 820, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'no-preference' },
    { name: 'mobile', viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, reducedMotion: 'no-preference', isMobile: true, hasTouch: true },
    { name: 'reduced-motion', viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, reducedMotion: 'reduce', isMobile: true, hasTouch: true },
  ]) {
    const { name: profileName, ...contextOptions } = profile;
    const context = await browser.newContext(contextOptions);
    await tolerateNoAuthNotificationLogs(context);
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(String(error?.message || error)));
    try {
      await page.goto(origin, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !document.getElementById('app-loader'));
      const content = Array.from({ length: 26 }, (_, index) => `Paragraph ${index + 1}: Geometry selection stays intact and follows the viewport.`).join('\n\n');
      const response = await page.request.post(`${origin}/api/document`, {
        data: { title: `Geometry ${profile.name}`, content, language: 'markdown' },
      });
      assert.ok(response.ok(), `${profile.name}: real document API create failed (${response.status()})`);
      const doc = await response.json();
      await page.evaluate(async id => {
        const app = await import('/static/js/document.js');
        await app.openPanel();
        await app.loadDocument(id);
      }, doc.id);
      const rich = page.locator('#doc-email-richbody.markdown-visual-mode');
      await rich.waitFor({ state: 'visible' });
      const target = rich.locator('p').nth(20);
      await target.scrollIntoViewIfNeeded();
      const textNode = await target.evaluate(el => {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        return walker.nextNode()?.nodeValue || '';
      });
      const phrase = 'Geometry selection';
      const offset = textNode.indexOf(phrase);
      assert.ok(offset >= 0, `${profile.name}: target text should be present`);

      // Use an actual mouse drag over a character range, followed by a real
      // Shift+Arrow selection. Record CSS viewport and device pixel dimensions
      // separately so high-DPI profiles are unambiguous.
      const rangeRect = await target.evaluate((el, rangeSpec) => {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        const text = walker.nextNode();
        const range = document.createRange();
        range.setStart(text, rangeSpec.start);
        range.setEnd(text, rangeSpec.end);
        const rects = Array.from(range.getClientRects());
        return rects.length ? { start: { x: rects[0].left + 1, y: rects[0].top + rects[0].height / 2 },
          end: { x: rects.at(-1).right - 1, y: rects.at(-1).top + rects.at(-1).height / 2 } } : null;
      }, { start: offset, end: offset + phrase.length });
      assert.ok(rangeRect, `${profile.name}: target selection geometry should exist`);
      await page.mouse.move(rangeRect.start.x, rangeRect.start.y);
      await page.mouse.down();
      await page.mouse.move(rangeRect.end.x, rangeRect.end.y, { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(60);
      assert.equal(await page.locator('#doc-rich-selection-toolbar').count(), 0,
        `${profile.name}: floating toolbar must not appear in Markdown visual mode`);
      assert.equal(await page.locator('#doc-selection-badge').count(), 0,
        `${profile.name}: selection badge must not appear during ordinary Markdown selection`);

      const snapshot = async label => page.evaluate(label => {
        const editor = document.getElementById('doc-email-richbody');
        const toolbar = document.getElementById('doc-rich-selection-toolbar');
        const badge = document.getElementById('doc-selection-badge');
        const rect = toolbar?.getBoundingClientRect();
        return {
          label,
          viewportCss: { width: innerWidth, height: innerHeight },
          devicePixels: { width: Math.round(innerWidth * devicePixelRatio), height: Math.round(innerHeight * devicePixelRatio) },
          devicePixelRatio,
          visualViewport: { left: visualViewport.offsetLeft, top: visualViewport.offsetTop,
            width: visualViewport.width, height: visualViewport.height, scale: visualViewport.scale },
          hasFloatingToolbar: Boolean(toolbar && toolbar.offsetParent !== null),
          hasSelectionBadge: Boolean(badge && badge.style.display !== 'none'),
          editor: (() => { const r = editor.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height, scrollTop: editor.scrollTop }; })(),
          pageScroll: { x: scrollX, y: scrollY },
          focus: document.activeElement?.id || document.activeElement?.tagName || null,
          selectedText: getSelection()?.toString(),
          text: editor.textContent,
        };
      }, label);
      const before = await snapshot('drag-selection-before');
      assert.equal(before.selectedText, phrase, `${profile.name}: actual drag should select the target text`);
      const assertVisualStability = state => {
        assert.equal(state.hasFloatingToolbar, false, `${profile.name}: floating toolbar should remain absent`);
        assert.equal(state.hasSelectionBadge, false, `${profile.name}: selection badge should remain absent`);
        assert.ok(state.editor.width > 0 && state.editor.height > 0, `${profile.name}: editor geometry should remain intact`);
      };
      assertVisualStability(before);

      // Resize and scroll the actual editor, verifying a selected range stays
      // selected and the document geometry remains stable without floating jitter.
      const resized = { width: profile.viewport.width - 40, height: profile.viewport.height - 60 };
      await page.setViewportSize(resized);
      await page.waitForTimeout(80);
      const afterResize = await snapshot('after-resize');
      assert.equal(afterResize.selectedText, phrase, `${profile.name}: resize must preserve selection`);
      assertVisualStability(afterResize);

      await rich.evaluate(el => { el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + 120); });
      await page.waitForTimeout(80);
      const afterScroll = await snapshot('after-editor-scroll');
      assert.equal(afterScroll.selectedText, phrase, `${profile.name}: scroll must preserve selection`);
      assertVisualStability(afterScroll);

      // Keyboard selection through real input events and formatting action
      // via the static toolbar verify formatting works without stealing/collapsing the range.
      await target.click();
      await page.keyboard.press('Home');
      for (let i = 0; i < 8; i++) await page.keyboard.press('Shift+ArrowRight');
      await page.waitForTimeout(50);
      const keyboardSelection = await snapshot('shift-arrow-selection');
      assert.ok(keyboardSelection.selectedText?.length > 0, `${profile.name}: Shift+Arrow should create selection`);
      await page.locator('#doc-md-toolbar [data-md="bold"]').click();
      const afterBold = await snapshot('after-bold');
      assert.equal(afterBold.text, before.text, `${profile.name}: formatting must not change visible text`);
      assert.equal(afterBold.selectedText, keyboardSelection.selectedText);
      assert.equal(afterBold.editor.top, keyboardSelection.editor.top);
      assert.equal(afterBold.editor.scrollTop, keyboardSelection.editor.scrollTop);
      assert.equal(afterBold.focus, 'doc-email-richbody');
      assert.deepEqual(pageErrors, [], `${profile.name}: page errors`);

      const profileResult = { name: profile.name, reducedMotion: profile.reducedMotion,
        configuredViewportCss: profile.viewport, configuredDeviceScaleFactor: profile.deviceScaleFactor,
        before, afterResize, afterScroll, keyboardSelection, afterBold };
      if (profileName === 'desktop') {
        const cdp = await context.newCDPSession(page);
        profileResult.zoom = [];
        const first = rich.locator('p').first();
        await first.scrollIntoViewIfNeeded();
        const acrossParagraphs = await rich.evaluate(root => {
          const [first, second] = root.querySelectorAll('p');
          const boxAt = (p, offset) => {
            const range = document.createRange();
            range.setStart(p.firstChild, offset);
            range.setEnd(p.firstChild, offset + 1);
            const rect = range.getBoundingClientRect();
            return { x: rect.left + 1, y: rect.top + rect.height / 2 };
          };
          return { start: boxAt(first, 0), middle: boxAt(first, 18), end: boxAt(second, 26) };
        });
        const beforeMulti = await snapshot('before-multiline-drag');
        await page.mouse.move(acrossParagraphs.start.x, acrossParagraphs.start.y);
        await page.mouse.down();
        await page.mouse.move(acrossParagraphs.middle.x, acrossParagraphs.middle.y, { steps: 6 });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await page.locator('#doc-rich-selection-toolbar').count(), 0);
        await page.mouse.move(acrossParagraphs.end.x, acrossParagraphs.end.y, { steps: 8 });
        await page.mouse.up();
        await page.waitForTimeout(60);
        assert.equal(await page.locator('#doc-rich-selection-toolbar').count(), 0);
        assert.equal(await page.locator('#doc-selection-badge').count(), 0);
        const multiline = await snapshot('multiple-paragraphs');
        assert.ok(multiline.selectedText.includes('Paragraph 1:') && multiline.selectedText.includes('Paragraph 2:'));
        assert.deepEqual(multiline.editor, beforeMulti.editor);
        assert.deepEqual(multiline.pageScroll, beforeMulti.pageScroll);
        assert.equal(multiline.focus, 'doc-email-richbody');
        profileResult.multiline = multiline;
        await page.screenshot({ path: path.join(resultsDir, 'wysiwyg-geometry-multiple-paragraphs.png') });
        await first.dblclick({ position: { x: 30, y: 10 } });
        await page.waitForTimeout(60);
        assert.equal(await page.locator('#doc-rich-selection-toolbar').count(), 0);
        assert.equal(await page.locator('#doc-selection-badge').count(), 0);
        const doubleClick = await snapshot('double-click');
        assert.ok(doubleClick.selectedText?.length > 0);
        profileResult.doubleClick = doubleClick;
        for (const scale of [1.25, 1.5]) {
          await cdp.send('Emulation.setPageScaleFactor', { pageScaleFactor: scale });
          await page.waitForFunction(scale => Math.abs(visualViewport.scale - scale) < 0.01, scale);
          await page.waitForTimeout(80);
          const zoom = await snapshot('viewport-page-scale');
          assert.equal(zoom.selectedText, doubleClick.selectedText);
          assert.equal(zoom.hasFloatingToolbar, false);
          profileResult.zoom.push(zoom);
        }
        await cdp.send('Emulation.setPageScaleFactor', { pageScaleFactor: 1 });
        await page.keyboard.press('Escape');
        const cleared = await snapshot('selection-cleared');
        assert.equal(cleared.selectedText, '');
        assert.equal(cleared.focus, 'doc-email-richbody');
        profileResult.cleared = cleared;
      }
      report.profiles.push(profileResult);
      await page.screenshot({ path: path.join(resultsDir, `wysiwyg-geometry-${profile.name}.png`), fullPage: false });
      console.log(`[${profile.name}] drag, resize, scroll and Shift+Arrow selection passed`);
    } catch (error) {
      report.failures.push({ profile: profile.name, error: String(error?.stack || error) });
      await page.screenshot({ path: path.join(resultsDir, `wysiwyg-geometry-${profile.name}-failure.png`), fullPage: false }).catch(() => {});
      throw error;
    } finally {
      await context.close();
      writeReport();
    }
  }
} finally {
  writeReport();
  await browser.close();
}
