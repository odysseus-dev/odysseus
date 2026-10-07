"""Browser regression coverage for document autosave state and ordering."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags


ROOT = Path(__file__).resolve().parents[1]


def test_save_status_is_dirty_race_safe_and_reports_failures():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
      const bodies = [];
      let active = 0;
      let maxActive = 0;
      let requests = 0;
      await page.route('**/api/document/status-doc', async route => {
        requests += 1;
        active += 1;
        maxActive = Math.max(maxActive, active);
        bodies.push(route.request().postDataJSON().content);
        if (requests === 1) await new Promise(resolve => setTimeout(resolve, 120));
        const fail = requests === 3;
        await route.fulfill({
          status: fail ? 500 : 200,
          contentType: 'application/json',
          body: JSON.stringify(fail ? { error: 'failed' } : { version_count: 1 }),
        });
        active -= 1;
      });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&save-status-test=1');
        mod.init('/api');
        mod.injectFreshDoc({ id: 'status-doc', title: 'Status', language: 'markdown', current_content: 'initial', version_count: 1 });
        await new Promise(resolve => setTimeout(resolve, 180));
        window.__documentModuleForTest = mod;
      });

      const initial = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => {
        const textarea = document.querySelector('#doc-editor-textarea');
        textarea.value = 'first edit';
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      });
      const dirty = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => {
        const textarea = document.querySelector('#doc-editor-textarea');
        window.__saveOne = window.__documentModuleForTest.saveDocument({ silent: true });
        textarea.value = 'newer edit';
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        window.__saveTwo = window.__documentModuleForTest.saveDocument({ silent: true });
      });
      await page.waitForTimeout(35);
      const duringSave = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      await page.evaluate(() => Promise.all([window.__saveOne, window.__saveTwo]));
      const afterSave = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');

      await page.evaluate(async () => {
        const textarea = document.querySelector('#doc-editor-textarea');
        textarea.value = 'failing edit';
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        await window.__documentModuleForTest.saveDocument({ silent: true });
      });
      const afterFailure = await page.locator('#doc-footer-copy-btn').getAttribute('data-save-state');
      const footer = await page.locator('#doc-actions-footer').evaluate(el => ({
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
      }));
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(50);
      const mobile = await page.locator('#doc-actions-footer').evaluate(el => ({
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
        labelDisplay: getComputedStyle(document.querySelector('.doc-save-button-label')).display,
      }));
      console.log(JSON.stringify({ initial, dirty, duringSave, afterSave, afterFailure, bodies, maxActive, footer, mobile }));
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
    assert data["initial"] == "saved"
    assert data["dirty"] == "dirty"
    assert data["duringSave"] == "saving"
    assert data["afterSave"] == "saved"
    assert data["afterFailure"] == "error"
    assert data["bodies"] == ["first edit", "newer edit", "failing edit"]
    assert data["maxActive"] == 1
    assert data["footer"]["scrollWidth"] <= data["footer"]["clientWidth"]
    assert data["mobile"]["scrollWidth"] <= data["mobile"]["clientWidth"]
    assert data["mobile"]["labelDisplay"] == "none"
