// static/js/document/richTables.js
/**
 * Rich Text Table Manipulation Engine for Odysseus Document Editor.
 * Provides selection targeting, header toggling, row/column operations,
 * cell merge/split, alignment, and undoable table replacement.
 */

let _deps = {};

export function initRichTables(options = {}) {
  _deps = { ...options };
}

function _showToast(message) {
  if (_deps.showToast) {
    _deps.showToast(message);
  } else if (typeof window !== 'undefined' && window.uiModule?.showToast) {
    window.uiModule.showToast(message);
  }
}

/**
 * Identify the table cell containing the current selection inside rich editor.
 */
export function _richSelectionCell(rich) {
  if (!rich) return null;
  const selection = window.getSelection();
  if (!selection?.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!rich.contains(range.commonAncestorContainer)) return null;
  const node = range.startContainer;
  const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  const cell = element?.closest?.('td, th');
  return cell && rich.contains(cell) ? cell : null;
}

/**
 * Focus and place the cursor at the start of a table cell.
 */
export function _focusRichTableCell(rich, cell) {
  if (!cell) return;
  rich?.focus?.();
  const range = document.createRange();
  range.selectNodeContents(cell);
  range.collapse(true);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

/**
 * Replace a cell element's tag (e.g. td <-> th) while preserving attributes and innerHTML.
 */
export function _replaceRichTableCellTag(cell, tagName) {
  if (!cell || cell.tagName.toLowerCase() === tagName.toLowerCase()) return cell;
  const replacement = document.createElement(tagName);
  for (const attr of Array.from(cell.attributes)) {
    replacement.setAttribute(attr.name, attr.value);
  }
  replacement.innerHTML = cell.innerHTML;
  cell.replaceWith(replacement);
  return replacement;
}

/**
 * Detect whether the table's first row is all TH and first column is all TH.
 */
export function _richTableHeaderModes(table) {
  const firstRow = table?.rows?.[0];
  return {
    headerRow: !!firstRow?.cells?.length && Array.from(firstRow.cells).every(item => item.tagName === 'TH'),
    headerColumn: !!table?.rows?.length && Array.from(table.rows).every(row => row.cells[0]?.tagName === 'TH'),
  };
}

/**
 * Apply header row and column modes to all cells in a table.
 */
export function _applyRichTableHeaderModes(table, { headerRow, headerColumn }) {
  Array.from(table?.rows || []).forEach((row, rowIndex) => {
    Array.from(row.cells).forEach((cell, columnIndex) => {
      const shouldBeHeader = (headerRow && rowIndex === 0) || (headerColumn && columnIndex === 0);
      _replaceRichTableCellTag(cell, shouldBeHeader ? 'th' : 'td');
    });
  });
}

/**
 * Replace a table in the rich text editor using native document.execCommand('insertHTML'),
 * preserving native undo/redo history and restoring caret position.
 */
export function _replaceRichTable(rich, original, replacement, targetRow = 0, targetCell = 0) {
  const token = `doc-table-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  if (replacement) replacement.dataset.editorTableToken = token;
  const range = document.createRange();
  // Selecting the table node itself lets the browser merge the inserted table into a
  // block quote that directly follows it (the table ends up inside the quote). Selecting
  // its contents replaces the table in place; removing a table still selects the node.
  if (replacement) range.selectNodeContents(original);
  else range.selectNode(original);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  const html = replacement ? replacement.outerHTML : '<p><br></p>';
  const applied = document.execCommand('insertHTML', false, html);
  if (!applied) return false;
  if (!replacement) {
    rich?.focus?.();
    return true;
  }
  const inserted = rich.querySelector(`[data-editor-table-token="${token}"]`);
  if (!inserted) return false;
  inserted.removeAttribute('data-editor-table-token');
  const row = inserted.rows[Math.max(0, Math.min(targetRow, inserted.rows.length - 1))];
  const cell = row?.cells[Math.max(0, Math.min(targetCell, row.cells.length - 1))];
  _focusRichTableCell(rich, cell);
  return true;
}

/**
 * Check if a cell contains visible text or embedded media.
 */
export function _richTableCellHasContent(cell) {
  return !!cell?.textContent?.trim() || !!cell?.querySelector?.('img, table, hr');
}

/**
 * Compute the 2D layout grid of cells accounting for colSpan and rowSpan.
 */
function _getTableCellGrid(table) {
  const grid = [];
  for (let r = 0; r < table.rows.length; r++) {
    grid[r] = grid[r] || [];
    let col = 0;
    for (let c = 0; c < table.rows[r].cells.length; c++) {
      while (grid[r][col]) col++;
      const cell = table.rows[r].cells[c];
      const rs = cell.rowSpan || 1;
      const cs = cell.colSpan || 1;
      for (let i = 0; i < rs; i++) {
        for (let j = 0; j < cs; j++) {
          grid[r + i] = grid[r + i] || [];
          grid[r + i][col + j] = { cell, row: r, col, rowSpan: rs, colSpan: cs };
        }
      }
      col += cs;
    }
  }
  return grid;
}

/**
 * Check whether a cell can merge with its right neighbour.
 */
export function _richTableCanMergeRight(cell) {
  const right = cell?.nextElementSibling;
  return !!right && right.matches('td, th') && cell.rowSpan === right.rowSpan;
}

/**
 * Check whether a cell can merge with the cell directly below it.
 */
export function _richTableCanMergeDown(cell) {
  if (!cell) return false;
  const table = cell.closest('table');
  if (!table) return false;
  const grid = _getTableCellGrid(table);
  let cellEntry = null;
  for (let r = 0; r < grid.length; r++) {
    for (let c = 0; c < (grid[r]?.length || 0); c++) {
      if (grid[r][c]?.cell === cell) {
        cellEntry = grid[r][c];
        break;
      }
    }
    if (cellEntry) break;
  }
  if (!cellEntry) return false;
  const nextRowIndex = cellEntry.row + cellEntry.rowSpan;
  if (nextRowIndex >= grid.length) return false;
  const belowEntry = grid[nextRowIndex]?.[cellEntry.col];
  if (!belowEntry || !belowEntry.cell) return false;
  if (belowEntry.col !== cellEntry.col || belowEntry.colSpan !== cellEntry.colSpan) return false;
  return belowEntry.cell !== cell;
}

/**
 * Check whether a cell can be split.
 */
export function _richTableCanSplitCell(cell) {
  return !!cell && ((cell.colSpan || 1) > 1 || (cell.rowSpan || 1) > 1);
}

/**
 * In-place merge right on DOM element.
 */
function _mergeCellRightInPlace(cell) {
  if (!_richTableCanMergeRight(cell)) return false;
  const right = cell.nextElementSibling;
  const leftHasContent = _richTableCellHasContent(cell);
  const rightHasContent = _richTableCellHasContent(right);
  if (!leftHasContent) cell.innerHTML = '';
  if (leftHasContent && rightHasContent) cell.appendChild(document.createElement('br'));
  if (rightHasContent) cell.append(...Array.from(right.childNodes));
  if (!leftHasContent && !rightHasContent) cell.innerHTML = '<br>';
  cell.colSpan += right.colSpan;
  right.remove();
  return true;
}

/**
 * Merge cell with right neighbour.
 * Supports either (cell) in-place or (rich, cell) undoable replacement.
 */
export function _mergeRichTableCellRight(rich, cell) {
  if (!cell && rich && (rich.tagName === 'TD' || rich.tagName === 'TH')) {
    cell = rich;
    rich = null;
  }
  if (!cell) return false;
  if (!rich) {
    return _mergeCellRightInPlace(cell);
  }
  const original = cell.closest('table');
  if (!original) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell.parentElement.rowIndex;
  const cellIndex = cell.cellIndex;
  const target = clone.rows[rowIndex]?.cells[cellIndex];
  if (!target || !_mergeCellRightInPlace(target)) return false;
  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, rowIndex, cellIndex);
}

/**
 * In-place merge down on DOM element.
 */
function _mergeCellDownInPlace(cell) {
  if (!_richTableCanMergeDown(cell)) return false;
  const table = cell.closest('table');
  const grid = _getTableCellGrid(table);
  let cellEntry = null;
  for (let r = 0; r < grid.length; r++) {
    for (let c = 0; c < (grid[r]?.length || 0); c++) {
      if (grid[r][c]?.cell === cell) {
        cellEntry = grid[r][c];
        break;
      }
    }
    if (cellEntry) break;
  }
  if (!cellEntry) return false;
  const nextRowIndex = cellEntry.row + cellEntry.rowSpan;
  const belowEntry = grid[nextRowIndex]?.[cellEntry.col];
  const belowCell = belowEntry?.cell;
  if (!belowCell) return false;

  const topHasContent = _richTableCellHasContent(cell);
  const bottomHasContent = _richTableCellHasContent(belowCell);
  if (!topHasContent) cell.innerHTML = '';
  if (topHasContent && bottomHasContent) cell.appendChild(document.createElement('br'));
  if (bottomHasContent) cell.append(...Array.from(belowCell.childNodes));
  if (!topHasContent && !bottomHasContent) cell.innerHTML = '<br>';
  cell.rowSpan = cellEntry.rowSpan + (belowCell.rowSpan || 1);
  belowCell.remove();
  return true;
}

/**
 * Merge cell with cell below it.
 * Supports either (cell) in-place or (rich, cell) undoable replacement.
 */
export function _mergeRichTableCellDown(rich, cell) {
  if (!cell && rich && (rich.tagName === 'TD' || rich.tagName === 'TH')) {
    cell = rich;
    rich = null;
  }
  if (!cell) return false;
  if (!rich) {
    return _mergeCellDownInPlace(cell);
  }
  const original = cell.closest('table');
  if (!original) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell.parentElement.rowIndex;
  const cellIndex = cell.cellIndex;
  const target = clone.rows[rowIndex]?.cells[cellIndex];
  if (!target || !_mergeCellDownInPlace(target)) return false;
  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, rowIndex, cellIndex);
}

/**
 * In-place split on DOM element.
 */
function _splitCellInPlace(cell) {
  if (!_richTableCanSplitCell(cell)) return false;
  if ((cell.colSpan || 1) > 1) {
    const span = cell.colSpan;
    const segments = Array.from({ length: span }, () => document.createDocumentFragment());
    let segmentIndex = 0;
    Array.from(cell.childNodes).forEach(node => {
      if (node.nodeType === Node.ELEMENT_NODE && node.tagName === 'BR' && segmentIndex < span - 1) {
        segmentIndex += 1;
        return;
      }
      segments[segmentIndex].appendChild(node);
    });
    const tagName = cell.tagName.toLowerCase();
    const rowSpan = cell.rowSpan;
    cell.colSpan = 1;
    cell.innerHTML = '';
    cell.appendChild(segments[0]);
    if (!_richTableCellHasContent(cell)) cell.innerHTML = '<br>';
    let previous = cell;
    for (let index = 1; index < span; index++) {
      const created = document.createElement(tagName);
      created.rowSpan = rowSpan;
      created.appendChild(segments[index]);
      if (!_richTableCellHasContent(created)) created.innerHTML = '<br>';
      previous.after(created);
      previous = created;
    }
    return true;
  }
  if ((cell.rowSpan || 1) > 1) {
    const table = cell.closest('table');
    const grid = _getTableCellGrid(table);
    let cellEntry = null;
    for (let r = 0; r < grid.length; r++) {
      for (let c = 0; c < (grid[r]?.length || 0); c++) {
        if (grid[r][c]?.cell === cell) {
          cellEntry = grid[r][c];
          break;
        }
      }
      if (cellEntry) break;
    }
    if (!cellEntry) return false;
    const initialRowSpan = cell.rowSpan;
    const tagName = cell.tagName.toLowerCase();
    const colSpan = cell.colSpan || 1;
    cell.rowSpan = 1;

    for (let r = cellEntry.row + 1; r < cellEntry.row + initialRowSpan; r++) {
      const targetRow = table.rows[r];
      if (!targetRow) continue;
      let insertBeforeCell = null;
      for (let c = 0; c < targetRow.cells.length; c++) {
        const rowCell = targetRow.cells[c];
        const rowGridEntry = grid[r]?.find(entry => entry?.cell === rowCell);
        if (rowGridEntry && rowGridEntry.col > cellEntry.col) {
          insertBeforeCell = rowCell;
          break;
        }
      }
      const newCell = document.createElement(tagName);
      newCell.colSpan = colSpan;
      newCell.innerHTML = '<br>';
      if (insertBeforeCell) targetRow.insertBefore(newCell, insertBeforeCell);
      else targetRow.appendChild(newCell);
    }
    return true;
  }
  return false;
}

/**
 * Split cell into individual columns or rows.
 * Supports either (cell) in-place or (rich, cell) undoable replacement.
 */
export function _splitRichTableCell(rich, cell) {
  if (!cell && rich && (rich.tagName === 'TD' || rich.tagName === 'TH')) {
    cell = rich;
    rich = null;
  }
  if (!cell) return false;
  if (!rich) {
    return _splitCellInPlace(cell);
  }
  const original = cell.closest('table');
  if (!original) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell.parentElement.rowIndex;
  const cellIndex = cell.cellIndex;
  const target = clone.rows[rowIndex]?.cells[cellIndex];
  if (!target || !_splitCellInPlace(target)) return false;
  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, rowIndex, cellIndex);
}

/**
 * Set vertical alignment on a table cell ('top', 'middle', 'bottom').
 */
export function _setRichTableCellAlignment(rich, cell, align) {
  if (!['top', 'middle', 'bottom'].includes(align)) return false;
  if (!cell && rich && (rich.tagName === 'TD' || rich.tagName === 'TH')) {
    cell = rich;
    rich = null;
  }
  if (!cell) return false;
  if (!rich) {
    if (align === 'top') cell.style.removeProperty('vertical-align');
    else cell.style.verticalAlign = align;
    if (!cell.getAttribute('style')) cell.removeAttribute('style');
    return true;
  }
  const original = cell.closest('table');
  if (!original) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell.parentElement.rowIndex;
  const cellIndex = cell.cellIndex;
  const target = clone.rows[rowIndex]?.cells[cellIndex];
  if (!target) return false;
  if (align === 'top') target.style.removeProperty('vertical-align');
  else target.style.verticalAlign = align;
  if (!target.getAttribute('style')) target.removeAttribute('style');
  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, rowIndex, cellIndex);
}

/**
 * Append or insert a row above/below an existing cell, or at the end of a table.
 */
export function _appendRichTableRow(rich, cellOrTable, before) {
  const original = cellOrTable?.tagName === 'TABLE' ? cellOrTable : cellOrTable?.closest?.('table');
  if (!original?.rows?.length) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);

  if (before !== undefined && cellOrTable && cellOrTable !== original) {
    const rowIndex = cellOrTable.parentElement?.rowIndex ?? (clone.rows.length - 1);
    const cellIndex = cellOrTable.cellIndex ?? 0;
    const insertAt = before ? rowIndex : rowIndex + 1;
    const sourceRow = clone.rows[Math.min(rowIndex, clone.rows.length - 1)];
    const row = clone.insertRow(insertAt);
    const columnCount = Math.max(1, sourceRow?.cells.length || 1);
    for (let i = 0; i < columnCount; i++) {
      const newCell = document.createElement('td');
      newCell.innerHTML = '<br>';
      row.appendChild(newCell);
    }
    _applyRichTableHeaderModes(clone, headerModes);
    return _replaceRichTable(rich, original, clone, insertAt, Math.min(cellIndex, columnCount - 1));
  }

  const lastRow = clone.rows[clone.rows.length - 1];
  const columnCount = Math.max(1, lastRow?.cells.length || 1);
  const row = clone.insertRow(-1);
  for (let index = 0; index < columnCount; index++) {
    row.insertCell(-1).innerHTML = '<br>';
  }
  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, clone.rows.length - 1, 0);
}

/**
 * Append or insert a column to the left or right of a cell.
 */
export function _appendRichTableColumn(rich, cell, before) {
  const original = cell?.tagName === 'TABLE' ? cell : cell?.closest?.('table');
  if (!original?.rows?.length) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell?.parentElement?.rowIndex ?? 0;
  const cellIndex = cell?.cellIndex ?? (clone.rows[0]?.cells?.length ? clone.rows[0].cells.length - 1 : 0);
  const insertAt = before ? cellIndex : cellIndex + 1;

  Array.from(clone.rows).forEach(row => {
    const reference = row.cells[insertAt] || null;
    const newCell = document.createElement('td');
    newCell.innerHTML = '<br>';
    row.insertBefore(newCell, reference);
  });

  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, rowIndex, insertAt);
}

/**
 * Delete the row containing cell, or the table if it only has 1 row.
 */
export function _deleteRichTableRow(rich, cell) {
  const original = cell?.tagName === 'TABLE' ? cell : cell?.closest?.('table');
  if (!original?.rows?.length) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell?.parentElement?.rowIndex ?? 0;
  const cellIndex = cell?.cellIndex ?? 0;

  if (clone.rows.length <= 1) {
    _replaceRichTable(rich, original, null);
    return true;
  }
  clone.deleteRow(rowIndex);
  const targetRow = Math.min(rowIndex, clone.rows.length - 1);
  const targetCell = Math.min(cellIndex, clone.rows[targetRow].cells.length - 1);

  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, targetRow, targetCell);
}

/**
 * Delete the column containing cell, or the table if it only has 1 column.
 */
export function _deleteRichTableColumn(rich, cell) {
  const original = cell?.tagName === 'TABLE' ? cell : cell?.closest?.('table');
  if (!original?.rows?.length) return false;
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell?.parentElement?.rowIndex ?? 0;
  const cellIndex = cell?.cellIndex ?? 0;

  const rowLengths = Array.from(clone.rows).map(row => row.cells.length);
  const maxColumns = rowLengths.length ? Math.max(...rowLengths) : 0;
  if (maxColumns <= 1) {
    _replaceRichTable(rich, original, null);
    return true;
  }
  Array.from(clone.rows).forEach(row => {
    if (row.cells[cellIndex]) row.deleteCell(cellIndex);
  });
  const targetRow = Math.min(rowIndex, clone.rows.length - 1);
  const targetCell = Math.max(0, Math.min(cellIndex, maxColumns - 2));

  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, targetRow, targetCell);
}

/**
 * Insert a new table with rows x columns into rich editor.
 */
export function _insertRichTable(rich, rows, columns) {
  const table = document.createElement('table');
  const tbody = document.createElement('tbody');
  table.appendChild(tbody);
  for (let rowIndex = 0; rowIndex < rows; rowIndex++) {
    const row = document.createElement('tr');
    for (let columnIndex = 0; columnIndex < columns; columnIndex++) {
      const cell = document.createElement(rowIndex === 0 ? 'th' : 'td');
      cell.innerHTML = '<br>';
      row.appendChild(cell);
    }
    tbody.appendChild(row);
  }
  const token = `doc-table-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  table.dataset.editorTableToken = token;
  document.execCommand('insertHTML', false, table.outerHTML + '<p><br></p>');
  const inserted = rich.querySelector(`[data-editor-table-token="${token}"]`);
  if (!inserted) return null;
  inserted.removeAttribute('data-editor-table-token');
  _focusRichTableCell(rich, inserted.rows[0]?.cells[0]);
  return inserted;
}

/**
 * Apply table action dispatched from toolbar or keyboard shortcut.
 */
export function _applyRichTableAction(rich, action) {
  const insert = action.match(/^table:insert:(\d+):(\d+)$/);
  if (insert) {
    _insertRichTable(rich, Number(insert[1]), Number(insert[2]));
    return true;
  }

  const cell = _richSelectionCell(rich);
  if (!cell) {
    _showToast('Place the cursor inside a table first');
    return false;
  }
  const original = cell.closest('table');
  const clone = original.cloneNode(true);
  const headerModes = _richTableHeaderModes(clone);
  const rowIndex = cell.parentElement.rowIndex;
  const cellIndex = cell.cellIndex;
  let targetRow = rowIndex;
  let targetCell = cellIndex;

  if (action === 'table:row-above' || action === 'table:row-below') {
    const insertAt = rowIndex + (action.endsWith('below') ? 1 : 0);
    const sourceRow = clone.rows[Math.min(rowIndex, clone.rows.length - 1)];
    const row = clone.insertRow(insertAt);
    const columnCount = Math.max(1, sourceRow?.cells.length || 1);
    for (let i = 0; i < columnCount; i++) {
      const newCell = document.createElement('td');
      newCell.innerHTML = '<br>';
      row.appendChild(newCell);
    }
    targetRow = insertAt;
    targetCell = Math.min(cellIndex, columnCount - 1);
  } else if (action === 'table:toggle-header-row') {
    headerModes.headerRow = !headerModes.headerRow;
  } else if (action === 'table:toggle-header-column') {
    headerModes.headerColumn = !headerModes.headerColumn;
  } else if (action === 'table:merge-right') {
    const target = clone.rows[rowIndex]?.cells[cellIndex];
    if (!_mergeRichTableCellRight(target)) return false;
  } else if (action === 'table:merge-down') {
    const target = clone.rows[rowIndex]?.cells[cellIndex];
    if (!_mergeRichTableCellDown(target)) return false;
  } else if (action === 'table:split-cell') {
    const target = clone.rows[rowIndex]?.cells[cellIndex];
    if (!_splitRichTableCell(target)) return false;
  } else if (action.startsWith('table:cell-align:')) {
    const alignment = action.slice('table:cell-align:'.length);
    if (!['top', 'middle', 'bottom'].includes(alignment)) return false;
    const target = clone.rows[rowIndex]?.cells[cellIndex];
    if (!target) return false;
    if (alignment === 'top') target.style.removeProperty('vertical-align');
    else target.style.verticalAlign = alignment;
    if (!target.getAttribute('style')) target.removeAttribute('style');
  } else if (action.startsWith('table:column-align:')) {
    const alignment = action.slice('table:column-align:'.length);
    if (!['left', 'center', 'right', 'reset'].includes(alignment)) return false;
    for (const row of Array.from(clone.rows)) {
      const target = row.cells[cellIndex];
      if (!target) continue;
      if (alignment === 'reset') target.style.removeProperty('text-align');
      else target.style.textAlign = alignment;
      if (!target.getAttribute('style')) target.removeAttribute('style');
    }
  } else if (action === 'table:column-left' || action === 'table:column-right') {
    const insertAt = cellIndex + (action.endsWith('right') ? 1 : 0);
    Array.from(clone.rows).forEach(row => {
      const reference = row.cells[insertAt] || null;
      const newCell = document.createElement('td');
      newCell.innerHTML = '<br>';
      row.insertBefore(newCell, reference);
    });
    targetCell = insertAt;
  } else if (action === 'table:delete-row') {
    if (clone.rows.length <= 1) {
      _replaceRichTable(rich, original, null);
      return true;
    }
    clone.deleteRow(rowIndex);
    targetRow = Math.min(rowIndex, clone.rows.length - 1);
    targetCell = Math.min(cellIndex, clone.rows[targetRow].cells.length - 1);
  } else if (action === 'table:delete-column') {
    const rowLengths = Array.from(clone.rows).map(row => row.cells.length);
    const maxColumns = rowLengths.length ? Math.max(...rowLengths) : 0;
    if (maxColumns <= 1) {
      _replaceRichTable(rich, original, null);
      return true;
    }
    Array.from(clone.rows).forEach(row => {
      if (row.cells[cellIndex]) row.deleteCell(cellIndex);
    });
    targetCell = Math.max(0, Math.min(cellIndex, maxColumns - 2));
  } else if (action === 'table:delete') {
    _replaceRichTable(rich, original, null);
    return true;
  } else {
    return false;
  }

  _applyRichTableHeaderModes(clone, headerModes);
  return _replaceRichTable(rich, original, clone, targetRow, targetCell);
}
