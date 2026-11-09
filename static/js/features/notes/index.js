/** Notes composition and intentional public API. The legacy entry activates compatibility startup. */
import { createSelectionOperations } from './application/selection.js';
import { createToolbar } from './ui/toolbar.js';
import { createWindowLayout } from './ui/windowLayout.js';
import { createCardOperations } from './application/cardOperations.js';
import { createChecklistOperations } from './application/checklistOperations.js';
import { createNotesSubmission } from './application/submission.js';
import { createNotesDrafts } from './application/drafts.js';
import { createNotesLifecycle } from './ui/lifecycle.js';
import * as noteRules from './domain/noteRules.js';
import { createText } from './ui/text.js';
import { createColors } from './ui/colors.js';
import { createPanel } from './ui/panel.js';
import { createReminderDisplay } from './ui/reminderDisplay.js';
import { createSelection } from './ui/selection.js';
import { createFilters } from './ui/filters.js';
import { createCardRendering } from './ui/cardRendering.js';
import { createLayout } from './ui/layout.js';
import { createQuickAdd } from './ui/quickAdd.js';
import { createCardInteractions } from './ui/cardInteractions.js';
import { createCardChecklist } from './ui/cardChecklist.js';
import { createCardReorder } from './ui/cardReorder.js';
import { createDraftEditor } from './ui/draftEditor.js';
import { createEditor } from './ui/editor.js';
import { createReminderPicker } from './ui/reminderPicker.js';
import { createChecklist } from './ui/checklist.js';
import { createDrawing } from './ui/drawing.js';
import { createClipboard } from './ui/clipboard.js';
import { createMobileEditor } from './ui/mobileEditor.js';
import { createMobileReorder } from './ui/mobileReorder.js';
import { createChecklistReorder } from './ui/checklistReorder.js';
import { createQueries } from './application/queries.js';
import { createAgentActions } from './application/agentActions.js';
import { createDeletion } from './application/deletion.js';
import { createNotesReminders } from './application/reminders.js';
import { createNotesNotifications } from './infrastructure/notifications.js';
import { createNotesState } from './application/state.js';
import { createNotesWorkflows } from './application/workflows.js';
import { createNotesApi } from './infrastructure/http.js';
import { createNotesPersistence, REMINDER_DISMISSED_AT_KEY, NOTES_FIRST_OPEN_HINT_KEY, NOTES_LABELS_COLLAPSED_KEY } from './infrastructure/storage.js';
import * as dates from './domain/recurrence.js';
import uiModule from '/static/js/ui.js?v=20260916largetoolscroll1';
import { spawnConfetti } from '/static/js/shared/ui/confetti.js';
import * as Modals from '/static/js/modalManager.js';
import { attachColorPicker } from '/static/js/colorPicker.js?v=20260910eyedropper1';
import { makeWindowDraggable } from '/static/js/windowDrag.js';
import { snapModalToZone } from '/static/js/tileManager.js?v=20260910responsivebounds1';
import { applyEdgeDock, clearDockSide } from '/static/js/modalSnap.js';
import { topToolWindowZ, topPortalZ } from '/static/js/toolWindowZOrder.js';
import { bindMenuDismiss, dismissOrRemove } from '/static/js/escMenuStack.js';
import { bindTourHintDismiss } from '/static/js/tourHintDismiss.js';

const api = createNotesApi({ origin: window.location.origin, request: (...args) => fetch(...args) });
const persistence = createNotesPersistence({ storage: typeof localStorage === 'undefined' ? undefined : localStorage });
const { _loadPendingHighlights, _loadGlowedReminders, _saveGlowedReminders, _loadActiveHighlights, _saveActiveHighlights, _savePendingHighlights, _loadFiredReminders, _saveFiredReminders, _draftKey, _loadDraft, _clearDraft } = persistence;
const clock = { now: () => Date.now() };
const app = createNotesState({
 viewMode: (typeof localStorage !== 'undefined' && localStorage.getItem('odysseus-notes-view')) || 'list',
 labelsCollapsed: typeof localStorage !== 'undefined' && localStorage.getItem(NOTES_LABELS_COLLAPSED_KEY) === '1',
 firedDotDismissedAt: (() => { try { const v = parseInt(persistence.getItem(REMINDER_DISMISSED_AT_KEY) || '0', 10); return Number.isFinite(v) && v > 0 ? v : 0; } catch { return 0; } })(),
});
const state = app.state;
const lifecycle = createNotesLifecycle();
const scheduler = { setTimeout: (...args) => setTimeout(...args), clearTimeout: id => clearTimeout(id), setInterval: (...args) => setInterval(...args), clearInterval: id => clearInterval(id) };
const notifications = createNotesNotifications({ browser: window, notify: window.Notification, openPanel: () => openPanel(), toast: title => uiModule?.showToast?.(title) });
const reminders = createNotesReminders({
  app,
  api,
  persistence,
  notifications,
  clock,
  scheduler,
  fetchNotes: () => _fetchNotes(),
  isNoteFullyDone: note => _isNoteFullyDone(note),
  hasItems: note => _hasItems(note),
  dismissedKey: REMINDER_DISMISSED_AT_KEY,
  effects: { badge: () => _updateRailBadge(), highlight: id => {
 _setReminderCardGlow(id, true);
 const card = document.querySelector(`.note-card[data-note-id="${id}"]`);
 if (card) { card.classList.add('note-card-reminder-fired'); setTimeout(() => card.classList.remove('note-card-reminder-fired'), 3000); }
 else _queuePendingHighlight(id);
} }
});
const workflows = createNotesWorkflows({
  app,
  api,
  effects: {
 render: () => _renderNotes(),
 showError: (...args) => uiModule.showError(...args),
 archived: undo => uiModule.showToast('Archived', { duration: 6000, action: 'Undo', actionIcon: _NOTE_UNDO_ICON, onAction: undo, actionHint: 'Ctrl+Z' }),
 celebrateArchive: (note, card) => { if (_hasItems(note) && !(note.items || []).some(i => !i.done)) { const r = card.getBoundingClientRect(); spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 80); } },
}
});
const _NOTE_UNDO_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;"><polyline points="9 14 4 9 9 4"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>';
function _pushUndo(entry) { return workflows._pushUndo(entry); }
function _popAndRunUndo() { return workflows._popAndRunUndo(); }
function _undoArchive(note, prevIdx) { return workflows._undoArchive(note, prevIdx); }
function _archiveNoteById(id, options) { return workflows._archiveNoteById(id, options); }
function _fetchNotes() { return workflows._fetchNotes(); }

function _deleteNoteApi(id) { return workflows._deleteNoteApi(id); }
function _patchNote(id, patch) { return workflows._patchNote(id, patch); }
function _uid() { return Math.random().toString(36).slice(2, 10); }
function _formatDueDate(dateStr) { return dates._formatDueDate(dateStr, clock); }
function _isDueOverdue(dateStr) { return dates._isDueOverdue(dateStr, clock); }

function _laterTodayDate() { return dates._laterTodayDate(clock); }
function _tomorrowDate() { return dates._tomorrowDate(clock); }
function _nextWeekDate() { return dates._nextWeekDate(clock); }
function _formatReminderTag(dateStr) { return dates._formatReminderTag(dateStr, clock); }
function _snapToRepeat(currentDate, normRepeat) { return dates._snapToRepeat(currentDate, normRepeat, clock); }
function _ensureNotificationPermission() { return notifications.ensurePermission(); }

function _startReminderLoop() { return reminders._startReminderLoop(); }

function _countFiredReminders() { return reminders._countFiredReminders(); }

function _hasActiveReminder(...args) { return noteRules._hasActiveReminder(...args, clock); }
const _isNoteFullyDone = noteRules._isNoteFullyDone;
const _hasItems = noteRules._hasItems;
function _isPastReminder(...args) { return noteRules._isPastReminder(...args, clock); }
function _esc(...args) { return text._esc(...args); }

function _safeImgSrc(...args) { return text._safeImgSrc(...args); }
function _linkify(...args) { return text._linkify(...args); }
function _isBgImage(...args) { return colors._isBgImage(...args); }

function _dotBg(...args) { return colors._dotBg(...args); }
function _dotIsActive(...args) { return colors._dotIsActive(...args); }
function _customColorStyle(...args) { return colors._customColorStyle(...args); }
function _pickCustomBgImage(...args) { return colors._pickCustomBgImage(...args); }
function _wireFormColors(...args) { return colors._wireFormColors(...args); }

function openPanel(...args) { return panel.openPanel(...args); }

function _smoothScrollToNoteCard(...args) { return panel._smoothScrollToNoteCard(...args); }
function _clearViewedReminderGlows(...args) { return reminderDisplay._clearViewedReminderGlows(...args); }
function _setReminderCardGlow(...args) { return reminderDisplay._setReminderCardGlow(...args); }
function _queuePendingHighlight(...args) { return reminderDisplay._queuePendingHighlight(...args); }
function _flushPendingHighlights(...args) { return reminderDisplay._flushPendingHighlights(...args); }
function _updateRailBadge(...args) { return reminderDisplay._updateRailBadge(...args); }
function _enterSelectMode(...args) { return selection._enterSelectMode(...args); }
function _exitSelectMode(...args) { return selection._exitSelectMode(...args); }
function _updateBulkBar(...args) { return selection._updateBulkBar(...args); }

function _renderLabelsInto(...args) { return filters._renderLabelsInto(...args); }
function _renderLoadingSkeleton(...args) { return cardRendering._renderLoadingSkeleton(...args); }
function _renderNotes(...args) { return cardRendering._renderNotes(...args); }
function _captureCardPositions(...args) { return layout._captureCardPositions(...args); }
function _animateReflow(...args) { return layout._animateReflow(...args); }
function _applyMasonry(...args) { return layout._applyMasonry(...args); }
function _renderQuickAdd(...args) { return quickAdd._renderQuickAdd(...args); }

function _editNote(...args) { return quickAdd._editNote(...args); }
function _bindCardEvents(...args) { return cardInteractions._bindCardEvents(...args); }
function _wireCardChecklist(...args) { return cardChecklist._wireCardChecklist(...args); }
function _wireTodayView(...args) { return cardChecklist._wireTodayView(...args); }
function _wireCardReorder(...args) { return cardReorder._wireCardReorder(...args); }

function _wireDraftAutosave(...args) { return draftEditor._wireDraftAutosave(...args); }
function _commitOpenInPlaceEditor(...args) { return draftEditor._commitOpenInPlaceEditor(...args); }
function _applyDraftToNote(...args) { return draftEditor._applyDraftToNote(...args); }
function _buildForm(...args) { return editor._buildForm(...args); }
function _wireReminderPicker(...args) { return reminderPicker._wireReminderPicker(...args); }
function _buildGoalHtml(...args) { return checklist._buildGoalHtml(...args); }
function _wireGoalForm(...args) { return checklist._wireGoalForm(...args); }
function _buildChecklistHtml(...args) { return checklist._buildChecklistHtml(...args); }
function _wireRow(...args) { return checklist._wireRow(...args); }
function _wireChecklist(...args) { return checklist._wireChecklist(...args); }
function _collectItems(...args) { return checklist._collectItems(...args); }
function _buildDrawHtml(...args) { return drawing._buildDrawHtml(...args); }
function _wireCanvas(...args) { return drawing._wireCanvas(...args); }
function _uploadCanvasAsPng(...args) { return drawing._uploadCanvasAsPng(...args); }
function _openNoteCornerMenu(...args) { return clipboard._openNoteCornerMenu(...args); }
function _copyNote(...args) { return clipboard._copyNote(...args); }
function _isNotesMobileMode(...args) { return mobileEditor._isNotesMobileMode(...args); }
function _openMobileFullscreenEdit(...args) { return mobileEditor._openMobileFullscreenEdit(...args); }
function _closeMobileFullscreenEdit(...args) { return mobileEditor._closeMobileFullscreenEdit(...args); }

function _bindLongPressDrag(...args) { return mobileReorder._bindLongPressDrag(...args); }

function _wireChecklistTouchReorder(...args) { return checklistReorder._wireChecklistTouchReorder(...args); }

function _sortedVisibleNotes(...args) { return queries._sortedVisibleNotes(...args); }
function _userVisibleNotes(...args) { return queries._userVisibleNotes(...args); }
function _agentSolveNote(...args) { return agentActions._agentSolveNote(...args); }

function _clearPastReminders(...args) { return deletion._clearPastReminders(...args); }
const text = createText({
  uiModule
});
const colors = createColors({
  api
});
const { COLORS, COLOR_HEX } = colors;
const windowLayout = createWindowLayout({
  makeWindowDraggable,
  snapModalToZone,
  clearDockSide,
  applyEdgeDock,
  topToolWindowZ
});
const toolbar = createToolbar({
  app,
  state,
  persistence,
  _renderNotes,
  _fetchNotes,
  _applyMasonry
});
const panel = createPanel({
  wireToolbar: toolbar.wireToolbar,
  wireSelectionActions: () => selection.wireSelectionActions(),
  _wireNotesWindow: windowLayout._wireNotesWindow,
  _restoreNotesSidebarDock: windowLayout._restoreNotesSidebarDock,
  _bringNotesToFront: windowLayout._bringNotesToFront,
  disposeUI: () => { layout.dispose(); mobileReorder.dispose(); checklistReorder.dispose(); lifecycle.dispose(); },
  lifecycle,
  Modals,
  NOTES_FIRST_OPEN_HINT_KEY,
  REMINDER_DISMISSED_AT_KEY,
  _clearViewedReminderGlows,
  _closeMobileFullscreenEdit,
  _commitOpenInPlaceEditor,
  _copyNote,
  _enterSelectMode,
  _exitSelectMode,
  _fetchNotes,
  _flushPendingHighlights,
  _isNotesMobileMode,
  _popAndRunUndo,
  _renderLoadingSkeleton,
  _renderNotes,
  _startReminderLoop,
  app,
  bindTourHintDismiss,
  dismissOrRemove,
  persistence,
  state,
  workflows
});
const reminderDisplay = createReminderDisplay({
  _countFiredReminders,
  _hasActiveReminder,
  _loadActiveHighlights,
  _loadGlowedReminders,
  _loadPendingHighlights,
  _saveActiveHighlights,
  _saveGlowedReminders,
  _savePendingHighlights,
  _smoothScrollToNoteCard,
  state
});
const selectionOperations = createSelectionOperations({
  app,
  api,
  fetchNotes: _fetchNotes,
  exitSelection: _exitSelectMode,
  sendContext: detail => window.dispatchEvent(new CustomEvent('odysseus:notes-context-selected', { detail })),
  effects: { render: _renderNotes, styledConfirm: uiModule.styledConfirm, showToast: (...args) => uiModule.showToast(...args) }
});
const selection = createSelection({
  operations: selectionOperations,
  _renderNotes,
  app,
  state
});
const filters = createFilters({
  NOTES_LABELS_COLLAPSED_KEY,
  _clearPastReminders,
  _esc,
  _isPastReminder,
  _renderNotes,
  _userVisibleNotes,
  app,
  persistence,
  state
});
const cardRendering = createCardRendering({
  COLORS,
  COLOR_HEX,
  _animateReflow,
  _applyMasonry,
  _bindCardEvents,
  _captureCardPositions,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _esc,
  _formatDueDate,
  _formatReminderTag,
  _hasActiveReminder,
  _isBgImage,
  _isDueOverdue,
  _isNotesMobileMode,
  _linkify,
  _loadActiveHighlights,
  _renderLabelsInto,
  _renderQuickAdd,
  _safeImgSrc,
  _sortedVisibleNotes,
  _updateRailBadge,
  _wireTodayView,
  state,
  uiModule
});
const layout = createLayout({
  isOpen: () => state.open
});
const quickAdd = createQuickAdd({
  _applyDraftToNote,
  _applyMasonry,
  _buildForm,
  app,
  openPanel,
  state,
  uiModule
});
const cardOperations = createCardOperations({
  app,
  api,
  pushUndo: _pushUndo,
  undoArchive: _undoArchive,
  effects: { render: _renderNotes, showError: (...args) => uiModule.showError(...args), showToast: (...args) => uiModule.showToast(...args), archived: undo => uiModule.showToast('Archived', { duration: 6000, action: 'Undo', actionIcon: _NOTE_UNDO_ICON, onAction: undo, actionHint: 'Ctrl+Z' }) }
});
const checklistOperations = createChecklistOperations({
  app,
  api,
  uid: _uid,
  effects: { render: _renderNotes, showError: (...args) => uiModule.showError(...args) }
});
const cardInteractions = createCardInteractions({
  cardOperations,
  COLORS,
  _archiveNoteById,
  _bindLongPressDrag,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _editNote,
  _isBgImage,
  _isNotesMobileMode,
  _openMobileFullscreenEdit,
  _openNoteCornerMenu,
  _pickCustomBgImage,
  _renderNotes,
  _setReminderCardGlow,
  _smoothScrollToNoteCard,
  _updateBulkBar,
  _wireCardChecklist,
  _wireCardReorder,
  app,
  spawnConfetti,
  state,
  uiModule
});
const cardChecklist = createCardChecklist({
  checklistOperations,
  _editNote,
  _renderNotes,
  app,
  spawnConfetti,
  state
});
const cardReorder = createCardReorder({
  persistOrder: cardOperations.persistOrder,
  lifecycle,
  _isNotesMobileMode,
  state
});
const draftEditor = createDraftEditor({
  bindDraft: (...args) => drafts.bindDraft(...args),
  _collectItems,
  _loadDraft
});
const editor = createEditor({
  deleteFromEditor: id => deletion.deleteFromEditor(id),
  submitNote: (...args) => submission.submitNote(...args),
  COLORS,
  _applyMasonry,
  _archiveNoteById,
  _buildChecklistHtml,
  _buildDrawHtml,
  _buildGoalHtml,
  _collectItems,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _esc,
  _isBgImage,
  _renderNotes,
  _safeImgSrc,
  _uid,
  _uploadCanvasAsPng,
  _wireCanvas,
  _wireChecklist,
  _wireDraftAutosave,
  _wireFormColors,
  _wireGoalForm,
  _wireReminderPicker,
  api,
  app,
  state,
  uiModule
});
const reminderPicker = createReminderPicker({
  lifecycle,
  _ensureNotificationPermission,
  _esc,
  _formatReminderTag,
  _laterTodayDate,
  _nextWeekDate,
  _snapToRepeat,
  _tomorrowDate,
  bindMenuDismiss,
  dismissOrRemove
});
const checklist = createChecklist({
  _esc,
  _uid,
  spawnConfetti
});
const drawing = createDrawing({
  lifecycle,
  _safeImgSrc,
  api,
  attachColorPicker
});
const clipboard = createClipboard({
  _agentSolveNote,
  bindMenuDismiss,
  dismissOrRemove,
  state,
  topPortalZ,
  uiModule
});
const mobileEditor = createMobileEditor({
  _applyDraftToNote,
  _buildForm,
  _fetchNotes,
  _linkify,
  _renderNotes,
  _uid,
  _wireChecklistTouchReorder,
  _wireRow,
  app,
  state,
  uiModule
});
const mobileReorder = createMobileReorder({
  commitMobileOrder: cardOperations.commitMobileOrder,
  lifecycle,
  _applyMasonry,
  _enterSelectMode,
  app
});
const checklistReorder = createChecklistReorder({
  lifecycle
});
const queries = createQueries({
  _hasActiveReminder,
  state
});
const agentActions = createAgentActions({
  _patchNote,
  _renderNotes,
  api,
  app,
  createFormData: () => new FormData(),
  state,
  streamComplete: sid => { try { window.sessionModule?.markStreamComplete?.(sid); } catch {} },
  uiModule
});
const deletion = createDeletion({
  app,
  _deleteNoteApi,
  _fetchNotes,
  _isPastReminder,
  _renderNotes,
  effects: { styledConfirm: uiModule.styledConfirm, confirm: message => confirm(message), showToast: (...args) => uiModule.showToast(...args), showError: (...args) => uiModule.showError(...args) },
  state
});
const notesModule = { openPanel: panel.openPanel, closePanel: panel.closePanel, togglePanel: panel.togglePanel, isPanelOpen: panel.isPanelOpen, openNote: panel.openNote, newNote: quickAdd.newNote, openNotes: panel.openPanel, closeNotes: panel.closePanel, isNotesOpen: panel.isPanelOpen, refreshDueBadge: reminders.refreshDueBadge };
const publicOpenPanel = panel.openPanel, publicClosePanel = panel.closePanel, publicTogglePanel = panel.togglePanel, publicIsPanelOpen = panel.isPanelOpen, publicOpenNote = panel.openNote, publicNewNote = quickAdd.newNote, publicRefreshDueBadge = reminders.refreshDueBadge, publicDismissFiredReminderDot = reminders.dismissFiredReminderDot;
export { publicOpenPanel as openPanel, publicClosePanel as closePanel, publicTogglePanel as togglePanel, publicIsPanelOpen as isPanelOpen, publicOpenNote as openNote, publicNewNote as newNote, publicRefreshDueBadge as refreshDueBadge, publicDismissFiredReminderDot as dismissFiredReminderDot, publicOpenPanel as openNotes, publicClosePanel as closeNotes, publicIsPanelOpen as isNotesOpen };
export function startNotesBackground() { reminders.start(); }
export default notesModule;
const submission = createNotesSubmission({
  app,
  api,
  persistence,
  clock,
  uid: _uid,
  fetchNotes: _fetchNotes,
  effects: { render: _renderNotes, showError: (...args) => uiModule.showError(...args), clearGlow: id => _setReminderCardGlow(id, false) }
});
const drafts = createNotesDrafts({
  app,
  api,
  persistence,
  scheduler,
  effects: { showError: (...args) => uiModule.showError(...args) }
});
