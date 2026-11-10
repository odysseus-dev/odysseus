"""Browser coverage for editable, undoable Rich Text image captions."""

import json
import subprocess
from pathlib import Path

from tests.helpers.stylesheets import app_css
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_image_caption_uses_semantic_figure_and_structured_export_paths():
    caption = function_body("_editRichImageCaption")
    converter = function_body("_docxFigureBlocks")

    assert "function _promptImageCaption" in DOC_JS
    assert "function _replaceRichImageFigure" in DOC_JS
    assert "document.createElement('figure')" in caption
    assert "captionElement.textContent = caption" in caption
    assert "replacement = replacementImage" in caption
    assert "_docxImageAlignment(image, docx)" in converter
    assert "caption.childNodes" in converter
    assert "figure.richtext-image .richtext-image-caption" in STYLE


def test_mobile_image_caption_survives_resize_history_and_empty_removal():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&image-caption=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'image-caption-doc',
          title: 'Image caption',
          language: 'richtext',
          current_content: '<p>Before</p><img class="richtext-image richtext-image-align-center" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mNk+M/wHwAF/gL+AvzZ8QAAAABJRU5ErkJggg==" alt="Diagram"><p>After</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
      });

      async function selectImage() {
        await page.locator('#doc-email-richbody img.richtext-image').click();
      }
      async function openImageMenu() {
        await page.locator('[data-dd="image"]').focus();
        await page.keyboard.press('ArrowDown');
        await page.waitForSelector('#doc-md-dd-menu');
      }
      async function editCaption(value) {
        await selectImage();
        await openImageMenu();
        await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Edit caption' }).click();
        await page.waitForSelector('#doc-image-caption-input');
        await page.locator('#doc-image-caption-input').fill(value);
        await page.locator('#doc-image-caption-ok').click();
        await page.waitForTimeout(70);
      }
      async function state() {
        return page.evaluate(() => {
          const rich = document.querySelector('#doc-email-richbody');
          const image = rich.querySelector('img.richtext-image');
          const figure = image?.closest('figure.richtext-image');
          const caption = figure?.querySelector(':scope > .richtext-image-caption');
          return {
            figure: Boolean(figure),
            caption: caption?.textContent || '',
            size35: image?.classList.contains('richtext-image-size-35') || false,
            alignCenter: image?.classList.contains('richtext-image-align-center') || false,
            selected: image?.hasAttribute('data-editor-image-selected') || false,
            stored: document.querySelector('#doc-editor-textarea').value,
          };
        });
      }

      await editCaption('Quarterly revenue');
      const added = await state();
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(70);
      const undone = await state();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(70);
      const redone = await state();

      await selectImage();
      await openImageMenu();
      const menuRect = await page.locator('#doc-md-dd-menu').evaluate(menu => {
        const rect = menu.getBoundingClientRect();
        return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      });
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Small' }).click();
      await page.waitForTimeout(70);
      const resized = await state();

      await editCaption('');
      const removed = await state();
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(70);
      const removalUndone = await state();
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({ added, undone, redone, resized, removed, removalUndone, menuRect, overflow }));
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

    assert data["added"]["figure"] is True
    assert data["added"]["caption"] == "Quarterly revenue", json.dumps(data, indent=2)
    assert data["added"]["alignCenter"] is True
    assert data["added"]["selected"] is True
    assert ">Quarterly revenue</div>" in data["added"]["stored"]
    assert "data-editor-image-selected" not in data["added"]["stored"]
    assert data["undone"]["figure"] is False
    assert data["redone"]["caption"] == "Quarterly revenue"
    assert data["resized"]["figure"] is True, json.dumps(data, indent=2)
    assert data["resized"]["caption"] == "Quarterly revenue"
    assert data["resized"]["size35"] is True
    assert data["removed"]["figure"] is False
    assert data["removed"]["caption"] == ""
    assert "richtext-image-caption" not in data["removed"]["stored"]
    assert data["removalUndone"]["figure"] is True
    assert data["removalUndone"]["caption"] == "Quarterly revenue"
    assert data["menuRect"]["left"] >= 8
    assert data["menuRect"]["right"] <= 382
    assert data["menuRect"]["top"] >= 8
    assert data["menuRect"]["bottom"] <= 836
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]
