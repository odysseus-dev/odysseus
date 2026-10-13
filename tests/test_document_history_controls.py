"""Browser coverage for native rich-text undo and redo controls."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_history_buttons_start_disabled_and_follow_native_history():
    assert 'id="doc-undo-btn"' in DOC_JS
    assert 'aria-label="Undo" aria-disabled="true" disabled' in DOC_JS
    assert 'aria-label="Redo" aria-disabled="true" disabled' in DOC_JS
    assert "document.queryCommandEnabled('undo')" in DOC_JS
    assert "document.queryCommandEnabled('redo')" in DOC_JS
    assert "_setDocumentHistoryControlState(false, false);" in DOC_JS


def test_mobile_rich_text_history_state_and_document_switch():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&history-controls-test=1');
        window.documentModuleForTest = mod;
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'history-doc',
          title: 'History',
          language: 'richtext',
          current_content: '<p>Start</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
        const paragraph = document.querySelector('#doc-email-richbody p');
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        paragraph.closest('[contenteditable]').focus();
      });

      const state = () => page.evaluate(() => ({
        html: document.querySelector('#doc-email-richbody').innerHTML,
        undoDisabled: document.querySelector('#doc-undo-btn').disabled,
        redoDisabled: document.querySelector('#doc-redo-btn').disabled,
        undoAria: document.querySelector('#doc-undo-btn').getAttribute('aria-disabled'),
        redoAria: document.querySelector('#doc-redo-btn').getAttribute('aria-disabled'),
        undoOpacity: Number.parseFloat(getComputedStyle(document.querySelector('#doc-undo-btn')).opacity),
        redoOpacity: Number.parseFloat(getComputedStyle(document.querySelector('#doc-redo-btn')).opacity),
      }));

      await page.waitForTimeout(50);
      const initial = await state();
      await page.keyboard.type('!');
      await page.waitForTimeout(150);
      const typed = await state();
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(150);
      const undone = await state();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(150);
      const redone = await state();
      await page.evaluate(() => window.documentModuleForTest.injectFreshDoc({
        id: 'history-second',
        title: 'Second',
        language: 'richtext',
        current_content: '<p>Second</p>',
        version_count: 1,
      }));
      await page.waitForTimeout(50);
      const switched = await state();
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({ initial, typed, undone, redone, switched, overflow }));
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
    assert data["initial"]["undoDisabled"] is True
    assert data["initial"]["redoDisabled"] is True
    assert data["typed"]["html"] == "<p>Start!</p>"
    assert data["typed"]["undoDisabled"] is False
    assert data["typed"]["redoDisabled"] is True
    assert data["typed"]["undoOpacity"] - data["typed"]["redoOpacity"] >= 0.4
    assert data["undone"]["html"] == "<p>Start</p>"
    assert data["undone"]["undoAria"] == "true"
    assert data["undone"]["redoAria"] == "false"
    assert data["undone"]["redoOpacity"] - data["undone"]["undoOpacity"] >= 0.4
    assert data["redone"]["html"] == "<p>Start!</p>"
    assert data["redone"]["undoAria"] == "false"
    assert data["redone"]["redoAria"] == "true"
    assert data["switched"]["html"] == "<p>Second</p>"
    assert data["switched"]["undoDisabled"] is True
    assert data["switched"]["redoDisabled"] is True
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]
