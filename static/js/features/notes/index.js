/** Notes composition and intentional public API. The legacy entry activates compatibility startup. */
import { createSelectionOperations } from './application/selection.js';
import { createToolbar } from './ui/toolbar.js';
import { createWindowLayout } from './ui/windowLayout.js';
import { createCardOperations } from './application/cardOperations.js';
import { createChecklistOperations } from './application/checklistOperations.js';
import { createNoteSession } from './application/noteSession.js';
import { createNoteVersions } from './application/noteVersions.js';
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
import { createReminderPicker } from './ui/reminderPicker.js';
import { createDrawing } from './ui/drawing.js';
import { createClipboard } from './ui/clipboard.js';
import { createMobileReorder } from './ui/mobileReorder.js';
import { createRichBody } from './ui/richBody.js';
import { watchNoteImages } from './ui/noteImages.js';
import { createNoteTags } from './ui/noteTags.js';
import { createNoteWindow } from './ui/noteWindow.js';
import { createSharePopover } from './ui/sharePopover.js';
import { createNoteAgentUi } from './ui/noteAgent.js';
import { createContextStrip, openNoteOverChat } from './ui/contextStrip.js';
import { createWindowRoute } from './ui/windowRoute.js';
import { createQueries } from './application/queries.js';
import { createAgentActions } from './application/agentActions.js';
import { createAgentEdits } from './application/agentEdits.js';
import { createNoteHistory } from './application/noteHistory.js';
import { createExternalImagesSetting } from './application/externalImages.js';
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
import { nextToolWindowZ, topToolWindowZ, topPortalZ } from '/static/js/toolWindowZOrder.js';
import { bindMenuDismiss, dismissOrRemove } from '/static/js/escMenuStack.js';
import { bindTourHintDismiss } from '/static/js/tourHintDismiss.js';
import { mdToHtml, noteImageSrc } from '/static/js/markdown.js';

const rawApi = createNotesApi({ origin: window.location.origin, request: (...args) => fetch(...args) });
const persistence = createNotesPersistence({ storage: typeof localStorage === 'undefined' ? undefined : localStorage });
const { _loadPendingHighlights, _loadGlowedReminders, _saveGlowedReminders, _loadActiveHighlights, _saveActiveHighlights, _savePendingHighlights, _loadFiredReminders, _saveFiredReminders } = persistence;
const clock = { now: () => Date.now() };
const app = createNotesState({
 viewMode: (typeof localStorage !== 'undefined' && localStorage.getItem('odysseus-notes-view')) || 'list',
 labelsCollapsed: typeof localStorage !== 'undefined' && localStorage.getItem(NOTES_LABELS_COLLAPSED_KEY) === '1',
 firedDotDismissedAt: (() => { try { const v = parseInt(persistence.getItem(REMINDER_DISMISSED_AT_KEY) || '0', 10); return Number.isFinite(v) && v > 0 ? v : 0; } catch { return 0; } })(),
});
const state = app.state;
// Every writer but the note window's own session goes through the tracked
// API, so the window can tell a card checkbox ticked behind it from a change
// made on another device.
const noteVersions = createNoteVersions({ app });
const api = noteVersions.track(rawApi);
// The viewer's "Show external images in notes". Turning it on or off redraws
// the cards; an open note window keeps what it opened with until reopened.
const externalImages = createExternalImagesSetting({
  origin: window.location.origin,
  request: (...args) => fetch(...args),
  onChange: () => { if (state.open) _renderNotes(); else contextStrip.changed(); },
});
// A note image that does not load shows a placeholder, in the window, a card
// or the chat's note strip alike.
watchNoteImages(document);
// `mediaNote` is set for a shared note, whose uploads load through the note.
// The note window's editor asks for `sourceMap` (see mdToHtml), and passes
// the external-images setting it opened with.
const _renderNoteMarkdown = (source, mediaNote = null, { sourceMap = false, externalImages: showExternal = externalImages.enabled() } = {}) => mdToHtml(source, { tasks: 'interactive', images: 'notes', dialect: 'notes', mediaNote, sourceMap, externalImages: showExternal });
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
function _fetchNotes(options) { return workflows._fetchNotes(options); }

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
function _renderNotes(...args) {
  const result = cardRendering._renderNotes(...args);
  noteAgentUi.wireCards(document.querySelector('#notes-pane .notes-pane-body'));
  noteWindow.sync();
  contextStrip.changed();
  return result;
}
// Redraw only these notes' cards when the grid around them is unchanged;
// otherwise the whole grid.
function _renderNoteCards(ids) {
  if (!cardRendering._renderCardsInPlace(ids)) return _renderNotes();
  noteWindow.sync();
  contextStrip.changed();
}
function _captureCardPositions(...args) { return layout._captureCardPositions(...args); }
function _layoutCard(...args) { return layout._layoutCard(...args); }
function _animateReflow(...args) { return layout._animateReflow(...args); }
function _applyMasonry(...args) { return layout._applyMasonry(...args); }
function _renderQuickAdd(...args) { return quickAdd._renderQuickAdd(...args); }

function _openNoteWindow(id) { return noteWindow.open(id); }
function _bindCardEvents(...args) { return cardInteractions._bindCardEvents(...args); }
function _wireCardChecklist(...args) { return cardChecklist._wireCardChecklist(...args); }
function _wireTodayView(...args) { return cardChecklist._wireTodayView(...args); }
function _wireCardReorder(...args) { return cardReorder._wireCardReorder(...args); }

function _wireReminderPicker(...args) { return reminderPicker._wireReminderPicker(...args); }
function _openNoteCornerMenu(...args) { return clipboard._openNoteCornerMenu(...args); }
function _copyNote(...args) { return clipboard._copyNote(...args); }
function _isNotesMobileMode() { return ('ontouchstart' in window) && window.innerWidth <= 768; }
// Uploads are stored in a note as same-origin paths.
function _uploadPath(url) {
  try { const parsed = new URL(url, window.location.origin); return parsed.origin === window.location.origin ? parsed.pathname + parsed.search : url; }
  catch { return url; }
}

function _bindLongPressDrag(...args) { return mobileReorder._bindLongPressDrag(...args); }

function _sortedVisibleNotes(...args) { return queries._sortedVisibleNotes(...args); }
function _userVisibleNotes(...args) { return queries._userVisibleNotes(...args); }
function _agentSolveNote(...args) { return agentActions._agentSolveNote(...args); }
function _agentTagHtml(...args) { return noteAgentUi.cardTagHtml(...args); }

function _clearPastReminders(...args) { return deletion._clearPastReminders(...args); }
const text = createText({
  uiModule,
  noteImageSrc
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
// Notes shared with me change on other accounts. While the panel is open and
// the tab visible, re-read the list every 20 s, unless the open note holds
// unsaved typing, a card is being edited or the share popover is open. A failed read keeps what is shown.
const NOTES_REFRESH_MS = 20000;
let _refreshTimer = null;
const _noteKey = n => JSON.stringify([n.id, n.updated_at, n.can_edit, (n.shared_with || []).length]);
const _noteSignature = notes => JSON.stringify((notes || []).map(_noteKey));
// The list's ETag from the last re-read, so an unchanged list costs a 304
// with no body. Kept per view (active or archived).
let _refreshTag = { archived: null, etag: null };
async function _refreshNotes() {
  if (!state.open || state.loading || state.selectMode || document.hidden) return;
  if (noteWindow.hasUnsaved() || sharePopover.isOpen() || noteAgentUi.isAskOpen()) return;
  if (document.querySelector('#notes-pane .note-card [contenteditable="true"], #notes-pane input:focus')) return;
  try {
    const archived = state.showingArchived;
    const res = await rawApi.listNotes(archived, { etag: _refreshTag.archived === archived ? _refreshTag.etag : null });
    if (res.status === 304 || !res.ok) return;
    const data = await res.json();
    const notes = data.notes || [];
    if (!state.open || state.showingArchived !== archived) return;
    app.setMe(data.me);
    _refreshTag = { archived, etag: res.headers?.get?.('ETag') || null };
    if (_noteSignature(notes) === _noteSignature(state.notes)) return;
    const before = new Map(state.notes.map(n => [n.id, _noteKey(n)]));
    const agentEditsBefore = agentEdits.agentEditsOf(state.notes);
    const changed = notes.filter(n => before.get(n.id) !== _noteKey(n)).map(n => n.id);
    app.replaceNotes(notes);
    // Notes edited elsewhere: their cards only, unless one appeared, went or moved.
    _renderNoteCards(changed);
    // An agent edit made from the chat (a follow-up turn) shows up here.
    agentEdits.noticeInList(notes, agentEditsBefore);
  } catch {}
}
function _startNotesRefresh() {
  if (_refreshTimer) return;
  _refreshTimer = scheduler.setInterval(_refreshNotes, NOTES_REFRESH_MS);
}
function _stopNotesRefresh() {
  scheduler.clearInterval(_refreshTimer);
  _refreshTimer = null;
}
const panel = createPanel({
  _startNotesRefresh,
  _stopNotesRefresh,
  wireToolbar: toolbar.wireToolbar,
  wireSelectionActions: () => selection.wireSelectionActions(),
  _wireNotesWindow: windowLayout._wireNotesWindow,
  _restoreNotesSidebarDock: windowLayout._restoreNotesSidebarDock,
  _bringNotesToFront: windowLayout._bringNotesToFront,
  disposeUI: () => { layout.dispose(); mobileReorder.dispose(); lifecycle.dispose(); },
  lifecycle,
  Modals,
  NOTES_FIRST_OPEN_HINT_KEY,
  REMINDER_DISMISSED_AT_KEY,
  _clearViewedReminderGlows,
  _flushNoteWindow: () => noteWindow.flush(),
  _isNoteWindowShowing: () => noteWindow.isShowing(),
  _openNoteWindow,
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
  _agentTagHtml,
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
  _layoutCard,
  _linkify,
  _loadActiveHighlights,
  _renderLabelsInto,
  _renderNoteMarkdown,
  _renderQuickAdd,
  _sortedVisibleNotes,
  _updateRailBadge,
  _wireTodayView,
  _wireCards: root => noteAgentUi.wireCards(root),
  state,
  uiModule
});
const layout = createLayout({
  isOpen: () => state.open
});
const quickAdd = createQuickAdd({
  openNewNote: options => noteWindow.openNew(options),
  openPanel,
  state
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
  effects: { render: _renderNotes, showError: (...args) => uiModule.showError(...args), beforeWrite: id => noteWindow.settle(id), followed: id => { if (noteWindow.isOpenOn(id)) _renderNotes(); } }
});
const cardInteractions = createCardInteractions({
  cardOperations,
  COLORS,
  _archiveNoteById,
  _bindLongPressDrag,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _isBgImage,
  _isNotesMobileMode,
  _openNoteWindow,
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
  _openNoteWindow,
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
const drawing = createDrawing({
  lifecycle,
  _safeImgSrc,
  api,
  attachColorPicker
});
const clipboard = createClipboard({
  _agentSolveNote,
  _leaveNote: id => deletion.leaveNote(id),
  bindMenuDismiss,
  dismissOrRemove,
  state,
  topPortalZ,
  uiModule
});
const mobileReorder = createMobileReorder({
  commitMobileOrder: cardOperations.commitMobileOrder,
  lifecycle,
  _applyMasonry,
  _enterSelectMode,
  app
});
const queries = createQueries({
  _hasActiveReminder,
  state
});
const agentActions = createAgentActions({
  api,
  app,
  state,
  patchNote: _patchNote,
  createFormData: () => new FormData(),
  sessions: {
    markStreamComplete: sid => { try { window.sessionModule?.markStreamComplete?.(sid); } catch {} },
    open: async sid => {
      const sessions = window.sessionModule;
      if (!sessions?.selectSession) throw new Error('sessions unavailable');
      await sessions.loadSessions?.().catch(() => {});
      await sessions.selectSession(sid);
    },
  },
  // Opening the agent chat leaves Notes: the note window closes (pending
  // edits are flushed) and so does the panel.
  closeNotes: async () => { await noteWindow.close(); panel.closePanel(); },
  effects: {
    render: () => _renderNotes(),
    showToast: (...args) => uiModule.showToast(...args),
    showError: (...args) => uiModule.showError(...args),
    agentEdited: id => agentEdits.edited(id),
  }
});
const noteAgentUi = createNoteAgentUi({
  state,
  actions: agentActions,
  esc: _esc,
  bindMenuDismiss,
  dismissOrRemove,
  portalZ: () => topPortalZ()
});
const deletion = createDeletion({
  app,
  _deleteNoteApi,
  _leaveNoteApi: id => rawApi.unshare(id, state.me),
  _fetchNotes,
  _isPastReminder,
  _renderNotes,
  effects: { styledConfirm: uiModule.styledConfirm, confirm: message => confirm(message), showToast: (...args) => uiModule.showToast(...args), showError: (...args) => uiModule.showError(...args) },
  state
});
// A tab closed or sent to the background keeps the open note's last edits.
window.addEventListener('pagehide', () => noteWindow.flushDraft());
document.addEventListener('visibilitychange', () => { if (document.hidden) noteWindow.flushDraft(); });
// Edits that failed to save while offline go up as soon as the connection is back.
window.addEventListener('online', () => noteWindow.retry());
// A note window swiped down on a phone is closed, not only hidden: its card opens it again.
window.addEventListener('modal-dismissed', event => noteWindow.dismissed(event.detail?.id));
/** Open a note in the note window over the chat (a context card, a note named on a sent message). */
async function openNoteWindow(id, note = null) {
  let known = note;
  if (!known && !state.notes.some(n => n.id === id)) {
    try { known = await rawApi.getNote(id); } catch { known = null; }
  }
  return openNoteOverChat(id, known, { state, app, openWindow: noteId => noteWindow.open(noteId), openInPanel: noteId => panel.openNote(noteId) });
}
/** `#note-<id>` (a loaded URL, Forward): open the note when this user can read it. */
async function openNoteFromRoute(id) {
  if (noteWindow.open(id)) return true;
  let note = null;
  try { note = await rawApi.getNote(id); } catch { note = null; }
  if (!note?.id) return false;
  return !!(await openNoteWindow(note.id, note));
}
// Back closes the note window, Forward opens it again.
const windowRoute = createWindowRoute({
  openNote: openNoteFromRoute,
  closeWindow: () => noteWindow.closeFromHistory(),
  isOpen: () => noteWindow.isOpen(),
  isOpenOn: id => noteWindow.isOpenOn(id),
});
/** Open a note's History over the chat, on `focus` ({ sessionId }: that chat's latest agent edit). Falls back to opening the note. */
async function openNoteHistory(id, focus = null) {
  if (state.notes.some(n => n.id === id) && await noteWindow.showHistory(id, focus)) return true;
  if (await openNoteWindow(id) !== 'window') return false;
  return noteWindow.showHistory(id, focus);
}
const notesModule = { openNoteWindow, openNoteHistory, contextNotes: () => contextStrip.current(), openPanel: panel.openPanel, closePanel: panel.closePanel, togglePanel: panel.togglePanel, isPanelOpen: panel.isPanelOpen, openNote: panel.openNote, newNote: quickAdd.newNote, openNotes: panel.openPanel, closeNotes: panel.closePanel, isNotesOpen: panel.isPanelOpen, refreshDueBadge: reminders.refreshDueBadge };
const publicOpenPanel = panel.openPanel, publicClosePanel = panel.closePanel, publicTogglePanel = panel.togglePanel, publicIsPanelOpen = panel.isPanelOpen, publicOpenNote = panel.openNote, publicNewNote = quickAdd.newNote, publicRefreshDueBadge = reminders.refreshDueBadge, publicDismissFiredReminderDot = reminders.dismissFiredReminderDot;
export { publicOpenPanel as openPanel, publicClosePanel as closePanel, publicTogglePanel as togglePanel, publicIsPanelOpen as isPanelOpen, publicOpenNote as openNote, publicNewNote as newNote, publicRefreshDueBadge as refreshDueBadge, publicDismissFiredReminderDot as dismissFiredReminderDot, publicOpenPanel as openNotes, publicClosePanel as closeNotes, publicIsPanelOpen as isNotesOpen };
export function startNotesBackground() {
  reminders.start();
  contextStrip.start();
  windowRoute.start();
  externalImages.bind(document.getElementById('set-notes-external-images'), {
    showError: (...args) => uiModule.showError(...args),
    showToast: (...args) => uiModule.showToast(...args),
  });
  externalImages.load();
}
export default notesModule;
const richBody = createRichBody({
  renderMarkdown: _renderNoteMarkdown,
  bindMenuDismiss,
  dismissOrRemove,
  pickImageFile: () => colors._pickImageFile(),
  uploadImages: files => colors._uploadImages(files).then(({ urls, rateLimited }) => ({ urls: urls.map(_uploadPath), rateLimited })),
  sketch: insert => noteWindow.openSketch(insert),
  showError: (...args) => uiModule.showError(...args),
  portalZ: () => topPortalZ(),
  externalImages: () => externalImages.enabled(),
});
const noteTags = createNoteTags({
  esc: _esc,
  attrEsc: (...args) => text._attrEsc(...args),
  bindMenuDismiss,
  dismissOrRemove
});
const sharePopover = createSharePopover({
  api: rawApi,
  esc: _esc,
  bindMenuDismiss,
  dismissOrRemove,
  portalZ: () => topPortalZ()
});
const noteWindow = createNoteWindow({
  app,
  state,
  Modals,
  makeWindowDraggable,
  bindMenuDismiss,
  dismissOrRemove,
  richBody,
  noteTags,
  sharePopover,
  agentUi: noteAgentUi,
  agentActions,
  createSession: ({ note, collect, readOnly, effects }) => createNoteSession({
    api: rawApi,
    persistence,
    scheduler,
    clock,
    note,
    collect,
    readOnly,
    effects,
    // An edited note moves to the top of its section, as Save used to do.
    // Ordering is the owner's: a collaborator's edit does not move the note.
    firstWrite: () => (note?.pinned || !noteRules.isOwnNote(note) ? {} : { sort_order: noteRules.topSortOrder(state.notes, note?.id) }),
  }),
  wireReminderPicker: _wireReminderPicker,
  drawing,
  colors,
  esc: _esc,
  attrEsc: (...args) => text._attrEsc(...args),
  isMobile: _isNotesMobileMode,
  isLocalVersion: noteVersions.isLocal,
  navigation: {
    opened: id => windowRoute.opened(id),
    created: id => windowRoute.created(id),
    closed: () => windowRoute.closed(),
  },
  raise: modal => modal.style.setProperty('z-index', String(nextToolWindowZ({ exclude: modal, current: getComputedStyle(modal).zIndex })), 'important'),
  effects: {
    render: () => _renderNotes(),
    renderNote: id => _renderNoteCards([id]),
    showToast: (...args) => uiModule.showToast(...args),
    showError: (...args) => uiModule.showError(...args),
    archive: id => _archiveNoteById(id),
    unarchive: id => cardOperations.unarchive(id),
    confirmDelete: () => deletion.confirmDelete(),
    deleteNote: id => deletion.deleteNow(id),
    confirmLeave: () => deletion.confirmLeave(),
    leaveNote: id => deletion.leaveNow(id),
    celebrate: rect => spawnConfetti(rect.left + rect.width / 2, rect.top + rect.height / 2, 60),
    uploadPath: _uploadPath,
    undoAgentEdit: id => agentEdits.undo(id),
    history: {
      list: id => noteHistory.list(id),
      read: (id, versionId) => noteHistory.read(id, versionId),
      restore: (id, versionId, options) => noteHistory.restore(id, versionId, options),
    },
    // A changed reminder fires (and glows) again.
    reminderChanged: id => {
      const fired = _loadFiredReminders();
      fired.delete(id);
      _saveFiredReminders(fired);
      const glowed = _loadGlowedReminders();
      glowed.delete(id);
      _saveGlowedReminders(glowed);
      _setReminderCardGlow(id, false);
    },
  }
});
const agentEdits = createAgentEdits({
  api: rawApi,
  app,
  state,
  noteWindow,
  effects: {
    renderNote: id => _renderNoteCards([id]),
    showToast: (...args) => uiModule.showToast(...args),
    showError: (...args) => uiModule.showError(...args),
    undoIcon: _NOTE_UNDO_ICON,
    seeChanges: (id, sessionId) => openNoteHistory(id, { sessionId }),
  },
});

const noteHistory = createNoteHistory({
  api: rawApi,
  app,
  state,
  noteWindow,
  clock,
  effects: {
    renderNote: id => _renderNoteCards([id]),
    showToast: (...args) => uiModule.showToast(...args),
    showError: (...args) => uiModule.showError(...args),
    undoIcon: _NOTE_UNDO_ICON,
  },
});

// The notes in the current chat's context, as cards above the composer.
const contextStrip = createContextStrip({
  api: rawApi,
  esc: _esc,
  // Read-only: task lines render as plain boxes, not toggles.
  renderMarkdown: source => mdToHtml(source, { images: 'notes', dialect: 'notes', externalImages: externalImages.enabled() }),
  colorClass: note => (note.color && !_isBgImage(note.color) && Object.hasOwn(COLOR_HEX, note.color) ? ' note-color-' + note.color : ''),
  // The panel's list is the newest copy while it is open on active notes.
  localNote: id => (state.open && !state.showingArchived && !state.loading ? state.notes.find(n => n.id === id) || null : null),
  scopeNoteId: () => {
    const sessions = window.sessionModule;
    const sid = sessions?.getCurrentSessionId?.();
    return sid ? (sessions.getSessions?.() || []).find(s => String(s.id) === String(sid))?.note_scope_id || null : null;
  },
  openNote: (id, note) => openNoteWindow(id, note),
  removeFromContext: noteId => window.dispatchEvent(new CustomEvent('odysseus:notes-context-remove', { detail: { noteId } })),
  scheduler,
});
