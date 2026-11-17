"""Header-mode preservation across rich-text table structural edits."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_table_structure_uses_central_header_mode_normalization():
    helpers = DOC_JS.split("function _richTableHeaderModes", 1)[1].split(
        "function _replaceRichTable", 1
    )[0]
    append = function_body("_appendRichTableRow")
    actions = DOC_JS.split("function _applyRichTableAction", 1)[1].split(
        "function applyMdFormat", 1
    )[0]

    assert "headerRow:" in helpers
    assert "headerColumn:" in helpers
    assert "function _applyRichTableHeaderModes" in DOC_JS
    assert "const shouldBeHeader = (headerRow && rowIndex === 0) || (headerColumn && columnIndex === 0)" in helpers
    assert "_applyRichTableHeaderModes(clone, headerModes)" in append
    assert "_applyRichTableHeaderModes(clone, headerModes)" in actions
    assert "document.createElement('td')" in actions
    assert "row.cells[0]?.tagName === 'TH' ? 'th' : 'td'" not in actions


def test_mobile_structural_edits_preserve_header_modes_and_history():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&table-header-preservation-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'table-header-preservation-doc',
          title: 'Preserve table headers',
          language: 'richtext',
          current_content: '<table><tbody><tr><th>A</th><th>B</th></tr><tr><td>C</td><td>D</td></tr></tbody></table><p>After</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
      });

      async function focusCell(row, column, atEnd = false) {
        await page.evaluate(({ row, column, atEnd }) => {
          const cell = document.querySelector('#doc-email-richbody table').rows[row].cells[column];
          const range = document.createRange();
          range.selectNodeContents(cell);
          range.collapse(!atEnd);
          const selection = getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          cell.closest('[contenteditable]').focus();
        }, { row, column, atEnd });
      }
      async function tableAction(label) {
        await page.locator('[data-dd="table"]').focus();
        await page.keyboard.press('ArrowDown');
        await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: label }).click();
        await page.waitForTimeout(40);
      }
      async function tags() {
        return page.evaluate(() => Array.from(document.querySelectorAll('#doc-email-richbody tr')).map(row =>
          Array.from(row.cells).map(cell => cell.tagName)
        ));
      }
      async function undo() {
        await page.locator('#doc-undo-btn').click();
        await page.waitForTimeout(40);
        return tags();
      }

      const initial = await tags();
      await focusCell(0, 0);
      await tableAction('Add row below');
      const rowBelowHeader = await tags();
      const rowUndo = await undo();

      await focusCell(1, 0);
      await tableAction('Header column');
      const bothHeaders = await tags();

      await focusCell(1, 1, true);
      await page.keyboard.press('Tab');
      await page.waitForTimeout(40);
      const tabAppend = await tags();
      const tabUndo = await undo();

      await focusCell(1, 0);
      await tableAction('Add column right');
      const columnRight = await tags();
      const columnUndo = await undo();

      await focusCell(0, 0);
      await tableAction('Delete row');
      const deleteHeaderRow = await tags();
      const deleteRowUndo = await undo();

      await focusCell(0, 0);
      await tableAction('Delete column');
      const deleteHeaderColumn = await tags();
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({
        initial, rowBelowHeader, rowUndo, bothHeaders, tabAppend, tabUndo,
        columnRight, columnUndo, deleteHeaderRow, deleteRowUndo, deleteHeaderColumn, overflow,
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
    initial = [["TH", "TH"], ["TD", "TD"]]
    both = [["TH", "TH"], ["TH", "TD"]]
    assert data["initial"] == initial
    assert data["rowBelowHeader"] == [["TH", "TH"], ["TD", "TD"], ["TD", "TD"]]
    assert data["rowUndo"] == initial
    assert data["bothHeaders"] == both
    assert data["tabAppend"] == [["TH", "TH"], ["TH", "TD"], ["TH", "TD"]]
    assert data["tabUndo"] == both
    assert data["columnRight"] == [["TH", "TH", "TH"], ["TH", "TD", "TD"]]
    assert data["columnUndo"] == both
    assert data["deleteHeaderRow"] == [["TH", "TH"]]
    assert data["deleteRowUndo"] == both
    assert data["deleteHeaderColumn"] == [["TH"], ["TH"]]
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]
