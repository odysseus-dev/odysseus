"""Regression coverage for undoable keyboard navigation in rich-text tables."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_last_cell_tab_uses_the_undoable_table_replacement_path():
    helper = function_body("_appendRichTableRow")
    key_handler = DOC_JS.split("if (e.key === 'Tab')", 1)[1].split(
        "let inList = false", 1
    )[0]

    assert "const clone = original.cloneNode(true)" in helper
    assert "clone.insertRow(-1)" in helper
    assert "_replaceRichTable(rich, original, clone, clone.rows.length - 1, 0)" in helper
    assert "_appendRichTableRow(rich, table)" in key_handler
    assert "table.insertRow(-1)" not in key_handler
    assert "_scheduleDocumentHistoryControls()" in key_handler


def test_mobile_table_tab_navigation_row_creation_and_history():
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="chat-container"></div><div id="sidebar"></div>');
      await page.evaluate(async () => {
        const mod = await import('/static/js/document.js?v=20260831richtexttools91&table-tab-history-test=1');
        mod.init('/api');
        mod.injectFreshDoc({
          id: 'table-tab-history-doc',
          title: 'Table keyboard history',
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
      async function state() {
        return page.evaluate(() => {
          const selection = getSelection();
          const node = selection.anchorNode;
          const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
          const cell = element?.closest?.('td, th');
          return {
            rows: document.querySelectorAll('#doc-email-richbody tr').length,
            row: cell?.parentElement?.rowIndex ?? null,
            column: cell?.cellIndex ?? null,
            values: Array.from(document.querySelectorAll('#doc-email-richbody th, #doc-email-richbody td')).map(item => item.textContent),
            undoDisabled: document.querySelector('#doc-undo-btn').disabled,
            redoDisabled: document.querySelector('#doc-redo-btn').disabled,
          };
        });
      }

      await focusCell(1, 0);
      await page.keyboard.press('Tab');
      const forward = await state();
      await page.keyboard.press('Shift+Tab');
      const backward = await state();

      await focusCell(1, 1, true);
      await page.keyboard.press('Tab');
      await page.waitForTimeout(50);
      const appended = await state();
      await page.locator('#doc-undo-btn').click();
      await page.waitForTimeout(50);
      const undone = await state();
      await page.locator('#doc-redo-btn').click();
      await page.waitForTimeout(50);
      const redone = await state();
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));

      console.log(JSON.stringify({ forward, backward, appended, undone, redone, overflow }));
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
    assert (data["forward"]["rows"], data["forward"]["row"], data["forward"]["column"]) == (2, 1, 1)
    assert (data["backward"]["rows"], data["backward"]["row"], data["backward"]["column"]) == (2, 1, 0)
    assert (data["appended"]["rows"], data["appended"]["row"], data["appended"]["column"]) == (3, 2, 0)
    assert data["appended"]["values"] == ["A", "B", "C", "D", "", ""]
    assert data["appended"]["undoDisabled"] is False
    assert data["appended"]["redoDisabled"] is True
    assert data["undone"]["rows"] == 2
    assert data["undone"]["values"] == ["A", "B", "C", "D"]
    assert data["undone"]["undoDisabled"] is True
    assert data["undone"]["redoDisabled"] is False
    assert data["redone"]["rows"] == 3
    assert data["redone"]["values"] == ["A", "B", "C", "D", "", ""]
    assert data["redone"]["undoDisabled"] is False
    assert data["redone"]["redoDisabled"] is True
    assert data["overflow"]["scrollWidth"] == data["overflow"]["clientWidth"]
