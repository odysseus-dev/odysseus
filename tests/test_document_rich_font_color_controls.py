"""Numeric font sizes and the shared app color picker in Rich Text."""

import json
import re
import subprocess
from pathlib import Path

from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_font_and_color_controls_use_shared_components():
    assert re.search(r"import \{ attachColorPicker \} from './colorPicker\.js\?v=[A-Za-z0-9_-]+';", DOC_JS)
    assert 'data-dd="textsize" title="Font size" aria-label="Font size"' in DOC_JS
    for size, pixels in {1: 10, 2: 13, 3: 16, 4: 18, 5: 24, 6: 32, 7: 48}.items():
        assert f"{size}: {pixels}" in DOC_JS
    assert "slider.type = 'range'" in DOC_JS
    assert "slider.min = '1'" in DOC_JS
    assert "slider.max = '7'" in DOC_JS
    assert "applyMdFormat(`fontsize:${slider.value}`)" in DOC_JS
    assert "range.extractContents()" in DOC_JS
    assert "span.style.fontFamily = name" in DOC_JS
    assert "document.execCommand('fontName', false, name)" in DOC_JS
    assert "if (!sliderPointerActive) applySliderSize();" in DOC_JS
    assert "slider.addEventListener('change', applySliderSize);" in DOC_JS
    assert "valueLabel.type = 'number'" in DOC_JS
    assert "applyMdFormat(`fontsize:${pixels}px`)" in DOC_JS
    assert "attachColorPicker(customInput);" in DOC_JS
    assert "kind === 'color' || kind === 'highlight'" in DOC_JS
    assert "rich-highlight-swatch" in DOC_JS
    assert ".rich-highlight-swatch { border-radius: 50%; }" in STYLE


def test_email_preview_does_not_strip_rich_text_font_choices():
    assert "font-family: inherit !important;" not in STYLE[STYLE.index(".email-bubble-body *:not"):STYLE.index(".email-bubble-body *:not") + 220]
    assert "font-size: inherit !important;" not in STYLE[STYLE.index(".email-bubble-body *:not"):STYLE.index(".email-bubble-body *:not") + 220]


def test_horizontal_rule_is_ordered_after_clear_formatting():
    inline_color = DOC_JS.split("name: 'inline-color'", 1)[1].split("name: 'alignment'", 1)[0]
    assert inline_color.index("[data-dd=\"highlight\"]") < inline_color.index("#md-toolbar-sep-after-highlight")
    assert "[data-md=\"hr\"]" not in DOC_JS


def test_image_options_are_hidden_until_a_rich_image_is_selected():
    clear_fn = function_body("_clearRichImageSelection")
    select_fn = function_body("_selectRichImage")
    assert "imageButton.style.display = 'none';" in clear_fn
    assert "imageButton.style.display = '';" in select_fn


def test_rich_image_insert_button_uses_image_plus_icon():
    button = DOC_JS.split('id="md-toolbar-attach-btn"', 1)[1].split('</button>', 1)[0]
    assert '<rect x="3" y="3" width="18" height="18"' in button
    assert '<line x1="18" y1="4" x2="18" y2="10"' in button
    assert 'class="md-attach-paperclip-icon"' in button
    assert "paperclip.style.display = isEmail ? '' : 'none'" in DOC_JS
    assert "imageIcon.style.display = isEmail ? 'none' : ''" in DOC_JS


def test_selection_clear_formatting_only_shows_for_formatted_ranges():
    assert "function _richSelectionHasFormatting" in DOC_JS
    assert "clearButton.style.display = _richSelectionHasFormatting(rich, range) ? '' : 'none';" in DOC_JS
    assert "span[style]" in DOC_JS


def test_separator_is_a_style_dropdown_after_bullets_and_image_insert():
    assert "separator:solid" in DOC_JS
    assert "separator:dashed" in DOC_JS
    assert "separator:dotted" in DOC_JS
    assert "separator:double" in DOC_JS
    inline_color = DOC_JS.split("name: 'inline-color'", 1)[1].split("name: 'alignment'", 1)[0]
    paragraph = DOC_JS.split("name: 'paragraph'", 1)[1].split("name: 'inline-rich'", 1)[0]
    assert inline_color.index("#md-toolbar-sep-after-highlight") < inline_color.index("#md-toolbar-attach-btn")
    assert inline_color.index("#md-toolbar-attach-btn") < inline_color.index("#md-toolbar-inline-image-btn")
    assert paragraph.index("[data-dd=\"list\"]") < paragraph.index("[data-dd=\"separator\"]")
    assert "data-dd=\"separator\"" in DOC_JS
    assert ".rich-toolbar-color-input.cp-swatch-input" in STYLE
    assert ".rich-toolbar-range::-webkit-slider-thumb" in STYLE
    assert ".rich-toolbar-slider-value::-webkit-inner-spin-button" in STYLE
    assert "-webkit-appearance: textfield;" in STYLE
    assert "-webkit-appearance: none;" in STYLE


def test_numeric_font_size_and_custom_colors_work_on_desktop_and_mobile():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });

      async function exercise(viewport, suffix) {
        const page = await browser.newPage({ viewport });
        await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
        await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
        await page.evaluate(async suffix => {
          const mod = await import(`/static/js/document.js?v=20260831richtexttools91&font-color=${suffix}`);
          mod.init('/api');
          mod.injectFreshDoc({
            id: `font-color-${suffix}`,
            title: 'Font and color',
            language: 'richtext',
            current_content: '<p>Font target</p><p>Color target</p><p>Highlight target</p>',
            version_count: 1,
          });
        }, suffix);
        await page.waitForSelector('#doc-email-richbody p');

        async function selectParagraph(index) {
          await page.evaluate(index => {
            const rich = document.querySelector('#doc-email-richbody');
            const paragraph = rich.querySelectorAll('p')[index];
            if (!paragraph) throw new Error(`Missing paragraph ${index}: ${rich.innerHTML}`);
            rich.focus();
            const range = document.createRange();
            range.selectNodeContents(paragraph);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
          }, index);
        }

        async function openMenu(kind) {
          await page.locator(`[data-dd="${kind}"]`).focus();
          await page.keyboard.press('ArrowDown');
          await page.waitForSelector(`#doc-md-dd-menu[data-dd="${kind}"]`);
        }

        await selectParagraph(0);
        await openMenu('textsize');
        const initialSize = await page.locator('.rich-toolbar-range').evaluate(slider => ({
          value: slider.value,
          label: slider.getAttribute('aria-valuetext'),
          output: document.querySelector('.rich-toolbar-slider-value').value,
        }));
        await page.locator('.rich-toolbar-range').evaluate(slider => {
          slider.value = '5';
          slider.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await page.waitForTimeout(60);
        const sizeState = await page.evaluate(() => ({
          html: document.querySelector('#doc-email-richbody p').innerHTML,
          icon: document.querySelector('.rich-font-size-icon').textContent,
          stored: document.querySelector('#doc-editor-textarea').value,
          output: document.querySelector('.rich-toolbar-slider-value').value,
          menuOpen: Boolean(document.querySelector('#doc-md-dd-menu[data-dd="textsize"]')),
        }));
        await page.keyboard.press('Escape');

        await selectParagraph(1);
        await openMenu('color');
        const colorInputState = await page.locator('.rich-toolbar-color-input').evaluate(input => ({
          type: input.type,
          attached: input.dataset.cpAttached,
          label: input.getAttribute('aria-label'),
        }));
        await page.locator('.rich-toolbar-color-input').click();
        await page.locator('.cp-hex').fill('#123456');
        await page.waitForTimeout(60);
        const colorState = await page.evaluate(() => ({
          html: document.querySelectorAll('#doc-email-richbody p')[1].innerHTML,
          menuOpen: Boolean(document.querySelector('#doc-md-dd-menu[data-dd="color"]')),
          pickerOpen: Boolean(document.querySelector('.cp-popover')),
        }));
        const pickerRect = await page.locator('.cp-popover').evaluate(popover => {
          const rect = popover.getBoundingClientRect();
          return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
        });

        await page.mouse.click(4, viewport.height - 4);
        await selectParagraph(2);
        await openMenu('highlight');
        await page.locator('.rich-toolbar-color-input').click();
        await page.locator('.cp-hex').fill('#abcdef');
        await page.waitForTimeout(60);
        const highlightState = await page.evaluate(() => ({
          html: document.querySelectorAll('#doc-email-richbody p')[2].innerHTML,
          stored: document.querySelector('#doc-editor-textarea').value,
          pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        }));

        await page.close();
        return { initialSize, sizeState, colorInputState, colorState, pickerRect, highlightState };
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

    for state in data.values():
        assert state["initialSize"] == {
            "value": "2",
            "label": "13 pixels",
            "output": "13",
        }
        assert state["sizeState"]["html"] == '<font size="5">Font target</font>'
        assert state["sizeState"]["icon"] == "24"
        assert state["sizeState"]["output"] == "24"
        assert state["sizeState"]["menuOpen"] is True
        assert '<font size="5">Font target</font>' in state["sizeState"]["stored"]
        assert state["colorInputState"] == {
            "type": "text",
            "attached": "1",
            "label": "Custom text color",
        }
        assert state["colorState"]["html"] == '<font color="#123456">Color target</font>'
        assert state["colorState"]["menuOpen"] is True
        assert state["colorState"]["pickerOpen"] is True
        assert state["pickerRect"]["left"] >= 8
        assert state["pickerRect"]["right"] <= 892
        assert state["pickerRect"]["top"] >= 8
        assert state["pickerRect"]["bottom"] <= 836
        assert "Highlight target" in state["highlightState"]["html"]
        assert "background-color" in state["highlightState"]["html"]
        assert "#abcdef" in state["highlightState"]["stored"] or "rgb(171, 205, 239)" in state["highlightState"]["stored"]
        assert state["highlightState"]["pageOverflow"] == 0
