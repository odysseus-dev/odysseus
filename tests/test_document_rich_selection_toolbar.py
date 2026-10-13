"""Browser coverage for contextual Rich Text selection formatting."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_selection_toolbar_uses_shared_formatting_path_and_preserves_range():
    assert 'id = \'doc-rich-selection-toolbar\'' in DOC_JS
    assert "_restoreRichSelectionToolbarRange(rich)" in DOC_JS
    assert "applyMdFormat(button.dataset.richSelectionAction)" in DOC_JS
    assert "toolbar.addEventListener('pointerdown', preserve)" in DOC_JS
    assert "liveSelection.collapseToEnd()" in DOC_JS
    assert "const isMobileViewport = viewportWidth <= 768;" in DOC_JS
    assert ".doc-rich-selection-toolbar" in STYLE


def test_selection_toolbar_formats_and_stays_inside_desktop_and_mobile_viewports():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&selection-toolbar-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'selection-toolbar-doc',
          title: 'Selection toolbar',
          language: 'richtext',
          current_content: '<p><strong>Bold words</strong> and plain text for formatting.</p><p>Second paragraph.</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 100));
      });

      await page.evaluate(() => {
        const text = document.querySelector('#doc-email-richbody strong').firstChild;
        const range = document.createRange();
        range.selectNodeContents(text);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
      });
      await page.waitForSelector('#doc-rich-selection-toolbar');
      const activeBold = await page.locator('[data-rich-selection-action="bold"]').evaluate(el => el.classList.contains('is-active'));
      const desktopGeometry = await page.evaluate(() => {
          const selected = getSelection().getRangeAt(0).getBoundingClientRect();
        const toolbar = document.querySelector('#doc-rich-selection-toolbar').getBoundingClientRect();
        return {
          within: toolbar.left >= 0 && toolbar.right <= innerWidth && toolbar.top >= 0 && toolbar.bottom <= innerHeight,
          separate: toolbar.bottom <= selected.top || toolbar.top >= selected.bottom,
        };
      });
      await page.locator('[data-rich-selection-action="italic"]').click();
      await page.waitForTimeout(40);
      const formattedHtml = await page.locator('#doc-email-richbody').innerHTML();
      const selectedAfterFormat = await page.evaluate(() => getSelection().toString());
      await page.locator('#doc-email-richbody').press('Escape');
      await page.waitForTimeout(30);
      const dismissed = await page.locator('#doc-rich-selection-toolbar').count() === 0;

      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(60);
      await page.evaluate(() => {
        const rich = document.querySelector('#doc-email-richbody');
        rich.focus({ preventScroll: true });
        const range = document.createRange();
        range.selectNodeContents(rich.querySelector('p'));
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
      });
      await page.waitForSelector('#doc-rich-selection-toolbar');
      await page.waitForTimeout(380);
      const mobileGeometry = await page.evaluate(() => {
        const selected = getSelection().getRangeAt(0).getBoundingClientRect();
        const toolbar = document.querySelector('#doc-rich-selection-toolbar').getBoundingClientRect();
        const editor = document.querySelector('#doc-email-richbody').getBoundingClientRect();
        const mainToolbar = document.querySelector('#doc-md-toolbar').getBoundingClientRect();
        return {
          left: toolbar.left,
          right: toolbar.right,
          top: toolbar.top,
          bottom: toolbar.bottom,
          width: toolbar.width,
          separate: toolbar.bottom <= selected.top || toolbar.top >= selected.bottom,
          dockedToEditorBottom: Math.abs(toolbar.bottom - editor.bottom + 4) < 1,
          insideEditorTop: toolbar.top >= editor.top,
          clearOfMainToolbar: toolbar.top >= mainToolbar.bottom || toolbar.bottom <= mainToolbar.top,
          viewport: [innerWidth, innerHeight],
        };
      });
      console.log(JSON.stringify({ activeBold, desktopGeometry, formattedHtml, selectedAfterFormat, dismissed, mobileGeometry }));
      await browser.close();
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["activeBold"] is True
    assert data["desktopGeometry"] == {"within": True, "separate": True}
    assert "<i>" in data["formattedHtml"] or "<em>" in data["formattedHtml"]
    assert data["selectedAfterFormat"] == "Bold words"
    assert data["dismissed"] is True
    mobile = data["mobileGeometry"]
    assert mobile["left"] >= 0 and mobile["right"] <= mobile["viewport"][0]
    assert mobile["top"] >= 0 and mobile["bottom"] <= mobile["viewport"][1]
    assert mobile["separate"] is True
    assert mobile["dockedToEditorBottom"] is True
    assert mobile["insideEditorTop"] is True
    assert mobile["clearOfMainToolbar"] is True
    assert mobile["width"] <= mobile["viewport"][0] - 16
