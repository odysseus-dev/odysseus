/**
 * Notes Module — Google Keep-style notes and todos.
 * Renders as a sidebar panel (like document editor), not a modal.
 */

import uiModule from './ui.js';
import { spawnConfetti } from './compare/vote.js';
import * as Modals from './modalManager.js';
import { attachColorPicker } from './colorPicker.js';
import { makeWindowDraggable } from './windowDrag.js';
import { snapModalToZone } from './tileManager.js';
import { applyEdgeDock, clearDockSide } from './modalSnap.js';

const API_BASE = window.location.origin;
let _open = false;
let _notes = [];
let _editingId = null;
let _selectedIds = new Set();
let _activeLabel = null;
let _activeFilter = null; // null | 'default' | 'reminders' | 'no-reminders'
// Cycle order for the Reminders chip: each click on it advances reminders →
// null → no-reminders → null → reminders → ... This var tracks which non-null
// state the next click should land on after passing through null.
let _reminderChipNext = 'reminders';
let _searchQuery = '';
let _viewMode = (typeof localStorage !== 'undefined' && localStorage.getItem('odysseus-notes-view')) || 'list'; // 'list' or 'grid'
let _showingArchived = false;
let _selectMode = false;
let _reminderTimer = null;
// Tracks the global keydown listener so closePanel can remove it
// (previously leaked one per openPanel; on multi-open sessions this
// stacked dozens of identical handlers).
let _notesKeydownHandler = null;
const REMINDER_FIRED_KEY = 'odysseus-notes-reminder-fired';
// Note IDs already shown with the entry-glow once. Re-set when the user
// reschedules the reminder so the new firing glows again on next open.
const REMINDER_GLOWED_KEY = 'odysseus-notes-reminder-glowed';
// IDs of notes whose reminders fired while the notes panel was closed. On the
// next open of the panel we briefly glow those cards so the user can spot them.
const REMINDER_PENDING_HIGHLIGHT_KEY = 'odysseus-notes-reminder-pending-highlight';
const REMINDER_ACTIVE_HIGHLIGHT_KEY = 'odysseus-notes-reminder-active-highlight';
// Timestamp of the last time the user opened the notes panel — used to gate
// the rail "fired" badge so old reminders don't re-fire on every page reload.
const REMINDER_DISMISSED_AT_KEY = 'odysseus-notes-reminder-dismissed-at';
const NOTES_FIRST_OPEN_HINT_KEY = 'odysseus-notes-first-open-hint-v1';

function _forceCloseNotesPanel() {
  _open = false;
  _editingId = null;
  try { _commitOpenInPlaceEditor(); } catch {}
  try { _closeMobileFullscreenEdit({ save: true }); } catch {}
  try { _clearViewedReminderGlows(); } catch {}
  if (_notesKeydownHandler) {
    document.removeEventListener('keydown', _notesKeydownHandler);
    _notesKeydownHandler = null;
  }
  if (_reminderTimer) {
    clearInterval(_reminderTimer);
    _reminderTimer = null;
  }
  document.body.classList.remove('notes-view', 'notes-mobile-mode', 'notes-drag-mode');
  document.getElementById('tool-notes-btn')?.classList.remove('active');
  try { Modals.unregister('notes-panel'); } catch {}
  try { document.getElementById('notes-pane')?.remove(); } catch {}
  try { document.getElementById('notes-pane-backdrop')?.remove(); } catch {}
  try { window._restoreSidebarIfRouteCollapsed?.(); } catch {}
}

function _showNotesFirstOpenHint(pane) {
  if (!pane || typeof localStorage === 'undefined') return;
  try {
    if (localStorage.getItem(NOTES_FIRST_OPEN_HINT_KEY)) return;
    localStorage.setItem(NOTES_FIRST_OPEN_HINT_KEY, '1');
  } catch {
    return;
  }

  document.getElementById('notes-first-open-hint')?.remove();
  const hint = document.createElement('div');
  hint.id = 'notes-first-open-hint';
  hint.className = 'tour-hint';
  hint.innerHTML = `
    <div class="tour-hint-text"><b>Notes</b> is your basic todo list, and also where reminders are managed.</div>
    <button type="button" class="tour-hint-dismiss">OK</button>
  `;
  document.body.appendChild(hint);

  const place = () => {
    const r = pane.getBoundingClientRect();
    const hw = hint.offsetWidth || 260;
    hint.style.top = Math.max(12, r.top + 58) + 'px';
    hint.style.left = Math.min(window.innerWidth - hw - 12, Math.max(12, r.left + 18)) + 'px';
  };
  const close = () => {
    window.removeEventListener('resize', place);
    hint.classList.add('tour-hint-out');
    setTimeout(() => hint.remove(), 180);
  };

  requestAnimationFrame(() => {
    place();
    hint.classList.add('tour-hint-in');
  });
  window.addEventListener('resize', place);
  hint.querySelector('.tour-hint-dismiss')?.addEventListener('click', close);
  setTimeout(close, 6500);
}

function _notesFullscreenSafeRect() {
  const vw = window.innerWidth || document.documentElement.clientWidth || 0;
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  let left = 0;
  let right = vw;

  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  const hamburgerRight = document.body.classList.contains('hamburger-right')
    || sidebar?.classList.contains('right-side')
    || rail?.classList.contains('right-side');

  const reserve = (el) => {
    if (!el || getComputedStyle(el).display === 'none') return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    if (hamburgerRight) right = Math.min(right, rect.left);
    else left = Math.max(left, rect.right);
  };

  if (sidebar && !sidebar.classList.contains('hidden')) reserve(sidebar);
  reserve(rail);

  // The fixed hamburger can remain visible even when the rail/sidebar is
  // collapsed. Reserve its strip too so fullscreen Notes does not sit beneath it.
  const hamburger = document.getElementById('hamburger-btn');
  if (hamburger && getComputedStyle(hamburger).display !== 'none') {
    const rect = hamburger.getBoundingClientRect();
    const pad = 8;
    if (hamburgerRight) right = Math.min(right, rect.left - pad);
    else left = Math.max(left, rect.right + pad);
  }

  left = Math.max(0, Math.min(left, vw - 80));
  right = Math.max(left + 80, Math.min(right, vw));
  return { left, top: 0, width: right - left, height: vh };
}

function _wireNotesWindow(pane) {
  if (!pane || pane.dataset.windowDragWired === '1') return;
  const header = pane.querySelector('.notes-pane-header');
  if (!header) return;
  pane.dataset.windowDragWired = '1';
  makeWindowDraggable(pane, {
    content: pane,
    header,
    fsClass: 'notes-window-fullscreen',
    skipSelector: 'button, input, select, textarea, label, .notes-mobile-grabber',
    enableDock: true,
    enableLeftDock: true,
    onEnterFullscreen: () => {
      pane.classList.add('notes-window-fullscreen');
      snapModalToZone(pane, {
        name: 'fullscreen',
        rect: _notesFullscreenSafeRect(),
      });
    },
    onExitFullscreen: () => {
      _restoreNotesSidebarDock(pane);
    },
  });
}

function _clearNotesSnapStyles(pane) {
  if (!pane) return;
  const hadLeft = pane.classList.contains('modal-left-docked');
  const hadRight = pane.classList.contains('modal-right-docked');
  pane.classList.remove('notes-window-fullscreen', 'modal-left-docked', 'modal-right-docked');
  if (hadLeft) clearDockSide('left', pane);
  if (hadRight) clearDockSide('right', pane);
  ['position', 'left', 'top', 'right', 'bottom', 'width', 'max-width', 'height',
    'max-height', 'margin', 'transform', 'border-radius']
    .forEach((prop) => pane.style.removeProperty(prop));
  delete pane.dataset._tilePreSnap;
  delete pane.dataset._tileZone;
  delete pane._preDockSnapshot;
  delete pane._dockSide;
  delete pane._dockSuspended;
}

function _restoreNotesSidebarDock(pane) {
  if (!pane || window.innerWidth <= 768) return;
  _clearNotesSnapStyles(pane);
  if (!pane.isConnected) return;
  applyEdgeDock(pane, 'right');
}

function _loadPendingHighlights() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_PENDING_HIGHLIGHT_KEY) || '[]')); }
  catch { return new Set(); }
}
function _loadGlowedReminders() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_GLOWED_KEY) || '[]')); }
  catch { return new Set(); }
}
function _saveGlowedReminders(set) {
  try { localStorage.setItem(REMINDER_GLOWED_KEY, JSON.stringify([...set])); } catch {}
}
function _loadActiveHighlights() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_ACTIVE_HIGHLIGHT_KEY) || '[]')); }
  catch { return new Set(); }
}
function _saveActiveHighlights(set) {
  try { localStorage.setItem(REMINDER_ACTIVE_HIGHLIGHT_KEY, JSON.stringify([...set])); } catch {}
}
function _clearViewedReminderGlows() {
  const active = _loadActiveHighlights();
  if (!active.size) return;
  _saveActiveHighlights(new Set());
  document.querySelectorAll('.note-card-reminder-fired-sticky').forEach(card => {
    card.classList.remove('note-card-reminder-fired-sticky');
  });
}
function _setReminderCardGlow(noteId, on = true) {
  if (!noteId) return;
  const active = _loadActiveHighlights();
  if (on) active.add(noteId);
  else active.delete(noteId);
  _saveActiveHighlights(active);
  document.querySelectorAll(`.note-card[data-note-id="${noteId}"]`).forEach(card => {
    card.classList.toggle('note-card-reminder-fired-sticky', on);
  });
}
// A note has an active reminder when its due time has passed and the user
// hasn't archived or fully completed it. Used for both sorting (bumped above
// the rest of the unpinned section) and the entry-glow flush.
function _hasActiveReminder(n) {
  if (!n || n.archived || _isNoteFullyDone(n)) return false;
  if (!n.due_date) return false;
  const t = new Date(n.due_date).getTime();
  return !isNaN(t) && t <= Date.now();
}
function _savePendingHighlights(set) {
  try { localStorage.setItem(REMINDER_PENDING_HIGHLIGHT_KEY, JSON.stringify([...set])); }
  catch {}
}
function _queuePendingHighlight(noteId) {
  const set = _loadPendingHighlights();
  set.add(noteId);
  _savePendingHighlights(set);
}
function _flushPendingHighlights() {
  // Fresh firings (queued by the background loop while the panel was closed)
  // glow unconditionally — a notification just told the user something
  // happened, so we always point at the note even if it was glowed before.
  const queued = _loadPendingHighlights();
  const glowed = _loadGlowedReminders();
  const toGlow = new Set(queued);
  // For notes that are merely overdue at open time (no fresh firing event),
  // only glow the ones we haven't already shown — otherwise reopening the
  // panel keeps lighting up old reminders forever.
  for (const n of _notes) {
    if (!_hasActiveReminder(n) || !_hasTimeComponent(n.due_date)) continue;
    if (queued.has(n.id) || !glowed.has(n.id)) toGlow.add(n.id);
  }
  // Always consume the queue.
  _savePendingHighlights(new Set());
  if (!toGlow.size) return;
  let firstCard = null;
  for (const id of toGlow) {
    const card = document.querySelector(`.note-card[data-note-id="${id}"]`);
    if (!card) continue;
    _setReminderCardGlow(id, true);
    if (!firstCard) firstCard = card;
    glowed.add(id);
  }
  _saveGlowedReminders(glowed);
  // Bring the first one into view so it can't get buried below the fold.
  if (firstCard) {
    requestAnimationFrame(() => {
      try { firstCard.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      catch { firstCard.scrollIntoView(); }
    });
  }
}

const COLORS = [
  { name: 'none',    value: '' },
  { name: 'red',     value: 'red' },
  { name: 'orange',  value: 'orange' },
  { name: 'yellow',  value: 'yellow' },
  { name: 'green',   value: 'green' },
  { name: 'blue',    value: 'blue' },
  { name: 'purple',  value: 'purple' },
  { name: 'custom',  value: 'custom' },  // sentinel — clicking opens native color picker
];

const _CUSTOM_GRADIENT = 'conic-gradient(from 0deg, #e06c75, #d19a66, #e5c07b, #98c379, #61afef, #c678dd, #e06c75)';

// A note's color is one of: '' (none), a preset name (red/orange/…), or a
// sentinel "bg:<image-url>" for a custom background image uploaded by the user.
function _isBgImage(c) { return typeof c === 'string' && c.startsWith('bg:'); }
function _bgImageUrl(c) { return _isBgImage(c) ? c.slice(3) : ''; }

function _dotBg(value, noteColor) {
  if (value === 'custom') {
    const url = _bgImageUrl(noteColor);
    return url ? `center/cover no-repeat url('${url}')` : _CUSTOM_GRADIENT;
  }
  return COLOR_HEX[value];
}

function _dotIsActive(value, noteColor) {
  if (value === 'custom') return _isBgImage(noteColor);
  return value === (noteColor || '');
}

// Inline style for a note card/form when color is a custom bg image.
function _customColorStyle(c) {
  if (!_isBgImage(c)) return '';
  const url = _bgImageUrl(c);
  return `background-image: linear-gradient(color-mix(in srgb, var(--panel) 60%, transparent), color-mix(in srgb, var(--panel) 60%, transparent)), url('${url}'); background-size: cover; background-position: center; border-color: color-mix(in srgb, var(--fg) 25%, var(--border));`;
}

// Open a file picker, upload the chosen image, and resolve with the URL.
function _pickCustomBgImage() {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.style.cssText = 'position:fixed; left:-9999px; top:-9999px;';
    document.body.appendChild(input);
    let done = false;
    const finish = (v) => { if (done) return; done = true; input.remove(); resolve(v); };
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      if (!file) return finish(null);
      const fd = new FormData();
      fd.append('files', file);
      try {
        const res = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd, credentials: 'same-origin' });
        const data = await res.json();
        const fileId = data.files?.[0]?.id;
        if (!fileId) throw new Error('Upload failed');
        finish(`${API_BASE}/api/upload/${fileId}`);
      } catch { finish(null); }
    });
    // Best-effort cleanup if user dismisses the dialog.
    setTimeout(() => { if (!done && !input.files?.length) finish(null); }, 30000);
    input.click();
  });
}

const COLOR_HEX = {
  '':       'var(--border)',
  // Pale/pastel palette — matches the calendar event color picker.
  red:      '#f0b5ba',
  orange:   '#e8ccb2',
  yellow:   '#f2dfbd',
  green:    '#cce0bc',
  blue:     '#b0d7f7',
  purple:   '#e2bcee',
};

// ---- API ----

let _loading = false;
// Undo stack — most recent action is at the end. We cap it small because the
// only entries that survive a panel reload are in-memory anyway.
const _undoStack = [];
function _pushUndo(entry) {
  _undoStack.push(entry);
  if (_undoStack.length > 20) _undoStack.shift();
}
function _popAndRunUndo() {
  const entry = _undoStack.pop();
  if (entry) entry.run();
  return !!entry;
}

function _undoArchive(note, prevIdx) {
  // Re-insert at original position and clear archived flag on the server.
  const safeIdx = Math.min(Math.max(prevIdx, 0), _notes.length);
  _notes.splice(safeIdx, 0, { ...note, archived: false });
  _renderNotes();
  _patchNote(note.id, { archived: false }).catch(() => {
    // Roll back local insertion if the server refuses
    const i = _notes.findIndex(n => n.id === note.id);
    if (i >= 0) _notes.splice(i, 1);
    _renderNotes();
    uiModule.showError('Undo failed');
  });
}

async function _fetchNotes() {
  _loading = true;
  try {
    const url = `${API_BASE}/api/notes${_showingArchived ? '?archived=true' : ''}`;
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) { _notes = []; return; }
    const data = await res.json();
    _notes = data.notes || data || [];
  } catch (e) {
    console.error('Failed to fetch notes:', e);
    _notes = [];
  } finally {
    _loading = false;
  }
}

async function _saveNote(note) {
  const method = note.id ? 'PUT' : 'POST';
  const url = note.id ? `${API_BASE}/api/notes/${note.id}` : `${API_BASE}/api/notes`;
  const res = await fetch(url, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(note),
  });
  if (!res.ok) throw new Error('Failed to save note');
  return await res.json();
}

async function _deleteNoteApi(id) {
  // v2 review — used to swallow 4xx/5xx silently. Throw so callers can
  // distinguish success vs failure and toast accordingly.
  const r = await fetch(`${API_BASE}/api/notes/${id}`, { method: 'DELETE', credentials: 'same-origin' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

async function _patchNote(id, patch) {
  const res = await fetch(`${API_BASE}/api/notes/${id}`, {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error('Failed to update note');
  return await res.json();
}

// ---- Helpers ----

function _esc(s) { return uiModule.esc ? uiModule.esc(s || '') : (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function _attrEsc(s) {
  return String(s || '')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/`/g, '&#96;');
}
// Image src guard — reject anything that isn't a relative path, http(s), or
// raster data URL so an AI-saved note can't slip script-capable media into the
// rendered <img>.
function _safeImgSrc(s) {
  const v = (s || '').trim();
  if (!v) return '';
  if (v.startsWith('/') || v.startsWith('./') || v.startsWith('../')) return v;
  if (/^https?:\/\//i.test(v) || /^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(v)) return v;
  return '';
}

// Escape then turn http(s)://... URLs into clickable anchors. XSS-safe.
// Allow balanced `(...)` inside the URL (Wikipedia, MD links) by accepting
// `(` in the body, then trim a trailing unmatched `)` afterwards.
function _linkify(s) {
  const escaped = _esc(s);
  const urlRe = /\b((?:https?:\/\/|www\.)[^\s<>"']+[^\s<>"'.,;:!?\]])/g;
  return escaped.replace(urlRe, (m) => {
    let url = m;
    // Trim a trailing ')' that doesn't have a matching '(' inside the URL
    if (url.endsWith(')') && (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length) {
      url = url.slice(0, -1);
    }
    const href = url.startsWith('www.') ? `https://${url}` : url;
    return `<a href="${_attrEsc(href)}" class="note-link" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${url}</a>` + (url !== m ? m.slice(url.length) : '');
  });
}
function _uid() { return Math.random().toString(36).slice(2, 10); }

// Mobile swipe-to-dismiss for the notes sheet. Mirrors the document panel
// gesture (finger-following, velocity-based dismiss, rubber-band, snap-back)
// so both sheets feel identical; dismisses via the notes closePanel('down').
function _wireNotesSwipeDismiss(el, pane) {
  if (!el || !pane) return;
  const DISMISS_THRESHOLD = 50, VELOCITY_THRESHOLD = 0.3, RUBBER = 0.35;
  let startY = 0, startX = 0, lastY = 0, lastT = 0, velocity = 0;
  let dragging = false, cancelled = false;

  el.addEventListener('touchstart', (e) => {
    if (window.innerWidth > 768 || e.touches.length !== 1) return;
    if (e.target.closest('button, input, select, label, textarea')) return;
    const t = e.touches[0];
    startY = t.clientY; startX = t.clientX; lastY = startY; lastT = e.timeStamp;
    velocity = 0; dragging = false; cancelled = false;
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (cancelled || window.innerWidth > 768) return;
    const t = e.touches[0];
    const dx = Math.abs(t.clientX - startX);
    const dy = t.clientY - startY;
    if (!dragging) {
      if (dx > 40 && dx > Math.abs(dy) * 2) { cancelled = true; return; }
      if (Math.abs(dy) > 8) {
        dragging = true;
        pane.style.animation = 'none';
        pane.style.transition = 'none';
        pane.style.willChange = 'transform';
      } else return;
    }
    const dt = e.timeStamp - lastT;
    if (dt > 0) velocity = velocity * 0.6 + ((t.clientY - lastY) / dt) * 0.4;
    lastY = t.clientY; lastT = e.timeStamp;
    e.preventDefault();
    pane.style.transform = dy > 0 ? `translateY(${dy}px)` : `translateY(${dy * RUBBER}px)`;
  }, { passive: false });

  const endSwipe = () => {
    if (!dragging) return;
    dragging = false;
    pane.style.willChange = '';
    const dy = lastY - startY;
    if (dy > DISMISS_THRESHOLD || (dy > 20 && velocity > VELOCITY_THRESHOLD)) {
      // Slide fully off-screen, then minimise. Keep it translated down (don't
      // reset) so it doesn't flash back before closePanel removes it.
      pane.style.transition = 'transform 0.2s cubic-bezier(0.2, 0, 0.4, 1)';
      pane.style.transform = 'translateY(100%)';
      setTimeout(() => closePanel('down'), 200);
    } else {
      pane.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
      pane.style.transform = '';
      setTimeout(() => { pane.style.transition = ''; }, 260);
    }
  };
  el.addEventListener('touchend', endSwipe, { passive: true });
  el.addEventListener('touchcancel', endSwipe, { passive: true });
}

function _hasTimeComponent(dateStr) {
  return typeof dateStr === 'string' && /T\d{2}:\d{2}/.test(dateStr);
}

function _formatDueDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const now = new Date();
  const hasTime = _hasTimeComponent(dateStr);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((due - today) / 86400000);
  const timeStr = hasTime ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
  if (hasTime && d < now) return 'overdue';
  if (!hasTime && diffDays < 0) return 'overdue';
  if (diffDays === 0) return hasTime ? timeStr : 'today';
  if (diffDays === 1) return hasTime ? `tmrw ${timeStr}` : 'tomorrow';
  const dateLabel = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return hasTime ? `${dateLabel} ${timeStr}` : dateLabel;
}

function _isDueOverdue(dateStr) {
  if (!dateStr) return false;
  const d = new Date(dateStr);
  if (isNaN(d)) return false;
  if (_hasTimeComponent(dateStr)) return d < new Date();
  return d < new Date(new Date().toDateString());
}

function _isDueTodayOrOverdue(dateStr) {
  if (!dateStr) return false;
  const d = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return due <= today;
}

function _isNoteFullyDone(note) {
  if (_hasItems(note) && Array.isArray(note.items) && note.items.length > 0) {
    return note.items.every(it => it.done);
  }
  return false;
}

// A "checklist note" — todo or goal — has structured items[] that the cards
// render as checkboxes and that "fully done" / progress logic reads from.
function _hasItems(note) {
  return note && (note.note_type === 'todo' || note.note_type === 'goal');
}

// Compact " N/M" progress string for a goal's checklist. Empty when the goal
// has no steps yet (e.g. AI breakdown is still in flight or was cancelled).
function _goalProgress(note) {
  if (!Array.isArray(note?.items) || note.items.length === 0) return '';
  const done = note.items.filter(it => it.done).length;
  return ` ${done}/${note.items.length}`;
}

// The next unchecked step in a goal, or null if all done / no items.
function _nextGoalStep(note) {
  if (!Array.isArray(note?.items)) return null;
  for (let i = 0; i < note.items.length; i++) {
    if (!note.items[i].done) return { idx: i, item: note.items[i] };
  }
  return null;
}

// ---- Reminder presets ----

function _laterTodayDate() {
  const now = new Date();
  const eight = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0); // 6pm today
  // If less than 1 hour before 6pm, push to "in 3 hours" instead
  if (eight - now < 60 * 60 * 1000) return new Date(now.getTime() + 3 * 60 * 60 * 1000);
  return eight;
}
function _tomorrowDate() {
  const t = new Date();
  t.setDate(t.getDate() + 1);
  t.setHours(8, 0, 0, 0);
  return t;
}
function _nextWeekDate() {
  const t = new Date();
  const daysUntilMon = (8 - t.getDay()) % 7 || 7;
  t.setDate(t.getDate() + daysUntilMon);
  t.setHours(8, 0, 0, 0);
  return t;
}
function _toLocalDatetimeStr(d) {
  // Format as YYYY-MM-DDTHH:MM (local, no TZ)
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function _formatReminderTag(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1);
  const isTomorrow = d.toDateString() === tomorrow.toDateString();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (sameDay) return `Today, ${time}`;
  if (isTomorrow) return `Tomorrow, ${time}`;
  const dateLabel = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return `${dateLabel}, ${time}`;
}
// Build a human label for a date's nth-weekday-of-month, e.g. "2nd Tuesday"
const _ORDINALS = ['1st', '2nd', '3rd', '4th', '5th'];
const _DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
function _nthWeekdayLabel(d) {
  const n = Math.ceil(d.getDate() / 7); // 1..5
  return `${_ORDINALS[n - 1] || `${n}th`} ${_DAYS[d.getDay()]}`;
}
function _isLastWeekdayOfMonth(d) {
  const test = new Date(d);
  test.setDate(d.getDate() + 7);
  return test.getMonth() !== d.getMonth();
}
// Find the Nth occurrence of `weekday` in a given year/month. n=1..5.
// If n=5 and there's no 5th occurrence, returns the 4th (so "5th Monday" still works).
function _nthWeekdayOfMonth(year, month, weekday, n) {
  const first = new Date(year, month, 1);
  const offset = (weekday - first.getDay() + 7) % 7;
  let day = 1 + offset + (n - 1) * 7;
  // Last day of month
  const lastDay = new Date(year, month + 1, 0).getDate();
  if (day > lastDay) day -= 7;
  return new Date(year, month, day, 0, 0, 0);
}
function _lastWeekdayOfMonth(year, month, weekday) {
  const lastDay = new Date(year, month + 1, 0);
  const back = (lastDay.getDay() - weekday + 7) % 7;
  return new Date(year, month, lastDay.getDate() - back, 0, 0, 0);
}

// Snap a chosen datetime forward to the next slot matching a normalized
// recurrence pattern (preserving time-of-day, strictly in the future).
// Anchors to the user's chosen date when it's in the future (so picking a
// recurrence on a far-future date doesn't drag it back to today); otherwise
// anchors to "now". Returns null for daily/yearly/none.
function _snapToRepeat(currentDate, normRepeat) {
  const hh = currentDate.getHours();
  const mm = currentDate.getMinutes();
  const now = Date.now();
  const anchor = currentDate.getTime() > now ? currentDate : new Date();
  const parts = normRepeat.split(':');
  const kind = parts[0];
  if (kind === 'weekly') {
    const targetWd = parseInt(parts[1], 10);
    if (isNaN(targetWd)) return null;
    const d = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), hh, mm, 0, 0);
    const delta = (targetWd - d.getDay() + 7) % 7;
    d.setDate(d.getDate() + delta);
    if (d.getTime() <= now) d.setDate(d.getDate() + 7);
    return d;
  }
  if (kind === 'monthly') {
    const sub = parts[1];
    let y = anchor.getFullYear();
    let m = anchor.getMonth();
    // Walk forward up to 14 months to find the next matching slot.
    for (let tries = 0; tries < 14; tries++) {
      let target;
      if (sub === 'day') {
        const wantDay = parseInt(parts[2], 10);
        if (isNaN(wantDay)) return null;
        const lastDay = new Date(y, m + 1, 0).getDate();
        target = new Date(y, m, Math.min(wantDay, lastDay));
      } else if (sub === 'nth') {
        const n = parseInt(parts[2], 10);
        const wd = parseInt(parts[3], 10);
        if (isNaN(n) || isNaN(wd)) return null;
        target = _nthWeekdayOfMonth(y, m, wd, n);
      } else if (sub === 'last') {
        const wd = parseInt(parts[2], 10);
        if (isNaN(wd)) return null;
        target = _lastWeekdayOfMonth(y, m, wd);
      } else {
        return null;
      }
      target.setHours(hh, mm, 0, 0);
      if (target.getTime() > now && target.getTime() >= anchor.getTime()) return target;
      m++;
      if (m > 11) { m = 0; y++; }
    }
    return null;
  }
  return null;
}

// Render a repeat value as a human-readable label.
// originalDate is required only to interpret legacy bare values ("weekly", "monthly", ...).
// All call sites pass it; missing it would silently misinterpret legacy values.
function _formatRepeatLabel(repeat, originalDate) {
  if (!repeat || repeat === 'none') return '';
  const norm = _normalizeRepeat(repeat, originalDate);
  if (norm === 'daily') return 'Daily';
  if (norm === 'yearly') return 'Yearly';
  const parts = norm.split(':');
  if (parts[0] === 'weekly') {
    const wd = parseInt(parts[1], 10);
    if (isNaN(wd)) return 'Weekly';
    return `Weekly on ${_DAYS[wd]}s`;
  }
  if (parts[0] === 'monthly') {
    if (parts[1] === 'day') return `Monthly on day ${parts[2]}`;
    if (parts[1] === 'nth') {
      const n = parseInt(parts[2], 10);
      const wd = parseInt(parts[3], 10);
      return `Monthly on ${_ORDINALS[n - 1] || `${n}th`} ${_DAYS[wd]}`;
    }
    if (parts[1] === 'last') {
      const wd = parseInt(parts[2], 10);
      return `Monthly on last ${_DAYS[wd]}`;
    }
  }
  return norm;
}

// ---- Reminders ----

function _loadFiredReminders() {
  try { return new Set(JSON.parse(localStorage.getItem(REMINDER_FIRED_KEY) || '[]')); }
  catch { return new Set(); }
}

function _saveFiredReminders(set) {
  try { localStorage.setItem(REMINDER_FIRED_KEY, JSON.stringify([...set])); }
  catch {}
}

async function _ensureNotificationPermission() {
  if (!('Notification' in window)) return false;
  if (Notification.permission === 'granted') return true;
  if (Notification.permission === 'denied') return false;
  try { const p = await Notification.requestPermission(); return p === 'granted'; }
  catch { return false; }
}

// Repeat format:
//   none
//   daily
//   weekly:W              W = 0-6 (Sun..Sat)
//   monthly:day:D         D = 1-31 (calendar day)
//   monthly:nth:N:W       N = 1-4 (1st..4th), W = 0-6 (weekday)
//   monthly:last:W        W = 0-6 (last weekday of month)
//   yearly
// Legacy "weekly", "monthly", "monthly_nth_weekday", "monthly_last_weekday"
// are normalized using the original due_date's weekday/Nth.
function _normalizeRepeat(repeat, originalDate) {
  if (!repeat || repeat === 'none') return 'none';
  if (repeat === 'daily' || repeat === 'yearly') return repeat;
  if (/^(weekly|monthly):/.test(repeat)) return repeat;
  // Legacy bare values — derive params from the original date
  const wd = originalDate.getDay();
  const n = Math.ceil(originalDate.getDate() / 7);
  if (repeat === 'weekly') return `weekly:${wd}`;
  if (repeat === 'monthly') return `monthly:day:${originalDate.getDate()}`;
  if (repeat === 'monthly_nth_weekday') return `monthly:nth:${n}:${wd}`;
  if (repeat === 'monthly_last_weekday') return `monthly:last:${wd}`;
  return repeat;
}

function _advanceRecurring(dateStr, repeat) {
  const orig = new Date(dateStr);
  const hh = orig.getHours();
  const mm = orig.getMinutes();
  let d = new Date(orig);
  const norm = _normalizeRepeat(repeat, orig);
  if (norm === 'none') return null;

  function step() {
    if (norm === 'daily') {
      d.setDate(d.getDate() + 1);
      return;
    }
    if (norm === 'yearly') {
      d.setFullYear(d.getFullYear() + 1);
      return;
    }
    const parts = norm.split(':');
    const kind = parts[0];
    if (kind === 'weekly') {
      // Snap to the requested weekday in the next 1-7 days
      const targetWd = parseInt(parts[1], 10);
      let delta = (targetWd - d.getDay() + 7) % 7;
      if (delta === 0) delta = 7;
      d.setDate(d.getDate() + delta);
      d.setHours(hh, mm, 0, 0);
      return;
    }
    if (kind === 'monthly') {
      const sub = parts[1];
      const ny = d.getFullYear() + (d.getMonth() === 11 ? 1 : 0);
      const nm = (d.getMonth() + 1) % 12;
      let target;
      if (sub === 'day') {
        const wantDay = parseInt(parts[2], 10);
        const lastDay = new Date(ny, nm + 1, 0).getDate();
        target = new Date(ny, nm, Math.min(wantDay, lastDay));
      } else if (sub === 'nth') {
        const n = parseInt(parts[2], 10);
        const wd = parseInt(parts[3], 10);
        target = _nthWeekdayOfMonth(ny, nm, wd, n);
      } else if (sub === 'last') {
        const wd = parseInt(parts[2], 10);
        target = _lastWeekdayOfMonth(ny, nm, wd);
      } else {
        d = null; return;
      }
      target.setHours(hh, mm, 0, 0);
      d = target;
      return;
    }
    d = null;
  }

  step();
  if (d === null) return null;
  const now = Date.now();
  // Cap catch-up to avoid runaway on a malformed/very-old date.
  let guard = 5000;
  while (d.getTime() <= now) {
    if (--guard <= 0) return null;
    step();
    if (d === null) return null;
  }
  return _toLocalDatetimeStr(d);
}

function _checkReminders() {
  if (!_notes.length) return;
  const now = Date.now();
  const fired = _loadFiredReminders();
  let changed = false;
  for (const note of _notes) {
    if (!note.due_date || note.archived) continue;
    if (!_hasTimeComponent(note.due_date)) continue;
    if (fired.has(note.id)) continue;
    const due = new Date(note.due_date).getTime();
    if (isNaN(due)) continue;
    if (due <= now && due > now - 60000) {
      _fireReminder(note);
      // Recurring? advance the due_date instead of marking as fired
      if (note.repeat && note.repeat !== 'none') {
        const next = _advanceRecurring(note.due_date, note.repeat);
        if (next) {
          note.due_date = next;
          _patchNote(note.id, { due_date: next }).catch(() => {});
          // Don't add to fired — new due_date is in the future
          continue;
        }
      }
      fired.add(note.id);
      changed = true;
    } else if (due <= now - 60000) {
      // Past, never seen — silently advance recurring or mark fired
      if (note.repeat && note.repeat !== 'none') {
        const next = _advanceRecurring(note.due_date, note.repeat);
        if (next) {
          note.due_date = next;
          _patchNote(note.id, { due_date: next }).catch(() => {});
          continue;
        }
      }
      fired.add(note.id);
      changed = true;
    }
  }
  if (changed) _saveFiredReminders(fired);
  // Always refresh badge — fired state may have changed visually without note mutation
  _updateRailBadge();
}

function _fireReminder(note) {
  const title = note.title || 'Note reminder';
  // Include the verbatim note content so the email/notification actually
  // shows what to do, not just a count. Cap the per-item lines (8 max) and
  // total length so the body stays inbox-friendly.
  let rawBody;
  if (_hasItems(note)) {
    const pending = (note.items || [])
      .filter(i => !i.done && !i.checked)
      .map(i => (i.text || '').trim())
      .filter(Boolean);
    if (pending.length) {
      const shown = pending.slice(0, 8).map(t => `- ${t}`).join('\n');
      const extra = pending.length > 8 ? `\n…and ${pending.length - 8} more` : '';
      rawBody = `Pending (${pending.length}):\n${shown}${extra}`;
    } else {
      rawBody = `${(note.items || []).length} item${(note.items || []).length === 1 ? '' : 's'}`;
    }
  } else {
    rawBody = (note.content || '').slice(0, 400);
  }

  // Ask the server to dispatch according to user settings. The server may
  // return an LLM-written synthesis line and/or send an email. We still show
  // a local browser notification so the user gets immediate feedback even if
  // the server path is disabled or slow.
  const showLocal = (body) => {
    if ('Notification' in window && Notification.permission === 'granted') {
      try {
        const n = new Notification(title, { body, tag: 'note-' + note.id, icon: '/static/favicon.ico' });
        n.onclick = () => { window.focus(); openPanel(); n.close(); };
      } catch {}
    }
    if (uiModule?.showToast) uiModule.showToast(title);
  };

  // Fire-and-forget server dispatch. If synthesis comes back quickly enough,
  // use it as the notification body; otherwise the local notification has
  // already shown with the raw body.
  let shown = false;
  const timer = setTimeout(() => { if (!shown) { shown = true; showLocal(rawBody); } }, 1500);

  fetch('/api/notes/fire-reminder', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note_id: note.id, title, body: rawBody }),
  })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      clearTimeout(timer);
      if (shown) return;
      shown = true;
      const body = (data && data.synthesis) ? data.synthesis : rawBody;
      showLocal(body);
    })
    .catch(() => {
      clearTimeout(timer);
      if (!shown) { shown = true; showLocal(rawBody); }
    });

  // Pulse the card if visible; otherwise queue it so the next time the user
  // opens the notes panel the card gets a brief glow.
  _setReminderCardGlow(note.id, true);
  const card = document.querySelector(`.note-card[data-note-id="${note.id}"]`);
  if (card) {
    card.classList.add('note-card-reminder-fired');
    setTimeout(() => card.classList.remove('note-card-reminder-fired'), 3000);
  } else {
    _queuePendingHighlight(note.id);
  }
}

function _startReminderLoop() {
  if (_reminderTimer) return;
  _reminderTimer = setInterval(_checkReminders, 30000);
  _checkReminders(); // run once immediately
}

function _countDueReminders() {
  return _notes.filter(n => !n.archived && _isDueTodayOrOverdue(n.due_date) && !_isNoteFullyDone(n)).length;
}

let _firedDotDismissedAt = (() => {
  try {
    const v = parseInt(localStorage.getItem(REMINDER_DISMISSED_AT_KEY) || '0', 10);
    return Number.isFinite(v) && v > 0 ? v : 0;
  } catch { return 0; }
})();

function _countFiredReminders() {
  // Reminders whose time has actually passed (not just date-today),
  // and which fired after the last user dismissal.
  const now = Date.now();
  return _notes.filter(n => {
    if (n.archived || _isNoteFullyDone(n)) return false;
    if (!n.due_date || !_hasTimeComponent(n.due_date)) return false;
    const t = new Date(n.due_date).getTime();
    if (isNaN(t) || t > now) return false;
    return t > _firedDotDismissedAt;
  }).length;
}

export function dismissFiredReminderDot() {
  _firedDotDismissedAt = Date.now();
  try { localStorage.setItem(REMINDER_DISMISSED_AT_KEY, String(_firedDotDismissedAt)); } catch {}
  _updateRailBadge();
}

function _updateRailBadge() {
  const fired = _countFiredReminders();
  // Rail (mini sidebar) — only show the count when reminders have ACTUALLY
  // fired since the last dismissal (i.e. you haven't opened notes yet).
  // Showing every overdue note forever made the badge feel permanent.
  const railBtn = document.getElementById('rail-notes');
  if (railBtn) {
    let badge = railBtn.querySelector('.rail-notes-badge');
    if (fired > 0) {
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'rail-notes-badge';
        railBtn.appendChild(badge);
      }
      badge.textContent = fired > 99 ? '99+' : String(fired);
      badge.classList.add('fired');
    } else if (badge) {
      badge.remove();
    }
  }
  // Main sidebar button
  const sidebarBtn = document.getElementById('tool-notes-btn');
  if (sidebarBtn) {
    let dot = sidebarBtn.querySelector('.tool-notes-dot');
    if (fired > 0) {
      if (!dot) {
        dot = document.createElement('span');
        dot.className = 'tool-notes-dot';
        sidebarBtn.appendChild(dot);
      }
    } else if (dot) {
      dot.remove();
    }
  }
  // Individual note cards — pulse ones with fired reminders
  document.querySelectorAll('.note-card').forEach(card => {
    const id = card.dataset.noteId;
    const note = _notes.find(n => n.id === id);
    if (!note || note.archived || _isNoteFullyDone(note)) {
      card.classList.remove('note-card-reminder-due');
      return;
    }
    if (note.due_date && _hasTimeComponent(note.due_date)) {
      const t = new Date(note.due_date).getTime();
      card.classList.toggle('note-card-reminder-due', !isNaN(t) && t <= Date.now());
    } else {
      card.classList.remove('note-card-reminder-due');
    }
  });
}

export async function refreshDueBadge(opts = {}) {
  // Usually lightweight, but callers that just created a note reminder can
  // force a refresh so the background reminder loop sees it immediately.
  if (opts.force || _notes.length === 0) {
    try {
      const wasArchived = _showingArchived;
      _showingArchived = false;
      await _fetchNotes();
      _showingArchived = wasArchived;
    } catch {}
  }
  _updateRailBadge();
}

// ---- Panel ----

export function openPanel() {
  if (_open) return;
  _open = true;
  _editingId = null;
  _clearViewedReminderGlows();
  _firedDotDismissedAt = Date.now();
  try { localStorage.setItem(REMINDER_DISMISSED_AT_KEY, String(_firedDotDismissedAt)); } catch {}

  const container = document.getElementById('chat-container');
  if (!container) return;

  document.body.classList.add('notes-view');

  // On mobile the notes panel takes the whole screen — auto-close the
  // sidebar so the panel isn't cropped underneath it.
  if (window.innerWidth <= 768) {
    const sb = document.getElementById('sidebar');
    if (sb) sb.classList.add('hidden');
    document.body.classList.add('sidebar-collapsed');
  }
  // Mobile mode: tiles become read-only previews (no inline checkbox /
  // edit / archive / etc.), tap opens a fullscreen edit overlay,
  // long-press enters drag-to-reorder mode. See _bindCardEvents +
  // .notes-mobile-mode CSS rules.
  if (_isNotesMobileMode()) document.body.classList.add('notes-mobile-mode');

  // Toggle button state
  const btn = document.getElementById('tool-notes-btn');
  if (btn) btn.classList.add('active');

  // Create panel
  const pane = document.createElement('div');
  pane.id = 'notes-pane';
  pane.className = 'notes-pane';
  pane.innerHTML = `
    <div class="notes-mobile-grabber" id="notes-mobile-grabber" aria-hidden="true"></div>
    <div class="notes-pane-header">
      <h4 class="notes-pane-title"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2.5px;margin-right:6px"><path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h5"/><path d="M8 17.5 15.5 10l2.5 2.5L10.5 20H8z"/></svg>Notes</h4>
      <span style="flex:1"></span>
      <button id="notes-archive-toggle" class="doc-action-icon-btn notes-header-text-btn" title="View archive" style="opacity:0.8;gap:5px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg>
        <span class="notes-header-btn-label">Archive</span>
      </button>
      <button id="notes-view-toggle" class="doc-action-icon-btn notes-header-text-btn" title="Toggle view" style="opacity:0.8;gap:5px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        <span class="notes-header-btn-label">Toggle</span>
      </button>
      <button id="notes-minimize-btn" class="modal-minimize-btn" title="Minimize" aria-label="Minimize notes" style="position:relative;left:2px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" aria-hidden="true"><line x1="6" y1="18" x2="18" y2="18"/></svg></button>
    </div>
    <div class="notes-search-bar">
      <input type="text" id="notes-search" class="memory-search-input" placeholder="Search notes…" autocomplete="off" />
      <button id="notes-select-btn" class="notes-select-trigger" type="button">Select</button>
    </div>
    <div id="notes-bulk-bar" class="memory-bulk-bar hidden">
      <label class="memory-bulk-check-all"><input type="checkbox" id="notes-select-all" /> All</label>
      <span id="notes-selected-count">0 Selected</span>
      <span style="flex:1"></span>
      <button id="notes-bulk-archive" class="memory-toolbar-btn" disabled>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg>Archive
      </button>
      <button id="notes-bulk-delete" class="memory-toolbar-btn danger" disabled>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>Delete
      </button>
    </div>
    <div class="notes-pane-body"></div>
  `;

  // On mobile open as a full-screen bottom sheet (slide up), not the desktop
  // side panel. Set inline so it wins over the base .notes-pane rule regardless
  // of cascade specifics (the CSS @media override wasn't reliably applying,
  // which left it as a side panel squeezing the chat).
  if (window.innerWidth <= 768) {
    pane.style.position = 'fixed';
    pane.style.inset = '0';
    pane.style.width = '100%';
    pane.style.maxWidth = '100%';
    pane.style.zIndex = '170';
    pane.style.borderRadius = '14px 14px 0 0';
    pane.style.animation = 'sheet-enter 0.25s cubic-bezier(0.2, 0.8, 0.2, 1) both';
    pane.style.transformOrigin = 'bottom center';
  }

  // Mount on body so Notes can behave like the other draggable windows. On
  // desktop it is immediately docked to the right by _restoreNotesSidebarDock.
  const backdrop = document.createElement('div');
  backdrop.className = 'notes-pane-backdrop';
  backdrop.id = 'notes-pane-backdrop';
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) closePanel('down');
  });
  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);
  _wireNotesWindow(pane);
  _restoreNotesSidebarDock(pane);

  // Events
  // (Close chevron removed — swipe down on mobile, tool-rail toggle on desktop.)

  // Mobile: swipe the grab handle / header down to dismiss (minimise to chip).
  // Mirrors the document sheet gesture — finger-following, velocity-based
  // dismiss, rubber-band on up-drag, spring snap-back.
  _wireNotesSwipeDismiss(pane.querySelector('.notes-mobile-grabber'), pane);
  _wireNotesSwipeDismiss(pane.querySelector('.notes-pane-header'), pane);

  const minBtn = document.getElementById('notes-minimize-btn');
  if (minBtn) minBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    closePanel('down');
  });
  // Search
  const searchEl = document.getElementById('notes-search');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      _searchQuery = searchEl.value.trim().toLowerCase();
      _renderNotes();
    });
  }

  // View toggle
  const archiveBtn = document.getElementById('notes-archive-toggle');
  if (archiveBtn) {
    const ARCHIVE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg><span class="notes-header-btn-label">Archive</span>';
    const CLOSE_ICON   = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg><span class="notes-header-btn-label">Archive</span>';
    const syncArchiveBtn = () => {
      archiveBtn.classList.toggle('active', _showingArchived);
      archiveBtn.title = _showingArchived ? 'Exit archive' : 'View archive';
      archiveBtn.style.opacity = _showingArchived ? '1' : '0.8';
      // Swap to an X while in archive view so it doubles as a close-back-
      // to-active-notes toggle.
      archiveBtn.innerHTML = _showingArchived ? CLOSE_ICON : ARCHIVE_ICON;
      // Tint the whole pane so it's obvious you're not in the active list.
      pane.classList.toggle('notes-pane-archive', _showingArchived);
    };
    syncArchiveBtn();
    archiveBtn.addEventListener('click', async () => {
      _showingArchived = !_showingArchived;
      _selectedIds.clear();
      syncArchiveBtn();
      // Brief fade so the body content swap doesn't snap — the bg-tint
      // change is already eased by CSS transitions on .notes-pane*.
      const _bodyEl = document.querySelector('#notes-pane .notes-pane-body');
      if (_bodyEl) {
        _bodyEl.style.transition = 'opacity 0.18s ease';
        _bodyEl.style.opacity = '0.25';
      }
      await _fetchNotes();
      _renderNotes();
      if (_bodyEl) {
        requestAnimationFrame(() => {
          _bodyEl.style.opacity = '';
          _bodyEl.addEventListener('transitionend', () => { _bodyEl.style.transition = ''; }, { once: true });
        });
      }
    });
  }
  const viewBtn = document.getElementById('notes-view-toggle');
  if (viewBtn) {
    pane.classList.toggle('notes-view-grid', _viewMode === 'grid');
    // Label shows what you'll switch TO — "Grid" while in list, "List" while in grid.
    const _setViewLabel = () => {
      const lbl = viewBtn.querySelector('.notes-header-btn-label');
      if (lbl) lbl.textContent = _viewMode === 'grid' ? 'List' : 'Grid';
    };
    _setViewLabel();
    requestAnimationFrame(() => _applyMasonry(document.querySelector('#notes-pane .notes-pane-body')));
    viewBtn.addEventListener('click', () => {
      _viewMode = _viewMode === 'grid' ? 'list' : 'grid';
      try { localStorage.setItem('odysseus-notes-view', _viewMode); } catch {}
      pane.classList.toggle('notes-view-grid', _viewMode === 'grid');
      _setViewLabel();
      requestAnimationFrame(() => _applyMasonry(document.querySelector('#notes-pane .notes-pane-body')));
    });
  }
  // Select mode
  document.getElementById('notes-select-btn').addEventListener('click', () => {
    if (_selectMode) _exitSelectMode(); else _enterSelectMode();
  });
  // Esc cancels select mode. Notes uses a toggle "Select" button rather
  // than a *-bulk-cancel button, so the global Esc-cancel handler in
  // keyboard-shortcuts.js can't reach it — handle it here. Capture phase
  // + stopPropagation so Esc cancels select instead of closing the panel.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _selectMode) {
      e.preventDefault();
      e.stopPropagation();
      _exitSelectMode();
    }
  }, true);
  document.getElementById('notes-select-all').addEventListener('change', (e) => {
    if (e.target.checked) _notes.forEach(n => _selectedIds.add(n.id));
    else _selectedIds.clear();
    _renderNotes();
    _updateBulkBar();
  });
  document.getElementById('notes-bulk-archive').addEventListener('click', async () => {
    const ids = [..._selectedIds];
    if (!ids.length) return;
    await Promise.all(ids.map(id => _patchNote(id, { archived: true }).catch(() => {})));
    _exitSelectMode();
    await _fetchNotes();
    _renderNotes();
    uiModule.showToast(`Archived ${ids.length}`);
  });
  document.getElementById('notes-bulk-delete').addEventListener('click', async () => {
    const ids = [..._selectedIds];
    if (!ids.length) return;
    if (uiModule && uiModule.styledConfirm) {
      const ok = await uiModule.styledConfirm(`Delete ${ids.length} note${ids.length === 1 ? '' : 's'}?`, { confirmText: 'Delete', danger: true });
      if (!ok) return;
    }
    await Promise.all(ids.map(id => _deleteNoteApi(id).catch(() => {})));
    _exitSelectMode();
    await _fetchNotes();
    _renderNotes();
    uiModule.showToast(`Deleted ${ids.length}`);
  });
  // Escape: exit select mode first (if active), otherwise close the panel.
  // Skip when the user is editing a form field — those have their own
  // ESC-to-cancel handlers and we don't want to nuke the whole panel
  // mid-edit.
  // Idempotent: remove any previous handler from a prior openPanel so
  // re-opening doesn't stack multiple handlers.
  if (_notesKeydownHandler) {
    document.removeEventListener('keydown', _notesKeydownHandler);
    _notesKeydownHandler = null;
  }
  _notesKeydownHandler = (e) => {
    if (!_open) return;
    const t = e.target;
    const inField = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    // Ctrl/Cmd+Z anywhere in the panel — undo the last note action. Skip when
    // typing in a field so the browser's normal text-undo still works.
    if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z') && !e.shiftKey) {
      if (inField) return;
      if (_undoStack.length === 0) return;
      e.preventDefault();
      _popAndRunUndo();
      return;
    }
    // Ctrl/Cmd+C while hovering a note card → copy that note. Skip when the
    // user is editing or has an active text selection (let the browser handle
    // a real text copy in that case).
    if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'C') && !e.shiftKey && !e.altKey) {
      if (inField) return;
      const sel = window.getSelection?.();
      if (sel && sel.toString && sel.toString().length > 0) return;
      const hovered = document.querySelector('.note-card:hover');
      if (!hovered) return;
      const id = hovered.dataset.noteId;
      if (!id) return;
      e.preventDefault();
      // Flash the ⋯ menu button (copy now lives in that menu).
      const btn = hovered.querySelector('.note-card-corner-menu');
      _copyNote(id, btn);
      return;
    }
    if (e.key !== 'Escape') return;
    if (inField) return;
    if (_selectMode) { _exitSelectMode(); return; }
    if (_showingArchived) {
      // Mirror the archive toggle button: flip back to active notes.
      document.getElementById('notes-archive-toggle')?.click();
      return;
    }
    _forceCloseNotesPanel();
  };
  document.addEventListener('keydown', _notesKeydownHandler);

  // Load — show skeleton immediately, then fetch
  _renderLoadingSkeleton();
  // Defer the highlight flush to the next frame so it runs *after* the cards
  // are committed to the DOM (and any FLIP animations have settled), giving
  // the querySelector lookups inside something to find.
  _fetchNotes().then(() => {
    _renderNotes();
    requestAnimationFrame(() => _flushPendingHighlights());
    _startReminderLoop();
    _showNotesFirstOpenHint(pane);
  });
}

function _renderLoadingSkeleton() {
  const body = document.querySelector('#notes-pane .notes-pane-body');
  if (!body) return;
  body.innerHTML = '';
  _renderLabelsInto(body);
  _renderQuickAdd(body);
  const skel = document.createElement('div');
  skel.className = 'notes-skeleton';
  skel.innerHTML = `
    <div class="notes-skeleton-card"></div>
    <div class="notes-skeleton-card"></div>
    <div class="notes-skeleton-card short"></div>
    <div class="notes-skeleton-card"></div>
  `;
  body.appendChild(skel);
}

function _enterSelectMode() {
  _selectMode = true;
  _selectedIds.clear();
  const bar = document.getElementById('notes-bulk-bar');
  const btn = document.getElementById('notes-select-btn');
  if (bar) bar.classList.remove('hidden');
  if (btn) { btn.classList.add('active'); btn.textContent = 'Cancel'; }
  _renderNotes();
  _updateBulkBar();
}

function _exitSelectMode() {
  _selectMode = false;
  _selectedIds.clear();
  const bar = document.getElementById('notes-bulk-bar');
  const btn = document.getElementById('notes-select-btn');
  const all = document.getElementById('notes-select-all');
  if (bar) bar.classList.add('hidden');
  if (btn) { btn.classList.remove('active'); btn.textContent = 'Select'; }
  if (all) all.checked = false;
  _renderNotes();
}

function _updateBulkBar() {
  const count = _selectedIds.size;
  const countEl = document.getElementById('notes-selected-count');
  const archiveBtn = document.getElementById('notes-bulk-archive');
  const deleteBtn = document.getElementById('notes-bulk-delete');
  const allEl = document.getElementById('notes-select-all');
  if (countEl) countEl.textContent = `${count} Selected`;
  if (archiveBtn) archiveBtn.disabled = count === 0;
  if (deleteBtn) deleteBtn.disabled = count === 0;
  if (allEl) allEl.checked = _notes.length > 0 && _notes.every(n => _selectedIds.has(n.id));
  // Toggle select-mode class so todo dots don't react to hover
  const pane = document.getElementById('notes-pane');
  if (pane) pane.classList.toggle('notes-select-mode', count > 0);
}

// A note's label field may hold multiple space-separated tags. Split + dedupe.
function _noteTags(n) {
  const tags = [];
  if (n?.label) tags.push(...n.label.trim().split(/\s+/).filter(Boolean));
  if (n?.due_date && _hasTimeComponent(n.due_date)) tags.push('reminder');
  return [...new Set(tags.map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
}

function _visibleNoteTags(n) {
  return _noteTags(n).filter(t => t !== 'reminder');
}

function _isPastReminder(n) {
  if (!n?.due_date || !_hasTimeComponent(n.due_date)) return false;
  const due = new Date(n.due_date).getTime();
  return !isNaN(due) && due <= Date.now();
}

async function _clearPastReminders() {
  const targets = _notes.filter(n => !n.archived && _isPastReminder(n));
  if (!targets.length) {
    uiModule.showToast?.('No past reminders to clear');
    return;
  }
  const ok = uiModule?.styledConfirm
    ? await uiModule.styledConfirm(`Delete ${targets.length} past reminder${targets.length === 1 ? '' : 's'}?`, { confirmText: 'Delete', danger: true })
    : confirm(`Delete ${targets.length} past reminder${targets.length === 1 ? '' : 's'}?`);
  if (!ok) return;
  await Promise.all(targets.map(n => _deleteNoteApi(n.id).catch(() => {})));
  await _fetchNotes();
  _renderNotes();
  uiModule.showToast?.(`Cleared ${targets.length} past reminder${targets.length === 1 ? '' : 's'}`);
}

function _renderLabels(root = document) {
  const bar = root.querySelector?.('.notes-labels-bar') || document.querySelector('.notes-labels-bar');
  if (!bar) return;
  const labels = new Set();
  for (const n of _notes) for (const t of _visibleNoteTags(n)) labels.add(t);
  const sortedLabels = [...labels].sort();
  // Count active reminders (not archived, has datetime due_date)
  const reminderCount = _notes.filter(n => !n.archived && n.due_date && _hasTimeComponent(n.due_date)).length;
  const pastReminderCount = _notes.filter(n => !n.archived && _isPastReminder(n)).length;
  const defaultCount = _notes.filter(n => !n.archived && _visibleNoteTags(n).length === 0).length;
  // Active goals = non-archived goal notes. Today view lists pending steps
  // from each, so we surface the count next to the chip.
  const goalCount = _notes.filter(n => n.note_type === 'goal' && !n.archived).length;
  const todayCount = _notes.filter(n => n.note_type === 'goal' && !n.archived && _nextGoalStep(n)).length;
  bar.style.display = '';
  const allActive = _activeLabel === null && _activeFilter === null;
  let html = `<button class="notes-label-chip${allActive ? ' active' : ''}" data-action="all">All</button>`;
  html += `<button class="notes-label-chip${_activeFilter === 'default' ? ' active' : ''}" data-action="default" title="Show notes without tags">Default <span class="notes-label-chip-count">${defaultCount}</span></button>`;
  if (todayCount > 0) {
    const isOn = _activeFilter === 'today';
    const icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:2px"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
    html += `<button class="notes-label-chip notes-label-chip-today${isOn ? ' active' : ''}" data-action="today" title="Next step from every goal">${icon}Today <span class="notes-label-chip-count">${todayCount}</span></button>`;
  }
  if (goalCount > 0) {
    const isOn = _activeFilter === 'goals';
    const icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:-1px;margin-right:2px"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>';
    html += `<button class="notes-label-chip notes-label-chip-goals${isOn ? ' active' : ''}" data-action="goals" title="Show only goals">${icon}Goals <span class="notes-label-chip-count">${goalCount}</span></button>`;
  }
  const isReminderOn = _activeFilter === 'reminders';
  const isReminderOff = _activeFilter === 'no-reminders';
  const reminderCls = `notes-label-chip notes-label-chip-reminders${isReminderOn ? ' active' : ''}${isReminderOff ? ' active negated' : ''}`;
  const reminderIcon = isReminderOff
    // bell-off icon
    ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:2px"><path d="M13.73 21a2 2 0 0 1-3.46 0"/><path d="M18.63 13A17.89 17.89 0 0 1 18 8"/><path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14"/><path d="M18 8a6 6 0 0 0-9.33-5"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
    : '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:2px"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
  html += `<button class="${reminderCls}" data-action="reminders" title="${isReminderOn ? 'Showing only reminders — click to show all' : isReminderOff ? 'Hiding reminders — click to show only reminders' : 'Click to filter reminders'}">${reminderIcon}Reminders <span class="notes-label-chip-count">${reminderCount}</span></button>`;
  const showingReminders = _activeFilter === 'reminders';
  if (showingReminders && pastReminderCount > 0) {
    html += `<button class="notes-label-chip notes-label-clear-past" data-action="clear-past-reminders" title="Delete reminders whose time has passed"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>Clear past <span class="notes-label-chip-count">${pastReminderCount}</span></button>`;
  }
  for (const lbl of sortedLabels) {
    html += `<button class="notes-label-chip${_activeLabel === lbl ? ' active' : ''}" data-label="${_esc(lbl)}">#${_esc(lbl)}</button>`;
  }
  bar.innerHTML = html;
  bar.querySelectorAll('.notes-label-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      if (chip.dataset.action === 'all') {
        _activeLabel = null;
        _activeFilter = null;
      } else if (chip.dataset.action === 'today') {
        _activeLabel = null;
        _activeFilter = (_activeFilter === 'today') ? null : 'today';
      } else if (chip.dataset.action === 'goals') {
        _activeLabel = null;
        _activeFilter = (_activeFilter === 'goals') ? null : 'goals';
      } else if (chip.dataset.action === 'default') {
        _activeLabel = null;
        _activeFilter = (_activeFilter === 'default') ? null : 'default';
      } else if (chip.dataset.action === 'reminders') {
        _activeLabel = null;
        // Cycle: null → reminders → null → no-reminders → null → reminders → ...
        if (_activeFilter === null) {
          _activeFilter = _reminderChipNext;
          _reminderChipNext = (_reminderChipNext === 'reminders') ? 'no-reminders' : 'reminders';
        } else {
          _activeFilter = null;
        }
      } else if (chip.dataset.action === 'clear-past-reminders') {
        _clearPastReminders();
        return;
      } else {
        _activeFilter = null;
        _activeLabel = chip.dataset.label || null;
      }
      _renderNotes();
    });
  });
}

function _renderLabelsInto(_body) {
  if (!_body) return;
  let bar = _body.querySelector(':scope > .notes-labels-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'notes-labels-bar';
    _body.appendChild(bar);
  }
  _renderLabels(_body);
}

function _ensureNotesChipRegistered() {
  if (Modals.isRegistered('notes-panel')) return;
  Modals.register('notes-panel', {
    railBtnId: 'rail-notes',
    sidebarBtnId: 'tool-notes-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _forceCloseNotesPanel(); },
  });
}

// `direction === 'down'` (mobile swipe-down) MINIMIZES the panel to a
// dock chip instead of fully closing — tapping the chip reopens it.
// Any other call (close button, programmatic) is a full close.
export function closePanel(direction) {
  if (!_open) return;
  _open = false;
  _editingId = null;
  _clearViewedReminderGlows();
  const _minimize = direction === 'down';
  if (_minimize) {
    _ensureNotesChipRegistered();
  } else if (Modals.isRegistered('notes-panel')) {
    Modals.unregister('notes-panel');
  }

  // Drop the document keydown listener and the 30s reminder interval —
  // both leaked across open/close cycles in the v2 review.
  if (_notesKeydownHandler) {
    document.removeEventListener('keydown', _notesKeydownHandler);
    _notesKeydownHandler = null;
  }
  if (_reminderTimer) {
    clearInterval(_reminderTimer);
    _reminderTimer = null;
  }

  document.body.classList.remove('notes-view');
  document.body.classList.remove('notes-mobile-mode');
  document.body.classList.remove('notes-drag-mode');
  // Closing the panel should PRESERVE in-progress edits, not drop them.
  // Commit any open in-place editor, and close the mobile fullscreen
  // overlay with save=true so the note is persisted.
  try { _commitOpenInPlaceEditor(); } catch {}
  _closeMobileFullscreenEdit({ save: true });
  // /notes route may have collapsed the wide sidebar to a rail; restore.
  try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}

  const btn = document.getElementById('tool-notes-btn');
  if (btn) btn.classList.remove('active');

  const pane = document.getElementById('notes-pane');
  const backdrop = document.getElementById('notes-pane-backdrop');
  if (pane) {
    // Scale-out + fade. Match the enter animation duration so close feels
    // like the same gesture played backwards.
    pane.classList.add('notes-pane-leaving');
    const _cleanup = () => {
      try { pane.remove(); } catch {}
      try { backdrop?.remove(); } catch {}
    };
    pane.addEventListener('animationend', _cleanup, { once: true });
    // Belt-and-braces: if animation is skipped (reduced motion / detached
    // tab) the listener won't fire; remove after the expected duration.
    setTimeout(_cleanup, 220);
  } else if (backdrop) {
    backdrop.remove();
  }
  // Show the dock chip for a swipe-down minimize (tap it to reopen).
  if (_minimize) { try { Modals.minimize('notes-panel'); } catch {} }
}

export function togglePanel() {
  if (_open) closePanel();
  else openPanel();
}

export function isPanelOpen() { return _open; }

// ---- Render ----

// FLIP animation — capture positions before render, animate back after
function _captureCardPositions() {
  const body = document.querySelector('#notes-pane .notes-pane-body');
  if (!body) return null;
  const positions = new Map();
  body.querySelectorAll('.note-card').forEach(card => {
    const id = card.dataset.noteId;
    if (id) positions.set(id, card.getBoundingClientRect());
  });
  return positions;
}

function _animateReflow(prevPositions) {
  if (!prevPositions || !prevPositions.size) return;
  const body = document.querySelector('#notes-pane .notes-pane-body');
  if (!body) return;
  body.querySelectorAll('.note-card').forEach(card => {
    const id = card.dataset.noteId;
    const prev = prevPositions.get(id);
    if (!prev) return;
    const next = card.getBoundingClientRect();
    const dx = prev.left - next.left;
    const dy = prev.top - next.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    // Invert: jump back to old position
    card.style.transition = 'none';
    card.style.transform = `translate(${dx}px, ${dy}px)`;
    // Play: animate to 0
    requestAnimationFrame(() => {
      card.style.transition = 'transform 0.25s cubic-bezier(0.34, 1.2, 0.64, 1)';
      card.style.transform = '';
      card.addEventListener('transitionend', () => {
        card.style.transition = '';
      }, { once: true });
    });
  });
}

function _renderNotes() {
  _updateRailBadge();
  const body = document.querySelector('#notes-pane .notes-pane-body');
  if (!body) return;
  const prevPositions = _captureCardPositions();
  const activeReminderHighlights = _loadActiveHighlights();

  let filtered = _activeLabel ? _notes.filter(n => _noteTags(n).includes(_activeLabel)) : _notes;
  if (_activeFilter === 'reminders') {
    filtered = filtered.filter(n => n.due_date && _hasTimeComponent(n.due_date));
  } else if (_activeFilter === 'no-reminders') {
    filtered = filtered.filter(n => !(n.due_date && _hasTimeComponent(n.due_date)));
  } else if (_activeFilter === 'default') {
    filtered = filtered.filter(n => _visibleNoteTags(n).length === 0);
  } else if (_activeFilter === 'goals') {
    filtered = filtered.filter(n => n.note_type === 'goal' && !n.archived);
  } else if (_activeFilter === 'today') {
    // Today view: only goals that still have an unchecked step.
    filtered = filtered.filter(n => n.note_type === 'goal' && !n.archived && _nextGoalStep(n));
  }
  if (_searchQuery) {
    filtered = filtered.filter(n => {
      const q = _searchQuery;
      if ((n.title || '').toLowerCase().includes(q)) return true;
      if ((n.content || '').toLowerCase().includes(q)) return true;
      if ((n.label || '').toLowerCase().includes(q)) return true;
      if (Array.isArray(n.items) && n.items.some(it => (it.text || '').toLowerCase().includes(q))) return true;
      return false;
    });
  }
  const sorted = [...filtered].sort((a, b) => {
    // In reminders view: sort by due date ascending (soonest first)
    if (_activeFilter === 'reminders') {
      const da = new Date(a.due_date || 0).getTime();
      const db = new Date(b.due_date || 0).getTime();
      return da - db;
    }
    // Archived view: newest archived first (ignore manual sort_order).
    if (_showingArchived) {
      return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
    }
    if (a.pinned && !b.pinned) return -1;
    if (!a.pinned && b.pinned) return 1;
    // Active reminders (due date in the past, not done/archived) rank
    // immediately under the pinned block.
    const aActive = _hasActiveReminder(a);
    const bActive = _hasActiveReminder(b);
    if (aActive && !bActive) return -1;
    if (!aActive && bActive) return 1;
    const so = (a.sort_order || 0) - (b.sort_order || 0);
    if (so !== 0) return so;
    return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
  });

  let html = '';
  // Today view: render a compact card listing the next-unchecked step from
  // each active goal. Tapping a step toggles it done (same idx-based wiring
  // as regular checkboxes). Tapping the title opens the goal note for full
  // editing.
  if (_activeFilter === 'today') {
    body.innerHTML = '';
    _renderLabelsInto(body);
    _renderQuickAdd(body);
    if (sorted.length === 0) {
      body.insertAdjacentHTML('beforeend', `<div class="notes-empty">All caught up — no pending goal steps right now.</div>`);
    } else {
      let todayHtml = `<div class="notes-today-wrap">
        <div class="notes-today-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span>Today &middot; one step per goal</span>
        </div>
        <div class="notes-today-list">`;
      for (const note of sorted) {
        const next = _nextGoalStep(note);
        if (!next) continue;
        const progress = _goalProgress(note).trim();
        todayHtml += `<div class="notes-today-row" data-note-id="${note.id}">
          <span class="note-check-dot" data-note-id="${note.id}" data-idx="${next.idx}" title="Mark step done"></span>
          <div class="notes-today-text">
            <div class="notes-today-title" data-action="edit" data-note-id="${note.id}">${_esc(note.title || '(untitled goal)')}</div>
            <div class="notes-today-step">${_linkify(next.item.text || '')}</div>
          </div>
          <span class="notes-today-progress">${_esc(progress)}</span>
        </div>`;
      }
      todayHtml += `</div></div>`;
      body.insertAdjacentHTML('beforeend', todayHtml);
    }
    _wireTodayView(body);
    return;
  }
  for (const note of sorted) {
    if (_editingId === note.id) continue; // skip — form is shown instead
    const borderColor = COLOR_HEX[note.color || ''] || 'var(--border)';
    const dueFmt = _formatDueDate(note.due_date);
    const overdue = _isDueOverdue(note.due_date);

    let contentHtml = '';
    if (_hasItems(note) && Array.isArray(note.items)) {
      // Goal notes can carry a free-form description above the step list —
      // todos rarely do, but the same render works for both.
      if (note.note_type === 'goal' && (note.content || '').trim()) {
        const fullText = note.content || '';
        const preview = fullText.length > 300 ? fullText.slice(0, 300) + '…' : fullText;
        contentHtml += `<div class="note-goal-desc">${_esc(preview)}</div>`;
      }
      contentHtml += '<div class="note-checklist-preview">';
      // Show ALL items — the preview container is scrollable (CSS caps
      // its max-height + overflow-y:auto), so there's no need to truncate.
      for (let i = 0; i < note.items.length; i++) {
        const item = note.items[i];
        const doneClass = item.done ? ' done' : '';
        const indent = Math.min(item.indent || 0, 3);
        contentHtml += `<div class="note-checkbox${doneClass}" data-note-id="${note.id}" data-idx="${i}" style="padding-left:${indent * 16}px">
          <span class="note-check-dot" title="Mark done"></span>
          <span class="note-check-text">${_linkify(item.text)}</span>
          <button class="note-checkbox-rm" data-note-id="${note.id}" data-idx="${i}" title="Delete item">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>`;
      }
      contentHtml += '</div>';
    } else {
      const fullText = note.content || '';
      const preview = fullText.length > 600 ? fullText.slice(0, 600) + '…' : fullText;
      // _linkify already calls _esc internally, so URLs become clickable
      // anchors (used by e.g. the "remind me to reply" email deep-link).
      contentHtml = preview ? `<div class="note-content-preview">${_linkify(preview)}</div>` : '';
    }

    const isBg = _isBgImage(note.color);
    const cc = (note.color && !isBg) ? ' note-color-' + note.color : '';
    const cardStyle = isBg ? ` style="${_customColorStyle(note.color)}"` : '';
    const sel = _selectedIds.has(note.id) ? ' note-card-selected' : '';
    const reminderTagHtml = note.due_date && _hasTimeComponent(note.due_date)
      ? `<div class="note-card-reminder${overdue ? ' overdue' : ''}">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
          <span>${_esc(_formatReminderTag(note.due_date))}${note.repeat && note.repeat !== 'none' ? ' · ' + _esc(_formatRepeatLabel(note.repeat, new Date(note.due_date))) : ''}</span>
        </div>`
      : '';
    const noteTags = _visibleNoteTags(note);
    const dueBadge = dueFmt && !_hasTimeComponent(note.due_date) ? `<span class="note-due-inline${overdue ? ' note-due-overdue' : ''}">${dueFmt}</span>` : '';
    const colorDots = COLORS.map(c => `<span class="note-card-color-dot${_dotIsActive(c.value, note.color) ? ' active' : ''}" data-color="${c.value}" style="background:${_dotBg(c.value, note.color)}" title="${c.name || 'default'}"></span>`).join('');
    const goalClass = note.note_type === 'goal' ? ' note-card-goal' : '';
    const reminderGlowClass = activeReminderHighlights.has(note.id) && _hasActiveReminder(note) ? ' note-card-reminder-fired-sticky' : '';
    const goalPill = note.note_type === 'goal'
      ? `<span class="note-goal-pill" title="AI-broken-down goal">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
          Goal${_goalProgress(note)}
        </span>`
      : '';
    html += `<div class="note-card${note.pinned ? ' note-card-pinned' : ''}${cc}${sel}${goalClass}${reminderGlowClass}${_selectMode ? ' note-card-selectmode' : ''}" draggable="${(_selectMode || _isNotesMobileMode()) ? 'false' : 'true'}" data-note-id="${note.id}"${cardStyle}>
      ${_selectMode ? `<input type="checkbox" class="memory-select-cb note-card-cb" data-note-id="${note.id}" ${_selectedIds.has(note.id) ? 'checked' : ''} />` : ''}
      ${goalPill}
      <button class="note-card-pin${note.pinned ? ' active' : ''}" data-note-id="${note.id}" title="${note.pinned ? 'Unpin' : 'Pin'}">
        <svg width="16" height="16" viewBox="0 0 24 28" fill="${note.pinned ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"${note.pinned ? ' style="color:var(--accent,var(--red));"' : ''}><g transform="rotate(${note.pinned ? 0 : 45} 12 14)" style="transition:transform 0.2s ease;"><line x1="12" y1="17" x2="12" y2="27"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17z"/></g></svg>
      </button>
      ${_showingArchived
        ? `<button class="note-card-corner-trash" data-note-id="${note.id}" title="Delete forever" aria-label="Delete forever">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
          <button class="note-card-corner-unarchive" data-note-id="${note.id}" title="Unarchive" aria-label="Unarchive note">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14l-5-5 5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>
          </button>`
        : `<button class="note-card-done" data-note-id="${note.id}" title="Mark done" aria-label="Mark done">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </button>
          ${_hasItems(note) ? `<button class="note-card-copy note-card-copy-corner" data-note-id="${note.id}" title="Copy all items" aria-label="Copy all items">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>` : ''}`}
      <div class="note-card-header">
        <div class="note-card-title${note.title ? '' : ' empty'}" data-action="edit">${_esc(note.title || '')}</div>
        ${dueBadge}
      </div>
      ${_safeImgSrc(note.image_url) ? `<img class="note-card-image" src="${_esc(_safeImgSrc(note.image_url))}" alt="" draggable="false" />` : ''}
      ${contentHtml}
      ${_hasItems(note) ? `<div class="note-cl-quickadd"><input type="text" class="note-cl-quickadd-input" placeholder="+ Add item" data-note-id="${note.id}" /></div>` : ''}
      ${reminderTagHtml}
      ${noteTags.length ? `<div class="note-card-label">${noteTags.map(t => `<button type="button" class="note-card-label-chip" data-note-label-filter="${_esc(t)}" title="Filter #${_esc(t)}">#${_esc(t)}</button>`).join(' ')}</div>` : ''}
      ${note.agent_session_id ? `<button class="note-agent-tag" data-note-id="${note.id}" data-session-id="${_esc(note.agent_session_id)}" title="Open the agent's chat for this note">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
        <span>Agent</span>
      </button>` : ''}
      <div class="note-card-actions">
        <div class="note-card-colors">${colorDots}</div>
        <span style="flex:1"></span>
        ${_showingArchived ? `
        <button class="note-card-action note-card-delete" data-note-id="${note.id}" title="Delete permanently">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
        </button>
        <button class="note-card-action note-card-unarchive" data-note-id="${note.id}" title="Unarchive">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><polyline points="3 4 3 10 9 10"/></svg>
        </button>` : `
        ${_hasItems(note) ? `
        <button class="note-card-action note-card-copy" data-note-id="${note.id}" title="Copy all items">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </button>` : ''}
        <button class="note-card-action note-card-archive" data-note-id="${note.id}" title="Save (archive)">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        </button>
        <button class="note-card-action note-card-delete" data-note-id="${note.id}" title="Delete">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        <button class="note-card-action note-card-corner-menu" data-note-id="${note.id}" title="More" aria-label="More actions">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>
        </button>`}
      </div>
    </div>`;
  }

  // Always render quick-add at top (collapsed unless user is typing)
  const existingForm = body.querySelector('.note-form');
  if (existingForm && _editingId === '__new__') {
    // Keep the expanded form, replace cards after it
    const next = [...body.children].filter(c => c !== existingForm);
    next.forEach(c => c.remove());
    if (sorted.length === 0) {
      body.insertAdjacentHTML('beforeend', '<div class="notes-empty-msg">No notes <span style="vertical-align:-3px;margin-left:4px;">' + uiModule.emptyStateIcon('smiley') + '</span></div>');
    } else {
      existingForm.insertAdjacentHTML('afterend', html);
    }
  } else {
    body.innerHTML = '';
    _renderLabelsInto(body);
    _renderQuickAdd(body);
    if (sorted.length === 0) {
      body.insertAdjacentHTML('beforeend', '<div class="notes-empty-msg">No notes yet <span style="vertical-align:-3px;margin-left:4px;">' + uiModule.emptyStateIcon('smiley') + '</span></div>');
    } else {
      body.insertAdjacentHTML('beforeend', html);
    }
  }

  _bindCardEvents(body);
  _animateReflow(prevPositions);
  _applyMasonry(body);
}

// In grid view, lay out the cards as masonry by
// computing each card's `grid-row-end: span N` from its measured height
// (rows are 4px tall + 8px row gap simulated via card margin-bottom). The
// grid's `grid-auto-flow: dense` packs columns independently so left/right
// lanes no longer share row heights.
//
// Re-runs on layout-affecting changes via ResizeObserver bound per-card.
let _masonryObserver = null;
function _applyMasonry(body) {
  if (!body) return;
  const pane = body.closest('.notes-pane');
  const isGrid = pane?.classList.contains('notes-view-grid');
  const isMobileGrid = isGrid && window.matchMedia('(max-width: 768px)').matches;
  // Tear down any prior observer (defensive — _renderNotes wipes body.innerHTML).
  if (_masonryObserver) { try { _masonryObserver.disconnect(); } catch {} _masonryObserver = null; }
  if (!isGrid) {
    // Clear any leftover inline spans so list view lays out normally.
    body.querySelectorAll('.note-card, .notes-labels-bar, .notes-quick-add, .note-form').forEach(c => { c.style.gridRowEnd = ''; });
    return;
  }
  const ROW_PX = 4;
  const spanForHeight = (h) => Math.max(1, Math.ceil(h / ROW_PX));
  const recomputeFullRows = () => {
    const quickAdd = body.querySelector('.notes-quick-add');
    const labelsBar = body.querySelector('.notes-labels-bar');
    if (labelsBar && getComputedStyle(labelsBar).display !== 'none') {
      const shave = isMobileGrid ? 4 : 0;
      labelsBar.style.gridRowEnd = `span ${Math.max(1, spanForHeight(labelsBar.scrollHeight) - shave)}`;
    }
    if (quickAdd) {
      const shave = isMobileGrid ? 4 : 0;
      quickAdd.style.gridRowEnd = `span ${Math.max(1, spanForHeight(quickAdd.scrollHeight + 10) - shave)}`;
    }
    body.querySelectorAll('.note-form').forEach(form => {
      form.style.gridColumn = '1 / -1';
      const isDrawForm = !!form.querySelector('.note-form-type-seg.is-draw');
      const minSpan = isMobileGrid ? (isDrawForm ? 104 : 64) : 1;
      const renderedHeight = form.getBoundingClientRect?.().height || 0;
      const drawReserve = isDrawForm && isMobileGrid ? 12 : 12;
      const measuredHeight = Math.max(form.scrollHeight, renderedHeight) + drawReserve;
      form.style.gridRowEnd = `span ${Math.max(minSpan, spanForHeight(measuredHeight))}`;
    });
  };
  const recompute = (card) => {
    // scrollHeight returns the natural content height — card.getBoundingClientRect()
    // would return the grid cell height (collapsed to 4px until the span is set,
    // which is the value we're trying to compute).
    const h = card.scrollHeight + (isMobileGrid ? 6 : 8);
    if (h <= 0) return;
    card.style.gridRowEnd = `span ${spanForHeight(h)}`;
  };
  recomputeFullRows();
  body.querySelectorAll('.note-card').forEach(recompute);
  // Watch masonry participants — content can grow (image load, todo edits,
  // quick-add/form expansion), and stale spans are what cause visual merging.
  if ('ResizeObserver' in window) {
    _masonryObserver = new ResizeObserver(entries => {
      let fullRowsChanged = false;
      for (const e of entries) {
        if (e.target.classList.contains('note-card')) recompute(e.target);
        else fullRowsChanged = true;
      }
      if (fullRowsChanged) recomputeFullRows();
    });
    body.querySelectorAll('.note-card').forEach(c => _masonryObserver.observe(c));
    body.querySelectorAll('.notes-labels-bar, .notes-quick-add, .note-form').forEach(c => _masonryObserver.observe(c));
  }
}

// Wire the Today aggregated view: tap a step's dot toggles it done; tap
// the goal title opens the full note for editing. Done steps fade and the
// next pending step rotates in on the next render.
function _wireTodayView(body) {
  body.querySelectorAll('.notes-today-row .note-check-dot').forEach(dot => {
    dot.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = dot.dataset.noteId;
      const idx = parseInt(dot.dataset.idx);
      const note = _notes.find(n => n.id === id);
      if (!note || !Array.isArray(note.items) || !note.items[idx]) return;
      note.items[idx].done = !note.items[idx].done;
      const row = dot.closest('.notes-today-row');
      if (row) row.classList.add('done');
      try {
        await _patchNote(id, { items: note.items });
        // Re-render so the next pending step bubbles up (or the row drops
        // out entirely if the goal is fully done now).
        _renderNotes();
        // Confetti when ALL items just turned done.
        if (note.items.every(it => it.done)) {
          const r = (row || dot).getBoundingClientRect();
          spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 60);
        }
      } catch {
        note.items[idx].done = !note.items[idx].done;
      }
    });
  });
  body.querySelectorAll('.notes-today-title').forEach(el => {
    el.addEventListener('click', () => {
      const id = el.dataset.noteId;
      if (!id) return;
      // Drop the Today filter first so the regular card list is rendered;
      // _editNote needs to find a .note-card in the DOM to replace with
      // the editor form.
      _activeFilter = null;
      _renderNotes();
      _editNote(id);
    });
  });
}

function _renderQuickAdd(body) {
  const wrap = document.createElement('div');
  wrap.className = 'notes-quick-add';
  // 2-pill Note/Todo toggle mirrors the full form's type-seg (minus Draw —
  // drawing happens in the expanded form). The pill that's active steers
  // both the placeholder and the type the form opens in.
  wrap.innerHTML = `
    <div class="notes-quick-type-seg is-todo" role="group" aria-label="New item type">
      <button type="button" class="notes-quick-type-pill" data-type="note" aria-label="Note" aria-pressed="false" title="Note">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="14" y2="18"/></svg>
      </button>
      <button type="button" class="notes-quick-type-pill active" data-type="todo" aria-label="To-do" aria-pressed="true" title="To-do">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      </button>
    </div>
    <input type="text" class="notes-quick-input" placeholder="Add a to-do…" />
    <button class="notes-quick-icon" data-action="photo" title="Attach photo">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    </button>
  `;
  body.appendChild(wrap);

  const input = wrap.querySelector('.notes-quick-input');
  const seg = wrap.querySelector('.notes-quick-type-seg');
  let currentType = 'todo';
  const setType = (t) => {
    if (t !== 'note' && t !== 'todo') return;
    currentType = t;
    seg.classList.toggle('is-todo', t === 'todo');
    seg.classList.toggle('is-note', t === 'note');
    seg.querySelectorAll('.notes-quick-type-pill').forEach(p => {
      const on = p.dataset.type === t;
      p.classList.toggle('active', on);
      p.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    input.placeholder = t === 'note' ? 'Add a note…' : 'Add a to-do…';
  };
  seg.querySelectorAll('.notes-quick-type-pill').forEach(p => {
    p.addEventListener('click', (e) => {
      e.stopPropagation();
      setType(p.dataset.type);
    });
  });
  // Click input or type → expand to full form
  const expandToForm = (initialType = 'note', initialText = '') => {
    _editingId = '__new__';
    const form = _buildForm({ note_type: initialType });
    form.classList.add('note-form-new');
    if (initialText) {
      const titleEl = form.querySelector('.note-form-title');
      if (titleEl) titleEl.value = initialText;
    }
    const mobileGrid = body.closest('.notes-pane')?.classList.contains('notes-view-grid')
      && window.matchMedia('(max-width: 768px)').matches;
    if (mobileGrid) {
      form.style.gridColumn = '1 / -1';
      form.style.gridRowEnd = 'span 64';
    }
    wrap.replaceWith(form);
    _applyMasonry(body);
    requestAnimationFrame(() => _applyMasonry(body));
    const titleEl = form.querySelector('.note-form-title');
    if (titleEl) {
      titleEl.focus();
      // Move caret to end
      titleEl.setSelectionRange(titleEl.value.length, titleEl.value.length);
    }
  };
  // Expand only on real intent: a click directly on the input, or actual
  // typing. Focus alone — including focus stolen from a missed nearby
  // click — no longer creates an empty form.
  input.addEventListener('click', () => expandToForm(currentType, input.value));
  input.addEventListener('input', () => expandToForm(currentType, input.value));
  wrap.querySelector('[data-action="photo"]').addEventListener('click', (e) => {
    e.stopPropagation();
    expandToForm(currentType);
    // Trigger photo input on the new form
    setTimeout(() => document.querySelector('.note-form-photo-btn')?.click(), 50);
  });
}

function _bindCardEvents(body) {
  const tapToEditOrSelect = (cardEl) => {
    const id = cardEl.dataset.noteId;
    if (_selectMode) {
      const cb = cardEl.querySelector('.note-card-cb');
      if (cb) {
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event('change'));
      }
    } else if (_isNotesMobileMode()) {
      // Mobile: open the per-note fullscreen edit overlay instead of the
      // in-place form. Tiles on mobile are read-only previews.
      _openMobileFullscreenEdit(id, cardEl);
    } else {
      _editNote(id);
    }
  };
  // Mobile: long-press anywhere on a note card → enter drag-to-reorder mode.
  // Cancelled by movement (so it doesn't interfere with vertical scrolling)
  // or by lifting the finger before the timer fires.
  if (_isNotesMobileMode()) {
    body.querySelectorAll('.note-card').forEach(card => _bindLongPressDrag(card));
  }
  body.querySelectorAll('.note-card.note-card-reminder-fired-sticky').forEach(card => {
    card.addEventListener('click', () => _setReminderCardGlow(card.dataset.noteId, false), true);
  });
  // Click title — edit, or toggle select in select mode
  body.querySelectorAll('.note-card-title[data-action="edit"]').forEach(el => {
    el.addEventListener('click', (e) => { e.stopPropagation(); tapToEditOrSelect(el.closest('.note-card')); });
  });
  // Click content — edit, or toggle select in select mode
  body.querySelectorAll('.note-content-preview').forEach(el => {
    el.addEventListener('click', (e) => { e.stopPropagation(); tapToEditOrSelect(el.closest('.note-card')); });
  });
  // Click empty area of checklist preview (not on checkbox/X) — edit
  body.querySelectorAll('.note-checklist-preview').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('.note-checkbox, .note-checkbox-rm, .note-cl-quickadd, input')) return;
      e.stopPropagation();
      tapToEditOrSelect(el.closest('.note-card'));
    });
  });
  // Clicking todo item text now toggles its checkbox — let the click bubble
  // up to the parent .note-checkbox row handler. To open the editor, the
  // user clicks the pencil corner.
  // (No-op block kept as a marker — removing the listener entirely means
  // clicks naturally bubble to the row toggle below.)
  // In select mode, clicking anywhere on the card toggles selection
  if (_selectMode) {
    body.querySelectorAll('.note-card').forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('.note-card-cb')) return; // checkbox handles itself
        e.stopPropagation();
        tapToEditOrSelect(card);
      });
    });
  }
  // Mobile, non-select: tapping anywhere on the card body (not on an
  // interactive child — buttons, pin, checkbox, color dot, reminder pill,
  // agent tag, links) opens the fullscreen editor. Previously only the
  // title / content preview triggered edit, so padding + empty gutters were
  // dead zones that felt broken on mobile.
  if (_isNotesMobileMode() && !_selectMode) {
    const _INTERACTIVE = 'button, a, input, label, .note-card-color-dot, .note-checkbox, .note-checkbox-rm, .note-cl-quickadd, .note-agent-tag, .note-card-pin, .note-card-corner-trash, .note-card-corner-menu, .note-card-corner-unarchive, .note-card-edit-corner, .note-card-reminder, .note-card-cb';
    body.querySelectorAll('.note-card').forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest(_INTERACTIVE)) return;
        e.stopPropagation();
        tapToEditOrSelect(card);
      });
    });
  }
  // Multi-select checkbox (only in select mode)
  body.querySelectorAll('.note-card-cb').forEach(cb => {
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      const id = cb.dataset.noteId;
      if (cb.checked) _selectedIds.add(id);
      else _selectedIds.delete(id);
      cb.closest('.note-card').classList.toggle('note-card-selected', cb.checked);
      _updateBulkBar();
    });
  });
  // Pin toggle (optimistic)
  body.querySelectorAll('.note-card-pin').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      const note = _notes.find(n => n.id === id);
      if (!note) return;
      const prevPinned = note.pinned;
      const prevSortOrder = note.sort_order;
      note.pinned = !prevPinned;
      const patch = { pinned: note.pinned };
      if (note.pinned) {
        const minPinned = _notes
          .filter(n => n.pinned && n.id !== id)
          .reduce((m, n) => Math.min(m, n.sort_order || 0), 0);
        note.sort_order = minPinned - 1;
        patch.sort_order = note.sort_order;
      }
      _renderNotes();
      _patchNote(id, patch).catch(() => {
        note.pinned = prevPinned;
        note.sort_order = prevSortOrder;
        _renderNotes();
        uiModule.showError('Failed to pin');
      });
    });
  });
  // Color picker
  const _applyCardColor = async (card, id, newColor) => {
    const isBg = _isBgImage(newColor);
    COLORS.forEach(c => { if (c.value && c.value !== 'custom') card.classList.remove('note-color-' + c.value); });
    if (newColor && !isBg) card.classList.add('note-color-' + newColor);
    if (isBg) card.setAttribute('style', _customColorStyle(newColor));
    else card.removeAttribute('style');
    card.querySelectorAll('.note-card-color-dot').forEach(d => {
      d.classList.toggle('active', _dotIsActive(d.dataset.color, newColor));
      d.style.background = _dotBg(d.dataset.color, newColor);
    });
    try { await _patchNote(id, { color: newColor || null }); const note = _notes.find(n => n.id === id); if (note) note.color = newColor; }
    catch { uiModule.showError('Failed to update color'); }
  };
  body.querySelectorAll('.note-card-color-dot').forEach(dot => {
    dot.addEventListener('click', (e) => {
      e.stopPropagation();
      const card = dot.closest('.note-card');
      const id = card.dataset.noteId;
      if (dot.dataset.color === 'custom') {
        _pickCustomBgImage().then(url => { if (url) _applyCardColor(card, id, 'bg:' + url); });
        return;
      }
      _applyCardColor(card, id, dot.dataset.color);
    });
  });
  // Plain pencil corner → open editor. The unarchive corner shares the
  // .note-card-edit-corner class for styling, so :not() keeps the edit
  // handler off it.
  body.querySelectorAll('.note-card-edit-corner:not(.note-card-unarchive-corner)').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      if (id) _editNote(id);
    });
  });
  // Copy corner — bottom-right, just left of the Done check. Shared with
   // the Ctrl/Cmd+C shortcut wired further down so both code paths run the
   // same serializer + feedback flash.
  // ⋯ corner menu — Copy + Agent (solve-this-todo).
  body.querySelectorAll('.note-card-corner-menu').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      _openNoteCornerMenu(btn);
    });
  });
  // Agent tag — opens the chat session the agent ran for this note.
  body.querySelectorAll('.note-agent-tag').forEach(tag => {
    tag.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sid = tag.dataset.sessionId;
      const _sm = window.sessionModule;
      if (sid && _sm && _sm.selectSession) { closePanel(); _sm.selectSession(sid); }
    });
  });
  body.querySelectorAll('.note-card-label-chip').forEach(chip => {
    chip.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const label = chip.dataset.noteLabelFilter;
      if (!label) return;
      if (_activeLabel === label && _activeFilter === null) {
        _activeLabel = null;
      } else {
        _activeFilter = null;
        _activeLabel = label;
      }
      _renderNotes();
    });
  });
  // Done (✓) at bottom-right — only visible on hover for active notes.
  body.querySelectorAll('.note-card-done').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      const card = btn.closest('.note-card');
      const idx = _notes.findIndex(n => n.id === id);
      if (idx < 0) return;
      // Celebrate completion — same confetti shower the bulk-archive uses.
      if (card) {
        const r = card.getBoundingClientRect();
        spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 80);
      }
      const removed = _notes.splice(idx, 1)[0];
      const undo = () => _undoArchive(removed, idx);
      _pushUndo({ label: 'archive', run: undo });
      const _undoIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;"><polyline points="9 14 4 9 9 4"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>';
      const finish = () => {
        _renderNotes();
        _patchNote(id, { archived: true }).then(() => {
          uiModule.showToast('Archived', { duration: 6000, action: 'Undo', actionIcon: _undoIcon, onAction: undo, actionHint: 'Ctrl+Z' });
        }).catch(() => {
          _notes.splice(idx, 0, removed);
          _renderNotes();
          uiModule.showError('Failed to archive');
        });
      };
      if (card) {
        card.classList.add('note-card-sliding-out');
        let done = false;
        const once = () => { if (done) return; done = true; finish(); };
        card.addEventListener('transitionend', once, { once: true });
        setTimeout(once, 400);
      } else {
        finish();
      }
    });
  });
  // Unarchive corner — only visible in archive view.
  body.querySelectorAll('.note-card-corner-unarchive').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      const idx = _notes.findIndex(n => n.id === id);
      if (idx < 0) return;
      const removed = _notes.splice(idx, 1)[0];
      _renderNotes();
      _patchNote(id, { archived: false }).then(() => uiModule.showToast('Unarchived')).catch(() => {
        _notes.splice(idx, 0, removed);
        _renderNotes();
        uiModule.showError('Failed to unarchive');
      });
    });
  });
  // Trash corner — archive view only. Permanent delete, no confirmation.
  body.querySelectorAll('.note-card-corner-trash').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      const idx = _notes.findIndex(n => n.id === id);
      if (idx < 0) return;
      const removed = _notes.splice(idx, 1)[0];
      _renderNotes();
      _deleteNoteApi(id).then(() => uiModule.showToast('Deleted')).catch(() => {
        _notes.splice(idx, 0, removed);
        _renderNotes();
        uiModule.showError('Failed to delete');
      });
    });
  });

  body.querySelectorAll('.note-card-archive').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      if (!id) return;
      const note = _notes.find(n => n.id === id);
      const card = btn.closest('.note-card');
      // Confetti when archiving a fully-completed checklist (todo or goal).
      if (note && _hasItems(note) && card) {
        const undone = (note.items || []).filter(i => !i.done);
        if (undone.length === 0) {
          const r = card.getBoundingClientRect();
          spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 80);
        }
      }
      let done = false;
      const finishRemove = () => {
        if (done) return;
        done = true;
        const curIdx = _notes.findIndex(n => n.id === id);
        if (curIdx < 0) return;
        const removed = _notes.splice(curIdx, 1)[0];
        _renderNotes();
        const undo = () => _undoArchive(removed, curIdx);
        _pushUndo({ label: 'archive', run: undo });
        const _undoIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;"><polyline points="9 14 4 9 9 4"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>';
        _patchNote(id, { archived: true }).then(() => {
          uiModule.showToast('Archived', { duration: 6000, action: 'Undo', actionIcon: _undoIcon, onAction: undo, actionHint: 'Ctrl+Z' });
        }).catch(() => {
          _notes.splice(curIdx, 0, removed);
          _renderNotes();
          uiModule.showError('Failed to archive');
        });
      };
      if (card) {
        card.classList.add('note-card-sliding-out');
        card.addEventListener('transitionend', finishRemove, { once: true });
        setTimeout(finishRemove, 400);
      } else {
        finishRemove();
      }
    });
  });
  // Unarchive (optimistic) — only present in archive view
  body.querySelectorAll('.note-card-unarchive').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      const idx = _notes.findIndex(n => n.id === id);
      if (idx < 0) return;
      const removed = _notes.splice(idx, 1)[0];
      _renderNotes();
      _patchNote(id, { archived: false }).then(() => uiModule.showToast('Unarchived')).catch(() => {
        _notes.splice(idx, 0, removed);
        _renderNotes();
        uiModule.showError('Failed to unarchive');
      });
    });
  });
  // Delete (optimistic)
  body.querySelectorAll('.note-card-delete, .note-card-x').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.noteId;
      const idx = _notes.findIndex(n => n.id === id);
      if (idx < 0) return;
      const removed = _notes.splice(idx, 1)[0];
      _renderNotes();
      _deleteNoteApi(id).catch(() => {
        _notes.splice(idx, 0, removed);
        _renderNotes();
        uiModule.showError('Failed to delete');
      });
    });
  });
  // Copy entire checklist (title + items, markdown-style)
  body.querySelectorAll('.note-card-copy').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      const id = btn.dataset.noteId;
      const note = _notes.find(n => n.id === id);
      if (!note) return;
      const lines = [];
      if (note.title) lines.push(note.title);
      if (note.content) lines.push(note.content);
      if (lines.length) lines.push('');
      for (const it of (note.items || [])) {
        if (!it || !(it.text || '').trim()) continue;
        lines.push(`- [${it.done ? 'x' : ' '}] ${(it.text || '').trim()}`);
      }
      const text = lines.join('\n').trim();
      try {
        await navigator.clipboard.writeText(text);
        uiModule.showToast?.(`Copied ${(note.items || []).filter(i => (i?.text || '').trim()).length} items`);
      } catch {
        // Fallback for browsers blocking the async API
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed'; ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); uiModule.showToast?.('Copied'); }
        catch { uiModule.showError?.('Copy failed'); }
        ta.remove();
      }
    });
  });

  // Remove a single checklist item (hover X)
  body.querySelectorAll('.note-checkbox-rm').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (_selectMode) return;
      const noteId = btn.dataset.noteId;
      const idx = parseInt(btn.dataset.idx);
      const note = _notes.find(n => n.id === noteId);
      if (!note || !Array.isArray(note.items) || !note.items[idx]) return;
      const removed = note.items[idx];
      note.items = note.items.filter((_, i) => i !== idx);
      _renderNotes();
      _patchNote(noteId, { items: note.items }).catch(() => {
        note.items.splice(idx, 0, removed);
        _renderNotes();
        uiModule.showError('Failed to remove item');
      });
    });
  });

  // Quick-add new checklist item (hover input at bottom of todo cards)
  body.querySelectorAll('.note-cl-quickadd-input').forEach(input => {
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('keydown', async (e) => {
      e.stopPropagation();
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      const noteId = input.dataset.noteId;
      const note = _notes.find(n => n.id === noteId);
      if (!note) return;
      const items = Array.isArray(note.items) ? [...note.items] : [];
      items.push({ id: _uid(), text, done: false });
      note.items = items;
      input.value = '';
      _renderNotes();
      // Refocus the input on the same card
      setTimeout(() => {
        const next = document.querySelector(`.note-cl-quickadd-input[data-note-id="${noteId}"]`);
        if (next) next.focus();
      }, 0);
      _patchNote(noteId, { items }).catch(() => {
        note.items = items.slice(0, -1);
        _renderNotes();
        uiModule.showError('Failed to add item');
      });
    });
  });

  // Checkboxes (dot toggle, optimistic) — disabled in select mode
  body.querySelectorAll('.note-checkbox').forEach(el => {
    el.addEventListener('click', (e) => {
      if (_selectMode) return; // let card-level handler take over
      e.stopPropagation();
      const noteId = el.dataset.noteId;
      const idx = parseInt(el.dataset.idx);
      const note = _notes.find(n => n.id === noteId);
      if (!note || !note.items || !note.items[idx]) return;
      const wasAllDone = note.items.length > 0 && note.items.every(it => it.done);
      note.items[idx].done = !note.items[idx].done;
      el.classList.toggle('done', note.items[idx].done);
      const isAllDone = note.items.length > 0 && note.items.every(it => it.done);
      if (!wasAllDone && isAllDone) {
        const card = el.closest('.note-card');
        if (card) {
          const r = card.getBoundingClientRect();
          spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 60);
        }
      }
      _patchNote(noteId, { items: note.items }).catch(() => {
        note.items[idx].done = !note.items[idx].done;
        el.classList.toggle('done', note.items[idx].done);
      });
    });
  });

  // Drag-reorder notes on pointer/mouse devices. Mobile uses the custom
  // placeholder sorter below `_bindLongPressDrag`; native HTML5 dragging is
  // unreliable on touch browsers and can compete with the long-press flow.
  if (!_isNotesMobileMode()) {
    body.querySelectorAll('.note-card').forEach(card => {
      card.addEventListener('dragstart', (e) => {
        if (e.target.closest('.note-checkbox, .note-card-x, .note-card-select, .note-card-pin, .note-card-action, .note-card-color-dot, .note-card-title, .note-card-edit, .note-card-edit-corner, .note-card-done, .note-card-corner-menu, .note-agent-tag, .note-card-label-chip')) {
          e.preventDefault();
          return;
        }
        card.classList.add('dragging');
        body.classList.add('drag-active');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', card.dataset.noteId); } catch {}
      });
      card.addEventListener('dragend', async () => {
        card.classList.remove('dragging');
        body.classList.remove('drag-active');
        body.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
        const ids = [...body.querySelectorAll('.note-card')].map(c => c.dataset.noteId);
        try { await fetch(`${API_BASE}/api/notes/reorder`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) }); }
        catch {}
      });
    });
  }
  // Track which card we last swapped with so a single hover-over triggers
  // one swap, not a jitter as the pointer keeps moving inside the same card.
  let _lastSwapId = null;
  function _maybeSwap(dragging, clientX, clientY) {
    const target = document.elementFromPoint(clientX, clientY)?.closest('.note-card');
    if (!target || target === dragging || !body.contains(target)) return;
    const id = target.dataset.noteId;
    if (id === _lastSwapId) return;
    // FLIP across ALL siblings. In list view only `target` moves visually, but
    // in grid view (2-col) and when the pinned-section grid-column-start rule
    // shifts, several cards reflow at once. Capture every card's pre-swap rect,
    // do the DOM swap, then animate any that actually moved. The dragging card
    // is excluded — it's already finger-tracked via translate3d.
    const cards = [...body.querySelectorAll('.note-card')].filter(c => c !== dragging);
    const prevRects = new Map(cards.map(c => [c, c.getBoundingClientRect()]));
    const draggingNext = dragging.nextSibling === target ? dragging : dragging.nextSibling;
    body.insertBefore(dragging, target);
    body.insertBefore(target, draggingNext);
    for (const c of cards) {
      const prev = prevRects.get(c);
      const next = c.getBoundingClientRect();
      const dx = prev.left - next.left;
      const dy = prev.top - next.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      c.style.transition = 'none';
      c.style.transform = `translate(${dx}px, ${dy}px)`;
      requestAnimationFrame(() => {
        c.style.transition = 'transform 0.22s cubic-bezier(0.34, 1.2, 0.64, 1)';
        c.style.transform = '';
        c.addEventListener('transitionend', () => { c.style.transition = ''; }, { once: true });
      });
    }
    _lastSwapId = id;
  }
  body.addEventListener('dragover', (e) => {
    e.preventDefault();
    const dragging = body.querySelector('.note-card.dragging');
    if (!dragging) return;
    _maybeSwap(dragging, e.clientX, e.clientY);
  });
  body.addEventListener('dragend', () => { _lastSwapId = null; });

  // Legacy touch drag for larger touch devices only. Phone-sized Notes uses
  // the placeholder sorter wired by `_bindLongPressDrag`; running both flows
  // makes one press start two independent drag sessions.
  if (!_isNotesMobileMode() && 'ontouchstart' in window && !body.dataset.touchDragBound) {
    body.dataset.touchDragBound = '1';
    let dragCard = null;
    let isDragging = false;
    let longPressTimer = null;
    let startX = 0, startY = 0;
    const LONG_PRESS_MS = 350;
    const MOVE_THRESHOLD_PX = 8;
    const _selectorSkip = '.note-checkbox, .note-card-x, .note-card-select, .note-card-pin, .note-card-action, .note-card-color-dot, .note-card-title, .note-card-edit, .note-card-edit-corner, .note-card-done, .note-card-corner-menu, .note-agent-tag, .note-card-label-chip, input, textarea, button, a';

    // Anchor for the finger-follow transform. Recomputed after every swap so
    // the card stays under the finger across reorderings.
    let anchorX = 0, anchorY = 0;
    const _follow = (clientX, clientY) => {
      if (!dragCard) return;
      const dx = clientX - anchorX;
      const dy = clientY - anchorY;
      // Compose with the CSS .dragging transform (scale + rotate).
      dragCard.style.transform = `translate3d(${dx}px, ${dy}px, 0) scale(1.03) rotate(-0.6deg)`;
    };
    const _reanchor = (clientX, clientY) => {
      if (!dragCard) return;
      // Clear any prior translate so the card resettles in its natural slot,
      // then anchor at the finger's current position. Subsequent _follow calls
      // translate by (finger - anchor), keeping the finger at the same relative
      // spot on the card it had when we re-anchored.
      dragCard.style.transform = '';
      anchorX = clientX;
      anchorY = clientY;
    };

    const _endDrag = (committed) => {
      clearTimeout(longPressTimer);
      longPressTimer = null;
      if (dragCard) {
        dragCard.classList.remove('dragging');
        dragCard.style.transform = '';
      }
      body.classList.remove('drag-active');
      _lastSwapId = null;
      if (isDragging) {
        document.documentElement.style.touchAction = '';
        if (committed) {
          const ids = [...body.querySelectorAll('.note-card')].map(c => c.dataset.noteId);
          fetch(`${API_BASE}/api/notes/reorder`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) }).catch(() => {});
        }
      }
      dragCard = null;
      isDragging = false;
    };

    body.addEventListener('touchstart', (e) => {
      if (_selectMode) return;
      const card = e.target.closest('.note-card');
      if (!card) return;
      if (e.target.closest(_selectorSkip)) return;
      const t = e.touches[0];
      startX = t.clientX; startY = t.clientY;
      dragCard = card;
      longPressTimer = setTimeout(() => {
        if (!dragCard) return;
        isDragging = true;
        dragCard.classList.add('dragging');
        body.classList.add('drag-active');
        document.documentElement.style.touchAction = 'none';
        _reanchor(startX, startY);
        try { if (navigator.vibrate) navigator.vibrate(15); } catch {}
      }, LONG_PRESS_MS);
    }, { passive: true });

    body.addEventListener('touchmove', (e) => {
      if (!dragCard) return;
      const t = e.touches[0];
      if (!isDragging) {
        // Movement before long-press fires = user is scrolling; cancel pickup.
        if (Math.abs(t.clientX - startX) > MOVE_THRESHOLD_PX || Math.abs(t.clientY - startY) > MOVE_THRESHOLD_PX) {
          clearTimeout(longPressTimer);
          longPressTimer = null;
          dragCard = null;
        }
        return;
      }
      e.preventDefault();
      // Live-follow the finger first, then check for a swap. After a swap the
      // card's natural slot moves, so we re-anchor and re-apply the offset.
      _follow(t.clientX, t.clientY);
      const before = dragCard.parentNode && [...dragCard.parentNode.children].indexOf(dragCard);
      _maybeSwap(dragCard, t.clientX, t.clientY);
      const after = dragCard.parentNode && [...dragCard.parentNode.children].indexOf(dragCard);
      if (before !== after) {
        _reanchor(t.clientX, t.clientY);
        _follow(t.clientX, t.clientY);
      }
    }, { passive: false });

    body.addEventListener('touchend', () => _endDrag(true));
    body.addEventListener('touchcancel', () => _endDrag(false));
  }
}

// ── Draft autosave ──────────────────────────────────────────────────
// While a note is open in the editor, its form is snapshotted to
// localStorage on every change (debounced). If the connection drops, the
// tab closes, or the page reloads before Save is hit, reopening that note
// restores the unsaved text. Drafts are cleared on an explicit Save or
// Cancel. Survives offline because it never touches the network.
const _DRAFT_PREFIX = 'odysseus-note-draft-';
function _draftKey(id) { return _DRAFT_PREFIX + (id || '__new__'); }
function _loadDraft(id) {
  try { return JSON.parse(localStorage.getItem(_draftKey(id)) || 'null'); } catch { return null; }
}
function _clearDraft(id) { try { localStorage.removeItem(_draftKey(id)); } catch {} }
function _collectFormDraft(form) {
  if (!form) return null;
  const type = form.querySelector('.note-form-type-pill.active')?.dataset.type || 'note';
  const d = {
    _ts: Date.now(),
    note_type: type,
    title: form.querySelector('.note-form-title')?.value || '',
    label: form.querySelector('.note-form-label')?.value || '',
    due_date: form.querySelector('.note-form-due')?.value || null,
    repeat: form.querySelector('.note-form-repeat')?.value || 'none',
  };
  if (type === 'note') d.content = form.querySelector('.note-form-content')?.value || '';
  else if (type === 'goal') { d.content = form.querySelector('.note-form-goal-desc')?.value || ''; d.items = _collectItems(form); }
  else d.items = _collectItems(form);
  return d;
}
function _isDraftEmpty(d) {
  if (!d) return true;
  if ((d.title || '').trim()) return false;
  if ((d.content || '').trim()) return false;
  if (Array.isArray(d.items) && d.items.some(it => (it.text || '').trim())) return false;
  return true;
}
function _wireDraftAutosave(form, id) {
  let t = null;
  const save = () => {
    const d = _collectFormDraft(form);
    if (_isDraftEmpty(d)) { _clearDraft(id); return; }
    try { localStorage.setItem(_draftKey(id), JSON.stringify(d)); } catch {}
  };
  form._flushDraft = () => { clearTimeout(t); save(); };
  const sched = () => { clearTimeout(t); t = setTimeout(save, 600); };
  form.addEventListener('input', sched);
  form.addEventListener('change', sched);
}

// Commit whatever in-place editor is open (called when the panel closes
// or another note is opened) so edits aren't lost when the user navigates
// away without clicking Save. Empty notes are discarded instead of saved.
function _commitOpenInPlaceEditor() {
  const form = document.querySelector('#notes-pane .note-form');
  if (!form) return;
  const d = _collectFormDraft(form);
  if (_isDraftEmpty(d)) { form.querySelector('.note-form-cancel')?.click(); return; }
  form.querySelector('.note-form-save')?.click();
}
// Merge a stored draft over a note so _buildForm renders the unsaved edits.
function _applyDraftToNote(note, id) {
  const d = _loadDraft(id);
  if (_isDraftEmpty(d)) return { note, restored: false };
  const merged = { ...(note || {}) };
  ['note_type', 'title', 'label', 'due_date', 'repeat', 'content', 'items'].forEach(k => {
    if (d[k] !== undefined) merged[k] = d[k];
  });
  return { note: merged, restored: true };
}

// ---- Create / Edit Form ----

function _buildForm(note = null) {
  const isEdit = note && note.id;
  const type = note?.note_type || 'note';
  const color = note?.color || '';
  const items = note?.items || [{ id: _uid(), text: '', done: false }];

  const form = document.createElement('div');
  form.className = 'note-form';
  if (color && !_isBgImage(color)) form.classList.add('note-color-' + color);
  if (_isBgImage(color)) form.setAttribute('style', _customColorStyle(color));
  let currentImageUrl = _safeImgSrc(note?.image_url || '');
  form.innerHTML = `
    <div class="note-form-header">
      <input type="text" class="note-form-title" placeholder="Title" value="${_esc(note?.title || '')}" />
      <button type="button" class="note-form-icon-btn note-form-remind-btn${note?.due_date ? ' has-date' : ''}" title="Remind me">
        <svg width="31" height="31" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
      </button>
      <input type="hidden" class="note-form-due" value="${note?.due_date || ''}" />
      <input type="hidden" class="note-form-repeat" value="${note?.repeat || 'none'}" />
    </div>
    ${currentImageUrl && type !== 'draw' ? `<div class="note-form-image-wrap"><img class="note-form-image" src="${_esc(currentImageUrl)}" draggable="false" /><button class="note-form-image-rm" title="Remove">&times;</button></div>` : ''}
    <div class="note-form-body">
      ${type === 'note'
        ? `<textarea class="note-form-content" placeholder="Take a note..." rows="4">${_esc(note?.content || '')}</textarea>`
        : type === 'draw'
        ? _buildDrawHtml()
        : type === 'goal'
        ? _buildGoalHtml(note, items)
        : _buildChecklistHtml(items)}
    </div>
    <div class="note-form-reminder-tags"></div>
    <div class="note-form-meta">
      <div class="note-form-type-seg${type === 'todo' ? ' is-todo' : type === 'draw' ? ' is-draw' : ''}" role="group">
        <button type="button" class="note-form-type-pill${type === 'note' ? ' active' : ''}" data-type="note">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="14" y2="18"/></svg>
          <span>Note</span>
        </button>
        <button type="button" class="note-form-type-pill${type === 'todo' ? ' active' : ''}" data-type="todo">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
          <span>Todo</span>
        </button>
        <button type="button" class="note-form-type-pill${type === 'draw' ? ' active' : ''}" data-type="draw">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>
          <span>Draw</span>
        </button>
      </div>
      <button class="note-form-photo-btn" title="Attach photo">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
      </button>
      <input type="file" class="note-form-photo-input" accept="image/*" capture="environment" style="display:none" />
      <div class="note-color-picker">
        ${COLORS.map(c => `<span class="note-color-dot${_dotIsActive(c.value, color) ? ' active' : ''}" data-color="${c.value}" style="background:${_dotBg(c.value, color)}" title="${c.name || 'default'}"></span>`).join('')}
      </div>
      <input type="text" class="note-form-label" value="${_esc(note?.label || '')}" placeholder="#tag1 #tag2" title="Tag(s) — space-separated" />
      <div class="note-form-actions-group">
        ${isEdit ? `
        <button type="button" class="note-form-text-btn note-form-archive-btn note-form-collapsible" title="Archive">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg><span class="nft-label">Archive</span>
        </button>
        <button type="button" class="note-form-text-btn note-form-delete-btn note-form-collapsible danger" title="Delete">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg><span class="nft-label">Delete</span>
        </button>
        ` : ''}
        <span class="note-form-actions-spacer"></span>
        <button class="note-form-cancel note-form-text-btn note-form-collapsible" title="Cancel">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg><span class="nft-label">Cancel</span>
        </button>
        <button class="note-form-save note-form-text-btn" title="${isEdit ? 'Update' : 'Save'}">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span class="nft-label">${isEdit ? 'Update' : 'Save'}</span>
        </button>
      </div>
    </div>
  `;

  let currentType = type;
  let currentColor = color;
  // Stash original-form values so round-trips (Note→Todo→Note) restore the
  // user's hand-formatted text instead of a join of generated items. Same the
  // other way: if you started in todo, switch to note, switch back, items
  // come back unchanged.
  let _stashedNoteText = (type === 'note') ? (note?.content || '') : null;
  let _stashedTodoItems = (type === 'todo' && Array.isArray(note?.items)) ? note.items.slice() : null;
  // Goal mode kept its own pair of stashes (description + steps) so a
  // Todo→Goal→Todo round-trip wouldn't lose either side. The Goal pill in
  // the type picker was later removed, so the only entry point now is
  // *editing* an existing goal-typed note — but the switch handler still
  // accepts Goal→Todo/Note transitions (downgrading legacy goals), so
  // these stashes still earn their keep.
  let _stashedGoalDesc = (type === 'goal') ? (note?.content || '') : null;
  let _stashedGoalItems = (type === 'goal' && Array.isArray(note?.items)) ? note.items.slice() : null;

  // Drawing also stashes the saved image URL so it survives Note↔Draw flips.
  let _stashedDrawUrl = (type === 'draw') ? (_safeImgSrc(note?.image_url) || null) : null;
  const _refreshFormLayout = () => {
    const body = form.closest('.notes-pane-body');
    if (!body) return;
    _applyMasonry(body);
    requestAnimationFrame(() => {
      _applyMasonry(body);
      requestAnimationFrame(() => _applyMasonry(body));
    });
  };

  // Type segmented control — Note | Todo | Draw
  form.querySelectorAll('.note-form-type-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      const newType = pill.dataset.type;
      if (newType === currentType) return;
      const bodyEl = form.querySelector('.note-form-body');
      // Stash whatever the user has in the current mode before swapping it
      // out, so a subsequent flip back restores their work.
      if (currentType === 'note') {
        _stashedNoteText = form.querySelector('.note-form-content')?.value || '';
      } else if (currentType === 'todo') {
        _stashedTodoItems = _collectItems(form);
      } else if (currentType === 'goal') {
        _stashedGoalDesc = form.querySelector('.note-form-goal-desc')?.value || '';
        _stashedGoalItems = _collectItems(form);
      } else if (currentType === 'draw') {
        const c = form.querySelector('.note-form-canvas');
        if (c) { try { _stashedDrawUrl = c.toDataURL('image/png'); } catch {} }
      }
      // Render the new mode's body and re-wire its inputs.
      if (newType === 'todo') {
        let nextItems;
        if (_stashedTodoItems && _stashedTodoItems.length) {
          nextItems = _stashedTodoItems;
        } else if (_stashedGoalItems && _stashedGoalItems.length) {
          // Going Goal→Todo keeps the AI-generated steps as a plain checklist.
          nextItems = _stashedGoalItems;
        } else if (_stashedNoteText) {
          const lines = _stashedNoteText.split('\n').map(s => s.trim()).filter(Boolean);
          nextItems = lines.length ? lines.map(t => ({ id: _uid(), text: t, done: false })) : [{ id: _uid(), text: '', done: false }];
        } else {
          nextItems = [{ id: _uid(), text: '', done: false }];
        }
        bodyEl.innerHTML = _buildChecklistHtml(nextItems);
        _wireChecklist(bodyEl);
      } else if (newType === 'draw') {
        bodyEl.innerHTML = _buildDrawHtml();
        // If the user just attached a photo (via the photo button) and then
        // toggled to Draw, paint that photo onto the canvas so they can draw
        // on top of it. _stashedDrawUrl wins if they were drawing earlier in
        // the same edit session.
        _wireCanvas(bodyEl, _stashedDrawUrl || currentImageUrl || _safeImgSrc(note?.image_url) || null);
      } else {
        const text = (_stashedNoteText !== null && _stashedNoteText !== undefined && _stashedNoteText !== '')
          ? _stashedNoteText
          : (_stashedGoalDesc && _stashedGoalDesc)
          || (_stashedTodoItems || _stashedGoalItems || []).map(i => i.text).join('\n');
        bodyEl.innerHTML = `<textarea class="note-form-content" placeholder="Take a note..." rows="4">${_esc(text)}</textarea>`;
        _wireHashtag(bodyEl.querySelector('.note-form-content'));
      }
      const focusEl = newType === 'note'
        ? bodyEl.querySelector('.note-form-content')
        : newType === 'todo'
          ? bodyEl.querySelector('.note-cl-text')
          : null;
      if (focusEl) {
        requestAnimationFrame(() => {
          focusEl.focus({ preventScroll: true });
          try {
            const end = focusEl.value.length;
            focusEl.setSelectionRange(end, end);
          } catch {}
        });
      }
      currentType = newType;
      const seg = form.querySelector('.note-form-type-seg');
      seg?.classList.toggle('is-todo', newType === 'todo');
      seg?.classList.toggle('is-draw', newType === 'draw');
      form.querySelectorAll('.note-form-type-pill').forEach(p => p.classList.toggle('active', p.dataset.type === newType));
      // The standalone image preview (form-image-wrap) and the canvas would
      // otherwise both show the same image_url when editing a drawn note.
      // Hide it in draw mode, restore it when leaving draw mode.
      const imgWrap = form.querySelector('.note-form-image-wrap');
      if (imgWrap) imgWrap.style.display = (newType === 'draw') ? 'none' : '';
      // The background-color dots set the note card's bg — they make no sense
      // for a drawn note (the canvas image IS the card content), so hide them.
      const bgPicker = form.querySelector('.note-color-picker');
      if (bgPicker) bgPicker.style.display = (newType === 'draw') ? 'none' : '';
      if (form.closest('.notes-pane.notes-view-grid') && window.matchMedia('(max-width: 768px)').matches) {
        form.style.gridColumn = '1 / -1';
        form.style.gridRowEnd = newType === 'draw' ? 'span 152' : 'span 64';
      }
      _refreshFormLayout();
    });
  });

  // Slide a finger across the Note/Todo/Draw control to switch modes (mobile).
  // On touchmove we find the pill under the finger and click it — reusing the
  // pill click handler above, so the body re-renders + content stashing all
  // work. Only fires when crossing into a *different* pill.
  const _typeSeg = form.querySelector('.note-form-type-seg');
  if (_typeSeg) {
    let _sliding = false;
    const _activateAt = (x, y) => {
      const pill = document.elementFromPoint(x, y)?.closest?.('.note-form-type-pill');
      if (pill && !pill.classList.contains('active')) pill.click();
    };
    _typeSeg.addEventListener('touchstart', () => { _sliding = true; }, { passive: true });
    _typeSeg.addEventListener('touchmove', (e) => {
      if (_sliding && e.touches[0]) _activateAt(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: true });
    _typeSeg.addEventListener('touchend', () => { _sliding = false; });
    _typeSeg.addEventListener('touchcancel', () => { _sliding = false; });
  }

  // Color dots — apply to entire form immediately
  const _applyFormColor = (newColor) => {
    currentColor = newColor || '';
    const isBg = _isBgImage(currentColor);
    COLORS.forEach(c => { if (c.value && c.value !== 'custom') form.classList.remove('note-color-' + c.value); });
    if (currentColor && !isBg) form.classList.add('note-color-' + currentColor);
    if (isBg) form.setAttribute('style', _customColorStyle(currentColor));
    else form.removeAttribute('style');
    form.querySelectorAll('.note-color-dot').forEach(d => {
      d.classList.toggle('active', _dotIsActive(d.dataset.color, currentColor));
      d.style.background = _dotBg(d.dataset.color, currentColor);
    });
  };
  form.querySelectorAll('.note-color-dot').forEach(dot => {
    dot.addEventListener('click', () => {
      if (dot.dataset.color === 'custom') {
        _pickCustomBgImage().then(url => { if (url) _applyFormColor('bg:' + url); });
        return;
      }
      _applyFormColor(dot.dataset.color);
    });
  });

  if (currentType === 'todo') _wireChecklist(form.querySelector('.note-form-body'));
  if (currentType === 'goal') _wireGoalForm(form, form.querySelector('.note-form-body'));
  if (currentType === 'draw') {
    _wireCanvas(form.querySelector('.note-form-body'), _safeImgSrc(note?.image_url) || null);
    // Same hides we apply on type-switch — keep them consistent on initial open.
    const _ip = form.querySelector('.note-form-image-wrap'); if (_ip) _ip.style.display = 'none';
    const _cp = form.querySelector('.note-color-picker'); if (_cp) _cp.style.display = 'none';
  }

  // Auto-grow the plain-note textarea so editing longer notes is
  // comfortable — it expands with the content (up to a cap) instead of
  // staying a cramped 4-row box. The user can still drag-resize too.
  const _contentTa = form.querySelector('.note-form-content');
  if (_contentTa) {
    const _grow = () => {
      _contentTa.style.height = 'auto';
      // Inline form: cap at ~50vh so a huge note doesn't push the action
      // buttons off-screen. Fullscreen mobile overlay: the body scrolls and
      // there are no inline buttons crowding, so allow nearly the full height
      // — capping at 50vh there clipped longer notes ("part disappears").
      const inFullscreen = !!_contentTa.closest('.note-fullscreen-overlay');
      const max = Math.round(window.innerHeight * (inFullscreen ? 0.9 : 0.5));
      _contentTa.style.height = Math.min(_contentTa.scrollHeight, max) + 'px';
    };
    _contentTa.addEventListener('input', _grow);
    // Grow on open so existing content is fully visible. Run again after the
    // fullscreen overlay's open animation settles — measuring mid-animation
    // (the overlay starts scaled/transitioning) can under-size the box.
    setTimeout(_grow, 0);
    setTimeout(_grow, 360);
  }

  // Reminder bell — opens dropdown menu
  const remindBtn = form.querySelector('.note-form-remind-btn');
  const dueInput = form.querySelector('.note-form-due');
  const repeatInput = form.querySelector('.note-form-repeat');
  const tagsEl = form.querySelector('.note-form-reminder-tags');

  function _renderReminderTag() {
    if (!tagsEl) return;
    const v = dueInput.value;
    const rep = repeatInput.value || 'none';
    if (!v) { tagsEl.innerHTML = ''; return; }
    const label = _formatReminderTag(v);
    const repLabel = rep !== 'none' ? ` · ${_formatRepeatLabel(rep, new Date(v))}` : '';
    tagsEl.innerHTML = `<button class="note-reminder-tag" type="button" title="Edit reminder">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
      <span>${_esc(label)}${_esc(repLabel)}</span>
      <span class="note-reminder-tag-x" title="Remove">×</span>
    </button>`;
    tagsEl.querySelector('.note-reminder-tag').addEventListener('click', (e) => {
      if (e.target.classList.contains('note-reminder-tag-x')) {
        dueInput.value = '';
        repeatInput.value = 'none';
        _renderReminderTag();
        return;
      }
      _openReminderMenu(remindBtn || tagsEl, true);
    });
  }

  function _openReminderMenu(anchor, isEdit = false) {
    // Close any existing menu
    document.querySelectorAll('.note-reminder-menu').forEach(m => m.remove());
    const menu = document.createElement('div');
    menu.className = 'note-reminder-menu';
    document.body.appendChild(menu);

    const presetItems = [
      { label: 'Later today', sub: _laterTodayDate().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), action: () => _setReminder(_toLocalDatetimeStr(_laterTodayDate())) },
      { label: 'Tomorrow', sub: _tomorrowDate().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), action: () => _setReminder(_toLocalDatetimeStr(_tomorrowDate())) },
      { label: 'Next week', sub: _nextWeekDate().toLocaleDateString([], { weekday: 'short' }) + ' ' + _nextWeekDate().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }), action: () => _setReminder(_toLocalDatetimeStr(_nextWeekDate())) },
      { label: 'Select date and time', sub: '', action: () => _pickCustomDate() },
    ];

    // Sub-page state for the repeat picker. null = top page.
    // 'weekly' | 'monthly' | 'monthly_nth'
    let subMode = null;
    // Temporary state for monthly_nth so user can click N then weekday (or vice versa)
    // before committing.
    let nthDraft = { n: 0, w: -1 };

    const DAY_SHORT = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

    function getNorm() {
      if (!dueInput.value) return 'none';
      return _normalizeRepeat(repeatInput.value || 'none', new Date(dueInput.value));
    }

    function commit(val) {
      repeatInput.value = val;
      _renderReminderTag();
      menu.remove();
    }

    // Like commit, but first snaps dueInput.value forward to the next matching
    // slot for the chosen recurrence. Use for weekly/monthly variants where the
    // current due date may not match the chosen pattern (e.g. user picks
    // "weekly on Mondays" while the date is a Wednesday).
    function snapAndCommit(val) {
      if (dueInput.value) {
        const cur = new Date(dueInput.value);
        const norm = _normalizeRepeat(val, cur);
        const snapped = _snapToRepeat(cur, norm);
        if (snapped) {
          dueInput.value = _toLocalDatetimeStr(snapped);
          if (remindBtn) remindBtn.classList.add('has-date');
        }
      }
      commit(val);
    }

    function reposition() {
      const rect = anchor.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const mw = menu.offsetWidth || 220;
      const mh = menu.offsetHeight || 280;
      let top = rect.bottom + 4;
      let left = rect.left;
      if (top + mh > vh - 8) top = Math.max(8, rect.top - mh - 4);
      if (left + mw > vw - 8) left = Math.max(8, vw - mw - 8);
      if (left < 8) left = 8;
      menu.style.top = top + 'px';
      menu.style.left = left + 'px';
    }

    function render() {
      let html = '';

      if (subMode === null) {
        html += '<div class="note-reminder-menu-title">Remind me later</div>';
        for (let i = 0; i < presetItems.length; i++) {
          const it = presetItems[i];
          html += `<button class="note-reminder-menu-item" data-action="preset" data-i="${i}"><span>${it.label}</span><span class="note-reminder-menu-sub">${it.sub}</span></button>`;
        }
        if (isEdit && dueInput.value) {
          const norm = getNorm();
          html += '<div class="note-reminder-menu-divider"></div>';
          html += '<div class="note-reminder-menu-title">Repeat</div>';
          // None
          html += `<button class="note-reminder-menu-item${norm === 'none' ? ' active' : ''}" data-action="set" data-val="none"><span>Doesn't repeat</span>${norm === 'none' ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
          // Daily
          html += `<button class="note-reminder-menu-item${norm === 'daily' ? ' active' : ''}" data-action="set" data-val="daily"><span>Daily</span>${norm === 'daily' ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
          // Weekly →
          {
            const isW = norm.startsWith('weekly:');
            const wd = isW ? parseInt(norm.split(':')[1], 10) : null;
            const sub = isW && !isNaN(wd) ? `<span class="note-reminder-menu-sub">${_DAYS[wd]}</span>` : '';
            html += `<button class="note-reminder-menu-item${isW ? ' active' : ''}" data-action="sub" data-sub="weekly"><span>Weekly</span>${sub}<span class="note-reminder-menu-arrow">›</span></button>`;
          }
          // Monthly →
          {
            const isM = norm.startsWith('monthly:');
            const sub = isM ? `<span class="note-reminder-menu-sub">${_monthlyShortDescriptor(norm)}</span>` : '';
            html += `<button class="note-reminder-menu-item${isM ? ' active' : ''}" data-action="sub" data-sub="monthly"><span>Monthly</span>${sub}<span class="note-reminder-menu-arrow">›</span></button>`;
          }
          // Yearly
          html += `<button class="note-reminder-menu-item${norm === 'yearly' ? ' active' : ''}" data-action="set" data-val="yearly"><span>Yearly</span>${norm === 'yearly' ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
        }
      } else if (subMode === 'weekly') {
        const norm = getNorm();
        const curWd = norm.startsWith('weekly:') ? parseInt(norm.split(':')[1], 10) : -1;
        html += `<button class="note-reminder-menu-back" data-action="back"><span class="note-reminder-menu-arrow-back">‹</span> Repeat</button>`;
        html += '<div class="note-reminder-menu-title">Weekly on…</div>';
        html += '<div class="note-reminder-weekday-row">';
        for (let i = 0; i < 7; i++) {
          html += `<button class="note-reminder-day-chip${curWd === i ? ' active' : ''}" data-action="weekly-pick" data-wd="${i}" title="${_DAYS[i]}">${DAY_SHORT[i]}</button>`;
        }
        html += '</div>';
      } else if (subMode === 'monthly') {
        const norm = getNorm();
        const dueDate = new Date(dueInput.value);
        const dayN = dueDate.getDate();
        html += `<button class="note-reminder-menu-back" data-action="back"><span class="note-reminder-menu-arrow-back">‹</span> Repeat</button>`;
        html += '<div class="note-reminder-menu-title">Monthly on…</div>';
        // Day N — uses the chosen date's day. Always offered.
        const dayVal = `monthly:day:${dayN}`;
        html += `<button class="note-reminder-menu-item${norm === dayVal ? ' active' : ''}" data-action="set" data-val="${dayVal}"><span>Day ${dayN} every month</span>${norm === dayVal ? '<span class="note-reminder-menu-check">✓</span>' : ''}</button>`;
        // Nth weekday →
        {
          const isNth = norm.startsWith('monthly:nth:');
          const sub = isNth ? `<span class="note-reminder-menu-sub">${_monthlyShortDescriptor(norm)}</span>` : '';
          html += `<button class="note-reminder-menu-item${isNth ? ' active' : ''}" data-action="sub" data-sub="monthly_nth"><span>Nth weekday</span>${sub}<span class="note-reminder-menu-arrow">›</span></button>`;
        }
      } else if (subMode === 'monthly_nth') {
        // Pick ordinal (1..4) and weekday (0..6); commit when both chosen.
        html += `<button class="note-reminder-menu-back" data-action="back-monthly"><span class="note-reminder-menu-arrow-back">‹</span> Monthly</button>`;
        html += '<div class="note-reminder-menu-title">Nth weekday of month</div>';
        html += '<div class="note-reminder-menu-sublabel">Which one</div>';
        html += '<div class="note-reminder-weekday-row">';
        for (let i = 1; i <= 4; i++) {
          html += `<button class="note-reminder-day-chip wide${nthDraft.n === i ? ' active' : ''}" data-action="nth-n" data-n="${i}">${_ORDINALS[i - 1]}</button>`;
        }
        html += '</div>';
        html += '<div class="note-reminder-menu-sublabel">Weekday</div>';
        html += '<div class="note-reminder-weekday-row">';
        for (let i = 0; i < 7; i++) {
          html += `<button class="note-reminder-day-chip${nthDraft.w === i ? ' active' : ''}" data-action="nth-w" data-wd="${i}" title="${_DAYS[i]}">${DAY_SHORT[i]}</button>`;
        }
        html += '</div>';
        html += '<div class="note-reminder-menu-divider"></div>';
        const ready = nthDraft.n > 0 && nthDraft.w >= 0;
        const lbl = ready ? `Save: ${_ORDINALS[nthDraft.n - 1]} ${_DAYS[nthDraft.w]}` : 'Pick week and weekday';
        html += `<button class="note-reminder-menu-item note-reminder-menu-confirm${ready ? '' : ' disabled'}" data-action="nth-save" ${ready ? '' : 'disabled'}><span>${lbl}</span></button>`;
      }

      menu.innerHTML = html;
      reposition();
      wire();
    }

    function wire() {
      menu.querySelectorAll('[data-action]').forEach(el => {
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          const a = el.dataset.action;
          if (a === 'preset') {
            const it = presetItems[parseInt(el.dataset.i, 10)];
            it.action();
            menu.remove();
          } else if (a === 'set') {
            snapAndCommit(el.dataset.val);
          } else if (a === 'sub') {
            subMode = el.dataset.sub;
            // Seed nth draft from saved value only on first entry — preserve
            // in-progress picks across back-trips (Nth → back → Nth again).
            if (subMode === 'monthly_nth' && nthDraft.n === 0 && nthDraft.w === -1) {
              const norm = getNorm();
              const m = norm.match(/^monthly:nth:(\d):(\d)$/);
              if (m) nthDraft = { n: parseInt(m[1], 10), w: parseInt(m[2], 10) };
            }
            render();
          } else if (a === 'back') {
            subMode = null;
            render();
          } else if (a === 'back-monthly') {
            subMode = 'monthly';
            render();
          } else if (a === 'weekly-pick') {
            snapAndCommit(`weekly:${el.dataset.wd}`);
          } else if (a === 'nth-n') {
            nthDraft.n = parseInt(el.dataset.n, 10);
            render();
          } else if (a === 'nth-w') {
            nthDraft.w = parseInt(el.dataset.wd, 10);
            render();
          } else if (a === 'nth-save') {
            if (nthDraft.n > 0 && nthDraft.w >= 0) {
              snapAndCommit(`monthly:nth:${nthDraft.n}:${nthDraft.w}`);
            }
          }
        });
      });
    }

    render();
    // Click outside to close (single global handler attached after first paint)
    setTimeout(() => {
      const close = (e) => {
        if (!menu.isConnected) { document.removeEventListener('click', close); return; }
        if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); }
      };
      document.addEventListener('click', close);
    }, 0);
  }

  function _monthlyShortDescriptor(norm) {
    const parts = norm.split(':');
    if (parts[1] === 'day') return `Day ${parts[2]}`;
    if (parts[1] === 'nth') {
      const n = parseInt(parts[2], 10);
      const wd = parseInt(parts[3], 10);
      return `${_ORDINALS[n - 1] || `${n}th`} ${_DAYS[wd].slice(0, 3)}`;
    }
    if (parts[1] === 'last') {
      const wd = parseInt(parts[2], 10);
      return `Last ${_DAYS[wd].slice(0, 3)}`;
    }
    return '';
  }

  function _setReminder(datetimeLocalStr) {
    dueInput.value = datetimeLocalStr;
    if (remindBtn) {
      remindBtn.classList.add('has-date');
      // Jingle the bell. CSS handles the animation; remove + reflow + re-add
      // so it replays every time the user sets/changes a reminder.
      const _bell = remindBtn.querySelector('svg');
      if (_bell) {
        _bell.classList.remove('jingling');
        void _bell.offsetWidth;
        _bell.classList.add('jingling');
        setTimeout(() => _bell.classList.remove('jingling'), 700);
      }
    }
    _renderReminderTag();
    _ensureNotificationPermission();
  }

  function _pickCustomDate() {
    // Replace the dropdown menu with a small inline picker
    document.querySelectorAll('.note-reminder-menu').forEach(m => m.remove());
    const menu = document.createElement('div');
    menu.className = 'note-reminder-menu';
    const initial = dueInput.value || _toLocalDatetimeStr(_tomorrowDate());
    menu.innerHTML = `
      <div class="note-reminder-menu-title">Pick date and time</div>
      <div class="note-reminder-menu-picker">
        <input type="datetime-local" class="note-reminder-date-input" value="${initial}" />
      </div>
      <div class="note-reminder-menu-divider"></div>
      <button class="note-reminder-menu-item note-reminder-menu-confirm">
        <span>Save</span>
      </button>
    `;
    document.body.appendChild(menu);
    // Position next to the bell button
    const anchor = remindBtn || form.querySelector('.note-form-reminder-tags');
    const rect = anchor.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const mw = menu.offsetWidth || 240;
    const mh = menu.offsetHeight || 200;
    let top = rect.bottom + 4;
    let left = rect.left;
    if (top + mh > vh - 8) top = Math.max(8, rect.top - mh - 4);
    if (left + mw > vw - 8) left = Math.max(8, vw - mw - 8);
    if (left < 8) left = 8;
    menu.style.top = top + 'px';
    menu.style.left = left + 'px';
    const dInput = menu.querySelector('.note-reminder-date-input');
    dInput.focus();
    if (typeof dInput.showPicker === 'function') {
      try { dInput.showPicker(); } catch {}
    }
    menu.querySelector('.note-reminder-menu-confirm').addEventListener('click', () => {
      if (dInput.value) _setReminder(dInput.value);
      menu.remove();
    });
    setTimeout(() => {
      const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); } };
      document.addEventListener('click', close);
    }, 0);
  }

  if (remindBtn) remindBtn.addEventListener('click', (e) => { e.stopPropagation(); _openReminderMenu(remindBtn, !!dueInput.value); });
  _renderReminderTag();

  // Photo upload
  const photoBtn = form.querySelector('.note-form-photo-btn');
  const photoInput = form.querySelector('.note-form-photo-input');
  if (photoBtn && photoInput) {
    photoBtn.addEventListener('click', () => photoInput.click());
    photoInput.addEventListener('change', async () => {
      const file = photoInput.files?.[0];
      if (!file) return;
      const fd = new FormData();
      fd.append('files', file);
      try {
        const res = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd, credentials: 'same-origin' });
        const data = await res.json();
        const fileId = data.files?.[0]?.id;
        if (!fileId) throw new Error('Upload failed');
        currentImageUrl = `${API_BASE}/api/upload/${fileId}`;
        // Only ever keep the latest attached photo — drop any existing wrap
        // before inserting a fresh one. Picking a second photo replaces the
        // first instead of stacking.
        form.querySelector('.note-form-image-wrap')?.remove();
        const wrap = document.createElement('div');
        wrap.className = 'note-form-image-wrap';
        wrap.innerHTML = `<img class="note-form-image" draggable="false" /><button class="note-form-image-rm" title="Remove">&times;</button>`;
        // Insert AFTER the whole header (a flex-row), not after the
        // title input itself — otherwise the image lands as a sibling
        // of the title inside the header and flex puts them side-by-side.
        form.querySelector('.note-form-header').after(wrap);
        wrap.querySelector('.note-form-image-rm').addEventListener('click', () => { wrap.remove(); currentImageUrl = ''; });
        wrap.querySelector('img').src = currentImageUrl;
      } catch (err) { uiModule.showError('Image upload failed'); }
      photoInput.value = '';
    });
  }
  // Existing image remove
  form.querySelector('.note-form-image-rm')?.addEventListener('click', () => {
    form.querySelector('.note-form-image-wrap')?.remove();
    currentImageUrl = '';
  });

  // Title Enter -> focus body (textarea or first checklist item)
  form.querySelector('.note-form-title').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const ta = form.querySelector('.note-form-content');
      if (ta) { ta.focus(); return; }
      const firstItem = form.querySelector('.note-cl-text');
      if (firstItem) firstItem.focus();
    }
  });

  // Hashtag → label: typing "#foo " in title/content appends "foo" to the
  // space-separated tag list. Repeats are deduplicated, so #foo #foo only
  // keeps one. Tags already present in the label field are left alone.
  const labelInput = form.querySelector('.note-form-label');
  const _hashtagRe = /(^|\s)#([A-Za-z0-9][\w-]*)\s$/;
  function _wireHashtag(el) {
    if (!el || !labelInput) return;
    el.addEventListener('input', () => {
      const m = _hashtagRe.exec(el.value);
      if (!m) return;
      const tag = m[2];
      // Dedup against the stripped form — labelInput may already hold `#tag`
      // (after Enter normalised), so includes(tag) on the raw split would
      // miss the duplicate and append a bare `tag` next to `#tag`.
      const existing = labelInput.value.trim().split(/\s+/).filter(Boolean);
      const stripped = existing.map(t => t.replace(/^#+/, ''));
      if (!stripped.includes(tag)) {
        existing.push('#' + tag);
        labelInput.value = existing.join(' ');
        labelInput.classList.add('flash-once');
        setTimeout(() => labelInput.classList.remove('flash-once'), 600);
      }
      const cut = el.value.length - m[0].length + m[1].length;
      el.value = el.value.slice(0, cut);
    });
  }
  _wireHashtag(form.querySelector('.note-form-title'));
  _wireHashtag(form.querySelector('.note-form-content'));
  // Pressing Enter in the tag field commits the current word as its own tag
  // and parks the cursor after a trailing space, so the next word becomes a
  // separate tag rather than overwriting the previous one.
  labelInput?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    e.stopPropagation();
    // Strip any leading #s the user typed, dedupe, then re-prepend exactly
    // one #. So typing "foo" or "#foo" both end up as "#foo " in the input;
    // the save handler keeps stripping #s before storing so DB stays clean.
    const tags = [...new Set(labelInput.value.split(/\s+/).map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
    if (!tags.length) return;
    labelInput.value = tags.map(t => '#' + t).join(' ') + ' ';
    labelInput.setSelectionRange(labelInput.value.length, labelInput.value.length);
    labelInput.classList.add('flash-once');
    setTimeout(() => labelInput.classList.remove('flash-once'), 600);
  });

  // Shift+Enter (or Cmd/Ctrl+Enter) anywhere in the form -> save
  // Escape -> cancel edit
  form.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.shiftKey || e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      form.querySelector('.note-form-save')?.click();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      form.querySelector('.note-form-cancel')?.click();
    }
  });

  // Save. Prevent the button from stealing focus on press: on mobile, the
  // first tap would otherwise just blur the focused textarea/input (closing
  // the keyboard and shifting layout), so the tap never reached the button and
  // you had to tap "Done" twice. mousedown preventDefault keeps focus put while
  // still letting the click fire.
  const _saveBtnEl0 = form.querySelector('.note-form-save');
  _saveBtnEl0.addEventListener('mousedown', (e) => e.preventDefault());
  _saveBtnEl0.addEventListener('click', async () => {
    // Guard against spam-clicks: the drawing save AWAITS a canvas upload before
    // the optimistic re-render removes the form, so without this a slow upload
    // let repeated clicks create duplicate notes.
    const _saveBtn = form.querySelector('.note-form-save');
    if (_saveBtn._saving) return;
    // Mobile: when an existing note is opened and closed without edits, the
    // Update (✓) button morphs into Archive (set up below). Route the click
    // to the hidden archive button so the existing archive flow + undo toast
    // run unchanged.
    if (_saveBtn.classList.contains('archive-mode')) {
      form.querySelector('.note-form-archive-btn')?.click();
      return;
    }
    _saveBtn._saving = true; _saveBtn.disabled = true; _saveBtn.style.opacity = '0.5';
    try {
    const title = form.querySelector('.note-form-title').value.trim();
    // Normalize tag input: split on whitespace, strip leading #s, dedupe,
    // re-join with single spaces. Empty → null.
    const _rawLabel = form.querySelector('.note-form-label')?.value || '';
    const _tags = [...new Set(_rawLabel.split(/\s+/).map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
    if (form.querySelector('.note-form-due').value && !_tags.includes('reminder')) _tags.push('reminder');
    const labelVal = _tags.length ? _tags.join(' ') : null;
    const payload = {
      title,
      note_type: currentType,
      color: currentColor,
      label: labelVal,
      due_date: form.querySelector('.note-form-due').value || null,
      repeat: form.querySelector('.note-form-repeat')?.value || 'none',
      image_url: currentImageUrl || null,
    };
    if (currentType === 'note') {
      payload.content = form.querySelector('.note-form-content')?.value || '';
    } else if (currentType === 'draw') {
      // Upload the canvas PNG before saving so image_url points to a
      // persistent file. We block the save until upload completes — drawings
      // can't be re-rendered later without the URL.
      const canvas = form.querySelector('.note-form-canvas');
      const url = await _uploadCanvasAsPng(canvas);
      if (!url) { uiModule.showError('Failed to save drawing'); return; }
      payload.image_url = url;
    } else if (currentType === 'goal') {
      // Legacy: existing goal-type notes still edit through this branch.
      // No AI involvement — save as a normal note with description + items.
      payload.content = form.querySelector('.note-form-goal-desc')?.value || '';
      payload.items = _collectItems(form);
    } else {
      payload.items = _collectItems(form);
    }
    if (isEdit) payload.id = note.id;
    // Reset fired reminder if due_date changed (so re-arm works), and also
    // clear the entry-glow seen flag so the new firing glows again on the
    // next time the user opens the panel.
    if (isEdit && note.due_date !== payload.due_date) {
      const fired = _loadFiredReminders();
      fired.delete(note.id);
      _saveFiredReminders(fired);
      const glowed = _loadGlowedReminders();
      glowed.delete(note.id);
      _saveGlowedReminders(glowed);
      _setReminderCardGlow(note.id, false);
    }
    // Edited notes move to the top of their section (under pinned). Compute
    // sort_order = (min unpinned sort_order) - 1 so the saved note sorts above
    // siblings; the pin block keeps its own ordering above this.
    if (!payload.pinned) {
      // Both edits AND newly-created notes anchor above the rest of the
      // unpinned section. Without this, freshly created notes sit at the
      // bottom because manually-reordered siblings already carry negative
      // sort_order values.
      const minUnpinned = _notes
        .filter(n => !n.pinned && (!isEdit || n.id !== note.id))
        .reduce((m, n) => Math.min(m, n.sort_order || 0), 0);
      payload.sort_order = minUnpinned - 1;
    }
    // Optimistic update — update local state first, render, then save in background
    _editingId = null;
    _clearDraft(isEdit ? note.id : '__new__');  // saved → discard the draft
    if (isEdit) {
      const idx = _notes.findIndex(n => n.id === note.id);
      if (idx >= 0) _notes[idx] = { ..._notes[idx], ...payload };
    } else {
      _notes.unshift({ ...payload, id: 'tmp_' + _uid(), created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
    }
    _renderNotes();
    // Background save
    _saveNote(payload).then(saved => {
      if (!isEdit && saved && saved.id) {
        // Replace temp ID with real one from server. AND re-render — the
        // existing card's `data-note-id="tmp_xxx"` is stale after Object.assign
        // bumps the in-memory id, so all subsequent clicks (edit, done, copy,
        // archive, delete) silently fail to find the note in `_notes`.
        const tmp = _notes.find(n => n.id.startsWith('tmp_'));
        if (tmp) Object.assign(tmp, saved);
        _renderNotes();
      }
    }).catch(err => {
      uiModule.showError('Save failed: ' + err.message);
      _fetchNotes().then(() => _renderNotes());
    });
    } finally {
      // Re-enable on early returns / errors. On success the form is removed by
      // the optimistic re-render, so re-enabling the detached button is a no-op.
      _saveBtn._saving = false; _saveBtn.disabled = false; _saveBtn.style.opacity = '';
    }
  });

  // Mobile-only: when editing an existing note, the Update (✓) button starts in
  // archive-mode (visually + behaviorally) and flips to Update on the first
  // edit. Lets the user tap a note to skim, then tap ✓ to archive without ever
  // touching a separate Archive button.
  if (isEdit && window.innerWidth <= 768) {
    const _saveLabelEl = _saveBtnEl0.querySelector('.nft-label');
    const _enterArchive = () => {
      _saveBtnEl0.classList.add('archive-mode');
      if (_saveLabelEl) _saveLabelEl.textContent = 'Archive';
      _saveBtnEl0.title = 'Archive';
    };
    const _enterUpdate = () => {
      if (!_saveBtnEl0.classList.contains('archive-mode')) return;
      _saveBtnEl0.classList.remove('archive-mode');
      if (_saveLabelEl) _saveLabelEl.textContent = 'Update';
      _saveBtnEl0.title = 'Update';
    };
    _enterArchive();
    form.addEventListener('input', _enterUpdate, true);
    form.addEventListener('change', _enterUpdate, true);
  }

  // Cancel
  form.querySelector('.note-form-cancel').addEventListener('click', () => { _clearDraft(isEdit ? note.id : '__new__'); _editingId = null; _renderNotes(); });

  // Archive / Delete — edit-mode-only buttons, mirror the (now-hidden) card actions.
  form.querySelector('.note-form-archive-btn')?.addEventListener('click', () => {
    if (!isEdit) return;
    const id = note.id;
    const idx = _notes.findIndex(n => n.id === id);
    if (idx < 0) return;
    const removed = _notes.splice(idx, 1)[0];
    _editingId = null;
    _renderNotes();
    const undo = () => _undoArchive(removed, idx);
    _pushUndo({ label: 'archive', run: undo });
    const _undoIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;"><polyline points="9 14 4 9 9 4"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>';
    _patchNote(id, { archived: true }).then(() => {
      uiModule.showToast('Archived', { duration: 6000, action: 'Undo', actionIcon: _undoIcon, onAction: undo, actionHint: 'Ctrl+Z' });
    }).catch(() => {
      _notes.splice(idx, 0, removed);
      _renderNotes();
      uiModule.showError('Failed to archive');
    });
  });
  form.querySelector('.note-form-delete-btn')?.addEventListener('click', async () => {
    if (!isEdit) return;
    const id = note.id;
    if (uiModule.styledConfirm) {
      const ok = await uiModule.styledConfirm('Delete this note?', { confirmText: 'Delete', danger: true });
      if (!ok) return;
    } else if (!confirm('Delete this note?')) {
      return;
    }
    const idx = _notes.findIndex(n => n.id === id);
    if (idx >= 0) _notes.splice(idx, 1);
    _editingId = null;
    _renderNotes();
    _deleteNoteApi(id).then(() => uiModule.showToast('Deleted')).catch(() => {
      uiModule.showError('Failed to delete');
      _fetchNotes().then(() => _renderNotes());
    });
  });

  // Autosave a draft to localStorage on every change so unsaved edits
  // survive connection loss / reload / accidental close.
  _wireDraftAutosave(form, isEdit ? note.id : '__new__');

  return form;
}

// Legacy goal-typed notes still render through this branch so existing
// data isn't lost. The "Goal" type is no longer exposed in the form picker
// or quick-add — these notes show with a description + manual checklist
// editor, just like a regular todo with a body.
function _buildGoalHtml(note, items) {
  const desc = (note?.content || '').toString();
  return `
    <div class="note-form-goal">
      <textarea class="note-form-goal-desc" placeholder="Description (optional)" rows="3">${_esc(desc)}</textarea>
      ${_buildChecklistHtml(items)}
    </div>
  `;
}

function _wireGoalForm(form, container) {
  if (!container) return;
  // _wireHashtag is a closure inside _buildForm — out of scope here. Inline
  // the same behavior (type "#foo " in the description → tag added to the
  // form's label input) so editing a goal note doesn't ReferenceError.
  const desc = container.querySelector('.note-form-goal-desc');
  const labelInput = form?.querySelector('.note-form-label');
  if (desc && labelInput) {
    const tagRe = /(^|\s)#([A-Za-z0-9][\w-]*)\s$/;
    desc.addEventListener('input', () => {
      const m = tagRe.exec(desc.value);
      if (!m) return;
      const tag = m[2];
      // Same dedup-after-stripping fix as the plain note hashtag handler.
      const existing = labelInput.value.trim().split(/\s+/).filter(Boolean);
      const stripped = existing.map(t => t.replace(/^#+/, ''));
      if (!stripped.includes(tag)) {
        existing.push('#' + tag);
        labelInput.value = existing.join(' ');
        labelInput.classList.add('flash-once');
        setTimeout(() => labelInput.classList.remove('flash-once'), 600);
      }
      const cut = desc.value.length - m[0].length + m[1].length;
      desc.value = desc.value.slice(0, cut);
    });
  }
  // Always wire the checklist. The previous gate on a `note-form-goal-fresh`
  // class was dead code — nothing ever set that class, so the editor never
  // hooked up add/drag/Tab.
  _wireChecklist(container);
}

function _buildChecklistHtml(items) {
  let html = '<div class="note-checklist-inputs">';
  for (const item of items) {
    const indent = Math.min(item.indent || 0, 3);
    html += `<div class="note-cl-row${item.done ? ' done' : ''}" draggable="true" data-item-id="${item.id || _uid()}" data-indent="${indent}" style="padding-left:${indent * 16}px">
      <span class="note-cl-grip" title="Drag to reorder">⋮⋮</span>
      <span class="note-cl-dot"></span>
      <input type="text" class="note-cl-text" value="${_esc(item.text)}" placeholder="Item..." />
      <button type="button" class="note-cl-rm">&times;</button>
    </div>`;
  }
  // `type="button"` matters on mobile — without it some browsers treat
  // bare <button> as form-submit and the click handler never fires inside
  // certain containers. Also bumped tap target so fingers don't miss.
  html += `<button type="button" class="note-cl-add">+ Add</button></div>`;
  return html;
}

function _wireRow(row, container) {
  row.querySelector('.note-cl-rm')?.addEventListener('click', () => row.remove());
  row.querySelector('.note-cl-dot')?.addEventListener('click', () => {
    const wasDone = row.classList.contains('done');
    row.classList.toggle('done');
    const becameDone = !wasDone;  // we just flipped it
    const dot = row.querySelector('.note-cl-dot');
    const dRect = (dot || row).getBoundingClientRect();
    // Small confetti burst on each fresh check so the user gets a
    // "well done" beat per item, not just the grand-finale on all-done.
    if (becameDone) {
      spawnConfetti(dRect.left + dRect.width / 2, dRect.top + dRect.height / 2, 16);
    }
    // Bigger burst when the whole list is now done.
    const rows = [...container.querySelectorAll('.note-cl-row')];
    const hasText = rows.some(r => (r.querySelector('.note-cl-text')?.value || '').trim().length > 0);
    if (hasText && rows.every(r => r.classList.contains('done') || !(r.querySelector('.note-cl-text')?.value || '').trim())) {
      spawnConfetti(dRect.left + dRect.width / 2, dRect.top + dRect.height / 2, 60);
    }
  });
  const txt = row.querySelector('.note-cl-text');
  txt?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); container.querySelector('.note-cl-add')?.click(); }
    else if (e.key === 'Tab') {
      e.preventDefault();
      const cur = parseInt(row.dataset.indent || '0');
      const next = e.shiftKey ? Math.max(0, cur - 1) : Math.min(3, cur + 1);
      row.dataset.indent = String(next);
      row.style.paddingLeft = (next * 16) + 'px';
    } else if (e.key === 'Backspace' && txt.value === '') {
      e.preventDefault();
      const prev = row.previousElementSibling;
      row.remove();
      if (prev && prev.classList.contains('note-cl-row')) prev.querySelector('.note-cl-text')?.focus();
    }
  });
  // Drag handlers
  row.addEventListener('dragstart', (e) => {
    row.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', row.dataset.itemId); } catch {}
  });
  row.addEventListener('dragend', () => {
    row.classList.remove('dragging');
    container.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
  });
}

function _wireChecklist(container) {
  if (!container) return;
  // Delegate the + Add click off the container so re-renders + mobile
  // touch quirks don't leave the button dead. The previous direct
  // `addEventListener` on the button silently broke on mobile when
  // _wireChecklist ran more than once (or before the button was in DOM).
  if (!container._addDelegated) {
    container._addDelegated = true;
    container.addEventListener('click', (ev) => {
      const addBtn = ev.target.closest('.note-cl-add');
      if (!addBtn || !container.contains(addBtn)) return;
      ev.preventDefault();
      ev.stopPropagation();
      const inputs = container.querySelector('.note-checklist-inputs');
      if (!inputs) return;
      const row = document.createElement('div');
      row.className = 'note-cl-row';
      row.draggable = true;
      row.dataset.itemId = _uid();
      row.dataset.indent = '0';
      row.innerHTML = `<span class="note-cl-grip" title="Drag">⋮⋮</span><span class="note-cl-dot"></span><input type="text" class="note-cl-text" placeholder="Item..." /><button type="button" class="note-cl-rm">&times;</button>`;
      inputs.insertBefore(row, addBtn);
      row.querySelector('.note-cl-text').focus();
      _wireRow(row, container);
    });
  }
  container.querySelectorAll('.note-cl-row').forEach(row => _wireRow(row, container));

  // Drag-over handler on the inputs container
  const inputs = container.querySelector('.note-checklist-inputs');
  if (inputs) {
    inputs.addEventListener('dragover', (e) => {
      e.preventDefault();
      const dragging = inputs.querySelector('.note-cl-row.dragging');
      if (!dragging) return;
      inputs.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
      const rows = [...inputs.querySelectorAll('.note-cl-row:not(.dragging)')];
      const after = rows.find(r => {
        const box = r.getBoundingClientRect();
        return e.clientY < box.top + box.height / 2;
      });
      if (after) {
        after.classList.add('drop-before');
        inputs.insertBefore(dragging, after);
      } else if (rows.length) {
        rows[rows.length - 1].classList.add('drop-after');
        inputs.insertBefore(dragging, container.querySelector('.note-cl-add'));
      }
    });
    inputs.addEventListener('dragleave', (e) => {
      if (!inputs.contains(e.relatedTarget)) {
        inputs.querySelectorAll('.drop-before, .drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
      }
    });
  }
}

function _collectItems(form) {
  const items = [];
  form.querySelectorAll('.note-cl-row').forEach(row => {
    const text = row.querySelector('.note-cl-text')?.value?.trim();
    if (text) items.push({
      id: row.dataset.itemId || _uid(),
      text,
      done: row.classList.contains('done'),
      indent: parseInt(row.dataset.indent || '0'),
    });
  });
  return items;
}

// ---- Draw mode (canvas) ----

function _buildDrawHtml() {
  return `
    <div class="note-form-draw-wrap">
      <canvas class="note-form-canvas" width="600" height="320"></canvas>
      <div class="note-form-draw-toolbar">
        <input type="color" class="note-form-draw-color" title="Stroke color" value="#222222" />
        <label class="note-form-draw-tool note-form-draw-size-wrap" title="Stroke size">
          <input type="range" class="note-form-draw-size" min="1" max="24" value="3" />
        </label>
        <div class="note-form-draw-be" role="group">
          <button type="button" class="note-form-draw-be-btn note-form-draw-brush active" data-mode="pen" title="Brush">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04 0-1.67-1.34-3.02-3-3.02z"/></svg>
          </button>
          <button type="button" class="note-form-draw-be-btn note-form-draw-eraser" data-mode="eraser" title="Eraser">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/></svg>
          </button>
        </div>
        <button type="button" class="note-form-draw-text" title="Add text — click to cycle size">T<span class="note-form-draw-text-badge"></span></button>
        <button type="button" class="note-form-draw-line" title="Line — click to cycle size">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="20" x2="20" y2="4"/></svg>
          <span class="note-form-draw-shape-badge"></span>
        </button>
        <button type="button" class="note-form-draw-circle" title="Circle — click to cycle size">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/></svg>
          <span class="note-form-draw-shape-badge"></span>
        </button>
        <button type="button" class="note-form-draw-undo" title="Undo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>
        </button>
      </div>
    </div>
  `;
}

// Attach drawing handlers to the canvas inside `container`. Optionally loads
// `initialImageUrl` as a background, so editing an existing drawing keeps it.
function _wireCanvas(container, initialImageUrl) {
  const canvas = container.querySelector('.note-form-canvas');
  if (!canvas) return;
  // Bump the backing-store resolution for retina displays so strokes stay
  // crisp. Set only style.width — leaving style.height to auto so the canvas
  // scales uniformly via its intrinsic aspect ratio. If both CSS dimensions
  // are pinned, max-width:100% shrinks the width only, leaving rasterized
  // glyphs visibly stretched relative to their on-screen input.
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.width;
  const cssH = canvas.height;
  // Fill the container up to the logical width (don't pin a hard 600px,
  // which on a narrow phone forces the card wider than the viewport and
  // pushes the drawing outside the note). _pos() scales pointer coords by
  // the actual displayed width, so accuracy is preserved at any size.
  canvas.style.width = '100%';
  canvas.style.maxWidth = cssW + 'px';
  canvas.style.height = 'auto';
  canvas.style.aspectRatio = cssW + ' / ' + cssH;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, cssW, cssH);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  // Load prior drawing as starting point so consecutive edits compose.
  const safeInitialImageUrl = _safeImgSrc(initialImageUrl);
  if (safeInitialImageUrl) {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => { try { ctx.drawImage(img, 0, 0, cssW, cssH); } catch {} };
    img.src = safeInitialImageUrl;
    // Float an X over the canvas so the user can blank it out and go back to
    // a clean draw surface. Removes itself once clicked.
    const wrap = container.querySelector('.note-form-draw-wrap');
    if (wrap && !wrap.querySelector('.note-form-draw-bg-rm')) {
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'note-form-draw-bg-rm';
      rm.title = 'Clear photo (regular draw)';
      rm.innerHTML = '&times;';
      rm.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, cssW, cssH);
        rm.remove();
      });
      wrap.appendChild(rm);
    }
  }

  const colorInput = container.querySelector('.note-form-draw-color');
  // Swap the native browser color dialog for the in-house HSV picker
  // (same one used by Themes + the gallery editor). Existing `input` event
  // listeners + .value reads keep working — see colorPicker.js.
  if (colorInput) attachColorPicker(colorInput);
  const sizeInput = container.querySelector('.note-form-draw-size');
  const beSeg = container.querySelector('.note-form-draw-be');
  const brushBtn = container.querySelector('.note-form-draw-brush');
  const eraserBtn = container.querySelector('.note-form-draw-eraser');
  const textBtn = container.querySelector('.note-form-draw-text');
  const lineBtn = container.querySelector('.note-form-draw-line');
  const circleBtn = container.querySelector('.note-form-draw-circle');
  const undoBtn = container.querySelector('.note-form-draw-undo');
  // Single source of truth for what clicks/drags do. Other booleans are
  // derived from this so we never end up with conflicting "eraser AND text"
  // states (the bug that made T appear broken after using the eraser).
  // Modes: 'pen' | 'eraser' | 'text-s' | 'text-m' | 'text-l' | 'line' | 'circle'
  let mode = 'pen';
  let drawing = false;
  let last = null;
  // The text tool has three preset sizes (CSS px font-size).
  const TEXT_SIZES = { 's': 16, 'm': 26, 'l': 40 };
  // Line / circle stroke widths in logical pixels — three crisp options.
  const SHAPE_WIDTHS = { 's': 2, 'm': 5, 'l': 10 };
  // Snapshot taken at the start of a shape drag so the preview can repaint
  // cleanly on each move without accumulating intermediate strokes.
  let _shapeSnapshot = null;

  // Per-canvas undo stack. We snapshot the bitmap (as ImageData) BEFORE each
  // operation — stroke, text commit, or future operations — and pop+restore
  // on Undo. Cap to 30 to keep memory bounded.
  const _undoStack = [];
  const UNDO_LIMIT = 30;
  const _snapshot = () => {
    try {
      const w = canvas.width, h = canvas.height;
      _undoStack.push(ctx.getImageData(0, 0, w, h));
      if (_undoStack.length > UNDO_LIMIT) _undoStack.shift();
    } catch {}
  };
  const _undo = () => {
    const prev = _undoStack.pop();
    if (!prev) return;
    // Restore against the raw backing store: temporarily reset the active
    // ctx scale, paint the snapshot 1:1, then reapply our standard transform.
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.putImageData(prev, 0, 0);
    ctx.restore();
  };

  const _pos = (e) => {
    // CSS can shrink the canvas (max-width:100%) without changing its logical
    // size, so compute the displayed-to-logical scale per axis. Pointer coords
    // are in CSS pixels; the dpr-scaled ctx expects logical (cssW × cssH).
    const r = canvas.getBoundingClientRect();
    const sx = cssW / r.width;
    const sy = cssH / r.height;
    const t = e.touches ? e.touches[0] : e;
    return { x: (t.clientX - r.left) * sx, y: (t.clientY - r.top) * sy };
  };
  const _begin = (e) => {
    if (mode.startsWith('text-')) {
      // Stop the event so the browser doesn't synthesize a follow-up click
      // that would blur the input we're about to create.
      e.preventDefault?.();
      e.stopPropagation?.();
      _openTextInput(e);
      return;
    }
    _snapshot();
    last = _pos(e);
    drawing = true;
    if (mode.startsWith('line-') || mode.startsWith('circle-')) {
      // Capture the backing pixels so the preview can replay each move from
      // the same starting state (otherwise live shapes accumulate).
      try { _shapeSnapshot = ctx.getImageData(0, 0, canvas.width, canvas.height); } catch {}
      return;
    }
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
  };

  // Drop an HTML input at the click position so the user can type, then
  // rasterize the text onto the canvas at blur/Enter. Mirrors how PDF form
  // annotations work in our doc editor.
  let _activeTextInput = null;
  const _openTextInput = (e) => {
    // Commit any prior pending input before starting a new one — otherwise
    // the first click leaves an orphaned input the user thinks "didn't work".
    if (_activeTextInput) { try { _activeTextInput.blur(); } catch {} }
    const r = canvas.getBoundingClientRect();
    const t = e.touches ? e.touches[0] : e;
    // Position is anchored to the wrap, not the canvas; since the canvas is
    // the first child of the wrap and the wrap has no padding, they share an
    // origin, so we offset from the canvas rect directly.
    const px = t.clientX - r.left;
    const py = t.clientY - r.top;
    const logical = _pos(e);
    // Size is decided by which T variant is active (S/M/L), not the stroke
    // slider — those are independent dials.
    const sizeKey = mode.startsWith('text-') ? mode.slice(-1) : 'm';
    const sizeCss = TEXT_SIZES[sizeKey] || TEXT_SIZES.m;
    const wrap = container.querySelector('.note-form-draw-wrap');
    if (!wrap) return;
    if (getComputedStyle(wrap).position === 'static') wrap.style.position = 'relative';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'note-form-draw-textinput';
    input.placeholder = 'type then Enter';
    const color = colorInput?.value || '#222';
    const maxW = Math.max(120, Math.floor(r.width - px - 4));
    input.style.cssText = [
      'position:absolute',
      `left:${px}px`,
      `top:${Math.max(0, py - sizeCss * 0.7)}px`,
      `font:${sizeCss}px Arial, sans-serif`,
      `color:${color}`,
      'background:#ffffff',
      'border:2px solid var(--accent)',
      'border-radius:4px',
      'outline:none',
      'padding:2px 6px',
      'min-width:120px',
      `max-width:${maxW}px`,
      'z-index:1000',
      'box-shadow:0 2px 8px rgba(0,0,0,0.25)',
      'pointer-events:auto',
    ].join(';');
    wrap.appendChild(input);
    _activeTextInput = input;
    // Focus synchronously so the call still counts as inside the user gesture
    // on iOS / Android, then re-focus on the next frame in case a competing
    // synthetic event (touch → click) moved focus away.
    input.focus();
    requestAnimationFrame(() => { if (document.activeElement !== input) input.focus(); });
    let committed = false;
    const commit = () => {
      if (committed) return;
      committed = true;
      const text = input.value;
      if (_activeTextInput === input) _activeTextInput = null;
      input.remove();
      if (!text) return;
      // Snapshot BEFORE rasterizing so Undo removes the text in one step.
      _snapshot();
      ctx.save();
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = color;
      // Canvas now scales uniformly (style.height: auto), so a single ratio
      // suffices for picking the logical font size that matches what the
      // user just saw in the HTML input.
      const sx = cssW / r.width;
      const logicalSize = sizeCss * sx;
      ctx.font = `${logicalSize}px sans-serif`;
      ctx.textBaseline = 'top';
      ctx.fillText(text, logical.x, logical.y - logicalSize * 0.7);
      ctx.restore();
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
      else if (ev.key === 'Escape') { input.value = ''; input.blur(); }
    });
  };
  const _move = (e) => {
    if (!drawing) return;
    e.preventDefault?.();
    const p = _pos(e);
    if (mode.startsWith('line-') || mode.startsWith('circle-')) {
      // Restore the pre-shape bitmap, then redraw the preview shape from
      // anchor → current pointer.
      if (_shapeSnapshot) {
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.putImageData(_shapeSnapshot, 0, 0);
        ctx.restore();
      }
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = colorInput?.value || '#222';
      const sizeKey = mode.slice(-1);
      ctx.lineWidth = SHAPE_WIDTHS[sizeKey] || SHAPE_WIDTHS.m;
      ctx.beginPath();
      if (mode.startsWith('line-')) {
        ctx.moveTo(last.x, last.y);
        ctx.lineTo(p.x, p.y);
      } else {
        const dx = p.x - last.x;
        const dy = p.y - last.y;
        const radius = Math.hypot(dx, dy);
        ctx.arc(last.x, last.y, radius, 0, Math.PI * 2);
      }
      ctx.stroke();
      return;
    }
    const erasing = mode === 'eraser';
    ctx.globalCompositeOperation = erasing ? 'destination-out' : 'source-over';
    ctx.strokeStyle = erasing ? 'rgba(0,0,0,1)' : (colorInput?.value || '#222');
    ctx.lineWidth = Number(sizeInput?.value || 3) * (erasing ? 2.5 : 1);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last = p;
  };
  const _end = () => { drawing = false; last = null; _shapeSnapshot = null; };

  canvas.addEventListener('mousedown', _begin);
  canvas.addEventListener('mousemove', _move);
  window.addEventListener('mouseup', _end);
  // Non-passive so text mode can preventDefault — otherwise the synthetic
  // mousedown/click that follows a touch can blur the freshly-created text
  // input on iOS Safari, making T look like a no-op.
  canvas.addEventListener('touchstart', (e) => { if (mode.startsWith('text-')) e.preventDefault(); _begin(e); }, { passive: false });
  canvas.addEventListener('touchmove', _move, { passive: false });
  canvas.addEventListener('touchend', _end);
  canvas.addEventListener('touchcancel', _end);

  // Single mode setter — keeps the toolbar, swatch, and cursor in sync, and
  // makes sure exiting the eraser restores the user's chosen color whether
  // they exit by clicking another tool or by toggling eraser off.
  let _preEraseColor = null;
  const _setMode = (next) => {
    const wasEraser = mode === 'eraser';
    mode = next;
    const isEraser = next === 'eraser';
    const isPen = next === 'pen';
    const isText = next.startsWith('text-');
    // Swatch: white while erasing, restored as soon as we leave that mode.
    if (isEraser && !wasEraser && colorInput) {
      _preEraseColor = colorInput.value;
      colorInput.value = '#ffffff';
    } else if (!isEraser && wasEraser && colorInput && _preEraseColor) {
      colorInput.value = _preEraseColor;
      _preEraseColor = null;
    }
    // Brush/Eraser segmented pill — slides to the active side. When a non-
    // pen/eraser tool (T / line / circle) is active, neither half is
    // highlighted, but the pill still indicates which side the user was on
    // last so they can return with one click.
    const isLine = next.startsWith('line-');
    const isCircle = next.startsWith('circle-');
    beSeg?.classList.toggle('is-eraser', isEraser);
    brushBtn?.classList.toggle('active', isPen);
    eraserBtn?.classList.toggle('active', isEraser);
    textBtn?.classList.toggle('active', isText);
    lineBtn?.classList.toggle('active', isLine);
    circleBtn?.classList.toggle('active', isCircle);
    // Per-button size badges (S/M/L), driven off the mode suffix.
    const tBadge = textBtn?.querySelector('.note-form-draw-text-badge');
    if (tBadge) tBadge.textContent = isText ? next.slice(-1).toUpperCase() : '';
    const lBadge = lineBtn?.querySelector('.note-form-draw-shape-badge');
    if (lBadge) lBadge.textContent = isLine ? next.slice(-1).toUpperCase() : '';
    const cBadge = circleBtn?.querySelector('.note-form-draw-shape-badge');
    if (cBadge) cBadge.textContent = isCircle ? next.slice(-1).toUpperCase() : '';
    // Reflect the chosen size in the icon itself — thicker line/circle stroke
    // for M+L, larger T glyph for M+L. CSS rules read `.size-s/.size-m/.size-l`.
    const _sz = next.slice(-1);
    [textBtn, lineBtn, circleBtn].forEach(b => b?.classList.remove('size-s', 'size-m', 'size-l'));
    if (isText && /[sml]/.test(_sz)) textBtn?.classList.add('size-' + _sz);
    if (isLine && /[sml]/.test(_sz)) lineBtn?.classList.add('size-' + _sz);
    if (isCircle && /[sml]/.test(_sz)) circleBtn?.classList.add('size-' + _sz);
    canvas.style.cursor = isText ? 'text' : 'crosshair';
  };
  brushBtn?.addEventListener('click', () => _setMode('pen'));
  eraserBtn?.addEventListener('click', () => _setMode('eraser'));
  // T / Line / Circle: each cycles its own three sizes, then back to pen.
  const _cycle = (prefix) => {
    const seq = ['s', 'm', 'l'];
    if (!mode.startsWith(prefix)) return prefix + 's';
    const cur = mode.slice(-1);
    const next = seq[seq.indexOf(cur) + 1];
    return next ? prefix + next : 'pen';
  };
  textBtn?.addEventListener('click', () => _setMode(_cycle('text-')));
  lineBtn?.addEventListener('click', () => _setMode(_cycle('line-')));
  circleBtn?.addEventListener('click', () => _setMode(_cycle('circle-')));
  undoBtn?.addEventListener('click', () => _undo());

  // Stash so the save handler can read it later without re-resolving DOM.
  canvas._cssW = cssW;
  canvas._cssH = cssH;
  return canvas;
}

// Export the canvas as a PNG dataURL, upload it via the existing /api/upload
// endpoint, and resolve to a persistent URL. Returns null on failure.
async function _uploadCanvasAsPng(canvas) {
  if (!canvas) return null;
  const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
  if (!blob) return null;
  const fd = new FormData();
  fd.append('files', blob, 'drawing.png');
  try {
    const res = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd, credentials: 'same-origin' });
    const data = await res.json();
    const id = data.files?.[0]?.id;
    return id ? `${API_BASE}/api/upload/${id}` : null;
  } catch { return null; }
}

// ---- Create / Edit / Delete ----

function _createNote(type = 'todo') {
  const body = document.querySelector('#notes-pane .notes-pane-body');
  if (!body || _editingId === '__new__') return;
  _editingId = '__new__';
  // Restore an unsaved new-note draft if one survived a prior close/loss.
  const { note: _n, restored } = _applyDraftToNote({ note_type: type }, '__new__');
  const form = _buildForm(_n);
  form.classList.add('note-form-new');
  body.prepend(form);
  form.querySelector('.note-form-title').focus();
  if (restored) uiModule.showToast('Restored unsaved note');
}

// Build the plain-text/markdown form of a note for clipboard copy.
function _serializeNoteForCopy(note) {
  const lines = [];
  if (note.title) lines.push(note.title);
  if (note.content) lines.push(note.content);
  if (Array.isArray(note.items) && note.items.length) {
    if (lines.length) lines.push('');
    for (const it of note.items) {
      if (!it || !(it.text || '').trim()) continue;
      lines.push(`- [${it.done ? 'x' : ' '}] ${(it.text || '').trim()}`);
    }
  }
  return lines.join('\n').trim();
}

// Copy a note to the clipboard, briefly swap btnEl's icon to a checkmark, and
// toast. Shared by the corner-copy button click and the Ctrl/Cmd+C shortcut.
// ── ⋯ corner menu (Copy + Agent) ───────────────────────────────────
function _openNoteCornerMenu(btn) {
  document.querySelectorAll('.note-corner-menu-dropdown').forEach(d => d.remove());
  const id = btn.dataset.noteId;
  const note = _notes.find(n => n.id === id);
  if (!note) return;
  const menu = document.createElement('div');
  menu.className = 'note-corner-menu-dropdown';
  menu.innerHTML = `
    <button type="button" class="ncm-item" data-act="copy">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      <span>Copy</span>
    </button>
    <button type="button" class="ncm-item" data-act="agent">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>
      <span>${note.agent_session_id ? 'Re-run agent' : 'Agent: solve this'}</span>
    </button>`;
  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  // Right-align to the ⋯ button, clamped to the viewport.
  const mw = 168;
  let left = Math.min(r.right - mw, window.innerWidth - mw - 8);
  left = Math.max(8, left);
  // Drop down by default; flip up if there isn't room below (the button
  // sits at the card's bottom edge now).
  const mh = menu.offsetHeight || 96;
  const below = window.innerHeight - r.bottom;
  const top = (below < mh + 8 && r.top > mh + 8) ? (r.top - mh - 4) : (r.bottom + 4);
  menu.style.cssText += `position:fixed;z-index:11000;top:${Math.round(top)}px;left:${Math.round(left)}px;`;
  const close = (ev) => {
    if (ev && menu.contains(ev.target)) return;
    menu.remove();
    document.removeEventListener('click', close, true);
  };
  setTimeout(() => document.addEventListener('click', close, true), 0);
  menu.querySelector('[data-act="copy"]').addEventListener('click', () => { menu.remove(); _copyNote(id, btn); });
  menu.querySelector('[data-act="agent"]').addEventListener('click', () => { menu.remove(); _agentSolveNote(id); });
}

// Build the prompt the agent gets from a note: title + body, plus any
// not-yet-done checklist items.
function _noteToAgentPrompt(note) {
  const parts = [];
  if ((note.title || '').trim()) parts.push(note.title.trim());
  if ((note.content || '').trim()) parts.push(note.content.trim());
  if (Array.isArray(note.items)) {
    note.items.filter(it => !it.done && (it.text || '').trim())
      .forEach(it => parts.push('- ' + it.text.trim()));
  }
  const body = parts.join('\n');
  return body ? `Help me get this done:\n\n${body}` : '';
}

// Agent-solve: create a chat session server-side, kick off an agent run
// on it IN THE BACKGROUND (the user stays in notes), and link the session
// to the note via a clickable tag. Tapping the tag later opens the chat.
async function _agentSolveNote(id) {
  const note = _notes.find(n => n.id === id);
  if (!note) return;
  const prompt = _noteToAgentPrompt(note);
  if (!prompt) { uiModule.showToast('Nothing to solve — note is empty'); return; }
  try {
    const dc = await (await fetch(`${API_BASE}/api/default-chat`, { credentials: 'same-origin' })).json();
    if (!dc.endpoint_url || !dc.model) { uiModule.showError('No default chat model configured'); return; }

    // 1. Create the session server-side (no UI switch). skip_validation
    //    avoids re-probing — the default-chat endpoint is already known good.
    const label = (note.title || (Array.isArray(note.items) && note.items[0]?.text) || 'todo').slice(0, 40);
    const csFd = new FormData();
    csFd.append('name', 'Agent: ' + label);
    csFd.append('endpoint_url', dc.endpoint_url);
    csFd.append('model', dc.model);
    if (dc.endpoint_id) csFd.append('endpoint_id', dc.endpoint_id);
    csFd.append('skip_validation', 'true');
    const csRes = await fetch(`${API_BASE}/api/session`, { method: 'POST', credentials: 'same-origin', body: csFd });
    if (!csRes.ok) { uiModule.showError('Could not create agent session'); return; }
    const sess = await csRes.json();
    const sid = sess.id;

    // 2. Link the session to the note right away so the tag appears.
    const n = _notes.find(x => x.id === id);
    if (n) n.agent_session_id = sid;
    _renderNotes();
    _patchNote(id, { agent_session_id: sid }).catch(() => {});

    // 3. Kick off the agent run in the background. POST to chat_stream in
    //    agent mode and drain the SSE so the server runs the loop to
    //    completion + saves — without rendering anything in the chat UI.
    const fd = new FormData();
    fd.append('message', prompt);
    fd.append('session', sid);
    fd.append('mode', 'agent');
    fetch(`${API_BASE}/api/chat_stream`, { method: 'POST', credentials: 'same-origin', body: fd })
      .then(async (res) => {
        if (!res.ok || !res.body) return;
        const reader = res.body.getReader();
        // Drain to completion (server finishes + persists the run).
        while (true) { const { done } = await reader.read(); if (done) break; }
        if (window.sessionModule && window.sessionModule.markStreamComplete) {
          try { window.sessionModule.markStreamComplete(sid); } catch {}
        }
      })
      .catch(() => {});

    uiModule.showToast('Agent working in background — tap the Agent tag when ready');
  } catch (e) {
    uiModule.showError('Agent failed: ' + (e.message || e));
  }
}

async function _copyNote(noteId, btnEl) {
  const note = _notes.find(n => n.id === noteId);
  if (!note) return false;
  const text = _serializeNoteForCopy(note);
  if (!text) return false;
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
  }
  if (ok) {
    if (btnEl && !btnEl._copyFlashing) {
      const original = btnEl.innerHTML;
      btnEl._copyFlashing = true;
      btnEl.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
      btnEl.classList.add('copied');
      setTimeout(() => {
        btnEl.innerHTML = original;
        btnEl.classList.remove('copied');
        btnEl._copyFlashing = false;
      }, 1200);
    }
    uiModule.showToast?.('Copied');
  } else {
    uiModule.showError?.('Copy failed');
  }
  return ok;
}

function _editNote(id) {
  const note = _notes.find(n => n.id === id);
  if (!note) return;
  _editingId = id;
  const card = document.querySelector(`.note-card[data-note-id="${id}"]`);
  if (!card) return;
  // Restore an unsaved draft (from a prior connection loss / close) over
  // the saved note so the user picks up where they left off.
  const { note: _n, restored } = _applyDraftToNote(note, id);
  const form = _buildForm(_n);
  card.replaceWith(form);
  if (restored) uiModule.showToast('Restored unsaved changes');
  // Pinned notes live in the first masonry column — the edit form has
  // column-span:all, which can leave the form rendered above the fold or
  // visually buried under neighboring pinned cards. Bring it into view
  // (and onto a higher stacking context) so editing a pinned note always
  // pops to the top.
  form.style.position = 'relative';
  form.style.zIndex = '5';
  // Grid view: the form keeps the CSS default `grid-row-end: span 16` (64px)
  // after replaceWith, which is much shorter than the actual form. Recompute
  // masonry so the form gets the correct row span and cards below stop
  // overlapping it. ResizeObserver inside _applyMasonry keeps it in sync as
  // the user types / adds checklist items.
  const _body = form.closest('.notes-pane-body');
  if (_body) {
    _applyMasonry(_body);
    requestAnimationFrame(() => _applyMasonry(_body));
  }
  requestAnimationFrame(() => {
    try { form.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    catch { form.scrollIntoView(); }
  });
  // Pick the most useful field to focus. On phones especially, the user
  // taps Edit to type — landing in the title when there's already a title
  // (and likely a body to extend) loses momentum. Prefer the body textarea
  // for plain notes, the first checklist item for todos, fall back to title.
  const _focusBest = () => {
    if (note.note_type === 'note' || !note.note_type) {
      const ta = form.querySelector('.note-form-content');
      if (ta) { ta.focus(); try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch {} return; }
    }
    if (note.note_type === 'todo' || note.note_type === 'goal' || note.note_type === 'checklist') {
      // Last non-empty checklist row, or the first row if all empty.
      const rows = form.querySelectorAll('.note-cl-row .note-cl-text');
      let target = null;
      for (const inp of rows) { if ((inp.value || '').trim()) target = inp; }
      target = target || rows[0];
      if (target) { target.focus(); try { target.setSelectionRange(target.value.length, target.value.length); } catch {} return; }
    }
    const titleEl = form.querySelector('.note-form-title');
    if (titleEl) titleEl.focus();
  };
  _focusBest();
}

async function _deleteNote(id) {
  const ok = uiModule?.styledConfirm
    ? await uiModule.styledConfirm('Delete this note?', { confirmText: 'Delete', danger: true })
    : confirm('Delete this note?');
  if (!ok) return;
  try { await _deleteNoteApi(id); await _fetchNotes(); _renderNotes(); uiModule.showToast('Deleted'); }
  catch (err) { uiModule.showError(err.message); }
}

// ────────────────────────────────────────────────────────────────────
// MOBILE NOTES UX — fullscreen tap-to-edit + long-press drag-to-reorder.
// On a touch device ≤768px wide, note tiles become read-only previews;
// a single tap opens the note in a full-bleed overlay (where all real
// editing happens), and a long-press flips the whole grid into
// rearrange mode where tiles can be dragged to a new sort_order.
// ────────────────────────────────────────────────────────────────────

function _isNotesMobileMode() {
  return ('ontouchstart' in window) && window.innerWidth <= 768;
}

// ── Fullscreen single-note edit overlay ──────────────────────────────
let _mobileFsOverlay = null;
let _mobileFsNoteId = null;

function _openMobileFullscreenEdit(id, fromCard) {
  const note = _notes.find(n => n.id === id);
  if (!note) return;
  // Tear down any previous overlay (defensive).
  _closeMobileFullscreenEdit({ save: false });
  _mobileFsNoteId = id;
  _editingId = id;

  const overlay = document.createElement('div');
  overlay.className = 'note-fullscreen-overlay';
  overlay.innerHTML = `
    <div class="note-fullscreen-header">
      <button type="button" class="note-fullscreen-back" title="Back">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
      </button>
      <div class="note-fullscreen-actions"></div>
    </div>
    <div class="note-fullscreen-body"></div>
  `;
  const body = overlay.querySelector('.note-fullscreen-body');
  // Reuse the same edit form the in-place flow builds. Save buttons,
  // checklist toggles, etc. all work as-is. Restore any unsaved draft.
  const { note: _n, restored } = _applyDraftToNote(note, id);
  const form = _buildForm(_n);
  body.appendChild(form);
  if (restored) uiModule.showToast('Restored unsaved changes');
  document.body.appendChild(overlay);
  _mobileFsOverlay = overlay;

  // Animate up from the tapped tile's position so the transition reads
  // as a zoom rather than a hard cut.
  if (fromCard) {
    const r = fromCard.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    overlay.style.transformOrigin =
      `${((r.left + r.width / 2) / vw) * 100}% ${((r.top + r.height / 2) / vh) * 100}%`;
  }
  overlay.classList.add('opening');
  requestAnimationFrame(() => overlay.classList.add('open'));

  // Wire the back button — saves whatever the form has and closes.
  // mousedown preventDefault so it doesn't blur the input on first tap (which
  // would eat the tap and require a second one).
  const _backBtn = overlay.querySelector('.note-fullscreen-back');
  _backBtn.addEventListener('mousedown', (e) => e.preventDefault());
  _backBtn.addEventListener('click', () => {
    _closeMobileFullscreenEdit({ save: true });
  });

  // The form's built-in Cancel only resets the in-place edit state; in
  // the overlay context it does nothing visible. Replace its handler so
  // Cancel actually dismisses the overlay without saving.
  const cancelBtn = form.querySelector('.note-form-cancel');
  if (cancelBtn) {
    const fresh = cancelBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(fresh, cancelBtn);
    fresh.addEventListener('mousedown', (e) => e.preventDefault());
    fresh.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      _closeMobileFullscreenEdit({ save: false });
    });
  }
  // The built-in Save handler does the API call + refresh, but doesn't
  // dismiss our overlay. Augment it (do NOT replace — the original is
  // async and we'd lose the API call) to schedule a close once the
  // save+render has had time to complete.
  const saveBtn = form.querySelector('.note-form-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      setTimeout(() => _closeMobileFullscreenEdit({ save: false }), 350);
    });
  }
  // Make the checklist row drag handle (⋮⋮) actually work on touch.
  // The form's default uses HTML5 native draggable which never fires
  // on iOS/Android. Wire touch-based reorder for any row inside the
  // overlay's checklist.
  _wireChecklistTouchReorder(form);

  // For each todo row whose text contains a URL, swap the bare <input>
  // for a linkified <span> so URLs are tappable. Tapping anywhere that
  // ISN'T a link flips back to the input for editing.
  form.querySelectorAll('.note-cl-row').forEach(_addRowReadMode);

  // Move Archive + Delete from the form's footer actions row up into
  // the header bar (to the right of the back chevron) so they're
  // always reachable without scrolling and free up the footer for
  // Cancel/Save only. Handlers stay attached when nodes move.
  const headerActions = overlay.querySelector('.note-fullscreen-actions');
  const archiveBtn = form.querySelector('.note-form-archive-btn');
  const deleteBtn  = form.querySelector('.note-form-delete-btn');
  if (headerActions && archiveBtn) headerActions.appendChild(archiveBtn);
  if (headerActions && deleteBtn)  headerActions.appendChild(deleteBtn);
  // The built-in archive/delete handlers re-render the notes grid but
  // leave THIS overlay sitting in front of it — looks like nothing
  // happened. Add follow-up listeners that close the overlay so the
  // user sees the action take effect.
  if (archiveBtn) {
    archiveBtn.addEventListener('click', () => {
      setTimeout(() => _closeMobileFullscreenEdit({ save: false }), 200);
    });
  }
  if (deleteBtn) {
    deleteBtn.addEventListener('click', () => {
      // Delete shows a styled confirm — give it room to resolve before
      // we try to dismiss the overlay.
      setTimeout(() => _closeMobileFullscreenEdit({ save: false }), 500);
    });
  }

  // Tuck the tags input into the bottom actions row (Cancel / Update),
  // pinned to the LEFT. Frees the meta row of an extra wrapping line
  // and groups all the "exit" controls together.
  const actionsGroup = form.querySelector('.note-form-actions-group');
  const tagsInput    = form.querySelector('.note-form-label');
  if (actionsGroup && tagsInput) {
    actionsGroup.insertBefore(tagsInput, actionsGroup.firstChild);
  }

  // For checklist-type notes, move the photo (attach image) button into
  // the same row as the + Add button (right side) — keeps the meta row
  // tidy and puts the camera within thumb-reach of the active edit.
  const addBtn   = form.querySelector('.note-cl-add');
  const photoBtn = form.querySelector('.note-form-photo-btn');
  if (addBtn && photoBtn) {
    const addRow = document.createElement('div');
    addRow.className = 'note-cl-add-row';
    addBtn.parentNode.insertBefore(addRow, addBtn);
    addRow.appendChild(addBtn);
    addRow.appendChild(photoBtn);
    // Tapping anywhere on the row (the empty gap, the dashed border,
    // the "+ Add" label) triggers add. The photo button keeps its own
    // click target so attach-image isn't ambushed.
    addRow.addEventListener('click', (e) => {
      if (e.target.closest('.note-form-photo-btn')) return;
      if (e.target === addBtn || addBtn.contains(e.target)) return;
      addBtn.click();
    });
    // The form's delegated "+ Add" handler does
    //   inputs.insertBefore(newRow, addBtn)
    // — but addBtn is no longer a direct child of `.note-checklist-inputs`
    // now that we wrapped it. Bind a fresh handler that does the same
    // thing but inserts before the WRAPPING row, and stop propagation
    // so the broken delegate never runs.
    addBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const inputs = form.querySelector('.note-checklist-inputs');
      if (!inputs) return;
      const newRow = document.createElement('div');
      newRow.className = 'note-cl-row';
      newRow.draggable = true;
      newRow.dataset.itemId = _uid();
      newRow.dataset.indent = '0';
      newRow.innerHTML = '<span class="note-cl-grip" title="Drag">⋮⋮</span><span class="note-cl-dot"></span><input type="text" class="note-cl-text" placeholder="Item..." /><button type="button" class="note-cl-rm">&times;</button>';
      inputs.insertBefore(newRow, addRow);
      _wireRow(newRow, inputs);
      // Touch reorder on the freshly-added row's grip.
      _wireChecklistTouchReorder(form);
      newRow.querySelector('.note-cl-text')?.focus();
    }, { capture: true });
  }

  // Read-mode overlay for plain notes: render the content as a div with
  // clickable hyperlinks, layered above the textarea. Tapping anywhere
  // in the overlay that ISN'T a link hides the overlay and focuses the
  // textarea so the user can start editing. Tapping a link opens it.
  const ta = form.querySelector('.note-form-content');
  if (ta && (note.content || '').trim()) {
    const reader = document.createElement('div');
    reader.className = 'note-form-content-reader';
    reader.innerHTML = _linkify(note.content || '');
    ta.style.display = 'none';
    ta.insertAdjacentElement('beforebegin', reader);
    reader.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;  // let links open normally
      reader.remove();
      ta.style.display = '';
      // Let the browser place the cursor naturally — forcing
      // setSelectionRange right after focus() raced with the underlying
      // tap event and produced inconsistent cursor positions on mobile.
      ta.focus({ preventScroll: true });
    });
  }

  // Opening an EXISTING note → read mode, no keyboard pop. Only a
  // brand-new note (created via the + button) should auto-focus an
  // input field. The user can tap the content to switch to edit.
  // (New-note creation flows through _createNote, not this function.)
}

function _closeMobileFullscreenEdit(opts = {}) {
  if (!_mobileFsOverlay) return;
  const overlay = _mobileFsOverlay;
  _mobileFsOverlay = null;
  // If the form has a Save button, click it on close so edits aren't lost
  // when the user uses the back arrow instead of an explicit Save.
  if (opts.save) {
    const saveBtn = overlay.querySelector('.note-form-save, [data-action="save"]');
    if (saveBtn) try { saveBtn.click(); } catch {}
  }
  overlay.classList.remove('open');
  overlay.classList.add('closing');
  setTimeout(() => {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    _mobileFsNoteId = null;
    _editingId = null;
    // Refresh the grid so any save the user made is reflected.
    if (opts.save !== false) { _fetchNotes().then(_renderNotes).catch(() => {}); }
  }, 220);
}

// ── Long-press drag-to-reorder ───────────────────────────────────────
function _bindLongPressDrag(card) {
  let pressTimer = null;
  let startX = 0, startY = 0;
  let armed = false;
  const CANCEL_PX = 8;
  const HOLD_MS = 450;

  card.addEventListener('touchstart', (e) => {
    // Don't fight scroll on touchpoints over real interactive children
    // (in mobile-mode they're CSS-hidden anyway, but be defensive).
    if (e.target.closest('button, input, a, .note-form')) return;
    if (e.touches.length !== 1) return;
    armed = true;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    // Capture the touch object so the timer callback can pass it to
    // _enterDragMode → _beginGrab. The finger is still held down, so
    // the drag starts the instant the timer fires.
    const heldTouch = { clientX: startX, clientY: startY };
    pressTimer = setTimeout(() => {
      if (!armed) return;
      try { navigator.vibrate?.(15); } catch {}
      _enterDragMode(card, heldTouch);
    }, HOLD_MS);
  }, { passive: true });
  card.addEventListener('touchmove', (e) => {
    if (!armed) return;
    const t = e.touches[0];
    if (Math.abs(t.clientX - startX) > CANCEL_PX || Math.abs(t.clientY - startY) > CANCEL_PX) {
      armed = false;
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    }
  }, { passive: true });
  const cancel = () => {
    armed = false;
    if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
  };
  card.addEventListener('touchend', cancel, { passive: true });
  card.addEventListener('touchcancel', cancel, { passive: true });
}

// Lift-and-placeholder drag implementation. The dragged card detaches
// from the grid (position:fixed, anchored to the finger) while a same-
// sized placeholder takes its slot. Only the PLACEHOLDER moves between
// siblings as the finger crosses midpoints — the card never re-parents
// during the drag, which eliminates the oscillation/jumping the
// previous swap-on-every-frame implementation had.

let _dragState = null;          // { card, placeholder, grabOffsetX, grabOffsetY, grid, prevStyle }
let _docDragHandlersBound = false;

function _enterDragMode(initialCard, initialTouch) {
  document.body.classList.add('notes-drag-mode');
  document.querySelectorAll('.note-card').forEach(_setupDragForCard);
  if (!_docDragHandlersBound) {
    document.addEventListener('touchmove', _onDocTouchMove, { passive: false });
    document.addEventListener('touchend',  _onDocTouchEnd,  { passive: true });
    document.addEventListener('touchcancel', _onDocTouchEnd, { passive: true });
    _docDragHandlersBound = true;
  }
  // Auto-grab the card the user long-pressed — they're already holding
  // their finger on it, so begin the drag straight away. Releasing
  // (touchend) commits the reorder AND exits drag mode in one motion.
  if (initialCard && initialTouch) {
    _beginGrab(initialCard, initialTouch);
  }
}

function _exitDragMode() {
  document.body.classList.remove('notes-drag-mode');
  if (_dragState) {
    // Defensive: if exit fires while a drag is in flight, snap the card back.
    _onDocTouchEnd();
  }
  // Leave _docDragHandlersBound true so re-entering drag mode reuses them.
}

function _setupDragForCard(card) {
  if (card.dataset.dragBound === '1') return;
  card.dataset.dragBound = '1';
  card.addEventListener('touchstart', (e) => {
    if (!document.body.classList.contains('notes-drag-mode')) return;
    if (e.touches.length !== 1) return;
    if (_dragState) return;
    e.preventDefault();
    e.stopPropagation();
    _beginGrab(card, e.touches[0]);
  }, { passive: false });
}

function _beginGrab(card, touch) {
  const rect = card.getBoundingClientRect();
  const prevStyle = card.getAttribute('style') || '';
  // Placeholder fills the card's old slot so the grid layout doesn't reflow.
  const placeholder = document.createElement('div');
  placeholder.className = 'note-card-placeholder';
  placeholder.style.width = rect.width + 'px';
  placeholder.style.height = rect.height + 'px';
  placeholder.style.margin = getComputedStyle(card).margin;
  if (card.style.gridRowEnd) placeholder.style.gridRowEnd = card.style.gridRowEnd;
  const grid = card.parentNode;
  grid.insertBefore(placeholder, card);

  // Detach the card visually — fixed-position, anchored to the finger.
  card.classList.add('note-card-dragging');
  card.style.position = 'fixed';
  card.style.left = rect.left + 'px';
  card.style.top  = rect.top + 'px';
  card.style.width  = rect.width + 'px';
  card.style.height = rect.height + 'px';
  card.style.margin = '0';
  card.style.zIndex = '10001';
  // pointer-events:none so elementFromPoint sees the card BENEATH the finger
  card.style.pointerEvents = 'none';

  _dragState = {
    card, placeholder, grid, prevStyle,
    grabOffsetX: touch.clientX - rect.left,
    grabOffsetY: touch.clientY - rect.top,
  };
  try { navigator.vibrate?.(8); } catch {}
}

function _onDocTouchMove(e) {
  if (!_dragState) return;
  if (e.touches.length !== 1) return;
  e.preventDefault();
  const touch = e.touches[0];
  const { card, placeholder, grid } = _dragState;
  card.style.left = (touch.clientX - _dragState.grabOffsetX) + 'px';
  const quickAdd = grid.querySelector('.notes-quick-add');
  const minTop = quickAdd ? quickAdd.getBoundingClientRect().bottom + 4 : grid.getBoundingClientRect().top;
  const maxTop = Math.max(minTop, window.innerHeight - card.getBoundingClientRect().height - 8);
  const nextTop = Math.max(minTop, Math.min(maxTop, touch.clientY - _dragState.grabOffsetY));
  card.style.top = nextTop + 'px';

  const hitY = Math.max(minTop + 1, Math.min(window.innerHeight - 1, touch.clientY));
  const under = document.elementFromPoint(touch.clientX, hitY);
  const target = under && under.closest
    ? under.closest('.note-card:not(.note-card-dragging)')
    : null;
  if (!target || target === card) return;
  if (target.parentNode !== grid) return;

  // Move the PLACEHOLDER (not the card) above or below the target depending
  // on which half of the target the finger is in. This is the hysteresis
  // that stops the oscillation — once the placeholder moves past a card,
  // the cursor has to cross THAT card's midpoint in the other direction
  // to swap back.
  const tRect = target.getBoundingClientRect();
  const targetMidY = tRect.top + tRect.height / 2;
  if (touch.clientY < targetMidY) {
    if (placeholder.nextElementSibling !== target) {
      grid.insertBefore(placeholder, target);
    }
  } else {
    if (target.nextElementSibling !== placeholder) {
      grid.insertBefore(placeholder, target.nextElementSibling);
    }
  }
}

function _onDocTouchEnd() {
  if (!_dragState) return;
  const { card, placeholder, grid, prevStyle } = _dragState;
  _dragState = null;
  // Animate the card from its current fixed position to where the
  // placeholder sits, then re-parent and clear inline styles. Drag
  // mode auto-exits once the snap finishes — release = done.
  const phRect = placeholder.getBoundingClientRect();
  card.style.transition = 'left 0.2s ease, top 0.2s ease';
  card.style.left = phRect.left + 'px';
  card.style.top  = phRect.top + 'px';
  setTimeout(() => {
    placeholder.parentNode.insertBefore(card, placeholder);
    placeholder.remove();
    card.classList.remove('note-card-dragging');
    // Restore the card's pre-drag inline styles. Mobile masonry stores
    // grid-row-end inline, and custom backgrounds use inline style too; wiping
    // cssText made dropped cards collapse into neighboring notes in grid view.
    if (prevStyle) card.setAttribute('style', prevStyle);
    else card.removeAttribute('style');
    _applyMasonry(grid);
    _commitNoteReorder();
    // One drag, one exit — release ends the rearrange session entirely.
    if (document.body.classList.contains('notes-drag-mode')) {
      document.body.classList.remove('notes-drag-mode');
    }
  }, 210);
}

// Per-row read mode for todo items — replaces the plain <input> with
// a linkified <span> when the value contains a URL, so tapping a link
// opens it instead of just placing the caret. Tapping non-link area
// restores the input for editing.
function _addRowReadMode(row) {
  const txt = row.querySelector('.note-cl-text');
  if (!txt) return;
  const val = txt.value || '';
  if (!/(https?:\/\/|www\.)/i.test(val)) return;
  if (row.querySelector('.note-cl-text-reader')) return;  // already wired
  const span = document.createElement('span');
  span.className = 'note-cl-text-reader';
  span.innerHTML = _linkify(val);
  txt.style.display = 'none';
  txt.insertAdjacentElement('beforebegin', span);
  span.addEventListener('click', (e) => {
    if (e.target.closest('a')) return;  // let the link open
    span.remove();
    txt.style.display = '';
    txt.focus({ preventScroll: true });
  });
}

// ── Checklist row reorder via touch (inside fullscreen edit) ────────
// The default checklist drag uses HTML5 `draggable="true"`, which is
// desktop-mouse-only. Wire touch handlers on each `.note-cl-grip` so
// the user can long-grab a row and drag it to a new position in the
// checklist. Uses the same lift-and-placeholder pattern as the card
// drag (no oscillation when hovering between siblings).

let _clDrag = null;          // { row, placeholder, container, grabOffsetX, grabOffsetY }
let _clDocBound = false;

function _wireChecklistTouchReorder(form) {
  const container = form.querySelector('.note-checklist-inputs');
  if (!container) return;
  container.querySelectorAll('.note-cl-grip').forEach(grip => {
    if (grip.dataset.touchBound === '1') return;
    grip.dataset.touchBound = '1';
    grip.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      e.preventDefault();
      const row = grip.closest('.note-cl-row');
      if (!row) return;
      _beginChecklistGrab(row, container, e.touches[0]);
    }, { passive: false });
  });
  if (!_clDocBound) {
    document.addEventListener('touchmove', _onClTouchMove, { passive: false });
    document.addEventListener('touchend',  _onClTouchEnd,  { passive: true });
    document.addEventListener('touchcancel', _onClTouchEnd, { passive: true });
    _clDocBound = true;
  }
}

function _beginChecklistGrab(row, container, touch) {
  if (_clDrag) return;
  const rect = row.getBoundingClientRect();
  const placeholder = document.createElement('div');
  placeholder.className = 'note-cl-row-placeholder';
  placeholder.style.height = rect.height + 'px';
  container.insertBefore(placeholder, row);

  row.classList.add('note-cl-row-dragging');
  row.style.position = 'fixed';
  row.style.left = rect.left + 'px';
  row.style.top  = rect.top + 'px';
  row.style.width = rect.width + 'px';
  row.style.zIndex = '10002';
  row.style.pointerEvents = 'none';

  _clDrag = {
    row, placeholder, container,
    grabOffsetX: touch.clientX - rect.left,
    grabOffsetY: touch.clientY - rect.top,
  };
  try { navigator.vibrate?.(8); } catch {}
}

function _onClTouchMove(e) {
  if (!_clDrag) return;
  if (e.touches.length !== 1) return;
  e.preventDefault();
  const t = e.touches[0];
  const { row, placeholder, container } = _clDrag;
  row.style.left = (t.clientX - _clDrag.grabOffsetX) + 'px';
  row.style.top  = (t.clientY - _clDrag.grabOffsetY) + 'px';

  const under = document.elementFromPoint(t.clientX, t.clientY);
  const target = under && under.closest
    ? under.closest('.note-cl-row:not(.note-cl-row-dragging)')
    : null;
  if (!target || target === row) return;
  if (target.parentNode !== container) return;

  const tRect = target.getBoundingClientRect();
  const targetMidY = tRect.top + tRect.height / 2;
  if (t.clientY < targetMidY) {
    if (placeholder.nextElementSibling !== target) {
      container.insertBefore(placeholder, target);
    }
  } else {
    if (target.nextElementSibling !== placeholder) {
      container.insertBefore(placeholder, target.nextElementSibling);
    }
  }
}

function _onClTouchEnd() {
  if (!_clDrag) return;
  const { row, placeholder } = _clDrag;
  _clDrag = null;
  const phRect = placeholder.getBoundingClientRect();
  row.style.transition = 'left 0.18s ease, top 0.18s ease';
  row.style.left = phRect.left + 'px';
  row.style.top  = phRect.top + 'px';
  setTimeout(() => {
    placeholder.parentNode.insertBefore(row, placeholder);
    placeholder.remove();
    row.classList.remove('note-cl-row-dragging');
    row.style.cssText = '';
    // Order is persisted as part of the form's normal save (rows are
    // re-serialized in DOM order via _collectItems).
  }, 200);
}

async function _commitNoteReorder() {
  const grid = document.querySelector('#notes-pane .notes-pane-body');
  if (!grid) return;
  const ids = Array.from(grid.querySelectorAll('.note-card')).map(c => c.dataset.noteId).filter(Boolean);
  if (!ids.length) return;
  try {
    await fetch(`${API_BASE}/api/notes/reorder`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    // Update local sort_order so subsequent renders agree with the server.
    ids.forEach((nid, i) => {
      const n = _notes.find(nn => nn.id === nid);
      if (n) n.sort_order = i;
    });
  } catch (e) {
    console.warn('reorder failed', e);
  }
}


// Background reminder loop — runs whether panel is open or not
async function _initReminders() {
  try {
    const res = await fetch(`${API_BASE}/api/notes`, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      _notes = data.notes || data || [];
      _startReminderLoop();
    }
  } catch {}
}

const notesModule = { openPanel, closePanel, togglePanel, isPanelOpen, openNotes: openPanel, closeNotes: closePanel, isNotesOpen: isPanelOpen, refreshDueBadge };
export default notesModule;
export { openPanel as openNotes, closePanel as closeNotes, isPanelOpen as isNotesOpen };
window.notesModule = notesModule;

// Start reminder loop on module load (after a short delay so app loads first)
if (typeof window !== 'undefined') {
  setTimeout(_initReminders, 3000);
}
