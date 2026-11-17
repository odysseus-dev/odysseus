"""Browser coverage for standard Rich Text document shortcuts."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags


ROOT = Path(__file__).resolve().parents[1]


def test_rich_document_shortcuts_work_at_desktop_and_mobile_widths():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });

      async function exercise(viewport, suffix) {
        const page = await browser.newPage({ viewport });
        await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
        await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
        await page.evaluate(async suffix => {
          const mod = await import(`/static/js/document.js?v=20260831richtexttools91&keyboard-shortcuts=${suffix}`);
          mod.init('/api');
          mod.injectFreshDoc({
            id: `keyboard-shortcuts-${suffix}`,
            title: 'Keyboard shortcuts',
            language: 'richtext',
            current_content: '<p>Heading target</p><p>Align target</p><p>Indent target</p><p>Strike target</p>',
            version_count: 1,
          });
          await new Promise(resolve => setTimeout(resolve, 400));
        }, suffix);

        async function selectText(text, collapse = true) {
          await page.evaluate(({ text, collapse }) => {
            const rich = document.querySelector('#doc-email-richbody');
            const walker = document.createTreeWalker(rich, NodeFilter.SHOW_ELEMENT);
            let block = null;
            while (walker.nextNode()) {
              if (walker.currentNode.textContent === text) {
                block = walker.currentNode;
                break;
              }
            }
            const range = document.createRange();
            range.selectNodeContents(block);
            if (collapse) range.collapse(true);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            rich.focus();
          }, { text, collapse });
        }

        await selectText('Heading target');
        await page.keyboard.press('Control+Alt+6');
        const headingTag = await page.locator('#doc-email-richbody').evaluate(root => root.firstElementChild.tagName);
        await page.locator('[data-dd="heading"]').click();
        const h6Current = await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Heading 6' }).getAttribute('aria-checked');
        await page.keyboard.press('Escape');
        await selectText('Heading target');
        await page.keyboard.press('Control+Alt+0');
        const paragraphTag = await page.locator('#doc-email-richbody').evaluate(root => root.firstElementChild.tagName);

        await selectText('Align target');
        await page.keyboard.press('Control+Shift+e');
        const center = await page.locator('#doc-email-richbody').evaluate(root => root.children[1].style.textAlign);
        await page.locator('[data-dd="align"]').click();
        await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Align right' }).click();
        const right = await page.locator('#doc-email-richbody').evaluate(root => root.children[1].style.textAlign);
        await page.keyboard.press('Control+Shift+j');
        const justify = await page.locator('#doc-email-richbody').evaluate(root => root.children[1].style.textAlign);
        await page.keyboard.press('Control+Shift+l');
        const left = await page.locator('#doc-email-richbody').evaluate(root => root.children[1].style.textAlign);

        await selectText('Indent target');
        await page.keyboard.press('Control+]');
        const indented = await page.locator('#doc-email-richbody').evaluate(root => !!Array.from(root.children).find(child => child.tagName === 'BLOCKQUOTE' && child.textContent === 'Indent target'));
        await page.keyboard.press('Control+[');
        const outdented = await page.locator('#doc-email-richbody').evaluate(root => Array.from(root.children).some(child => child.tagName === 'P' && child.textContent === 'Indent target'));

        await selectText('Strike target', false);
        await page.keyboard.press('Control+Shift+5');
        const struck = await page.locator('#doc-email-richbody').evaluate(root => !!root.querySelector('strike, s'));
        const geometry = await page.evaluate(() => ({
          viewport: document.documentElement.clientWidth,
          document: document.documentElement.scrollWidth,
        }));
        await page.close();
        return { headingTag, h6Current, paragraphTag, center, right, justify, left, indented, outdented, struck, geometry };
      }

      const desktop = await exercise({ width: 900, height: 700 }, 'desktop');
      const mobile = await exercise({ width: 390, height: 844 }, 'mobile');
      console.log(JSON.stringify({ desktop, mobile }));
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
    for layout in (data["desktop"], data["mobile"]):
        assert layout["headingTag"] == "H6"
        assert layout["h6Current"] == "true"
        assert layout["paragraphTag"] == "P"
        assert layout["center"] == "center"
        assert layout["right"] == "right"
        assert layout["justify"] == "justify"
        assert layout["left"] == "left"
        assert layout["indented"] is True
        assert layout["outdented"] is True
        assert layout["struck"] is True
        assert layout["geometry"]["document"] <= layout["geometry"]["viewport"]
