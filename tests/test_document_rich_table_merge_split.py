"""Browser coverage for undoable Rich Text table cell merge and split."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_merge_split_commands_use_contextual_undoable_table_path():
    actions = function_body("_applyRichTableAction")
    menu = DOC_JS.split("function _showMdDropdown", 1)[1].split(
        "function initMdToolbar", 1
    )[0]

    assert "function _richTableCanMergeRight" in DOC_JS
    assert "function _richTableCanSplitCell" in DOC_JS
    assert "function _mergeRichTableCellRight" in DOC_JS
    assert "function _splitRichTableCell" in DOC_JS
    assert "table:merge-right" in actions
    assert "table:split-cell" in actions
    assert "_applyRichTableHeaderModes(clone, headerModes)" in actions
    assert "_replaceRichTable(rich, original, clone" in actions
    assert "unavailableTableAction" in menu


def test_mobile_merge_split_round_trip_preserves_headers_formatting_and_history():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&table-merge-split=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'table-merge-split-doc',
          title: 'Merge and split',
          language: 'richtext',
          current_content: '<table><tbody><tr><th>Label</th><th>Q1</th><th>Q2</th></tr><tr><th>Revenue</th><td><strong>10</strong></td><td><em>20</em></td></tr></tbody></table><p>After</p>',
          version_count: 1,
        });
        await new Promise(resolve => setTimeout(resolve, 450));
      });

          async function focusCell(row, column) {
            await page.evaluate(({ row, column }) => {
              const rich = document.querySelector('#doc-email-richbody');
              const cell = rich.querySelector('table').rows[row].cells[column];
              const range = document.createRange();
              rich.focus();
              range.selectNodeContents(cell);
              range.collapse(true);
              const selection = getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
            }, { row, column });
          }
      async function openTableMenu() {
        await page.locator('button[data-dd="table"]').focus();
        await page.keyboard.press('ArrowDown');
        await page.waitForSelector('#doc-md-dd-menu');
      }
      async function menuDisabled(label) {
        return page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: label }).isDisabled();
      }
      async function state() {
        return page.evaluate(() => {
          const table = document.querySelector('#doc-email-richbody table');
          return {
            tags: Array.from(table.rows).map(row => Array.from(row.cells).map(cell => cell.tagName)),
            spans: Array.from(table.rows).map(row => Array.from(row.cells).map(cell => cell.colSpan)),
            text: Array.from(table.rows).map(row => Array.from(row.cells).map(cell => cell.textContent)),
            formatted: {
              bold: table.rows[1].cells[1]?.querySelector('strong')?.textContent || '',
              italic: table.rows[1].cells[1]?.querySelector('em')?.textContent || '',
              italicNext: table.rows[1].cells[2]?.querySelector('em')?.textContent || '',
            },
            stored: document.querySelector('#doc-editor-textarea').value,
          };
        });
      }

      await focusCell(1, 1);
      await openTableMenu();
      const initialMenu = {
        merge: await menuDisabled('Merge with right'),
        split: await menuDisabled('Split cell'),
      };
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Merge with right' }).click();
      await page.waitForTimeout(60);
      const merged = await state();

      await focusCell(1, 1);
      await openTableMenu();
      const mergedMenu = {
        merge: await menuDisabled('Merge with right'),
        split: await menuDisabled('Split cell'),
      };
      await page.keyboard.press('Escape');

      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(60);
      const undone = await state();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(60);
      const redone = await state();

      await focusCell(1, 1);
      await openTableMenu();
      await page.locator('#doc-md-dd-menu .doc-overflow-item').filter({ hasText: 'Split cell' }).click();
      await page.waitForTimeout(60);
      const split = await state();
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(60);
      const splitUndone = await state();

      await focusCell(0, 2);
      await openTableMenu();
      const lastCellMergeDisabled = await menuDisabled('Merge with right');
      const menuRect = await page.locator('#doc-md-dd-menu').evaluate(menu => {
        const rect = menu.getBoundingClientRect();
        return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      });
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({
        initialMenu, merged, mergedMenu, undone, redone, split, splitUndone,
        lastCellMergeDisabled, menuRect, overflow,
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

    initial_tags = [["TH", "TH", "TH"], ["TH", "TD", "TD"]]
    merged_tags = [["TH", "TH", "TH"], ["TH", "TD"]]
    assert data["initialMenu"] == {"merge": False, "split": True}
    assert data["merged"]["tags"] == merged_tags
    assert data["merged"]["spans"] == [[1, 1, 1], [1, 2]]
    assert data["merged"]["text"] == [["Label", "Q1", "Q2"], ["Revenue", "1020"]]
    assert data["merged"]["formatted"] == {"bold": "10", "italic": "20", "italicNext": ""}
    assert 'colspan="2"' in data["merged"]["stored"]
    assert data["mergedMenu"] == {"merge": True, "split": False}
    assert data["undone"]["tags"] == initial_tags
    assert data["undone"]["text"] == [["Label", "Q1", "Q2"], ["Revenue", "10", "20"]]
    assert data["redone"]["tags"] == merged_tags
    assert data["split"]["tags"] == initial_tags
    assert data["split"]["spans"] == [[1, 1, 1], [1, 1, 1]]
    assert data["split"]["text"] == [["Label", "Q1", "Q2"], ["Revenue", "10", "20"]]
    assert data["split"]["formatted"] == {"bold": "10", "italic": "", "italicNext": "20"}
    assert data["splitUndone"]["tags"] == merged_tags
    assert data["lastCellMergeDisabled"] is True
    assert data["menuRect"]["left"] >= 8
    assert data["menuRect"]["right"] <= 382
    assert data["menuRect"]["top"] >= 8
    assert data["menuRect"]["bottom"] <= 836
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]
