"""Browser coverage for structure-safe find and replace in Rich Text documents."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_find_index_inserts_boundaries_without_flattening_inline_spans():
    section = function_body("_buildRichFindRanges")
    assert "const blockSelector = 'p,div,h1,h2,h3,h4,h5,h6,li,blockquote,pre,td,th'" in section
    assert "block !== previousBlock" in section
    assert "between.cloneContents().querySelector?.('br')" in section
    assert "if (startsNewSegment) fullText += '\\n';" in section


def test_find_rejects_cross_block_matches_but_supports_inline_matches_and_replacement():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&find-boundaries-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'find-boundaries-doc',
          title: 'Find boundaries',
          language: 'richtext',
          current_content: '<p>alpha end</p><p>start beta</p><p><strong>hel</strong><em>lo</em> world</p><p>line one<br>line two</p><p><strong>target</strong> tail</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
        document.querySelector('#doc-email-richbody').focus();
      });

      await page.keyboard.press('Control+f');
      const find = page.locator('#doc-find-input');
      const count = page.locator('#doc-find-count');

      await find.fill('endstart');
      const crossParagraph = await count.textContent();
      await find.fill('oneline');
      const crossBreak = await count.textContent();
      await find.fill('hello');
      const crossInline = await count.textContent();

      await find.fill('target');
      await page.locator('#doc-find-replace-toggle').click();
      await page.locator('#doc-replace-input').fill('updated');
      await page.locator('#doc-replace-current').click();

      const data = await page.locator('#doc-email-richbody').evaluate(el => {
        const persisted = document.createElement('template');
        persisted.innerHTML = document.querySelector('#doc-editor-textarea').value;
        return {
          paragraphs: el.querySelectorAll(':scope > p').length,
          first: el.querySelector(':scope > p:nth-of-type(1)')?.textContent,
          second: el.querySelector(':scope > p:nth-of-type(2)')?.textContent,
          inlineText: el.querySelector(':scope > p:nth-of-type(3)')?.textContent,
          updatedStrong: el.querySelector(':scope > p:nth-of-type(5) > strong')?.textContent,
          persistedParagraphs: Array.from(persisted.content.children).filter(child => child.tagName === 'P').length,
          persistedStrong: persisted.content.querySelectorAll('p')[4]?.querySelector('strong')?.textContent,
        };
      });

      console.log(JSON.stringify({ crossParagraph, crossBreak, crossInline, ...data }));
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
    assert data == {
        "crossParagraph": "0 results",
        "crossBreak": "0 results",
        "crossInline": "1 / 1",
        "paragraphs": 5,
        "first": "alpha end",
        "second": "start beta",
        "inlineText": "hello world",
        "updatedStrong": "updated",
        "persistedParagraphs": 5,
        "persistedStrong": "updated",
    }
