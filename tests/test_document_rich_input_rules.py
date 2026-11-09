"""Browser coverage for Markdown-style block shortcuts in Rich Text documents."""

import json
import subprocess
from pathlib import Path

from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_input_rules_are_scoped_to_plain_blocks():
    for marker in ("#", "##", "###", "####", "-", "*", "1.", ">", "```", "- [ ]", "- [x]"):
        assert f"['{marker}', {{ action:" in DOC_JS
    assert "listItem.parentElement.classList.add('rich-checklist')" in DOC_JS
    assert "element.closest('pre, blockquote, td, th')" in DOC_JS
    assert "_applyRichBlockInputRule(rich)" in DOC_JS
    assert ".doc-email-richbody.richtext-mode ol > li" in STYLE
    assert "padding-inline-start: 0.2em" in STYLE


def test_typing_markers_converts_blocks_and_preserves_following_text():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&input-rules-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'input-rules-doc',
          title: 'Input rules',
          language: 'richtext',
          current_content: '<p><br></p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
      });

      async function focusNewBlock() {
        await page.evaluate(() => {
          const rich = document.querySelector('#doc-email-richbody');
          const block = document.createElement('p');
          block.innerHTML = '<br>';
          rich.appendChild(block);
          const range = document.createRange();
          range.selectNodeContents(block);
          range.collapse(true);
          const selection = getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          rich.focus();
        });
      }

      const rich = page.locator('#doc-email-richbody');
      await page.locator('#doc-email-richbody').focus();
      await page.keyboard.type('## Heading two');

      await focusNewBlock();
      await page.keyboard.type('- Bullet item');

      await focusNewBlock();
      await page.keyboard.type('1. Numbered item');

      await focusNewBlock();
      await page.keyboard.type('> Quoted text');

      await focusNewBlock();
      await page.keyboard.type('``` const answer = 42;');

      await focusNewBlock();
      await page.keyboard.type('- [x] Finished task');

      await focusNewBlock();
      await page.keyboard.type('Intro # remains text');

      const data = await rich.evaluate(el => {
        const persisted = document.createElement('template');
        persisted.innerHTML = document.querySelector('#doc-editor-textarea').value;
        return {
          heading: el.querySelector('h2')?.textContent,
          bullet: el.querySelector('ul:not(.rich-checklist) li')?.textContent,
          numbered: el.querySelector('ol li')?.textContent,
          quote: el.querySelector('blockquote')?.textContent,
          code: el.querySelector('pre')?.textContent,
          task: el.querySelector('ul.rich-checklist li')?.textContent,
          checked: el.querySelector('ul.rich-checklist li')?.dataset.checked,
          plain: Array.from(el.querySelectorAll('p')).at(-1)?.textContent,
          persistedHeading: persisted.content.querySelector('h2')?.textContent,
          persistedTask: persisted.content.querySelector('ul.rich-checklist li')?.textContent,
          persistedChecked: persisted.content.querySelector('ul.rich-checklist li')?.dataset.checked,
          invalidPersistedNesting: !!persisted.content.querySelector('p > h2, p > ul, p > ol, p > blockquote, p > pre'),
        };
      });
      console.log(JSON.stringify(data));
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
    assert json.loads(result.stdout) == {
        "heading": "Heading two",
        "bullet": "Bullet item",
        "numbered": "Numbered item",
        "quote": "Quoted text",
        "code": "const answer = 42;",
        "task": "Finished task",
        "checked": "true",
        "plain": "Intro # remains text",
        "persistedHeading": "Heading two",
        "persistedTask": "Finished task",
        "persistedChecked": "true",
        "invalidPersistedNesting": False,
    }
