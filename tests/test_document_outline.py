"""Parser and browser coverage for document heading navigation."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def _run_node(script: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_markdown_outline_parses_structure_and_ignores_fenced_code():
    data = _run_node(
        r"""
        import { parseMarkdownOutline } from './static/js/documentOutline.js';
        const source = [
          '# Overview',
          '',
          '## [Linked section](https://example.com) ##',
          '',
          '```md',
          '# Not a real heading',
          '```',
          '',
          'Setext title',
          '------------',
          '',
          '#### Final `code` section',
        ].join('\n');
        console.log(JSON.stringify(parseMarkdownOutline(source)));
        """.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    )
    assert [(entry["level"], entry["text"]) for entry in data] == [
        (1, "Overview"),
        (2, "Linked section"),
        (2, "Setext title"),
        (4, "Final code section"),
    ]
    source_slice = data[1]["end"] - data[1]["start"]
    assert source_slice > len("Linked section")
    assert data[2]["line"] == 8


def test_outline_control_is_mode_aware_live_and_accessible():
    assert 'id="doc-outline-toolbar-btn"' in DOC_JS
    assert 'aria-haspopup="true"' in DOC_JS
    assert "language === 'markdown' || _isRichTextLang(language)" in DOC_JS
    assert "_refreshDocumentOutline();" in DOC_JS
    assert "bindMenuDismiss(menu" in DOC_JS
    assert ".doc-outline-menu" in STYLE
    assert "@media (max-width: 768px)" in STYLE


def test_outline_jumps_in_markdown_and_rich_text_and_fits_mobile():
    data = _run_node(
        r"""
        import { chromium } from 'playwright';
        const browser = await chromium.launch({ headless: true });
        const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
        await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
        await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
        await page.evaluate(async () => {
          const mod = await import('/static/js/document.js?v=20260831richtexttools91&outline-test=1');
          mod.init('/api');
          mod.injectFreshDoc({
            id: 'outline-md',
            title: 'Outline',
            language: 'markdown',
            current_content: '# Intro\n\nBody\n\n## Details\n\nMore\n\n### End',
            version_count: 1,
          });
          await new Promise(resolve => setTimeout(resolve, 100));
        });

        await page.locator('#doc-outline-toolbar-btn').click();
        const markdownLabels = await page.locator('.doc-outline-label').allTextContents();
        await page.keyboard.press('ArrowDown');
        await page.keyboard.press('Enter');
        const selected = await page.locator('#doc-editor-textarea').evaluate(el =>
          el.value.slice(el.selectionStart, el.selectionEnd));

        await page.locator('#doc-outline-toolbar-btn').click();
        await page.locator('#doc-editor-textarea').evaluate(el => {
          el.value += '\n\n#### Added live';
          el.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const liveLabels = await page.locator('.doc-outline-label').allTextContents();
        await page.keyboard.press('Escape');

        await page.setViewportSize({ width: 390, height: 844 });
        await page.locator('#doc-outline-toolbar-btn').click();
        const mobileBox = await page.locator('#doc-outline-menu').boundingBox();
        await page.keyboard.press('Escape');

        await page.evaluate(() => {
          window.documentModule.injectFreshDoc({
            id: 'outline-rich',
            title: 'Rich outline',
            language: 'richtext',
            current_content: '<h1>Rich start</h1><p>Body</p><h3>Rich details</h3>',
            version_count: 1,
          });
        });
        await page.waitForTimeout(100);
        await page.locator('#doc-outline-toolbar-btn').click();
        const richLabels = await page.locator('.doc-outline-label').allTextContents();
        await page.locator('.doc-outline-item').nth(1).click();
        const richCaretHeading = await page.evaluate(() => {
          const node = getSelection()?.anchorNode;
          const el = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
          return el?.closest('h1,h2,h3,h4,h5,h6')?.textContent || '';
        });

        console.log(JSON.stringify({ markdownLabels, selected, liveLabels, mobileBox, richLabels, richCaretHeading }));
        await browser.close();
        """.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    )
    assert data["markdownLabels"] == ["Intro", "Details", "End"]
    assert data["selected"] == "## Details"
    assert data["liveLabels"] == ["Intro", "Details", "End", "Added live"]
    box = data["mobileBox"]
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["y"] >= 0 and box["y"] + box["height"] <= 844
    assert data["richLabels"] == ["Rich start", "Rich details"]
    assert data["richCaretHeading"] == "Rich details"
