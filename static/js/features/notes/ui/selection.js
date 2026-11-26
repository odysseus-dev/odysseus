/** Selection controls and view feedback for accepted attachments. */
import { isOwnNote } from '../domain/noteRules.js';

// Only my own notes can be selected: a shared note's card has no checkbox,
// and the bulk actions (attach, archive, delete) are the owner's.
const selectable = notes => notes.filter(isOwnNote);

export function createSelection({
  operations,
  _renderNotes,
  app,
  state
}) {
  function _enterSelectMode(initialId = null) {
    app.setSelectMode(true);
    app.clearSelection();
    if (initialId) app.selectId(initialId);
    const bar = document.getElementById('notes-bulk-bar');
    const btn = document.getElementById('notes-select-btn');
    if (bar) bar.classList.remove('hidden');
    if (btn) { btn.classList.add('active'); btn.textContent = 'Cancel'; }
    _renderNotes();
    _updateBulkBar();
  }

  function _exitSelectMode() {
    app.setSelectMode(false);
    app.clearSelection();
    const bar = document.getElementById('notes-bulk-bar');
    const btn = document.getElementById('notes-select-btn');
    const all = document.getElementById('notes-select-all');
    if (bar) bar.classList.add('hidden');
    if (btn) { btn.classList.remove('active'); btn.textContent = 'Select'; }
    if (all) all.checked = false;
    _renderNotes();
  }

  function _updateBulkBar() {
    const count = state.selectedIds.size;
    const countEl = document.getElementById('notes-selected-count');
    const sendAgentBtn = document.getElementById('notes-send-agent');
    const archiveBtn = document.getElementById('notes-bulk-archive');
    const deleteBtn = document.getElementById('notes-bulk-delete');
    const allEl = document.getElementById('notes-select-all');
    if (countEl) countEl.textContent = `${count} Selected`;
    if (sendAgentBtn) sendAgentBtn.disabled = count === 0;
    if (archiveBtn) archiveBtn.disabled = count === 0;
    if (deleteBtn) deleteBtn.disabled = count === 0;
    const own = selectable(state.notes);
    if (allEl) allEl.checked = own.length > 0 && own.every(n => state.selectedIds.has(n.id));
    // Toggle select-mode class so todo dots don't react to hover
    const pane = document.getElementById('notes-pane');
    if (pane) pane.classList.toggle('notes-select-mode', count > 0);
  }

  function wireSelectionActions() {
    document.getElementById('notes-select-all').addEventListener('change', e => {
      if (e.target.checked) selectable(state.notes).forEach(n => app.selectId(n.id));
      else app.clearSelection();
      _renderNotes(); _updateBulkBar();
    });
    document.getElementById('notes-send-agent').addEventListener('click', operations.attachSelected);
    document.getElementById('notes-bulk-archive').addEventListener('click', operations.archiveSelected);
    document.getElementById('notes-bulk-delete').addEventListener('click', operations.deleteSelected);
    document.getElementById('notes-bulk-cancel').addEventListener('click', _exitSelectMode);
  }
  return { wireSelectionActions, _enterSelectMode, _exitSelectMode, _updateBulkBar };
}
