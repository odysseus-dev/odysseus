"""Browser coverage for explicit Ctrl/Cmd+S document saves."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_save_shortcut_uses_manual_version_path_and_cancels_autosave():
    section = DOC_JS.split("pane.addEventListener('keydown'", 1)[1].split(
        "// Delete (or Backspace)", 1
    )[0]
    assert "mod && key === 's'" in section
    assert "e.preventDefault()" in section
    assert "clearTimeout(_autoSaveDebounce)" in section
    assert "clearTimeout(_emailRichbodySaveDebounce)" in section
    assert "forceVersion: doc?.language !== 'email'" in section
    assert "status.title = `${meta.title} (Ctrl+S)`" in DOC_JS
    assert 'class="doc-save-button-label">Saved</span>' in DOC_JS


def test_ctrl_s_saves_rich_text_immediately_once_and_updates_status():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const requests = [];
      await page.route('**/api/document/save-shortcut-doc', async route => {
        requests.push(route.request().postDataJSON());
        await new Promise(resolve => setTimeout(resolve, 40));
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ version_count: 2 }),
        });
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&save-shortcut-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'save-shortcut-doc',
          title: 'Save shortcut',
          language: 'richtext',
          current_content: '<p>Draft</p>',
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

      await page.keyboard.type(' updated');
      const dirty = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.keyboard.press('Control+s');
      await page.waitForTimeout(100);
      const saved = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      const badge = await page.locator('.doc-tab[data-doc-id="save-shortcut-doc"] .doc-tab-version').textContent();
      await page.waitForTimeout(2700);

      console.log(JSON.stringify({ dirty, saved, badge, requests }));
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
    assert data["dirty"] == "dirty"
    assert data["saved"] == "saved"
    assert data["badge"] == "v2"
    assert len(data["requests"]) == 1
    assert data["requests"][0]["force_version"] is True
    assert data["requests"][0]["summary"] == "Saved version"
    assert "<p>Draft updated</p>" in data["requests"][0]["content"]
