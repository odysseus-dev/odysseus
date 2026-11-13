"""Dedicated unit and browser integration tests for richTables ES module."""

import json
import subprocess
from pathlib import Path
from tests.helpers.stylesheets import stylesheet_link_tags
from tests.helpers.document_source import document_source, function_body

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()


def test_rich_tables_module_exports_and_function_declarations():
    """Verify richTables module provides all required functions."""
    assert "export function _richSelectionCell" in DOC_JS
    assert "export function _focusRichTableCell" in DOC_JS
    assert "export function _replaceRichTableCellTag" in DOC_JS
    assert "export function _richTableHeaderModes" in DOC_JS
    assert "export function _applyRichTableHeaderModes" in DOC_JS
    assert "export function _appendRichTableRow" in DOC_JS
    assert "export function _appendRichTableColumn" in DOC_JS
    assert "export function _deleteRichTableRow" in DOC_JS
    assert "export function _deleteRichTableColumn" in DOC_JS
    assert "export function _mergeRichTableCellRight" in DOC_JS
    assert "export function _mergeRichTableCellDown" in DOC_JS
    assert "export function _splitRichTableCell" in DOC_JS
    assert "export function _setRichTableCellAlignment" in DOC_JS
    assert "export function _insertRichTable" in DOC_JS
    assert "export function _replaceRichTable" in DOC_JS


def test_table_insertion_and_selection_boundaries():
    """Verify table insertion, cell focus, and selection boundary targeting in browser."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="container"><div id="rich" contenteditable="true"><p id="intro">Intro paragraph</p></div><div id="outside">Outside</div></div>');

      const result = await page.evaluate(async () => {
        const {
          _insertRichTable,
          _richSelectionCell,
          _focusRichTableCell,
        } = await import('/static/js/document/ui/richTables.js');

        const rich = document.getElementById('rich');
        const outside = document.getElementById('outside');

        // Initial selection check: outside editor
        const outsideRange = document.createRange();
        outsideRange.selectNodeContents(outside);
        outsideRange.collapse(true);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(outsideRange);
        const initialCell = _richSelectionCell(rich);

        // Selection inside intro paragraph (not in a table)
        const intro = document.getElementById('intro');
        const introRange = document.createRange();
        introRange.selectNodeContents(intro);
        introRange.collapse(true);
        sel.removeAllRanges();
        sel.addRange(introRange);
        const pCell = _richSelectionCell(rich);

        // Insert a 2x3 table
        rich.focus();
        sel.removeAllRanges();
        sel.addRange(introRange);
        const inserted = _insertRichTable(rich, 2, 3);

        const table = rich.querySelector('table');
        const hasToken = table.hasAttribute('data-editor-table-token');
        const rowCount = table.rows.length;
        const colCount = table.rows[0].cells.length;
        const row0Tags = Array.from(table.rows[0].cells).map(c => c.tagName);
        const row1Tags = Array.from(table.rows[1].cells).map(c => c.tagName);

        // Verify selection is now in first cell
        const selectedCell = _richSelectionCell(rich);
        const isFirstCell = selectedCell === table.rows[0].cells[0];

        // Focus second row, third column
        const targetCell = table.rows[1].cells[2];
        _focusRichTableCell(rich, targetCell);
        const focusedCell = _richSelectionCell(rich);
        const isTargetCell = focusedCell === targetCell;

        return {
          initialCell,
          pCell,
          hasTable: !!table,
          hasToken,
          rowCount,
          colCount,
          row0Tags,
          row1Tags,
          isFirstCell,
          isTargetCell,
        };
      });

      await browser.close();
      console.log(JSON.stringify(result));
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["initialCell"] is None
    assert data["pCell"] is None
    assert data["hasTable"] is True
    assert data["hasToken"] is False
    assert data["rowCount"] == 2
    assert data["colCount"] == 3
    assert data["row0Tags"] == ["TH", "TH", "TH"]
    assert data["row1Tags"] == ["TD", "TD", "TD"]
    assert data["isFirstCell"] is True
    assert data["isTargetCell"] is True


def test_row_and_column_additions_and_deletions():
    """Verify adding/deleting rows and columns, including single-cell table removal and undo."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="rich" contenteditable="true"><table><tbody><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></tbody></table><p>After</p></div>');

      const outcome = await page.evaluate(async () => {
        const {
          _appendRichTableRow,
          _appendRichTableColumn,
          _deleteRichTableRow,
          _deleteRichTableColumn,
        } = await import('/static/js/document/ui/richTables.js');

        const rich = document.getElementById('rich');
        const table = rich.querySelector('table');

        // 1. Append row below row 1
        const cell1 = table.rows[1].cells[0];
        _appendRichTableRow(rich, cell1, false);
        const t1 = rich.querySelector('table');
        const rowsAfterAddBelow = t1.rows.length;

        // 2. Append row above row 0
        const cell0 = t1.rows[0].cells[0];
        _appendRichTableRow(rich, cell0, true);
        const t2 = rich.querySelector('table');
        const rowsAfterAddAbove = t2.rows.length;

        // 3. Append column right of column 1
        const curCell = t2.rows[1].cells[1];
        _appendRichTableColumn(rich, curCell, false);
        const t3 = rich.querySelector('table');
        const colsAfterAddRight = t3.rows[0].cells.length;

        // 4. Append column left of column 0
        const firstColCell = t3.rows[1].cells[0];
        _appendRichTableColumn(rich, firstColCell, true);
        const t4 = rich.querySelector('table');
        const colsAfterAddLeft = t4.rows[0].cells.length;

        // 5. Delete row
        _deleteRichTableRow(rich, t4.rows[0].cells[0]);
        const t5 = rich.querySelector('table');
        const rowsAfterDelete = t5.rows.length;

        // 6. Delete column
        _deleteRichTableColumn(rich, t5.rows[0].cells[0]);
        const t6 = rich.querySelector('table');
        const colsAfterDelete = t6.rows[0].cells.length;

        // 7. Delete until 1x1 then delete last row
        while (rich.querySelector('table')?.rows.length > 1) {
          const currentTable = rich.querySelector('table');
          _deleteRichTableRow(rich, currentTable.rows[0].cells[0]);
        }
        while (rich.querySelector('table')?.rows[0].cells.length > 1) {
          const currentTable = rich.querySelector('table');
          _deleteRichTableColumn(rich, currentTable.rows[0].cells[0]);
        }
        const singleRowCell = rich.querySelector('table').rows[0].cells[0];
        _deleteRichTableRow(rich, singleRowCell);
        const tableAfterDeleteAll = rich.querySelector('table');

        // 8. Test undo restores table
        document.execCommand('undo');
        const tableAfterUndo = rich.querySelector('table');

        return {
          rowsAfterAddBelow,
          rowsAfterAddAbove,
          colsAfterAddRight,
          colsAfterAddLeft,
          rowsAfterDelete,
          colsAfterDelete,
          tableAfterDeleteAll: !!tableAfterDeleteAll,
          tableAfterUndo: !!tableAfterUndo,
        };
      });

      await browser.close();
      console.log(JSON.stringify(outcome));
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["rowsAfterAddBelow"] == 3
    assert data["rowsAfterAddAbove"] == 4
    assert data["colsAfterAddRight"] == 3
    assert data["colsAfterAddLeft"] == 4
    assert data["rowsAfterDelete"] == 3
    assert data["colsAfterDelete"] == 3
    assert data["tableAfterDeleteAll"] is False
    assert data["tableAfterUndo"] is True


def test_colspan_and_rowspan_cell_merge_and_split():
    """Verify horizontal and vertical cell merging, splitting, and cell alignment."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="rich" contenteditable="true"><table><tbody><tr><th>H1</th><th>H2</th><th>H3</th></tr><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>D</td><td>E</td><td>F</td></tr></tbody></table></div>');

      const outcome = await page.evaluate(async () => {
        const {
          _richTableCanMergeRight,
          _mergeRichTableCellRight,
          _richTableCanSplitCell,
          _splitRichTableCell,
          _richTableCanMergeDown,
          _mergeRichTableCellDown,
          _setRichTableCellAlignment,
        } = await import('/static/js/document/ui/richTables.js');

        const rich = document.getElementById('rich');
        let table = rich.querySelector('table');

        // 1. Horizontal merge: Cell (1, 0) with Cell (1, 1)
        const cellA = table.rows[1].cells[0];
        const canMergeRight = _richTableCanMergeRight(cellA);
        _mergeRichTableCellRight(rich, cellA);
        table = rich.querySelector('table');
        const mergedCell = table.rows[1].cells[0];
        const colSpanAfterMerge = mergedCell.colSpan;
        const cellCountRow1 = table.rows[1].cells.length;
        const textAfterMerge = mergedCell.textContent.replace(/\s+/g, '');

        // 2. Horizontal split
        const canSplitH = _richTableCanSplitCell(mergedCell);
        _splitRichTableCell(rich, mergedCell);
        table = rich.querySelector('table');
        const splitCellCountRow1 = table.rows[1].cells.length;
        const colSpanAfterSplit = table.rows[1].cells[0].colSpan;

        // 3. Vertical merge: Cell (1, 2) with Cell (2, 2)
        const cellC = table.rows[1].cells[2];
        const canMergeDown = _richTableCanMergeDown(cellC);
        _mergeRichTableCellDown(rich, cellC);
        table = rich.querySelector('table');
        const vMergedCell = table.rows[1].cells[2];
        const rowSpanAfterMerge = vMergedCell.rowSpan;
        const cellCountRow2AfterVMerge = table.rows[2].cells.length;

        // 4. Vertical split
        const canSplitV = _richTableCanSplitCell(vMergedCell);
        _splitRichTableCell(rich, vMergedCell);
        table = rich.querySelector('table');
        const vSplitCell = table.rows[1].cells[2];
        const rowSpanAfterSplit = vSplitCell.rowSpan;
        const cellCountRow2AfterVSplit = table.rows[2].cells.length;

        // 5. Alignment
        const alignCell = table.rows[1].cells[0];
        _setRichTableCellAlignment(rich, alignCell, 'middle');
        table = rich.querySelector('table');
        const middleAlign = table.rows[1].cells[0].style.verticalAlign;

        _setRichTableCellAlignment(rich, table.rows[1].cells[0], 'bottom');
        table = rich.querySelector('table');
        const bottomAlign = table.rows[1].cells[0].style.verticalAlign;

        _setRichTableCellAlignment(rich, table.rows[1].cells[0], 'top');
        table = rich.querySelector('table');
        const topAlign = table.rows[1].cells[0].style.verticalAlign;

        return {
          canMergeRight,
          colSpanAfterMerge,
          cellCountRow1,
          textAfterMerge,
          canSplitH,
          splitCellCountRow1,
          colSpanAfterSplit,
          canMergeDown,
          rowSpanAfterMerge,
          cellCountRow2AfterVMerge,
          canSplitV,
          rowSpanAfterSplit,
          cellCountRow2AfterVSplit,
          middleAlign,
          bottomAlign,
          topAlign,
        };
      });

      await browser.close();
      console.log(JSON.stringify(outcome));
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["canMergeRight"] is True
    assert data["colSpanAfterMerge"] == 2
    assert data["cellCountRow1"] == 2
    assert "AB" in data["textAfterMerge"]
    assert data["canSplitH"] is True
    assert data["splitCellCountRow1"] == 3
    assert data["colSpanAfterSplit"] == 1
    assert data["canMergeDown"] is True
    assert data["rowSpanAfterMerge"] == 2
    assert data["cellCountRow2AfterVMerge"] == 2
    assert data["canSplitV"] is True
    assert data["rowSpanAfterSplit"] == 1
    assert data["cellCountRow2AfterVSplit"] == 3
    assert data["middleAlign"] == "middle"
    assert data["bottomAlign"] == "bottom"
    assert data["topAlign"] == ""


def test_header_row_and_column_toggles():
    """Verify header row and header column detection and toggling via richTables API."""
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
      await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
      await page.setContent('__ODY_STYLESHEETS__<div id="toast"></div><div id="rich" contenteditable="true"><table><tbody><tr><th>Header 1</th><th>Header 2</th></tr><tr><td>Data 1</td><td>Data 2</td></tr></tbody></table></div>');

      const outcome = await page.evaluate(async () => {
        const {
          _richTableHeaderModes,
          _applyRichTableHeaderModes,
          _focusRichTableCell,
          _applyRichTableAction,
        } = await import('/static/js/document/ui/richTables.js');

        const rich = document.getElementById('rich');
        let table = rich.querySelector('table');

        // Initial mode: header row on, header col off
        const initialModes = _richTableHeaderModes(table);

        // Turn on header column as well
        _applyRichTableHeaderModes(table, { headerRow: true, headerColumn: true });
        const bothOnTags = Array.from(table.rows).map(r => Array.from(r.cells).map(c => c.tagName));
        const bothOnModes = _richTableHeaderModes(table);

        // Turn off header row
        _applyRichTableHeaderModes(table, { headerRow: false, headerColumn: true });
        const colOnlyTags = Array.from(table.rows).map(r => Array.from(r.cells).map(c => c.tagName));
        const colOnlyModes = _richTableHeaderModes(table);

        // Turn off both
        _applyRichTableHeaderModes(table, { headerRow: false, headerColumn: false });
        const noneTags = Array.from(table.rows).map(r => Array.from(r.cells).map(c => c.tagName));
        const noneModes = _richTableHeaderModes(table);

        // Test undoable action dispatch
        _focusRichTableCell(rich, table.rows[0].cells[0]);
        _applyRichTableAction(rich, 'table:toggle-header-row');
        table = rich.querySelector('table');
        const toggledRowModes = _richTableHeaderModes(table);

        _applyRichTableAction(rich, 'table:toggle-header-column');
        table = rich.querySelector('table');
        const toggledColModes = _richTableHeaderModes(table);

        return {
          initialModes,
          bothOnTags,
          bothOnModes,
          colOnlyTags,
          colOnlyModes,
          noneTags,
          noneModes,
          toggledRowModes,
          toggledColModes,
        };
      });

      await browser.close();
      console.log(JSON.stringify(outcome));
    """
    script = script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["initialModes"] == {"headerRow": True, "headerColumn": False}
    assert data["bothOnTags"] == [["TH", "TH"], ["TH", "TD"]]
    assert data["bothOnModes"] == {"headerRow": True, "headerColumn": True}
    assert data["colOnlyTags"] == [["TH", "TD"], ["TH", "TD"]]
    assert data["colOnlyModes"] == {"headerRow": False, "headerColumn": True}
    assert data["noneTags"] == [["TD", "TD"], ["TD", "TD"]]
    assert data["noneModes"] == {"headerRow": False, "headerColumn": False}
    assert data["toggledRowModes"]["headerRow"] is True
    assert data["toggledColModes"]["headerColumn"] is True
