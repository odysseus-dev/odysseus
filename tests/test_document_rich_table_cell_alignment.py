"""Browser coverage for contextual Rich Text table-cell vertical alignment."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_table_cell_alignment_uses_contextual_undoable_table_path():
    actions = function_body("_applyRichTableAction")
    state = function_body("_richDropdownCurrentActions")

    for alignment in ("top", "middle", "bottom"):
        assert f"table:cell-align:{alignment}" in DOC_JS
    assert "target.style.verticalAlign = alignment" in actions
    assert "target.style.removeProperty('vertical-align')" in actions
    assert "current.add(`table:cell-align:" in state
    assert "tableCellAlignment" in DOC_JS


def test_mobile_table_cell_alignment_tracks_state_and_native_history():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&table-cell-alignment=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'table-cell-alignment-doc',
          title: 'Cell alignment',
          language: 'richtext',
          current_content: '<table><tbody><tr><th>Label</th><th>Value</th></tr><tr><th>Revenue</th><td><strong>20</strong><br>million</td></tr></tbody></table><p>After</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
      });

      async function focusValueCell() {
        await page.evaluate(() => {
          const rich = document.querySelector('#doc-email-richbody');
          const cell = rich.querySelector('table').rows[1].cells[1];
          rich.focus();
          const range = document.createRange();
          range.selectNodeContents(cell);
          range.collapse(true);
          const selection = getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        });
      }

      async function openTableMenu() {
        await page.locator('[data-dd="table"]').focus();
        await page.keyboard.press('ArrowDown');
        await page.waitForSelector('#doc-md-dd-menu');
      }

      async function currentAlignment() {
        return page.locator('#doc-md-dd-menu [aria-checked="true"]')
          .filter({ hasText: 'Align cell' }).textContent();
      }

      async function state() {
        return page.evaluate(() => {
          const rich = document.querySelector('#doc-email-richbody');
          const table = rich.querySelector('table');
          const cell = table.rows[1].cells[1];
          return {
            alignment: cell.style.verticalAlign,
            tags: Array.from(table.rows).map(row => Array.from(row.cells).map(item => item.tagName)),
            bold: cell.querySelector('strong')?.textContent || '',
            stored: document.querySelector('#doc-editor-textarea').value,
          };
        });
      }

      await focusValueCell();
      await openTableMenu();
      const initialCurrent = await currentAlignment();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Align cell middle' }).click();
      await page.waitForTimeout(60);
      const middle = await state();

      await focusValueCell();
      await openTableMenu();
      const middleCurrent = await currentAlignment();
      const menuRect = await page.locator('#doc-md-dd-menu').evaluate(menu => {
        const rect = menu.getBoundingClientRect();
        return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      });
      await page.keyboard.press('Escape');

      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(60);
      const undone = await state();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(60);
      const redone = await state();

      await focusValueCell();
      await openTableMenu();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Align cell top' }).click();
      await page.waitForTimeout(60);
      const top = await state();
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({
        initialCurrent, middle, middleCurrent, menuRect, undone, redone, top, overflow,
      }));
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

    tags = [["TH", "TH"], ["TH", "TD"]]
    assert "Align cell top" in data["initialCurrent"]
    assert data["middle"]["alignment"] == "middle"
    assert data["middle"]["tags"] == tags
    assert data["middle"]["bold"] == "20"
    assert 'style="vertical-align: middle;"' in data["middle"]["stored"]
    assert "Align cell middle" in data["middleCurrent"]
    assert data["undone"]["alignment"] == ""
    assert data["redone"]["alignment"] == "middle"
    assert data["top"]["alignment"] == ""
    assert "vertical-align" not in data["top"]["stored"]
    assert data["menuRect"]["left"] >= 8
    assert data["menuRect"]["right"] <= 382
    assert data["menuRect"]["top"] >= 8
    assert data["menuRect"]["bottom"] <= 836
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]
