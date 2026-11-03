"""Browser coverage for predictable Rich Text heading Enter behavior."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_heading_enter_uses_single_native_history_commands():
    helper = function_body("_handleRichHeadingEnter")

    assert "selection.isCollapsed" in helper
    assert "h1, h2, h3, h4, h5, h6" in helper
    assert "document.execCommand('defaultParagraphSeparator', false, 'p')" in helper
    assert "document.execCommand('insertParagraph')" in helper
    assert "document.execCommand('formatBlock', false, 'p')" in helper
    assert "headingHasContent && !caretAtEnd" in helper
    assert "_handleRichHeadingEnter(rich)" in DOC_JS


def test_mobile_heading_enter_exits_cleanly_and_is_one_step_undoable():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&heading-enter=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'heading-enter-doc',
          title: 'Heading Enter',
          language: 'richtext',
          current_content: '<h2><strong>Project title</strong></h2><p>Following paragraph</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
        const rich = document.querySelector('#doc-email-richbody');
        const heading = rich.querySelector('h2');
        rich.focus();
        const range = document.createRange();
        range.selectNodeContents(heading);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      });

      const rich = page.locator('#doc-email-richbody');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(50);
      const entered = await rich.evaluate(el => ({
        html: el.innerHTML,
        tags: Array.from(el.children).map(item => item.tagName),
        heading: el.querySelector('h2')?.innerHTML,
        stored: document.querySelector('#doc-editor-textarea').value,
        activeBlock: getSelection().anchorNode?.parentElement?.closest('p, h1, h2, h3, h4, h5, h6')?.tagName,
      }));

      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(60);
      const undone = await rich.innerHTML();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(60);
      const redone = await rich.innerHTML();

      console.log(JSON.stringify({ entered, undone, redone }));
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

    assert data["entered"]["tags"] == ["H2", "P", "P"]
    assert data["entered"]["heading"] == "<strong>Project title</strong>"
    assert data["entered"]["activeBlock"] == "P"
    assert data["entered"]["html"] == data["entered"]["stored"]
    assert data["undone"] == "<h2><strong>Project title</strong></h2><p>Following paragraph</p>"
    assert data["redone"] == data["entered"]["html"]


def test_heading_enter_preserves_shift_middle_and_empty_heading_semantics():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&heading-enter-boundaries=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'heading-enter-boundaries-doc',
          title: 'Heading boundaries',
          language: 'richtext',
          current_content: '<h3>Line break</h3><h4>Split here</h4><h5><br></h5>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
      });

      async function caret(tag, offset = null) {
        await page.evaluate(({ tag, offset }) => {
          const rich = document.querySelector('#doc-email-richbody');
          const block = rich.querySelector(tag);
          const range = document.createRange();
          rich.focus();
          if (offset === null) {
            range.selectNodeContents(block);
            range.collapse(false);
          } else if (block.firstChild?.nodeType === Node.TEXT_NODE) {
            range.setStart(block.firstChild, offset);
            range.collapse(true);
          } else {
            range.selectNodeContents(block);
            range.collapse(true);
          }
          const selection = getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        }, { tag, offset });
      }

      await caret('h3');
      await page.keyboard.press('Shift+Enter');
      await caret('h4', 5);
      await page.keyboard.press('Enter');
      await caret('h5', 0);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(60);

      const state = await page.locator('#doc-email-richbody').evaluate(el => ({
        h3: el.querySelector('h3')?.innerHTML,
        h4: Array.from(el.querySelectorAll('h4')).map(item => item.textContent),
        h5Count: el.querySelectorAll('h5').length,
        emptyExit: Array.from(el.children).at(-1)?.tagName,
        stored: document.querySelector('#doc-editor-textarea').value,
        html: el.innerHTML,
      }));
      console.log(JSON.stringify(state));
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

    assert "<br>" in data["h3"]
    assert [value.replace("\xa0", " ") for value in data["h4"]] == ["Split", " here"]
    assert data["h5Count"] == 0
    assert data["emptyExit"] == "P"
    assert data["stored"] == data["html"]
