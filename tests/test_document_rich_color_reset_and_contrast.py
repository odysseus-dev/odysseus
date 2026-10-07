"""Rich text color reset, palette grouping, contrast, and undo coverage."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_color_controls_have_theme_reset_and_split_palettes():
    assert "forecolor:default" in DOC_JS
    assert "_applyRichDefaultTextColor" in DOC_JS
    assert "_applyRichHighlight" in DOC_JS
    assert "rich-color-palette-grid" in DOC_JS
    assert ".rich-color-palette-menu" in STYLE
    assert ".rich-color-palette-label" in STYLE


def test_rich_colors_follow_theme_and_undo_as_one_edit():
    # Two input conventions in here are platform-sensitive and must stay that
    # way. Palette entries are opened with a plain click: on macOS a
    # Control+click is delivered as `contextmenu`, so the menu item's `click`
    # handler never runs and nothing is applied. Undo uses Playwright's
    # `ControlOrMeta` alias because the editor's undo accelerator is Cmd+Z on
    # macOS and Ctrl+Z everywhere else.
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent(`<style>
        :root { --fg:#d8dee9; --bg:#17191f; --panel:#20232b; --border:#444; --red:#e45b6c; --accent-primary:#e45b6c; }
      </style>__ODY_STYLESHEETS__
      <div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>`);
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?rich-color-test=' + Date.now());
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'rich-color-test', title: 'Color controls', language: 'richtext',
          current_content: '<p>Highlight target</p><p><span style="color:#ff0000">Color target</span></p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 350));
      });
      const selectParagraph = async index => page.evaluate(index => {
        const rich = document.querySelector('#doc-email-richbody');
        const range = document.createRange();
        range.selectNodeContents(rich.querySelectorAll('p')[index]);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        rich.focus();
      }, index);
      const openMenu = async kind => {
        await page.locator(`[data-dd="${kind}"]`).click();
        await page.waitForSelector(`#doc-md-dd-menu[data-dd="${kind}"]`);
      };
      await selectParagraph(0);
      await openMenu('highlight');
      const palette = await page.evaluate(() => ({
        labels: [...document.querySelectorAll('.rich-color-palette-label')].map(item => item.textContent),
        reset: document.querySelector('.rich-color-reset')?.textContent.trim(),
      }));
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Lemon' }).click();
      const highlighted = await page.locator('#doc-email-richbody p').nth(0).locator('span').evaluate(span => ({
        color: getComputedStyle(span).color,
        background: getComputedStyle(span).backgroundColor,
      }));
      await page.locator('#doc-email-richbody').press('ControlOrMeta+z');
      const highlightUndone = await page.locator('#doc-email-richbody p').nth(0).innerHTML();

      await selectParagraph(1);
      await openMenu('color');
      await page.locator('.rich-color-reset').click();
      const defaultColor = await page.locator('#doc-email-richbody p').nth(1).locator('span').evaluate(span => ({
        style: span.getAttribute('style'),
        color: getComputedStyle(span).color,
      }));
      await page.evaluate(() => document.documentElement.style.setProperty('--fg', '#88cc44'));
      const changedThemeColor = await page.locator('#doc-email-richbody p').nth(1).locator('span').evaluate(span => getComputedStyle(span).color);
      await page.locator('#doc-email-richbody').press('ControlOrMeta+z');
      const colorUndone = await page.locator('#doc-email-richbody p').nth(1).innerHTML();
      console.log(JSON.stringify({ palette, highlighted, highlightUndone, defaultColor, changedThemeColor, colorUndone }));
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
    assert data["palette"] == {"labels": ["Dark", "Light"], "reset": "No highlight"}
    assert data["highlighted"] == {
        "color": "rgb(17, 24, 39)",
        "background": "rgb(254, 243, 199)",
    }
    assert data["highlightUndone"] == "Highlight target"
    assert "color: var(--fg)" in data["defaultColor"]["style"]
    assert data["changedThemeColor"] == "rgb(136, 204, 68)"
    assert data["colorUndone"] == '<span style="color:#ff0000">Color target</span>'
