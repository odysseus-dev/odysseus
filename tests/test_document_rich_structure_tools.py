"""Coverage for complete heading levels and rich-text page breaks."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import declaration, document_source, function_body

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_heading_levels_and_page_break_are_exposed_everywhere():
    slash = declaration("_RICH_SLASH_COMMANDS")
    rules = declaration("_RICH_BLOCK_INPUT_RULES")
    dropdown = DOC_JS.split("function _showMdDropdown", 1)[1].split(
        "function initMdToolbar", 1
    )[0]
    exporter = function_body("_richTextExportCss")

    for level in (5, 6):
        assert f"action: 'h{level}'" in slash
        assert f"['h{level}', 'Heading {level}', 'H{level}']" in dropdown
        assert f"h{level}: '{'#' * level} '" in DOC_JS
    assert "['#####', { action: 'h5' }]" in rules
    assert "['######', { action: 'h6' }]" in rules
    assert 'data-md="pagebreak"' in DOC_JS
    assert "action: 'pagebreak'" in slash
    assert "function _insertRichPageBreak" in DOC_JS
    assert "break-after:page" in exporter
    assert "page-break-after:always" in exporter
    assert ".doc-email-richbody.richtext-mode hr.richtext-page-break" in STYLE


def test_mobile_headings_page_break_history_and_persistence():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&structure-tools-test=1');
        window.__structureToolsDocModule = mod;
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'structure-tools-doc',
          title: 'Structure tools',
          language: 'richtext',
          current_content: '<p>First page</p><p>Section detail</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 400));
      });

      async function selectBlock(selector, atEnd = false) {
        await page.evaluate(({ selector, atEnd }) => {
          const block = document.querySelector(selector);
          const range = document.createRange();
          range.selectNodeContents(block);
          range.collapse(!atEnd);
          const selection = getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          block.closest('[contenteditable]').focus();
        }, { selector, atEnd });
      }

      await selectBlock('#doc-email-richbody p:last-child');
      await page.locator('[data-dd="heading"]').focus();
      await page.keyboard.press('ArrowDown');
      const menuLabels = await page.locator('#doc-md-dd-menu .doc-overflow-item').allTextContents();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Heading 6' }).click();
      await page.waitForTimeout(50);
      const headingTag = await page.locator('#doc-email-richbody').evaluate(root => root.lastElementChild.tagName);

      await page.evaluate(() => {
        const rich = document.querySelector('#doc-email-richbody');
        const paragraph = document.createElement('p');
        paragraph.innerHTML = '<br>';
        rich.appendChild(paragraph);
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(true);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        rich.focus();
      });
      await page.keyboard.type('#####');
      await page.keyboard.press('Space');
      await page.keyboard.type('Typed heading');
      const typedHeading = await page.locator('#doc-email-richbody').evaluate(root => ({
        tag: root.lastElementChild.tagName,
        text: root.lastElementChild.textContent,
      }));

      await selectBlock('#doc-email-richbody h5', true);
      await page.locator('[data-md="pagebreak"]').click();
      await page.waitForTimeout(50);
      const inserted = await page.evaluate(() => ({
        count: document.querySelectorAll('#doc-email-richbody hr.richtext-page-break').length,
        followingTag: document.querySelector('#doc-email-richbody hr.richtext-page-break')?.nextElementSibling?.tagName,
        stored: document.querySelector('#doc-editor-textarea').value,
      }));
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(50);
      const undoCount = await page.locator('#doc-email-richbody hr.richtext-page-break').count();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(50);
      const redoCount = await page.locator('#doc-email-richbody hr.richtext-page-break').count();

      await page.evaluate(() => {
        const last = document.querySelector('#doc-email-richbody p:last-child');
        last.textContent = '/page';
        const range = document.createRange();
        range.selectNodeContents(last);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        last.closest('[contenteditable]').focus();
        last.closest('[contenteditable]').dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.waitForSelector('#doc-rich-slash-menu');
      const slashLabels = await page.locator('#doc-rich-slash-menu .doc-rich-slash-label').allTextContents();
      const geometry = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        document: document.documentElement.scrollWidth,
      }));
      await page.evaluate(stored => {
        window.__structureToolsDocModule.injectFreshDoc({
          id: 'structure-tools-reload',
          title: 'Reloaded structure',
          language: 'richtext',
          current_content: stored,
          version_count: 1,
        });
      }, inserted.stored);
      await page.waitForTimeout(100);
      const reloadedCount = await page.locator('#doc-email-richbody hr.richtext-page-break').count();

      console.log(JSON.stringify({
        menuLabels, headingTag, typedHeading, inserted, undoCount, redoCount,
        slashLabels, geometry, reloadedCount,
      }));
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
    assert any("Heading 5" in label for label in data["menuLabels"])
    assert any("Heading 6" in label for label in data["menuLabels"])
    assert data["headingTag"] == "H6"
    assert data["typedHeading"] == {"tag": "H5", "text": "Typed heading"}
    assert data["inserted"]["count"] == 1
    assert data["inserted"]["followingTag"] == "P"
    assert 'class="richtext-page-break"' in data["inserted"]["stored"]
    assert "data-editor-page-break-token" not in data["inserted"]["stored"]
    assert data["undoCount"] == 0
    assert data["redoCount"] == 1
    assert data["reloadedCount"] == 1
    assert "Page break" in data["slashLabels"]
    assert data["geometry"]["document"] == data["geometry"]["viewport"]
