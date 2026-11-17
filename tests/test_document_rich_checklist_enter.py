"""Browser coverage for Enter behavior inside Rich Text checklists."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_checklist_enter_uses_native_edit_commands_and_resets_state():
    section = DOC_JS.split("function _handleRichChecklistEnter", 1)[1].split(
        "let _richInlineCodeTypingArmed", 1
    )[0]
    assert "document.execCommand('insertParagraph')" in section
    assert "nextItem.dataset.checked = 'false'" in section
    assert "document.execCommand('outdent')" in section
    assert "document.execCommand('removeFormat')" in section
    assert "document.execCommand('formatBlock', false, 'p')" in section


def test_enter_creates_unchecked_task_and_empty_enter_exits_cleanly():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentOutline.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&checklist-enter-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'checklist-enter-doc',
          title: 'Checklist Enter',
          language: 'richtext',
          current_content: '<h2>Release</h2><ul class="rich-checklist"><li data-checked="true" aria-checked="true">Finished item</li></ul>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
        const item = document.querySelector('#doc-email-richbody li');
        const range = document.createRange();
        range.selectNodeContents(item);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        item.closest('[contenteditable]').focus();
      });

      await page.keyboard.press('Enter');
      const afterFirstEnter = await page.locator('#doc-email-richbody ul.rich-checklist li').evaluateAll(items =>
        items.map(item => ({ text: item.textContent, checked: item.dataset.checked }))
      );
      await page.keyboard.type('Next item');
      await page.keyboard.press('Enter');
      await page.keyboard.press('Enter');
      await page.keyboard.type('Outside the checklist');

      const data = await page.locator('#doc-email-richbody').evaluate(el => {
        const items = Array.from(el.querySelectorAll('ul.rich-checklist > li'));
        const outside = Array.from(el.querySelectorAll('p')).at(-1);
        const persisted = document.createElement('template');
        persisted.innerHTML = document.querySelector('#doc-editor-textarea').value;
        const persistedItems = Array.from(persisted.content.querySelectorAll('ul.rich-checklist > li'));
        return {
          items: items.map(item => ({ text: item.textContent, checked: item.dataset.checked })),
          outsideText: outside?.textContent,
          outsideHasFormatting: !!outside?.querySelector('font, strike, s, del'),
          selectionOutsideChecklist: !getSelection().anchorNode?.parentElement?.closest('ul.rich-checklist'),
          persistedItems: persistedItems.map(item => ({ text: item.textContent, checked: item.dataset.checked })),
          persistedOutside: Array.from(persisted.content.querySelectorAll('p')).at(-1)?.textContent,
          persistedOutsideHasFormatting: !!Array.from(persisted.content.querySelectorAll('p')).at(-1)?.querySelector('font, strike, s, del'),
          scrollWidth: document.body.scrollWidth,
          viewportWidth: innerWidth,
        };
      });
      console.log(JSON.stringify({ afterFirstEnter, ...data }));
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
    assert data["afterFirstEnter"] == [
        {"text": "Finished item", "checked": "true"},
        {"text": "", "checked": "false"},
    ]
    expected_items = [
        {"text": "Finished item", "checked": "true"},
        {"text": "Next item", "checked": "false"},
    ]
    assert data["items"] == expected_items
    assert data["persistedItems"] == expected_items
    assert data["outsideText"] == "Outside the checklist"
    assert data["persistedOutside"] == "Outside the checklist"
    assert data["outsideHasFormatting"] is False
    assert data["persistedOutsideHasFormatting"] is False
    assert data["selectionOutsideChecklist"] is True
    assert data["scrollWidth"] == data["viewportWidth"]
