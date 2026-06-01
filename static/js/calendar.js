/**
 * Calendar Module — CalDAV-backed month/week/year calendar.
 */

import uiModule from './ui.js';
import spinnerModule from './spinner.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { attachColorPicker } from './colorPicker.js';
import {
  WEEKDAYS, MONTHS, MON_SHORT,
  CAL_PALETTE, CAL_COLORS, _CAL_CUSTOM_GRADIENT, _TYPE_PALETTE,
  _trashIcon, _moreIcon, _bellIcon,
  _isCalBgImage, _calBgImageUrl, _calBgCss,
  _ds, _addDays, _shiftDT, _tzOffset, _localDateOf,
} from './calendar/utils.js';

const API_BASE = window.location.origin;
// Open a file picker, upload the chosen image, return the URL string.
function _pickCalBgImage() {
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
    setTimeout(() => { if (!done && !input.files?.length) finish(null); }, 30000);
    input.click();
  });
}

let _open = false;
// Set when the calendar opens so the first month render scrolls today's
// cell into view — the grid scrolls on mobile and today can sit below the
// fold, so we always land on the current date.
let _scrollToTodayOnOpen = false;
let _currentDate = new Date();
let _events = [];
let _allEvents = {};
let _fetchedRanges = [];
let _calendars = [];
let _hiddenCals = new Set();
let _hiddenTypes = new Set();   // event_type values to hide
// "Only important" filter — when true, only events with importance
// high/critical render, regardless of their category. Toggled via the "!"
// chip; orthogonal to _hiddenTypes (which deals with event_type categories).
let _onlyImportant = false;

let _filtersCollapsed = localStorage.getItem('cal-filters-collapsed') === '1';
let _selectedDay = null;
let _view = 'month';
let _searchQuery = '';
let _escHandler = null;
let _modal = null;

let _dragUid = null;
let _sidebarWasOpen = false;
let _slideDir = 0;  // -1 = prev, +1 = next, 0 = none

// (Single undo stack lives at `_calUndoStack` further below; this used to
// hold a one-deep `_lastUndo` which has been collapsed into that stack.)

function _showCalUndoToast(label, undoFn) {
  // Push onto the shared undo stack (also used by month drag-drop) so
  // Cmd/Ctrl+Z and the toast button consume the same source of truth.
  _pushCalUndo({ label, run: undoFn });
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || '') || /Mac/.test(navigator.userAgent || '');
  uiModule.showToast(label, {
    action: 'Undo',
    actionHint: isMac ? '⌘Z' : 'Ctrl+Z',
    duration: 6000,
    onAction: _popAndRunCalUndo,
  });
}

// ── API ──

function _rangeIsCached(start, end) {
  // Check if [start, end] is fully covered by any single fetched range
  for (const [s, e] of _fetchedRanges) {
    if (s <= start && e >= end) return true;
  }
  return false;
}

function _filterPool(start, end) {
  // Return all events in pool that overlap [start, end)
  return Object.values(_allEvents).filter(ev => {
    const evStart = ev.all_day ? ev.dtstart : _localDateOf(ev.dtstart);
    const evEnd = ev.all_day ? ev.dtend : _localDateOf(ev.dtend || ev.dtstart);
    return evStart < end && evEnd >= start;
  }).sort((a, b) => a.dtstart < b.dtstart ? -1 : 1);
}

async function _fetchEvents(start, end, force) {
  if (!force && _rangeIsCached(start, end)) {
    _events = _filterPool(start, end);
    return;
  }
  // Render from pool immediately if we have any cached data
  const hasCache = Object.keys(_allEvents).length > 0;
  if (hasCache) _events = _filterPool(start, end);
  const fetchPromise = fetch(`${API_BASE}/api/calendar/events?start=${start}&end=${end}`, { credentials: 'same-origin' })
    .then(r => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(data => {
      // On first fetch after cache load, replace pool entirely to avoid
      // stale/duplicate UIDs from a previous backend (e.g. CalDAV → SQLite)
      if (hasCache && _fetchedRanges.length === 0) _allEvents = {};
      (data.events || []).forEach(ev => { _allEvents[ev.uid] = ev; });
      _fetchedRanges.push([start, end]);
      _events = _filterPool(start, end);
      if (typeof _saveCache === 'function') _saveCache();
      // Re-render in background when new data arrives (if calendar still open)
      if (_open && hasCache) _render();
    })
    .catch(e => { console.error('Calendar: failed to fetch events', e); });
  // If we have cache, don't block on fetch — return immediately so render is instant
  if (hasCache) return;
  // No cache — must await the fetch
  await fetchPromise;
}

// Prefetch surrounding months in background — fire-and-forget, no blocking
function _prefetchAdjacent() {
  const ranges = [];
  if (_view === 'month' || _view === 'week') {
    // Prefetch ±2 months around current
    for (let offset = -2; offset <= 2; offset++) {
      if (offset === 0) continue;
      const d = new Date(_currentDate.getFullYear(), _currentDate.getMonth() + offset, 1);
      ranges.push(_monthRange(d));
    }
  } else if (_view === 'year') {
    // Prefetch prev/next year
    ranges.push([`${_currentDate.getFullYear() - 1}-01-01`, `${_currentDate.getFullYear()}-01-01`]);
    ranges.push([`${_currentDate.getFullYear() + 1}-01-01`, `${_currentDate.getFullYear() + 2}-01-01`]);
  }
  // Fire all prefetches in parallel, ignore failures
  for (const [s, e] of ranges) {
    if (_rangeIsCached(s, e)) continue;
    fetch(`${API_BASE}/api/calendar/events?start=${s}&end=${e}`, { credentials: 'same-origin' })
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(d => {
        (d.events || []).forEach(ev => { _allEvents[ev.uid] = ev; });
        _fetchedRanges.push([s, e]);
      })
      .catch(() => {});
  }
}

let _calendarsError = null;
// Guard so we only trigger an on-open CalDAV pull once per page load —
// every list/render path calls _fetchCalendars, but we only want to
// hit the remote server lazily on the first user open.
let _caldavSyncedOnce = false;
async function _fetchCalendars() {
  _calendarsError = null;
  try {
    const res = await fetch(`${API_BASE}/api/calendar/calendars`, { credentials: 'same-origin' });
    const data = await res.json();
    _calendars = data.calendars || [];
    if (data.error) _calendarsError = data.error;
    _calendars.forEach((c, i) => {
      if (!c.color || c.color.startsWith('<')) c.color = CAL_PALETTE[i % CAL_PALETTE.length];
    });
  } catch (e) { _calendars = []; _calendarsError = e.message || 'Connection failed'; }

  // First open: fire a background CalDAV pull. We don't await — the
  // initial render uses whatever's already cached locally, and the
  // sync's writes show up on the next paint after it resolves.
  if (!_caldavSyncedOnce) {
    _caldavSyncedOnce = true;
    _syncCaldav(false);
  }
}

// Trigger a CalDAV pull. `interactive=true` waits for the result and
// refreshes the UI; false fires-and-forgets (used on first open). Both
// no-op silently if CalDAV isn't configured.
async function _syncCaldav(interactive) {
  try {
    const res = await fetch(`${API_BASE}/api/calendar/sync`, {
      method: 'POST', credentials: 'same-origin',
    });
    const data = await res.json().catch(() => ({}));
    if (interactive) return data;
    // Background path: if the pull actually changed anything, drop
    // local caches and re-render so new events appear.
    const changed = (data.calendars || 0) > 0 && ((data.events || 0) > 0 || (data.deleted || 0) > 0);
    if (changed) {
      _allEvents = {}; _fetchedRanges = [];
      try { localStorage.removeItem(LS_KEY); } catch (_) {}
      await _fetchCalendars();
      _render();
    }
  } catch (e) {
    if (interactive) return { errors: [e.message || 'Sync failed'] };
  }
}

function _optimisticEvent(data, uid) {
  const cal = _calendars.find(c => c.href === data.calendar_href) || _calendars[0];
  return {
    uid,
    summary: data.summary || '',
    dtstart: data.dtstart,
    dtend: data.dtend || data.dtstart,
    all_day: !!data.all_day,
    description: data.description || '',
    location: data.location || '',
    rrule: data.rrule || '',
    calendar: cal?.name || '',
    calendar_href: data.calendar_href || cal?.href || '',
    // Per-event color override (including the bg:<url> sentinel for custom
    // backgrounds) wins over the parent calendar's default hex.
    color: (data.color !== undefined && data.color !== null) ? data.color : (cal?.color || ''),
  };
}

// v2 review error-handling MEDs: every fetch here previously checked
// only `.then(r => r.json())` with no `r.ok` test. A 500/404 still
// resolved the promise and the optimistic state got promoted to truth.
// All three flows now inspect `r.ok` and roll back the optimistic
// state + surface a toast on the failure path.
async function _createEvent(data) {
  const tempUid = 'temp-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  _allEvents[tempUid] = _optimisticEvent(data, tempUid);
  fetch(`${API_BASE}/api/calendar/events`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }).then(async r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(d => {
    if (d.uid) {
      delete _allEvents[tempUid];
      _allEvents[d.uid] = _optimisticEvent(data, d.uid);
      _saveCache && _saveCache();
      if (_open) _render();
    }
  }).catch((e) => {
    delete _allEvents[tempUid];
    if (_open) _render();
    if (window.uiModule) window.uiModule.showError('Failed to create event: ' + (e?.message || 'unknown'));
  });
  return { uid: tempUid };
}

async function _updateEvent(uid, data) {
  const merged = { ...(_allEvents[uid] || {}), ...data };
  const _preMergeBackup = _allEvents[uid];
  _allEvents[uid] = _optimisticEvent(merged, uid);
  // For recurring events the uid is a compound "{base_uid}::{date}" —
  // the backend resolves it to the base series row. After the update,
  // other occurrences of the same series are stale. Wipe the cache so
  // a re-fetch picks up fresh data (next render + prefetch handles it).
  const isRecurring = uid.includes('::');
  fetch(`${API_BASE}/api/calendar/events/${encodeURIComponent(uid)}`, {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }).then(r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (isRecurring) {
      _fetchedRanges = [];
      localStorage.removeItem(LS_KEY);
    } else {
      _saveCache && _saveCache();
    }
  }).catch((e) => {
    if (_preMergeBackup) _allEvents[uid] = _preMergeBackup;
    else delete _allEvents[uid];
    if (_open) _render();
    if (window.uiModule) window.uiModule.showError('Failed to update event: ' + (e?.message || 'unknown'));
  });
  return { ok: true };
}

async function _deleteEvent(uid) {
  const backup = _allEvents[uid];
  delete _allEvents[uid];
  const isRecurring = uid.includes('::');
  fetch(`${API_BASE}/api/calendar/events/${encodeURIComponent(uid)}`, {
    method: 'DELETE', credentials: 'same-origin',
  }).then(r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (isRecurring) {
      _fetchedRanges = [];
      localStorage.removeItem(LS_KEY);
    } else {
      _saveCache && _saveCache();
    }
  }).catch((e) => {
    if (backup) _allEvents[uid] = backup;
    if (window.uiModule) window.uiModule.showError('Failed to delete event: ' + (e?.message || 'unknown'));
    if (_open) _render();
  });
  return { ok: true };
}

// ── Date helpers ──
// _ds, _addDays, _shiftDT, _localDateOf, _tzOffset live in ./calendar/utils.js
// _monthRange / _weekRange / _today depend on _ds so they stay here.

function _today() { return _ds(new Date()); }

function _monthRange(d) {
  const y = d.getFullYear(), m = d.getMonth();
  const first = new Date(y, m, 1);
  const dow = (first.getDay() + 6) % 7;
  const gs = new Date(y, m, 1 - dow);
  const ge = new Date(gs); ge.setDate(gs.getDate() + 42);
  return [_ds(gs), _ds(ge)];
}

function _weekRange(d) {
  const dow = (d.getDay() + 6) % 7;
  const s = new Date(d); s.setDate(d.getDate() - dow);
  const e = new Date(s); e.setDate(s.getDate() + 7);
  return [_ds(s), _ds(e)];
}

function _eventsForDay(dateStr) {
  return _events.filter(e => {
    if (!_eventVisible(e)) return false;
    if (e.all_day) {
      // Zero-duration all-day event (dtstart == dtend) is a single-day event
      if (e.dtstart === e.dtend) return e.dtstart === dateStr;
      return e.dtstart <= dateStr && e.dtend > dateStr;
    }
    // Multi-day timed events: show on each day they span
    const startDate = _localDateOf(e.dtstart);
    const endDate = _localDateOf(e.dtend);
    if (startDate !== endDate) return startDate <= dateStr && endDate >= dateStr;
    return startDate === dateStr;
  });
}

function _calColor(ev) {
  // Custom bg-image colors fall back to the parent calendar's solid hex
  // in spots that need a plain color (dots, multi-day bars, week tile
  // borders). The full image is shown via _calItemBgStyle() where it
  // makes sense (event-item rows).
  if (_isCalBgImage(ev.color)) {
    const c = _calendars.find(c => c.href === ev.calendar_href);
    return c?.color || 'var(--accent)';
  }
  if (ev.color && !ev.color.startsWith('<')) return ev.color;
  const c = _calendars.find(c => c.href === ev.calendar_href);
  return c?.color || 'var(--accent)';
}

// Extra inline style for an event row when the event has a custom BG image.
// Returns '' for normal solid-color events.
function _calItemBgStyle(ev) {
  if (!_isCalBgImage(ev.color)) return '';
  const url = _calBgImageUrl(ev.color).replace(/'/g, "\\'");
  return `background-image: linear-gradient(color-mix(in srgb, var(--bg) 70%, transparent), color-mix(in srgb, var(--bg) 70%, transparent)), url('${url}'); background-size: cover; background-position: center;`;
}

function _todayCount() {
  const t = _today();
  return _events.filter(e => {
    if (!_eventVisible(e)) return false;
    if (e.all_day) {
      if (e.dtstart === e.dtend) return e.dtstart === t;
      return e.dtstart <= t && e.dtend > t;
    }
    return _localDateOf(e.dtstart) === t;
  }).length;
}

// Per-event ⋮ menu: Remind me / Delete
function _wireQuickDelete(body) {
  body.querySelectorAll('.cal-event-more').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const uid = btn.dataset.uid;
      if (!uid) return;
      const ev = _allEvents[uid];
      if (!ev) return;
      _showEventMoreMenu(ev, btn);
    });
  });
}

function _clampDropdown(dropdown, anchorRect) {
  const margin = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const r = dropdown.getBoundingClientRect();
  const w = r.width, h = r.height;
  // Horizontal: prefer right-aligned with anchor, clamp to viewport
  let left = anchorRect.right - w;
  if (left + w > vw - margin) left = vw - margin - w;
  if (left < margin) left = margin;
  // Vertical: below anchor if it fits, else above
  let top = anchorRect.bottom + 4;
  if (top + h > vh - margin) {
    const above = anchorRect.top - 4 - h;
    top = above >= margin ? above : Math.max(margin, vh - margin - h);
  }
  dropdown.style.left = `${left}px`;
  dropdown.style.top = `${top}px`;
  dropdown.style.right = 'auto';
}

function _showEventMoreMenu(ev, anchor) {
  document.querySelectorAll('.cal-event-dropdown').forEach(d => d.remove());
  const dropdown = document.createElement('div');
  dropdown.className = 'cal-event-dropdown';
  const rect = anchor.getBoundingClientRect();
  dropdown.style.cssText = `position:fixed;z-index:10001;min-width:180px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;top:${rect.bottom + 4}px;left:0px;visibility:hidden;`;

  const _item = (icon, label, onClick, danger) => {
    const it = document.createElement('div');
    it.className = 'dropdown-item-compact' + (danger ? ' dropdown-item-danger' : '');
    it.innerHTML = `<span class="dropdown-icon">${icon}</span><span>${label}</span>`;
    it.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
    return it;
  };

  const _editIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';

  dropdown.appendChild(_item(_editIcon, 'Edit', () => {
    dropdown.remove();
    _showEventForm(ev);
  }));

  dropdown.appendChild(_item(_trashIcon, 'Delete', async () => {
    dropdown.remove();
    const name = ev.summary ? `"${ev.summary}"` : 'this event';
    const ok = await uiModule.styledConfirm(`Delete ${name}?`, { confirmText: 'Delete', danger: true });
    if (!ok) return;
    try { await _deleteEvent(ev.uid); setTimeout(() => _render(), 100); } catch (_) {}
  }, true));

  document.body.appendChild(dropdown);
  dropdown._anchorRect = rect;
  _clampDropdown(dropdown, rect);
  dropdown.style.visibility = '';
  const close = (ev2) => {
    if (!dropdown.contains(ev2.target) && ev2.target !== anchor) {
      dropdown.remove();
      document.removeEventListener('click', close, true);
    }
  };
  setTimeout(() => document.addEventListener('click', close, true), 10);
}

async function _createEventReminder(ev, dueDate) {
  // Store the reminder as an absolute UTC instant (with the Z suffix) so the
  // notification poller fires at the right wall-clock moment regardless of:
  //   - the event's source timezone (CalDAV/import may carry a TZID),
  //   - the user's current local timezone differing from when the reminder
  //     was created,
  //   - any naive ISO mis-interpretation downstream.
  // Both notes.js and the calendar poller already use `new Date(due_date)`,
  // which handles Z-suffixed ISO correctly and converts back to local time
  // when displayed.
  const iso = new Date(dueDate).toISOString();
  const startFmt = ev.all_day
    ? new Date(ev.dtstart).toLocaleDateString([], { weekday:'short', month:'short', day:'numeric' })
    : new Date(ev.dtstart).toLocaleString([], { weekday:'short', month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
  const summary = ev.summary || '(no title)';
  const loc = ev.location ? ` @ ${ev.location}` : '';
  const text = `${summary}${loc} — ${startFmt}`;
  const payload = {
    title: `Reminder: ${summary}`,
    note_type: 'todo',
    items: [{ text, done: false, checked: false }],
    label: 'calendar',
    due_date: iso,
    source: 'calendar',
    // Persist the EVENT'S absolute start so the notification body can be
    // computed live at fire time ("Starts in 5 min") instead of using a
    // stale string baked at scheduling time.
    event_dtstart: new Date(ev.dtstart).toISOString(),
  };
  try {
    const res = await fetch(`/api/notes`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('Failed');
    const fmt = dueDate.toLocaleString([], { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
    if (uiModule.showToast) uiModule.showToast(`Reminder set for ${fmt}`);
    try { window.notesModule?.refreshDueBadge?.({ force: true }); } catch {}
    if ('Notification' in window && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch {}
    }
  } catch (e) {
    if (uiModule.showError) uiModule.showError('Failed to create reminder');
  }
}

// ── Sidebar collapse ──

function _collapseSidebar() {
  const sb = document.getElementById('sidebar');
  if (sb && !sb.classList.contains('hidden')) {
    // Only remember the prior state on desktop. On mobile the sidebar is an
    // overlay that the user intentionally swipes/taps away when the tool
    // opens — popping it back on close is unwanted.
    if (window.innerWidth >= 700) _sidebarWasOpen = true;
    sb.classList.add('hidden');
    if (window.syncRailSide) window.syncRailSide();
  }
}

function _restoreSidebar() {
  if (_sidebarWasOpen) {
    const sb = document.getElementById('sidebar');
    if (sb) { sb.classList.remove('hidden'); if (window.syncRailSide) window.syncRailSide(); }
    _sidebarWasOpen = false;
  }
}

// ── Badge ──

const BADGE_SEEN_KEY = 'odysseus-calendar-badge-seen';

function _todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _isBadgeSeenToday() {
  try { return localStorage.getItem(BADGE_SEEN_KEY) === _todayStr(); } catch { return false; }
}

function _markBadgeSeen() {
  try { localStorage.setItem(BADGE_SEEN_KEY, _todayStr()); } catch {}
}

function _updateBadge() {
  const btn = document.getElementById('tool-calendar-btn');
  if (!btn) return;
  let badge = btn.querySelector('.cal-badge');
  const count = _todayCount();
  if (count > 0 && !_isBadgeSeenToday()) {
    if (!badge) { badge = document.createElement('span'); badge.className = 'cal-badge'; btn.appendChild(badge); }
    badge.title = `${count} event${count > 1 ? 's' : ''} today`;
  } else if (badge) badge.remove();
}

// ── Modal ──

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'calendar-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content cal-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>Calendar</h4>
        <button class="close-btn" id="cal-close">✖</button>
      </div>
      <div class="modal-body" id="cal-body"></div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#cal-close').addEventListener('click', closeCalendar);
  _modal.addEventListener('click', (e) => { if (e.target === _modal) closeCalendar(); });
  // Make draggable — replaced ~50 lines of inline drag/dock plumbing with
  // a single call to the shared helper. Calendar doesn't support fullscreen
  // snap so no fsClass / enter/exit callbacks here.
  {
    const content = _modal.querySelector('.modal-content');
    const header = _modal.querySelector('.modal-header');
    if (content && header) {
      makeWindowDraggable(_modal, { content, header });
    }
  }
  return _modal;
}

// ── Render dispatch ──

// Stash the quick-add input's state (focus + caret + value) before a
// re-render so background fetches don't kick the user out mid-type. Picked
// up by _wireAll after the new DOM lands.
let _qaPendingRestore = null;
function _saveQuickAddState() {
  const el = document.getElementById('cal-quickadd');
  if (!el || document.activeElement !== el) { _qaPendingRestore = null; return; }
  _qaPendingRestore = {
    value: el.value,
    selStart: el.selectionStart,
    selEnd: el.selectionEnd,
  };
}

// True while the user is actively in the quick-add field. On mobile a
// programmatic re-focus after a DOM rebuild can't reopen the soft keyboard, so
// we must NOT swap the calendar body out from under an active quick-add — we
// defer the render and flush it on blur instead.
let _renderPending = false;
let _qaSubmitting = false;
function _qaTyping() {
  const el = document.getElementById('cal-quickadd');
  return !!el && document.activeElement === el;
}

// Update only the search-results portion of the day-detail panel, keeping
// the search input element itself in the DOM so the on-screen keyboard
// doesn't dismiss between keystrokes. Used by the search input's `input`
// listener instead of a full _render().
function _updateDaySearchResults() {
  const dayDetail = document.querySelector('.cal-day-detail');
  if (!dayDetail) { _render(); return; }
  // Searching forces a selected day so the panel is always available
  // (matches the logic in _render).
  if (_searchQuery && !_selectedDay) _selectedDay = _today();
  const ds = _selectedDay || _today();
  // Build the day-detail HTML in a detached node so we can extract its
  // children (results, header, etc.) without touching the live input.
  const tmp = document.createElement('div');
  tmp.innerHTML = _dayDetailHTML(ds);
  const fresh = tmp.querySelector('.cal-day-detail');
  if (!fresh) return;
  // Remove every child of the live day-detail except the search-wrap.
  const keep = dayDetail.querySelector('.cal-search-wrap');
  [...dayDetail.children].forEach(c => { if (c !== keep) c.remove(); });
  // Move children from the fresh build into the live panel, skipping
  // the duplicate search-wrap.
  [...fresh.children].forEach(c => {
    if (!c.classList.contains('cal-search-wrap')) dayDetail.appendChild(c);
  });
  // Re-wire click handlers on the newly-inserted event rows.
  dayDetail.querySelectorAll('.cal-event-item').forEach(it => {
    it.addEventListener('click', (e) => {
      if (e.target.closest('.cal-event-more')) return;
      const ev = _events.find(x => x.uid === it.dataset.uid);
      if (ev) _showEventForm(ev);
    });
  });
  dayDetail.querySelector('#cal-add-day')?.addEventListener('click', () => _showEventForm(null, _selectedDay));
  _wireQuickDelete(dayDetail);
}

// Step between calendar views by "zoom level" — pinch IN goes year→month→week,
// pinch OUT goes the other way. Agenda is its own thing so it's excluded.
function _zoomView(direction) {
  const chain = ['year', 'month', 'week'];
  const idx = chain.indexOf(_view);
  if (idx < 0) return;
  const next = idx + direction;
  if (next < 0 || next >= chain.length) return;
  _view = chain[next];
  _render();
}

// Monotonic counter bumped on every _render() call. The async per-view
// render functions snapshot this at entry and bail before painting DOM if
// a newer render has already started. Stops fast prev/next/today clicks
// from letting a slow fetch clobber the latest layout.
let _renderToken = 0;
function _isStaleRender(t) { return t !== _renderToken; }

function _render() {
  // Don't rebuild the DOM while the user is typing in quick-add — defer it.
  if (_qaTyping()) { _renderPending = true; return; }
  // Empty state: no calendars configured or connection failed
  if (!_calendars.length) {
    _renderEmpty();
    return;
  }
  _renderToken++;
  // Search now lives inside the day-detail panel and filters in place,
  // so we don't replace the whole calendar body when a query is active.
  // Force a selected day in month/week so the panel (and its search box)
  // is always available.
  if (_searchQuery && (_view === 'month' || _view === 'week') && !_selectedDay) {
    _selectedDay = _today();
  }
  if (_view === 'agenda') _renderAgenda();
  else if (_view === 'year') _renderYear();
  else if (_view === 'week') _renderWeek();
  else _renderMonth();
  // Prefetch adjacent in background after a short delay
  setTimeout(() => _prefetchAdjacent(), 200);
}

function _renderEmpty() {
  const body = document.getElementById('cal-body');
  if (!body) return;
  const hasError = !!_calendarsError;
  body.innerHTML = `
    <div class="cal-empty-state">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3">
        <rect x="3" y="4" width="18" height="18" rx="2"/>
        <line x1="16" y1="2" x2="16" y2="6"/>
        <line x1="8" y1="2" x2="8" y2="6"/>
        <line x1="3" y1="10" x2="21" y2="10"/>
      </svg>
      <div class="cal-empty-title">${hasError ? 'Calendar unavailable' : 'No calendars yet'}</div>
      <div class="cal-empty-msg">${hasError ? _e(_calendarsError) : 'Create a local calendar, import an .ics file, or sync via CalDAV.'}</div>
      ${hasError ? `
        <button class="cal-btn cal-btn-primary" id="cal-goto-settings">Open Settings</button>
      ` : `
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:4px;">
          <button class="cal-btn cal-btn-primary" id="cal-empty-new">New calendar</button>
          <button class="cal-btn" id="cal-empty-import">Import .ics</button>
        </div>
        <div style="margin-top:10px;font-size:11px;opacity:0.55;">Or <a href="#" id="cal-empty-caldav" style="color:var(--accent, var(--red));text-decoration:none;font-weight:600;">set up CalDAV sync</a>.</div>
      `}
    </div>`;
  document.getElementById('cal-goto-settings')?.addEventListener('click', () => {
    closeCalendar();
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.remove('hidden');
      const tab = modal.querySelector('[data-settings-tab="integrations"]');
      if (tab) tab.click();
    }
  });
  // New / Import open the calendar settings panel; the panel already
  // has the "New calendar" button and the .ics file picker. Import
  // triggers the file picker immediately so it's a one-click flow.
  document.getElementById('cal-empty-new')?.addEventListener('click', () => {
    _showCalSettings();
    setTimeout(() => document.getElementById('cal-settings-add')?.click(), 50);
  });
  document.getElementById('cal-empty-import')?.addEventListener('click', () => {
    _showCalSettings();
    setTimeout(() => document.getElementById('cal-import-file')?.click(), 50);
  });
  document.getElementById('cal-empty-caldav')?.addEventListener('click', (e) => {
    e.preventDefault();
    closeCalendar();
    // Integrations is an admin tab — settingsModule.open() only sets
    // the .active class for admin tabs; the actual panel renders via
    // adminModule.open(). Without the admin-first branch the modal
    // appears with Integrations highlighted but showing the previous
    // panel, so the user has to click the tab again to land there.
    if (window.adminModule && typeof window.adminModule.open === 'function') {
      try { window.adminModule.open('integrations'); return; } catch (_) {}
    }
    if (window.settingsModule && typeof window.settingsModule.open === 'function') {
      try { window.settingsModule.open('integrations'); return; } catch (_) {}
    }
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.remove('hidden');
      const tab = modal.querySelector('[data-settings-tab="integrations"]');
      if (tab) tab.click();
    }
  });
}

// ── Header + Filters (shared) ──

function _isoWeekNumber(d) {
  // ISO 8601: weeks start Monday; week 1 contains the year's first Thursday.
  const tgt = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  // Move to Thursday of this week (so the year is determined correctly).
  tgt.setDate(tgt.getDate() + 3 - ((tgt.getDay() + 6) % 7));
  const yearStart = new Date(tgt.getFullYear(), 0, 1);
  return Math.ceil(((tgt - yearStart) / 86400000 + 1) / 7);
}

function _headerHTML() {
  const weekSuffix = _view === 'week'
    ? ` <span class="cal-week-no">W${_isoWeekNumber(_currentDate)}</span>`
    : '';
  return `<div class="cal-toolbar">
    <div class="cal-toolbar-nav">
      <button class="cal-nav" id="cal-prev">&larr;</button>
      <button class="cal-nav cal-today-btn" id="cal-today">Today</button>
      <span class="cal-title">${_view === 'agenda' ? 'Upcoming' : MONTHS[_currentDate.getMonth()] + ' ' + _currentDate.getFullYear()}${weekSuffix}</span>
      <button class="cal-nav" id="cal-next">&rarr;</button>
    </div>
    <div class="cal-toolbar-right">
      <div class="cal-view-toggle">
        ${['week', 'month', 'year', 'agenda'].map(v =>
          `<button class="cal-view-btn${_view === v ? ' active' : ''}" data-view="${v}">${v[0].toUpperCase() + v.slice(1)}</button>`
        ).join('')}
      </div>
      <button class="cal-nav" id="cal-settings" title="Calendar settings" style="position:relative;top:-3px;"><svg width="13" height="13" style="position:relative;top:2px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.68 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></button>
      <button class="cal-nav${window._calSyncing ? ' cal-syncing' : ''}${window._calSyncDone ? ' cal-sync-done' : ''}" id="cal-sync" title="Refresh from database" style="position:relative;top:-3px;">${window._calSyncDone ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>' : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>'}</button>
      ${_filtersToggleHTML()}
      <button class="cal-add-btn cal-add-btn-text" id="cal-add" title="New event"><span class="cal-add-plus">+</span><span class="cal-add-label">New</span></button>
    </div>
  </div>
  <div class="cal-quickadd-row" id="cal-quickadd-row">
    <input
      type="text"
      id="cal-quickadd"
      class="cal-quickadd-input"
      placeholder=" "
      autocomplete="off"
    />
    <span class="cal-quickadd-hint" id="cal-quickadd-hint" aria-hidden="true"><span class="qa-hint-accent">Quick add</span> — return home to Ithaca 1pm tmrw <svg class="qa-hint-enter" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg></span>
    <span class="cal-quickadd-status" id="cal-quickadd-status"></span>
  </div>`;
}

function _filtersData() {
  // Build chip HTML once; reused by toolbar toggle + chip-row renderers.
  let calFilters = '';
  if (_calendars.length > 1) {
    calFilters = _calendars.map(c => {
      const off = _hiddenCals.has(c.href);
      return `<label class="cal-filter-item${off ? ' cal-filter-off' : ''}" data-href="${_e(c.href)}">
        <span class="cal-filter-dot" style="background:${c.color}"></span>${_e(c.name)}</label>`;
    }).join('');
  }
  const presentTypes = new Set(_events.map(e => e.event_type).filter(Boolean));
  const hasUntagged = _events.some(e => !e.event_type);
  const hasImportant = _events.some(e => e.importance === 'high' || e.importance === 'critical');
  if (hasImportant) presentTypes.add('!');
  const typeOrder = ['!', 'work', 'personal', 'health', 'travel', 'meal', 'social', 'admin', 'other'];
  let typeFilters = '';
  for (const t of typeOrder) {
    if (!presentTypes.has(t)) continue;
    const off = (t === '!') ? false : _hiddenTypes.has(t);
    const active = (t === '!') && _onlyImportant;
    const label = t === '!' ? '! important' : t;
    typeFilters += `<label class="cal-filter-item${off ? ' cal-filter-off' : ''}${active ? ' cal-filter-active' : ''}${t === '!' ? ' cal-filter-important' : ''}" data-type="${t}">
      <span class="cal-filter-dot" style="background:${_TYPE_PALETTE[t]}"></span>${label}</label>`;
  }
  if (hasUntagged) {
    const off = _hiddenTypes.has('__untagged__');
    typeFilters += `<label class="cal-filter-item${off ? ' cal-filter-off' : ''}" data-type="__untagged__">
      <span class="cal-filter-dot" style="background:${_TYPE_PALETTE.untagged}"></span>untagged</label>`;
  }
  return { calFilters, typeFilters };
}

function _filtersToggleHTML() {
  // Inline toolbar button only. The chip row renders separately below.
  const { calFilters, typeFilters } = _filtersData();
  if (!calFilters && !typeFilters) return '';
  return `<button class="cal-filter-toggle" id="cal-filter-toggle" title="${_filtersCollapsed ? 'Show filters' : 'Hide filters'}">${_filtersCollapsed ? '+ tags' : '− tags'}</button>`;
}

function _filtersRowHTML() {
  // Chip row beneath the toolbar — empty when collapsed.
  if (_filtersCollapsed) return '';
  const { calFilters, typeFilters } = _filtersData();
  if (!calFilters && !typeFilters) return '';
  const sep = (calFilters && typeFilters) ? '<span style="opacity:0.3;margin:0 4px">·</span>' : '';
  return `<div class="cal-filters">${calFilters}${sep}${typeFilters}</div>`;
}

function _eventVisible(e) {
  if (_hiddenCals.has(e.calendar_href)) return false;
  // "Only important" mode short-circuits category filters: nothing else
  // matters except whether the event itself is high/critical.
  if (_onlyImportant) {
    return e.importance === 'high' || e.importance === 'critical';
  }
  if (e.event_type) {
    if (_hiddenTypes.has(e.event_type)) return false;
  } else if (_hiddenTypes.has('__untagged__')) {
    return false;
  }
  return true;
}

// ── Month View ──

async function _renderMonth() {
  const body = document.getElementById('cal-body');
  if (!body) return;
  const _tk = _renderToken;
  const [rs, re] = _monthRange(_currentDate);
  await _fetchEvents(rs, re);
  if (_isStaleRender(_tk)) return; // newer render already in flight
  const today = _today();
  const y = _currentDate.getFullYear(), m = _currentDate.getMonth();

  const slideClass = _slideDir > 0 ? ' cal-slide-in-right' : _slideDir < 0 ? ' cal-slide-in-left' : '';
  _slideDir = 0;
  let h = _headerHTML() + _filtersRowHTML() + `<div class="cal-grid${slideClass}">`;
  h += '<div class="cal-week-headers">';
  for (const wd of WEEKDAYS) h += `<div class="cal-weekday">${wd}</div>`;
  h += '</div>';

  const first = new Date(y, m, 1);
  const dow = (first.getDay() + 6) % 7;
  const gs = new Date(y, m, 1 - dow);

  const multiDay = _events.filter(e => {
    if (!_eventVisible(e)) return false;
    const startD = new Date(e.dtstart), endD = new Date(e.dtend);
    return Math.round((endD - startD) / 86400000) > 1 || (!e.all_day && _localDateOf(e.dtstart) !== _localDateOf(e.dtend));
  });
  const multiUids = new Set(multiDay.map(e => e.uid));

  // Render 6 week rows. Each row is a positioned container that holds
  // 7 day cells AND any multi-day bars that span the row, drawn as an
  // absolute overlay on top of the cells. This avoids the old "each
  // bar lives inside its start cell and gets clipped at the cell edge"
  // problem so a multi-day event reads as one continuous line across
  // every day it covers.
  for (let row = 0; row < 6; row++) {
    // Count how many multi-day bars overlap any column in this row so
    // cells can reserve top padding for them — otherwise the bars
    // (drawn as absolute overlays) sit on top of the day-number and
    // single-event rows below.
    const rowStartCd0 = new Date(gs); rowStartCd0.setDate(gs.getDate() + row * 7);
    const rowEndCd0 = new Date(gs); rowEndCd0.setDate(gs.getDate() + row * 7 + 6);
    const rowStart0 = _ds(rowStartCd0);
    const rowEnd0 = _ds(rowEndCd0);
    const barsInRow = multiDay.filter(md => {
      const mdStart = _localDateOf(md.dtstart);
      const mdEnd = _localDateOf(md.dtend);
      return !(mdEnd < rowStart0 || mdStart > rowEnd0);
    }).length;
    h += `<div class="cal-week-row" style="--bars:${barsInRow}">`;
    // Day cells for this row
    for (let col = 0; col < 7; col++) {
      const i = row * 7 + col;
      const cd = new Date(gs); cd.setDate(gs.getDate() + i);
      const d = _ds(cd);
      const isOther = cd.getMonth() !== m;
      const cls = 'cal-day' + (isOther ? ' cal-other' : '') + (d === today ? ' cal-today' : '') + (d === _selectedDay ? ' cal-selected' : '');
      h += `<div class="${cls}" data-date="${d}"><span class="cal-day-num">${cd.getDate()}</span>`;
      // Single events — show up to 3 inline rows (multi-day events are
      // drawn separately as an overlay below).
      const singles = _eventsForDay(d).filter(e => !multiUids.has(e.uid));
      if (singles.length) {
        const maxInline = window.innerWidth <= 768 ? 2 : 3;
        const showInline = singles.slice(0, maxInline);
        for (const ev of showInline) {
          const t = ev.all_day ? '' : _fmtTime(ev.dtstart);
          const _impMark = ev.importance === 'critical' ? '<span style="color:var(--red);margin-right:2px" title="critical">!!</span>'
                         : ev.importance === 'high' ? '<span style="color:var(--orange,#e5a33a);margin-right:2px" title="high">!</span>' : '';
          const _typeBadge = ev.event_type ? `<span class="cal-event-type-badge" data-type="${_e(ev.event_type)}" title="${_e(ev.event_type)}"></span>` : '';
          h += `<div class="cal-event-row" draggable="true" data-uid="${_e(ev.uid)}" title="${_e(ev.summary)}${ev.event_type ? ' · ' + ev.event_type : ''}${ev.importance && ev.importance !== 'normal' ? ' · ' + ev.importance : ''}">
            <span class="cal-event-row-dot" style="background:${_calColor(ev)}"></span>
            ${_typeBadge}
            ${t ? `<span class="cal-event-row-time">${t}</span>` : ''}
            <span class="cal-event-row-name">${_impMark}${_e(ev.summary)}</span>
          </div>`;
        }
        if (singles.length > maxInline) h += `<div class="cal-event-more">+${singles.length - maxInline} more</div>`;
      }
      h += '</div>';
    }
    // Multi-day overlay bars for this row. Stack each bar one slot below
    // the previous so two events on the same row don't overlap.
    let barSlot = 0;
    for (const md of multiDay) {
      const mdStart = _localDateOf(md.dtstart);
      const mdEnd = _localDateOf(md.dtend);
      // Compute the row's date range
      const rowStartCd = new Date(gs); rowStartCd.setDate(gs.getDate() + row * 7);
      const rowEndCd = new Date(gs); rowEndCd.setDate(gs.getDate() + row * 7 + 6);
      const rowStart = _ds(rowStartCd);
      const rowEnd = _ds(rowEndCd);
      if (mdEnd < rowStart || mdStart > rowEnd) continue; // not in this row
      // Column within the row where the bar starts and how many days it spans
      const startCol = mdStart < rowStart ? 0 : ((new Date(mdStart + 'T00:00:00') - rowStartCd) / 86400000);
      const endCol   = mdEnd > rowEnd     ? 6 : ((new Date(mdEnd   + 'T00:00:00') - rowStartCd) / 86400000);
      const startColInt = Math.round(startCol);
      const endColInt = Math.round(endCol);
      const span = endColInt - startColInt + 1;
      h += `<div class="cal-multiday" style="--col:${startColInt};--span:${span};--slot:${barSlot};background:${_calColor(md)}" draggable="true" data-uid="${_e(md.uid)}" title="${_e(md.summary)}">${_e(md.summary)}</div>`;
      barSlot++;
    }
    h += '</div>';
  }
  h += '</div>';
  if (_selectedDay) h += _dayDetailHTML(_selectedDay);
  // Capture the grid's scroll position before innerHTML wipes it —
  // selecting a day shouldn't jump the user back to the top of the
  // month, that hides the row they just clicked.
  const _prevGrid = body.querySelector('.cal-grid');
  const _prevScroll = _prevGrid ? _prevGrid.scrollTop : 0;
  // If the user grabbed the quick-add field mid-fetch, skip the swap (which
  // would destroy the focused input + drop the keyboard) and defer until blur.
  if (_qaTyping()) { _renderPending = true; return; }
  body.innerHTML = h;
  const _newGrid = body.querySelector('.cal-grid');
  if (_newGrid && _prevScroll) _newGrid.scrollTop = _prevScroll;
  // On open, scroll today's cell into view so the current date is always
  // visible even when its row sits below the fold (mobile scrolls the grid).
  if (_scrollToTodayOnOpen) {
    _scrollToTodayOnOpen = false;
    const todayCell = body.querySelector('.cal-day.cal-today');
    if (todayCell && _newGrid) {
      requestAnimationFrame(() => {
        try { todayCell.scrollIntoView({ block: 'center', behavior: 'auto' }); }
        catch { _newGrid.scrollTop = Math.max(0, todayCell.offsetTop - _newGrid.clientHeight / 2); }
      });
    }
  }
  _wireAll(body);
  _updateBadge();
}

// ── Week View ──

// Hour-grid week view. Each column is a day; a vertical hour rail on the
// left labels 6am–11pm. Events render as absolute-positioned blocks.
// Drag on an empty cell to scaffold a new event for that range.
// Render the full 24-hour day so events at any hour are reachable.
// On first open the grid auto-scrolls to ~7 AM so the default landing
// still matches the old "morning is visible" behaviour; subsequent
// renders preserve whatever scrollTop the user is on.
const WEEK_HOUR_START = 0;
const WEEK_HOUR_END   = 24;
const WK_DEFAULT_SCROLL_HOUR = 7;
let _wkScrollY = null;       // remembered scroll position across renders
let _wkScrolledOnce = false; // tracks the first auto-scroll-to-morning
// pixel height per hour — user-zoomable, persisted in localStorage so the
// preference sticks across reloads. Bounds keep the layout sane.
const WK_PX_MIN = 28;
const WK_PX_MAX = 120;
const WK_PX_DEFAULT = 64;
let WEEK_HOUR_PX = (() => {
  const saved = parseInt(localStorage.getItem('cal-wk-hour-px') || '', 10);
  return (saved >= WK_PX_MIN && saved <= WK_PX_MAX) ? saved : WK_PX_DEFAULT;
})();
function _wkSetZoom(px) {
  // Capture the hour currently at the top of the viewport so the same
  // hour stays put across the zoom-induced re-render — otherwise the
  // saved pixel scrollTop misaligns at the new px/hour.
  const wrap = document.querySelector('.cal-wk-wrap');
  let _hourAtTop = null;
  if (wrap && WEEK_HOUR_PX) _hourAtTop = wrap.scrollTop / WEEK_HOUR_PX;
  WEEK_HOUR_PX = Math.max(WK_PX_MIN, Math.min(WK_PX_MAX, Math.round(px)));
  try { localStorage.setItem('cal-wk-hour-px', String(WEEK_HOUR_PX)); } catch {}
  if (_hourAtTop != null) _wkScrollY = Math.round(_hourAtTop * WEEK_HOUR_PX);
  if (_view === 'week') _render();
}
function _wkZoomBy(delta) { _wkSetZoom(WEEK_HOUR_PX + delta); }
function _wkHours() { return WEEK_HOUR_END - WEEK_HOUR_START; }

// Round a Y offset (px from top of grid) to the nearest 15-minute slot,
// returns minutes-from-WEEK_HOUR_START.
function _wkPxToMin(y) {
  const totalMin = (y / WEEK_HOUR_PX) * 60;
  return Math.max(0, Math.round(totalMin / 15) * 15);
}
function _wkMinToHHMM(mins) {
  const t = WEEK_HOUR_START * 60 + mins;
  const h = Math.floor(t / 60), m = t % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}
function _wkFormatHourLabel(h) {
  const use12 = (new Date()).toLocaleString().toLowerCase().match(/am|pm/);
  if (!use12) return `${String(h).padStart(2, '0')}:00`;
  const ampm = h < 12 ? 'AM' : 'PM';
  const hh = ((h + 11) % 12) + 1;
  return `${hh} ${ampm}`;
}
function _wkEventTopHeight(ev, dayStr) {
  // Convert event start/end (local) into top/height in px relative to the
  // day's grid origin. Clamp to visible window.
  // The dtstart/dtend strings are like "2026-05-11T09:00:00" (no tz), so
  // pull the time portion directly to avoid TZ math drift; falls back to
  // Date math if the string isn't shaped as expected.
  const _toMin = (iso, fallbackDate) => {
    if (!iso) return null;
    const m = iso.match(/T(\d{2}):(\d{2})/);
    if (m) {
      // If the event spans into a previous/next day, clamp to today's bounds.
      const evDate = iso.slice(0, 10);
      if (evDate < fallbackDate) return 0;             // event started before today
      if (evDate > fallbackDate) return 24 * 60;       // event ends after today
      return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
    }
    // All-day or date-only — treat as start of day.
    return 0;
  };
  const startMin = _toMin(ev.dtstart, dayStr);
  const endMin   = _toMin(ev.dtend, dayStr) ?? (startMin + 60);
  const gridStart = WEEK_HOUR_START * 60;
  const gridEnd   = WEEK_HOUR_END * 60;
  const sMin = Math.max(gridStart, startMin);
  const eMin = Math.min(gridEnd, Math.max(endMin, sMin + 15));
  const top = (sMin - gridStart) * (WEEK_HOUR_PX / 60);
  const height = Math.max(18, (eMin - sMin) * (WEEK_HOUR_PX / 60));
  return { top, height };
}

async function _renderWeek() {
  const body = document.getElementById('cal-body');
  if (!body) return;
  const _tk = _renderToken;
  // Stash current scroll so we can restore after re-render (zoom, drag,
  // etc. all rebuild the body).
  const _prevWrap = body.querySelector('.cal-wk-wrap');
  if (_prevWrap) _wkScrollY = _prevWrap.scrollTop;
  const [rs, re] = _weekRange(_currentDate);
  await _fetchEvents(rs, re);
  if (_isStaleRender(_tk)) return;
  const today = _today();
  const ws = new Date(rs + 'T00:00:00');

  // Build day list once (used for both all-day strip and grid).
  const days = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(ws); d.setDate(ws.getDate() + i);
    days.push({ d, ds: _ds(d), idx: i });
  }

  // Hour rail on the left. The spacer up top hosts the zoom controls
  // (toolbar is already crowded — this empty 56-px corner is a free home).
  let railHtml = `<div class="cal-wk-rail">
    <div class="cal-wk-rail-spacer">
      <button class="cal-wk-zoom" id="cal-wk-zoom-out" title="Zoom out (–)" aria-label="Zoom out">−</button>
      <button class="cal-wk-zoom" id="cal-wk-zoom-in" title="Zoom in (+)" aria-label="Zoom in">+</button>
    </div>`;
  for (let h = WEEK_HOUR_START; h < WEEK_HOUR_END; h++) {
    railHtml += `<div class="cal-wk-rail-cell" style="height:${WEEK_HOUR_PX}px;"><span>${_wkFormatHourLabel(h)}</span></div>`;
  }
  railHtml += '</div>';

  // Day columns
  let colsHtml = '<div class="cal-wk-cols">';
  for (const { d, ds, idx } of days) {
    const isToday = ds === today;
    const allDayEvents = _eventsForDay(ds).filter(e => _eventVisible(e) && e.all_day);
    const timedEvents  = _eventsForDay(ds).filter(e => _eventVisible(e) && !e.all_day);

    const isSun = d.getDay() === 0;
    colsHtml += `<div class="cal-wk-col${isToday ? ' cal-wk-today' : ''}${isSun ? ' cal-wk-sun' : ''}" data-date="${ds}">`;
    colsHtml += `<div class="cal-wk-col-head"><span class="cal-wk-dn">${WEEKDAYS[idx]}</span><span class="cal-wk-dt">${d.getDate()}</span></div>`;
    // All-day strip
    colsHtml += `<div class="cal-wk-allday">`;
    for (const ev of allDayEvents) {
      colsHtml += `<div class="cal-wk-allday-event" data-uid="${_e(ev.uid)}" style="background:${_calColor(ev)};" title="${_e(ev.summary)}">${_e(ev.summary)}</div>`;
    }
    colsHtml += `</div>`;
    // Hour-grid body
    colsHtml += `<div class="cal-wk-grid" data-date="${ds}" style="height:${_wkHours() * WEEK_HOUR_PX}px;">`;
    // Hour cell lines
    for (let h = WEEK_HOUR_START; h < WEEK_HOUR_END; h++) {
      colsHtml += `<div class="cal-wk-cell" data-hour="${h}" style="height:${WEEK_HOUR_PX}px;"></div>`;
    }
    // Now-line indicator (only on today)
    if (isToday) {
      const now = new Date();
      const minSinceStart = (now.getHours() - WEEK_HOUR_START) * 60 + now.getMinutes();
      if (minSinceStart >= 0 && minSinceStart <= _wkHours() * 60) {
        const top = minSinceStart * (WEEK_HOUR_PX / 60);
        colsHtml += `<div class="cal-wk-now" style="top:${top}px;"></div>`;
      }
    }
    // Timed event blocks. Each block carries a 6-px bottom-edge handle
    // for drag-to-resize (extend duration without opening the form).
    for (const ev of timedEvents) {
      const { top, height } = _wkEventTopHeight(ev, ds);
      const t = _fmtTime(ev.dtstart) + '–' + _fmtTime(ev.dtend);
      // Custom-bg events get the image as the tile background; solid-color
      // events keep the original tinted treatment.
      let bgDecl;
      if (_isCalBgImage(ev.color)) {
        const _url = _calBgImageUrl(ev.color).replace(/'/g, "\\'");
        bgDecl = `background-image: linear-gradient(color-mix(in srgb, var(--bg) 55%, transparent), color-mix(in srgb, var(--bg) 55%, transparent)), url('${_url}'); background-size: cover; background-position: center;`;
      } else {
        bgDecl = `background:color-mix(in srgb, ${_calColor(ev)} 18%, var(--bg));`;
      }
      colsHtml += `<div class="cal-wk-block" data-uid="${_e(ev.uid)}" style="top:${top}px;height:${height}px;border-left-color:${_calColor(ev)};${bgDecl}">`;
      colsHtml += `<div class="cal-wk-block-name">${_e(ev.summary)}</div>`;
      colsHtml += `<div class="cal-wk-block-time">${t}</div>`;
      colsHtml += `<div class="cal-wk-block-resize" title="Drag to resize"></div>`;
      colsHtml += `</div>`;
    }
    colsHtml += `</div></div>`;  // /cal-wk-grid /cal-wk-col
  }
  colsHtml += '</div>';

  let h = _headerHTML() + _filtersRowHTML();
  h += `<div class="cal-wk-wrap">${railHtml}${colsHtml}</div>`;
  if (_selectedDay) h += _dayDetailHTML(_selectedDay);
  // If the user grabbed the quick-add field mid-fetch, skip the swap (which
  // would destroy the focused input + drop the keyboard) and defer until blur.
  if (_qaTyping()) { _renderPending = true; return; }
  body.innerHTML = h;
  _wireAll(body);

  // Single click (tap) an event block → open edit form. A drag-to-move or
  // drag-to-resize sets `justResized` in its mouseup so the trailing click
  // doesn't also open the form; the bottom-edge resize handle is ignored too.
  body.querySelectorAll('.cal-wk-block, .cal-wk-allday-event').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('cal-wk-block-resize')) return;
      if (el.dataset.justResized) { delete el.dataset.justResized; return; }
      e.stopPropagation();
      const ev = _events.find(x => x.uid === el.dataset.uid);
      if (ev) _showEventForm(ev);
    });
  });

  // Drag the body of a block to reschedule (different day or time). The
  // bottom-edge handle has its own gesture (resize) and stops here, so
  // the two never fight. Same duration is preserved.
  body.querySelectorAll('.cal-wk-block').forEach(block => {
    block.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target.classList.contains('cal-wk-block-resize')) return; // resize wins
      e.preventDefault();
      const uid = block.dataset.uid;
      const ev = _events.find(x => x.uid === uid);
      if (!ev) return;
      const cols = Array.from(body.querySelectorAll('.cal-wk-grid'));
      if (!cols.length) return;
      // Original timing
      const m1 = (ev.dtstart || '').match(/T(\d{2}):(\d{2})/);
      const m2 = (ev.dtend || '').match(/T(\d{2}):(\d{2})/);
      const startMin0 = m1 ? parseInt(m1[1], 10) * 60 + parseInt(m1[2], 10) : 0;
      const endMin0   = m2 ? parseInt(m2[1], 10) * 60 + parseInt(m2[2], 10) : startMin0 + 60;
      const durationMin = Math.max(15, endMin0 - startMin0);

      // Where did the cursor grab the block? (offset from block-top in px)
      const blockRect = block.getBoundingClientRect();
      const grabOffsetPx = e.clientY - blockRect.top;

      // Ghost that follows the cursor across columns.
      const ghost = block.cloneNode(true);
      ghost.classList.add('cal-wk-block-ghost');
      ghost.style.pointerEvents = 'none';
      ghost.style.opacity = '0.85';
      ghost.querySelector('.cal-wk-block-resize')?.remove();
      // Mute the original while dragging.
      block.style.opacity = '0.25';

      let nextDs = null;
      let nextStartMin = startMin0;
      let activeGrid = null;
      let moved = false;
      const _attachGhost = (grid) => {
        if (activeGrid === grid) return;
        activeGrid = grid;
        grid.appendChild(ghost);
      };
      const onMove = (mv) => {
        moved = true;
        // Pick the column under the cursor. If the cursor lands between
        // columns (gutter/border) or just outside the grid horizontally,
        // snap to the nearest column instead of giving up — that's why
        // horizontal cross-day drag could feel stuck before.
        let cur = cols.find(c => {
          const r = c.getBoundingClientRect();
          return mv.clientX >= r.left && mv.clientX <= r.right;
        });
        if (!cur) {
          let best = null, bestDist = Infinity;
          for (const c of cols) {
            const r = c.getBoundingClientRect();
            const cx = (r.left + r.right) / 2;
            const d = Math.abs(mv.clientX - cx);
            if (d < bestDist) { bestDist = d; best = c; }
          }
          cur = best;
        }
        if (!cur) return;
        _attachGhost(cur);
        const r = cur.getBoundingClientRect();
        const yIn = Math.max(0, Math.min(cur.clientHeight, mv.clientY - r.top));
        // Subtract the grab offset so the cursor stays at the same spot
        // inside the block as you drag it around.
        const blockTopY = yIn - grabOffsetPx;
        const snapMin = Math.max(0, Math.round(_wkPxToMin(blockTopY) / 15) * 15);
        nextStartMin = WEEK_HOUR_START * 60 + snapMin;
        nextDs = cur.dataset.date;
        const top = (nextStartMin - WEEK_HOUR_START * 60) * (WEEK_HOUR_PX / 60);
        const height = durationMin * (WEEK_HOUR_PX / 60);
        ghost.style.top = top + 'px';
        ghost.style.height = height + 'px';
        const hh = String(Math.floor(nextStartMin / 60)).padStart(2, '0');
        const mm = String(nextStartMin % 60).padStart(2, '0');
        const hh2 = String(Math.floor((nextStartMin + durationMin) / 60)).padStart(2, '0');
        const mm2 = String((nextStartMin + durationMin) % 60).padStart(2, '0');
        const timeEl = ghost.querySelector('.cal-wk-block-time');
        if (timeEl) timeEl.textContent = `${hh}:${mm}–${hh2}:${mm2}`;
      };
      const onUp = async (up) => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        ghost.remove();
        block.style.opacity = '';
        // Only suppress the trailing click-open if the user actually dragged —
        // a plain click (no movement) must still open the event.
        if (moved) block.dataset.justResized = '1';
        // Decide whether anything actually moved.
        const oldDs = (ev.dtstart || '').slice(0, 10);
        if (!nextDs) return;
        if (nextDs === oldDs && nextStartMin === startMin0) return;
        // Snapshot the original times so we can offer an Undo.
        const prevDtstart = ev.dtstart;
        const prevDtend = ev.dtend;
        const newEndMin = nextStartMin + durationMin;
        const hh = String(Math.floor(nextStartMin / 60)).padStart(2, '0');
        const mm = String(nextStartMin % 60).padStart(2, '0');
        const hh2 = String(Math.floor(newEndMin / 60)).padStart(2, '0');
        const mm2 = String((newEndMin) % 60).padStart(2, '0');
        const _tz = _tzOffset();
        const newDtstart = `${nextDs}T${hh}:${mm}:00${_tz}`;
        const newDtend   = `${nextDs}T${hh2}:${mm2}:00${_tz}`;
        try {
          await _updateEvent(uid, { dtstart: newDtstart, dtend: newDtend });
          _render();
          _showCalUndoToast('Moved event', async () => {
            try {
              await _updateEvent(uid, { dtstart: prevDtstart, dtend: prevDtend });
              _render();
            } catch (err) { console.error('Undo failed:', err); }
          });
        } catch {
          _render();
        }
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });

  // Drag the bottom edge of a timed block to extend / shrink the event.
  // Snaps to 15-min increments; releases with a PUT to /api/calendar/events.
  body.querySelectorAll('.cal-wk-block .cal-wk-block-resize').forEach(handle => {
    handle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      const block = handle.closest('.cal-wk-block');
      const grid = block.parentElement;
      const ds = grid.dataset.date;
      const uid = block.dataset.uid;
      const ev = _events.find(x => x.uid === uid);
      if (!ev || !grid || !ds) return;
      const startMin = (() => {
        const m = (ev.dtstart || '').match(/T(\d{2}):(\d{2})/);
        return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : 0;
      })();
      const initialTop = parseFloat(block.style.top || '0');
      const gridRect = grid.getBoundingClientRect();
      let newEndMin = startMin;
      let resized = false;
      const onMove = (mv) => {
        resized = true;
        const y = Math.max(0, Math.min(grid.clientHeight, mv.clientY - gridRect.top));
        // Snap to 15-min increments; enforce a 15-min minimum duration.
        newEndMin = Math.max(startMin + 15, Math.round(_wkPxToMin(y) / 15) * 15);
        const newHeight = Math.max(18, (newEndMin - startMin) * (WEEK_HOUR_PX / 60));
        block.style.height = newHeight + 'px';
        const timeEl = block.querySelector('.cal-wk-block-time');
        if (timeEl) {
          const hh = String(Math.floor(newEndMin / 60)).padStart(2, '0');
          const mm = String(newEndMin % 60).padStart(2, '0');
          timeEl.textContent = `${_fmtTime(ev.dtstart)}–${hh}:${mm}`;
        }
      };
      const onUp = async () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (resized) block.dataset.justResized = '1';
        if (newEndMin === startMin) return;
        const prevDtend = ev.dtend;
        const hh = String(Math.floor(newEndMin / 60)).padStart(2, '0');
        const mm = String(newEndMin % 60).padStart(2, '0');
        const newDtend = `${ds}T${hh}:${mm}:00${_tzOffset()}`;
        try {
          await _updateEvent(uid, { dtend: newDtend });
          _render();
          _showCalUndoToast('Resized event', async () => {
            try {
              await _updateEvent(uid, { dtend: prevDtend });
              _render();
            } catch (err) { console.error('Undo failed:', err); }
          });
        } catch (err) {
          // Roll back the visual on failure
          _render();
        }
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });

  // Drag-to-create on empty grid: mousedown on a cell, drag down, release.
  body.querySelectorAll('.cal-wk-grid').forEach(grid => {
    grid.addEventListener('mousedown', (e) => {
      // Don't start a drag-create when the press lands on an existing event.
      if (e.target.closest('.cal-wk-block')) return;
      if (e.button !== 0) return;
      e.preventDefault();
      const rect = grid.getBoundingClientRect();
      const ds = grid.dataset.date;
      const startY = e.clientY - rect.top;
      const ghost = document.createElement('div');
      ghost.className = 'cal-wk-ghost';
      grid.appendChild(ghost);
      const onMove = (mv) => {
        const y2 = Math.max(0, Math.min(grid.clientHeight, mv.clientY - rect.top));
        const y1 = Math.min(startY, y2);
        const yEnd = Math.max(startY, y2);
        const startMin = _wkPxToMin(y1);
        const endMin = Math.max(_wkPxToMin(yEnd), startMin + 15);
        ghost.style.top = (startMin / 60) * WEEK_HOUR_PX + 'px';
        ghost.style.height = ((endMin - startMin) / 60) * WEEK_HOUR_PX + 'px';
        ghost.dataset.start = _wkMinToHHMM(startMin);
        ghost.dataset.end = _wkMinToHHMM(endMin);
        ghost.textContent = `${ghost.dataset.start} – ${ghost.dataset.end}`;
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        const startHHMM = ghost.dataset.start;
        const endHHMM = ghost.dataset.end;
        ghost.remove();
        if (!startHHMM || !endHHMM) return;
        // Open the bespoke event form pre-filled with this slot.
        _showEventFormForRange(ds, startHHMM, endHHMM);
      };
      onMove(e);
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });

  // Restore scroll. Default-land at WK_DEFAULT_SCROLL_HOUR the first time
  // week view opens; afterwards keep the user's last position.
  const _wrap = body.querySelector('.cal-wk-wrap');
  if (_wrap) {
    if (_wkScrollY != null) {
      _wrap.scrollTop = _wkScrollY;
    } else if (!_wkScrolledOnce) {
      _wrap.scrollTop = WK_DEFAULT_SCROLL_HOUR * WEEK_HOUR_PX;
      _wkScrolledOnce = true;
    }
  }

  // Zoom buttons in the rail-spacer corner.
  document.getElementById('cal-wk-zoom-in')?.addEventListener('click', (e) => { e.stopPropagation(); _wkZoomBy(+12); });
  document.getElementById('cal-wk-zoom-out')?.addEventListener('click', (e) => { e.stopPropagation(); _wkZoomBy(-12); });

  // Keyboard zoom (`+` / `-`), Ctrl/Cmd-wheel zoom — both only fire while
  // we're in week view and no text input has focus.
  if (!body._wkZoomKeysWired) {
    body._wkZoomKeysWired = true;
    document.addEventListener('keydown', (e) => {
      if (_view !== 'week') return;
      const tag = (document.activeElement?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.key === '+' || e.key === '=' ) { e.preventDefault(); _wkZoomBy(+12); }
      else if (e.key === '-' || e.key === '_') { e.preventDefault(); _wkZoomBy(-12); }
      else if (e.key === '0') { e.preventDefault(); _wkSetZoom(WK_PX_DEFAULT); }
    });
  }
  body.querySelector('.cal-wk-wrap')?.addEventListener('wheel', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    _wkZoomBy(e.deltaY < 0 ? +8 : -8);
  }, { passive: false });

  _updateBadge();
}

function _showEventFormForRange(ds, startHHMM, endHHMM) {
  // Open the new-event form, then seed the time inputs with the dragged
  // range and force the details panel open so the user can see/adjust.
  _showEventForm(null, ds, ds);
  requestAnimationFrame(() => {
    const startEl = document.getElementById('cal-f-start');
    const endEl   = document.getElementById('cal-f-end');
    if (startEl) startEl.value = startHHMM;
    if (endEl)   endEl.value   = endHHMM;
    startEl?.dispatchEvent(new Event('input'));
    // Auto-expand details so the time fields are visible when someone
    // arrived here via drag-to-create rather than the +New button.
    document.querySelector('.cal-form-bespoke')?.classList.add('is-expanded');
    const details = document.getElementById('cal-form-details');
    if (details) details.setAttribute('aria-hidden', 'false');
  });
}

// ── Agenda View ──

async function _renderAgenda() {
  const body = document.getElementById('cal-body');
  if (!body) return;
  const _tk = _renderToken;
  // Fetch 3 months forward from current date
  const s = _ds(_currentDate);
  const eDate = new Date(_currentDate); eDate.setMonth(eDate.getMonth() + 3);
  const e = _ds(eDate);
  await _fetchEvents(s, e);
  if (_isStaleRender(_tk)) return;

  // Filter + group by date
  const visible = _events.filter(ev => !!_eventVisible(ev))
    .sort((a, b) => a.dtstart < b.dtstart ? -1 : 1);

  let h = _headerHTML() + _filtersRowHTML() + '<div class="cal-agenda">';
  // Group events by local date, then always surface today (when it's inside
  // the agenda window) even if it has no events, so the user can see "today".
  const byDate = new Map();
  for (const ev of visible) {
    const d = _localDateOf(ev.dtstart);
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(ev);
  }
  const today = _today();
  if (today >= s && today <= e && !byDate.has(today)) byDate.set(today, []);
  const dates = [...byDate.keys()].sort();

  if (!dates.length) {
    // Empty-state mirrors the email panel: short message + a Settings ›
    // Integrations link to set up CalDAV, OR a quick "Create event" action.
    h += '<div class="cal-empty" style="display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;">' +
      '<span>No upcoming events</span>' +
      '<span style="opacity:0.7;font-size:11px;">' +
        '<a href="#" data-cal-open-settings="integrations" style="color:var(--accent,var(--red));text-decoration:underline;">Settings &rsaquo; Integrations</a>' +
        ' &middot; ' +
        '<a href="#" data-cal-create-event="1" style="color:var(--accent,var(--red));text-decoration:underline;">Create event</a>' +
      '</span>' +
    '</div>';
  } else {
    for (const date of dates) {
      const evs = byDate.get(date);
      const todayBadge = (date === today) ? ' <span class="cal-agenda-today-badge">Today</span>' : '';
      h += `<div class="cal-agenda-day${date === today ? ' is-today' : ''}"><div class="cal-agenda-date">${_fmtDate(date)}${todayBadge}</div>`;
      if (!evs.length) {
        h += '<div class="cal-agenda-empty">No events</div>';
      }
      for (const ev of evs) {
        const t = ev.all_day ? 'All day' : _fmtTime(ev.dtstart) + ' – ' + _fmtTime(ev.dtend);
        const _typeTag = ev.event_type
          ? `<span class="cal-event-tag" style="color:${_TYPE_PALETTE[ev.event_type] || _TYPE_PALETTE.other};border-color:${_TYPE_PALETTE[ev.event_type] || _TYPE_PALETTE.other}">#${_e(ev.event_type)}</span>`
          : '';
        const _impMark = ev.importance === 'critical' ? '<span style="color:var(--red);margin-right:4px" title="critical">!!</span>'
                       : ev.importance === 'high' ? '<span style="color:var(--orange,#e5a33a);margin-right:4px" title="high">!</span>' : '';
        h += `<div class="cal-agenda-event" data-uid="${_e(ev.uid)}">
          <div class="cal-event-dot" style="background:${_calColor(ev)}"></div>
          <div class="cal-event-info">
            <div class="cal-event-name">${_impMark}${_e(ev.summary)} ${_typeTag}</div>
            <div class="cal-event-time">${t}${ev.location ? ' · ' + _locHTML(ev.location) : ''}</div>
          </div>
          <button class="cal-event-more" data-uid="${_e(ev.uid)}" title="More">${_moreIcon}</button>
        </div>`;
      }
      h += '</div>';
    }
  }
  h += '</div>';
  // If the user grabbed the quick-add field mid-fetch, skip the swap (which
  // would destroy the focused input + drop the keyboard) and defer until blur.
  if (_qaTyping()) { _renderPending = true; return; }
  body.innerHTML = h;
  _wireAll(body);
  _wireQuickDelete(body);
  body.querySelectorAll('.cal-agenda-event').forEach(el => el.addEventListener('click', (e) => {
    if (e.target.closest('.cal-event-more')) return;
    const ev = _events.find(e => e.uid === el.dataset.uid);
    if (ev) _showEventForm(ev);
  }));
  // Empty-state links: Settings › Integrations + Create event.
  body.querySelector('[data-cal-open-settings]')?.addEventListener('click', (e) => {
    e.preventDefault();
    closeCalendar();
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.remove('hidden');
      const tab = modal.querySelector('[data-settings-tab="integrations"]');
      if (tab) tab.click();
    }
  });
  body.querySelector('[data-cal-create-event]')?.addEventListener('click', (e) => {
    e.preventDefault();
    _showEventForm(null);
  });
  _updateBadge();
}

// ── Search View ──

async function _renderSearch() {
  const body = document.getElementById('cal-body');
  if (!body) return;
  // Search across all events in pool (no fetch needed — use what we have)
  const q = _searchQuery.toLowerCase();
  const results = Object.values(_allEvents)
    .filter(ev => !!_eventVisible(ev))
    .filter(ev =>
      (ev.summary || '').toLowerCase().includes(q) ||
      (ev.description || '').toLowerCase().includes(q) ||
      (ev.location || '').toLowerCase().includes(q)
    )
    .sort((a, b) => a.dtstart < b.dtstart ? -1 : 1);

  let h = _headerHTML() + _filtersRowHTML() + '<div class="cal-search-results">';
  h += `<div class="cal-search-count">${results.length} result${results.length !== 1 ? 's' : ''} for "${_e(_searchQuery)}"</div>`;
  if (!results.length) {
    h += '<div class="cal-empty">No events match your search</div>';
  } else {
    for (const ev of results) {
      const evDate = _localDateOf(ev.dtstart);
      const t = ev.all_day ? 'All day' : _fmtTime(ev.dtstart) + ' – ' + _fmtTime(ev.dtend);
      h += `<div class="cal-agenda-event" data-uid="${_e(ev.uid)}">
        <div class="cal-event-dot" style="background:${_calColor(ev)}"></div>
        <div class="cal-event-info">
          <div class="cal-event-name">${_e(ev.summary)}</div>
          <div class="cal-event-time">${_fmtDate(evDate)} · ${t}${ev.location ? ' · ' + _locHTML(ev.location) : ''}</div>
        </div>
        <button class="cal-event-more" data-uid="${_e(ev.uid)}" title="More">${_moreIcon}</button>
      </div>`;
    }
  }
  h += '</div>';
  // If the user grabbed the quick-add field mid-fetch, skip the swap (which
  // would destroy the focused input + drop the keyboard) and defer until blur.
  if (_qaTyping()) { _renderPending = true; return; }
  body.innerHTML = h;
  _wireAll(body);
  _wireQuickDelete(body);
  body.querySelectorAll('.cal-agenda-event').forEach(el => el.addEventListener('click', (e) => {
    if (e.target.closest('.cal-event-more')) return;
    const ev = _allEvents[el.dataset.uid];
    if (ev) _showEventForm(ev);
  }));
  // Focus search input after re-render
  const searchInput = document.getElementById('cal-search');
  if (searchInput && document.activeElement !== searchInput) {
    searchInput.focus();
    searchInput.setSelectionRange(searchInput.value.length, searchInput.value.length);
  }
}

// ── Year View ──

async function _renderYear() {
  const body = document.getElementById('cal-body');
  if (!body) return;
  const _tk = _renderToken;
  const y = _currentDate.getFullYear();
  await _fetchEvents(`${y}-01-01`, `${y + 1}-01-01`);
  if (_isStaleRender(_tk)) return;
  const today = _today();

  let h = _headerHTML() + _filtersRowHTML() + '<div class="cal-year">';
  for (let m = 0; m < 12; m++) {
    h += `<div class="cal-year-month" data-month="${m}"><div class="cal-year-month-title">${MON_SHORT[m]}</div>`;
    h += '<div class="cal-year-grid">';
    for (const wd of ['M', 'T', 'W', 'T', 'F', 'S', 'S']) h += `<div class="cal-year-wd">${wd}</div>`;
    const first = new Date(y, m, 1);
    const dow = (first.getDay() + 6) % 7;
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    for (let p = 0; p < dow; p++) h += '<div class="cal-year-cell"></div>';
    for (let d = 1; d <= daysInMonth; d++) {
      const ds = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const evs = _eventsForDay(ds);
      const isToday = ds === today;
      let cls = 'cal-year-cell cal-year-day';
      if (isToday) cls += ' cal-year-today';
      if (evs.length) cls += ' cal-year-has';
      h += `<div class="${cls}" data-date="${ds}" title="${evs.length ? evs.length + ' event' + (evs.length > 1 ? 's' : '') : ''}">${d}</div>`;
    }
    h += '</div></div>';
  }
  h += '</div>';
  // If the user grabbed the quick-add field mid-fetch, skip the swap (which
  // would destroy the focused input + drop the keyboard) and defer until blur.
  if (_qaTyping()) { _renderPending = true; return; }
  body.innerHTML = h;
  _wireAll(body);
  // Month box click → jump to month view (but not when clicking a specific day)
  body.querySelectorAll('.cal-year-month').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('.cal-year-day')) return;
      const m = parseInt(el.dataset.month);
      _currentDate = new Date(_currentDate.getFullYear(), m, 1);
      _view = 'month';
      _render();
    });
  });
  // Day click in year view → jump to month
  body.querySelectorAll('.cal-year-day').forEach(el => {
    el.addEventListener('click', () => {
      const d = el.dataset.date;
      _currentDate = new Date(d + 'T00:00:00');
      _selectedDay = d;
      _view = 'month';
      _render();
    });
  });
  _updateBadge();
}

// ── Shared HTML builders ──

function _dayDetailHTML(dateStr) {
  const isToday = dateStr === _today();
  // Search lives inside the day panel now — typing filters the panel
  // body to global search results instead of just this day's events.
  // Magnifying-glass icon inside the search field via a wrapper + padding-left.
  const searchInput = `<div class="cal-search-wrap">
    <svg class="cal-search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg>
    <input type="search" class="cal-search-input cal-day-search" id="cal-search" placeholder="Search all events…" value="${_e(_searchQuery)}" />
  </div>`;
  let h = `<div class="cal-splitter" role="separator" aria-orientation="horizontal" tabindex="0" title="Drag to resize"><div class="cal-splitter-grip"></div></div>
    <div class="cal-day-detail">
    ${searchInput}
    <div class="cal-detail-header">
      <span>${_fmtDate(dateStr)}${isToday ? ' <span style="color:var(--accent, var(--red));font-weight:600;">(Today)</span>' : ''}</span>
      <button class="cal-add-btn cal-add-btn-text cal-add-btn-sm" id="cal-add-day" title="New event"><span class="cal-add-plus">+</span><span class="cal-add-label">New</span></button>
    </div>`;
  if (_searchQuery) {
    const q = _searchQuery.toLowerCase();
    const results = _events
      .filter(_eventVisible)
      .filter(e =>
        (e.summary || '').toLowerCase().includes(q) ||
        (e.description || '').toLowerCase().includes(q) ||
        (e.location || '').toLowerCase().includes(q)
      )
      .sort((a, b) => (a.dtstart || '').localeCompare(b.dtstart || ''));
    h += `<div class="cal-day-search-meta">${results.length} result${results.length !== 1 ? 's' : ''}</div>`;
    if (!results.length) {
      h += '<div class="cal-empty">No events match</div>';
    } else {
      results.forEach(ev => {
        const date = ev.all_day ? ev.dtstart : _localDateOf(ev.dtstart);
        const t = ev.all_day ? 'All day' : _fmtTime(ev.dtstart) + ' – ' + _fmtTime(ev.dtend);
        const bgStyle = _calItemBgStyle(ev);
        h += `<div class="cal-event-item${bgStyle ? ' cal-event-item-bg' : ''}" data-uid="${_e(ev.uid)}"${bgStyle ? ` style="${bgStyle}"` : ''}>
          <div class="cal-event-dot" style="background:${_calColor(ev)}"></div>
          <div class="cal-event-info">
            <div class="cal-event-name">${_e(ev.summary)}</div>
            <div class="cal-event-time">${_fmtDate(date)} · ${t}</div>
            ${ev.location ? `<div class="cal-event-loc">${_locHTML(ev.location)}</div>` : ''}
          </div>
          <button class="cal-event-more" data-uid="${_e(ev.uid)}" title="More">${_moreIcon}</button>
        </div>`;
      });
    }
    return h + '</div>';
  }
  const evs = _eventsForDay(dateStr);
  if (!evs.length) h += '<div class="cal-empty">No events</div>';
  else evs.forEach(ev => {
    const t = ev.all_day ? 'All day' : _fmtTime(ev.dtstart) + ' – ' + _fmtTime(ev.dtend);
    const _bgStyle = _calItemBgStyle(ev);
    h += `<div class="cal-event-item${_bgStyle ? ' cal-event-item-bg' : ''}" data-uid="${_e(ev.uid)}"${_bgStyle ? ` style="${_bgStyle}"` : ''}><div class="cal-event-dot" style="background:${_calColor(ev)}"></div><div class="cal-event-info"><div class="cal-event-name">${_e(ev.summary)}</div><div class="cal-event-time">${t}</div>${ev.location ? `<div class="cal-event-loc">${_locHTML(ev.location)}</div>` : ''}</div><button class="cal-event-more" data-uid="${_e(ev.uid)}" title="More">${_moreIcon}</button></div>`;
  });
  return h + '</div>';
}

// ── Wire all common listeners ──

function _wireAll(body) {
  // ── Day-detail splitter (drag to resize) ────────────────────────
  // Restores the saved height each render so the user's choice survives
  // navigation between months/weeks. Drag adjusts a single CSS variable
  // on #cal-body — the grid clamps its height and the day-detail expands
  // / contracts accordingly via CSS rules.
  try {
    const calBody = document.getElementById('cal-body');
    const splitter = body.querySelector('.cal-splitter');
    if (calBody && splitter) {
      // Only seed from localStorage on the first wire-up. Subsequent
      // renders (every keystroke when the user is typing in search)
      // would otherwise clobber an in-progress focus-expand and bounce
      // the day-detail pane up and down on every character.
      const alreadySet = calBody.style.getPropertyValue('--cal-detail-h');
      if (!alreadySet) {
        const saved = parseInt(localStorage.getItem('odysseus.cal.detailH') || '0', 10);
        if (saved && saved > 80) calBody.style.setProperty('--cal-detail-h', saved + 'px');
      }
      let startY = 0, startH = 240, dragging = false;
      const onMove = (ev) => {
        if (!dragging) return;
        const y = ev.touches ? ev.touches[0].clientY : ev.clientY;
        // Drag UP (smaller y) → bigger day-detail. Allow the pane to grow
        // all the way to the top of the visible viewport so the user can
        // hide the calendar entirely. We leave ~24px headroom so the
        // splitter handle itself stays grabbable to drag back down.
        const vh = (window.visualViewport?.height) || window.innerHeight;
        const newH = Math.max(40, Math.min(vh - 24, startH + (startY - y)));
        calBody.style.setProperty('--cal-detail-h', newH + 'px');
      };
      const onUp = () => {
        if (!dragging) return;
        dragging = false;
        splitter.classList.remove('cal-splitter-dragging');
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onUp);
        const cur = calBody.style.getPropertyValue('--cal-detail-h');
        const px = parseInt(cur, 10);
        if (px) { try { localStorage.setItem('odysseus.cal.detailH', String(px)); } catch {} }
      };
      const onDown = (ev) => {
        ev.preventDefault();
        dragging = true;
        splitter.classList.add('cal-splitter-dragging');
        startY = ev.touches ? ev.touches[0].clientY : ev.clientY;
        const detail = body.querySelector('.cal-day-detail');
        startH = detail ? detail.getBoundingClientRect().height : 240;
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp, { once: false });
        document.addEventListener('touchmove', onMove, { passive: false });
        document.addEventListener('touchend', onUp);
      };
      splitter.addEventListener('pointerdown', onDown);
      splitter.addEventListener('touchstart', onDown, { passive: false });

      // Double-tap (or double-click) the splitter to reset the day-detail
      // pane to its CSS default height.
      let _lastTap = 0;
      const resetSplit = () => {
        calBody.style.removeProperty('--cal-detail-h');
        try { localStorage.removeItem('odysseus.cal.detailH'); } catch {}
      };
      splitter.addEventListener('dblclick', resetSplit);
      splitter.addEventListener('touchend', () => {
        const now = Date.now();
        if (now - _lastTap < 320) {
          resetSplit();
          _lastTap = 0;
        } else {
          _lastTap = now;
        }
      });
    }
  } catch {}

  // ── Quick-add input ─────────────────────────────────────────────
  const _qaInput = document.getElementById('cal-quickadd');
  const _qaStatus = document.getElementById('cal-quickadd-status');
  if (_qaInput && !_qaInput._wired) {
    _qaInput._wired = true;
    const _submitQA = async () => {
      const text = _qaInput.value.trim();
      if (!text || _qaSubmitting) return;
      // Use a flag rather than `disabled` to block double-submit — disabling
      // the input blurs it, which would flush a deferred render and wipe the
      // spinner's container mid-parse.
      _qaSubmitting = true;
      // Whirlpool spinner after the text — but only once parsing has run long
      // enough to be worth showing (~250ms), so fast parses don't flash it.
      let _qaSpin = null;
      let _qaSpinTimer = null;
      if (_qaStatus) {
        _qaStatus.textContent = '';
        try {
          const sp = (await import('./spinner.js')).default;
          _qaSpinTimer = setTimeout(() => {
            _qaSpin = sp.createWhirlpool(14);
            _qaSpin.element.style.cssText = 'display:inline-block;vertical-align:middle;position:relative;top:1px;left:-2px;margin-left:4px;';
            _qaStatus.appendChild(_qaSpin.element);
          }, 250);
        } catch {
          _qaSpinTimer = setTimeout(() => { if (_qaStatus) _qaStatus.textContent = 'parsing…'; }, 250);
        }
      }
      try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        const res = await fetch(`${API_BASE}/api/calendar/quick-parse`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, tz }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          if (_qaStatus) _qaStatus.textContent = '';
          uiModule.showError('Quick-add: ' + (data.error || data.detail || `HTTP ${res.status}`));
          return;
        }
        // Open the bespoke event form, then push the parsed fields in.
        const ev = data.event;
        const ds = (ev.dtstart || '').slice(0, 10);
        const de = (ev.dtend   || '').slice(0, 10) || ds;
        _showEventForm(null, ds, de);
        requestAnimationFrame(() => {
          const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
          set('cal-f-sum', ev.summary);
          set('cal-f-loc', ev.location);
          set('cal-f-desc', ev.description);
          if (ev.all_day) {
            const ad = document.getElementById('cal-f-allday');
            if (ad && !ad.checked) { ad.checked = true; ad.dispatchEvent(new Event('change')); }
          } else {
            const t1 = (ev.dtstart || '').match(/T(\d{2}:\d{2})/);
            const t2 = (ev.dtend || '').match(/T(\d{2}:\d{2})/);
            if (t1) set('cal-f-start', t1[1]);
            if (t2) set('cal-f-end', t2[1]);
            document.getElementById('cal-f-start')?.dispatchEvent(new Event('input'));
          }
          // Make sure the details panel is open so the user can verify time.
          document.querySelector('.cal-form-bespoke')?.classList.add('is-expanded');
          const det = document.getElementById('cal-form-details');
          if (det) det.setAttribute('aria-hidden', 'false');
          // Trigger Apple-Maps link sync now that location is filled in.
          document.getElementById('cal-f-loc')?.dispatchEvent(new Event('input'));
        });
        // Reset for next quick add.
        _qaInput.value = '';
      } catch (e) {
        uiModule.showError('Quick-add failed: ' + e.message);
      } finally {
        _qaSubmitting = false;
        clearTimeout(_qaSpinTimer);
        if (_qaSpin) { try { _qaSpin.destroy(); } catch {} _qaSpin.element?.remove(); }
        if (_qaStatus) _qaStatus.textContent = '';
      }
    };
    _qaInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _submitQA(); }
      else if (e.key === 'Escape') { _qaInput.value = ''; _qaInput.blur(); }
    });
    // Flush any render we deferred while the field was focused.
    _qaInput.addEventListener('blur', () => {
      if (_renderPending) { _renderPending = false; _render(); }
    });
  }
  // After a background re-render (e.g. /events fetch returning), restore
  // focus + caret + value so the user can keep typing uninterrupted.
  if (_qaInput && _qaPendingRestore) {
    _qaInput.value = _qaPendingRestore.value;
    _qaInput.focus();
    try {
      _qaInput.setSelectionRange(_qaPendingRestore.selStart, _qaPendingRestore.selEnd);
    } catch {}
    _qaPendingRestore = null;
  }
  // Q anywhere on the page (when not typing elsewhere) focuses quick-add.
  if (!body._qaShortcutWired) {
    body._qaShortcutWired = true;
    document.addEventListener('keydown', (e) => {
      if (!_open) return;
      if (e.key !== 'q' && e.key !== 'Q') return;
      const tag = (document.activeElement?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      const inp = document.getElementById('cal-quickadd');
      if (inp) { e.preventDefault(); inp.focus(); inp.select(); }
    });
  }

  // Pinch zoom on the calendar body changes the view granularity:
  // year ⇆ month ⇆ week. Pinch IN zooms to a tighter view, pinch OUT
  // zooms out. Fires once per gesture so a strong pinch doesn't skip
  // straight from year to week (the user gets one step at a time and
  // can release-and-pinch again).
  if (body && !body._pinchZoomWired) {
    body._pinchZoomWired = true;
    let pinchStart = 0, pinchActive = false, pinchFired = false;
    const dist = (ts) => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
    body.addEventListener('touchstart', (e) => {
      if (e.touches.length === 2) {
        pinchStart = dist(e.touches);
        pinchActive = true;
        pinchFired = false;
      }
    }, { passive: true });
    body.addEventListener('touchmove', (e) => {
      if (!pinchActive || pinchFired || e.touches.length !== 2) return;
      const ratio = dist(e.touches) / pinchStart;
      if (ratio > 1.35)      { _zoomView(+1); pinchFired = true; }
      else if (ratio < 0.7)  { _zoomView(-1); pinchFired = true; }
    }, { passive: true });
    body.addEventListener('touchend', (e) => {
      if (e.touches.length < 2) pinchActive = false;
    }, { passive: true });
  }

  // Touch swipe ← → on the calendar body switches months/weeks/etc. Only
  // fires when the swipe is clearly horizontal so vertical scrolling inside
  // long event lists isn't hijacked. Attached fresh on each render via
  // _wireAll → existing prev/next handlers do the actual navigation.
  if (body && !body._swipeWired) {
    body._swipeWired = true;
    let _sx = 0, _sy = 0, _t0 = 0, _tracking = false;
    body.addEventListener('touchstart', (e) => {
      if (!e.touches || e.touches.length !== 1) return;
      _sx = e.touches[0].clientX;
      _sy = e.touches[0].clientY;
      _t0 = Date.now();
      _tracking = true;
    }, { passive: true });
    body.addEventListener('touchend', (e) => {
      if (!_tracking) return;
      _tracking = false;
      const t = e.changedTouches && e.changedTouches[0];
      if (!t) return;
      const dx = t.clientX - _sx;
      const dy = t.clientY - _sy;
      const dt = Date.now() - _t0;
      // Threshold: at least 50px horizontal, dominant axis is horizontal,
      // and reasonably quick (under 600ms) so it feels intentional.
      if (Math.abs(dx) < 50) return;
      if (Math.abs(dx) < Math.abs(dy) * 1.3) return;
      if (dt > 600) return;
      if (dx < 0) document.getElementById('cal-next')?.click();
      else document.getElementById('cal-prev')?.click();
    }, { passive: true });
  }

  document.getElementById('cal-prev')?.addEventListener('click', () => {
    _slideDir = -1;
    if (_view === 'year') _currentDate = new Date(_currentDate.getFullYear() - 1, 0, 1);
    else if (_view === 'week') _currentDate.setDate(_currentDate.getDate() - 7);
    else if (_view === 'agenda') _currentDate.setDate(_currentDate.getDate() - 30);
    else _currentDate = new Date(_currentDate.getFullYear(), _currentDate.getMonth() - 1, 1);
    // Keep a day selected in month/week so the day-detail panel — which hosts
    // the search box — stays available (otherwise browsing hides search).
    _selectedDay = (_view === 'month' || _view === 'week') ? _ds(_currentDate) : null;
    _render();
  });
  document.getElementById('cal-next')?.addEventListener('click', () => {
    _slideDir = 1;
    if (_view === 'year') _currentDate = new Date(_currentDate.getFullYear() + 1, 0, 1);
    else if (_view === 'week') _currentDate.setDate(_currentDate.getDate() + 7);
    else if (_view === 'agenda') _currentDate.setDate(_currentDate.getDate() + 30);
    else _currentDate = new Date(_currentDate.getFullYear(), _currentDate.getMonth() + 1, 1);
    _selectedDay = (_view === 'month' || _view === 'week') ? _ds(_currentDate) : null;
    _render();
  });
  document.getElementById('cal-today')?.addEventListener('click', () => { _currentDate = new Date(); _selectedDay = _today(); _render(); });
  document.getElementById('cal-settings')?.addEventListener('click', () => _showCalSettings());
  document.getElementById('cal-sync')?.addEventListener('click', async () => {
    // Visible feedback: toggle a CSS class on the button so the spin runs
    // even if the network round-trip is too fast to perceive. We hold it
    // for at least 700ms (one full rotation) AND for as long as the actual
    // fetch is in flight, then clear. Previously `await _render()`
    // resolved instantly because _render is synchronous, so the spinner
    // was set→cleared in the same tick and you saw nothing.
    const btn = document.getElementById('cal-sync');
    btn?.classList.add('cal-syncing');
    window._calSyncing = true;
    _allEvents = {};
    _fetchedRanges = [];
    localStorage.removeItem(LS_KEY);

    // Compute the visible range and force-refetch — _render() kicks off
    // a fetch internally but doesn't return a promise, so we await our
    // own one to actually serialize on the network.
    const _range = (_view === 'year')
      ? [`${_currentDate.getFullYear()}-01-01`, `${_currentDate.getFullYear() + 1}-01-01`]
      : (_view === 'week') ? _weekRange(_currentDate) : _monthRange(_currentDate);
    const minSpin = new Promise(r => setTimeout(r, 700));
    try {
      await Promise.all([
        _fetchEvents(_range[0], _range[1], /*force*/ true).catch(() => {}),
        minSpin,
      ]);
    } finally {
      window._calSyncing = false;
      // Flash a checkmark for ~900ms. Drive it through a flag the toolbar
      // template reads (not a one-off innerHTML on the button), so a stray
      // _render() — the calendar re-renders mid-flow — can't wipe it. Same
      // reason the spin is flag-driven.
      window._calSyncDone = true;
      _render();
      setTimeout(() => {
        window._calSyncDone = false;
        if (_open) _render();
      }, 900);
      if (uiModule?.showToast) uiModule.showToast('Calendar refreshed');
    }
  });
  // Brief spin on the "+" glyph before the new-event form opens. The
  // glyph already rotates on hover (desktop). On mobile there's no
  // hover, so play the rotation on tap as a quick affordance.
  const _addClick = (e, openFn) => {
    if (window.innerWidth <= 768) {
      const plus = e.currentTarget.querySelector('.cal-add-plus');
      if (plus) {
        plus.classList.add('cal-add-spinning');
        setTimeout(() => plus.classList.remove('cal-add-spinning'), 360);
      }
      setTimeout(openFn, 220);
    } else {
      openFn();
    }
  };
  // If the user typed in quick-add but pressed "+ New" instead of Enter, treat
  // it as a quick-add (parse the text) rather than opening a blank event — a
  // common mix-up since the two controls sit side by side.
  const _tryQuickAddFromButton = () => {
    const qa = document.getElementById('cal-quickadd');
    if (qa && qa.value.trim()) {
      qa.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      return true;
    }
    return false;
  };
  document.getElementById('cal-add')?.addEventListener('click', (e) => _addClick(e, () => { if (!_tryQuickAddFromButton()) _showEventForm(null, _selectedDay || _today()); }));
  // Solo "+" on the day-detail header: no spin (the small round button
  // doesn't look good rotating in place — open the form immediately).
  document.getElementById('cal-add-day')?.addEventListener('click', () => { if (!_tryQuickAddFromButton()) _showEventForm(null, _selectedDay); });

  // Mobile: relocate the toolbar's +New pill so it sits NEXT TO the
  // quick-add row (not inside it — the row has its own border/background
  // that makes embedded buttons look like part of the input field).
  // Wrap the row and button in a flex container so they share one line.
  if (window.innerWidth <= 768) {
    const addBtn = document.getElementById('cal-add');
    const qaRow = document.getElementById('cal-quickadd-row');
    if (addBtn && qaRow) {
      let wrap = qaRow.parentElement;
      if (!wrap?.classList.contains('cal-quickadd-wrap')) {
        wrap = document.createElement('div');
        wrap.className = 'cal-quickadd-wrap';
        qaRow.parentElement?.insertBefore(wrap, qaRow);
        wrap.appendChild(qaRow);
      }
      if (addBtn.parentElement !== wrap) wrap.appendChild(addBtn);
    }
  }

  // Search input — re-render rebuilds the day-detail DOM on each keystroke,
  // so refocus and restore caret position to keep typing smooth.
  const searchInput = document.getElementById('cal-search');
  if (searchInput) {
    if (document.activeElement?.id === 'cal-search') {
      // First call after a re-render: refocus and place caret at end.
      searchInput.focus();
      const len = searchInput.value.length;
      try { searchInput.setSelectionRange(len, len); } catch {}
    }
    searchInput.addEventListener('input', (e) => {
      _searchQuery = e.target.value.trim();
      // Partial update: swap only the search results inside the day-detail
      // panel, leaving the search input element itself in place. A full
      // _render() destroys the input via innerHTML, and on iOS the
      // keyboard dismisses even if a brand-new input is focused
      // synchronously after. Keeping the same input element across
      // keystrokes is the only way to keep the keyboard up.
      _updateDaySearchResults();
    });
    // Mobile: when the search input gains focus the on-screen keyboard
    // pops up. Expand the day-detail pane to (near) the visible viewport
    // height so the search bar sits at the top of the screen, well above
    // the keyboard, instead of staying squashed behind it.
    searchInput.addEventListener('focus', () => {
      if (window.innerWidth > 768) return;
      const calBody = document.getElementById('cal-body');
      if (!calBody) return;
      const vh = (window.visualViewport?.height) || window.innerHeight;
      const target = vh - 24;
      // Skip if already expanded — every keystroke triggers a re-render
      // which re-focuses the input. Re-running this on each keystroke
      // would shove the layout around as the user types.
      const cur = parseInt(calBody.style.getPropertyValue('--cal-detail-h'), 10) || 0;
      if (cur >= target - 24) return;
      calBody.style.setProperty('--cal-detail-h', target + 'px');
    });
  }

  body.querySelectorAll('.cal-view-btn').forEach(b => b.addEventListener('click', () => {
    _view = b.dataset.view;
    _searchQuery = '';
    _selectedDay = null;
    // Switching to Agenda always lands on today so you see "what's coming
    // up" rather than wherever you happened to be browsing.
    if (_view === 'agenda') _currentDate = new Date();
    _render();
  }));
  body.querySelector('#cal-filter-toggle')?.addEventListener('click', () => {
    _filtersCollapsed = !_filtersCollapsed;
    localStorage.setItem('cal-filters-collapsed', _filtersCollapsed ? '1' : '0');
    _render();
  });
  body.querySelectorAll('.cal-filter-item').forEach(it => it.addEventListener('click', (e) => {
    const href = it.dataset.href;
    const type = it.dataset.type;
    if (href) {
      // Solo-filter: click = show only this calendar; click again = show all.
      // Shift/Ctrl+click = toggle individually (legacy hide/show).
      const allHrefs = Array.from(body.querySelectorAll('.cal-filter-item[data-href]')).map(el => el.dataset.href);
      if (e.shiftKey || e.ctrlKey || e.metaKey) {
        _hiddenCals.has(href) ? _hiddenCals.delete(href) : _hiddenCals.add(href);
      } else {
        const soloed = !_hiddenCals.has(href) && allHrefs.every(h => h === href || _hiddenCals.has(h));
        if (soloed) {
          _hiddenCals.clear();
        } else {
          _hiddenCals.clear();
          allHrefs.forEach(h => { if (h !== href) _hiddenCals.add(h); });
        }
      }
    } else if (type) {
      // "!" chip toggles a separate "only important" axis — clicking it
      // doesn't solo-hide other categories the way a normal type chip does.
      if (type === '!') {
        _onlyImportant = !_onlyImportant;
        // Clear category hides so importance becomes the active filter.
        if (_onlyImportant) _hiddenTypes.clear();
      } else {
        const allTypes = Array.from(body.querySelectorAll('.cal-filter-item[data-type]'))
          .map(el => el.dataset.type)
          .filter(t => t !== '!');
        // Engaging a category filter cancels "only important" so it doesn't
        // silently keep filtering on top.
        _onlyImportant = false;
        if (e.shiftKey || e.ctrlKey || e.metaKey) {
          _hiddenTypes.has(type) ? _hiddenTypes.delete(type) : _hiddenTypes.add(type);
        } else {
          const soloed = !_hiddenTypes.has(type) && allTypes.every(t => t === type || _hiddenTypes.has(t));
          if (soloed) {
            _hiddenTypes.clear();
          } else {
            _hiddenTypes.clear();
            allTypes.forEach(t => { if (t !== type) _hiddenTypes.add(t); });
          }
        }
      }
    }
    _render();
  }));
  body.querySelectorAll('.cal-day[data-date]').forEach(cell => cell.addEventListener('click', (e) => {
    if (e.target.closest('.cal-event-item,.cal-multiday')) return;
    const d = cell.dataset.date;
    // First click on a day: select it. Second click on the same already-
    // selected day: open the new-event form pre-filled with that date.
    if (_selectedDay === d) {
      _showEventForm(null, d);
      return;
    }
    _selectedDay = d;
    _render();
  }));
  body.querySelectorAll('.cal-event-item').forEach(it => it.addEventListener('click', (e) => {
    if (e.target.closest('.cal-event-more')) return;
    const ev = _events.find(e => e.uid === it.dataset.uid);
    if (ev) _showEventForm(ev);
  }));
  _wireQuickDelete(body);

  // Drag
  body.querySelectorAll('[draggable="true"][data-uid]').forEach(el => {
    el.addEventListener('dragstart', (e) => {
      _dragUid = el.dataset.uid;
      e.dataTransfer.effectAllowed = 'move';
      el.classList.add('cal-dragging');
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('cal-dragging');
      _dragUid = null;
      body.querySelectorAll('.cal-drag-over').forEach(d => d.classList.remove('cal-drag-over'));
    });
  });
  // Helper — find the day cell directly under the cursor at (x,y). Reading
  // it from the cursor is more reliable than trusting whichever cell fired
  // the `drop` event: if the user releases over a nested event item or
  // multi-day bar, the drop fires on the inner element and the calling
  // cell's `data-date` may be the wrong row.
  const _cellAtPoint = (x, y) => {
    const stack = document.elementsFromPoint ? document.elementsFromPoint(x, y) : [document.elementFromPoint(x, y)];
    for (const el of stack) {
      if (!el || !el.closest) continue;
      // Prefer the month-view day cell, fall back to any data-date target
      // (e.g. week-view column) so week-view drag still works.
      const dayCell = el.closest('.cal-day[data-date]');
      if (dayCell) return dayCell;
      const anyCell = el.closest('[data-date]');
      if (anyCell) return anyCell;
    }
    return null;
  };
  body.querySelectorAll('[data-date]').forEach(cell => {
    cell.addEventListener('dragover', (e) => {
      if (!_dragUid) return;
      e.preventDefault();
      // Only highlight the cell genuinely under the cursor — prevents two
      // adjacent cells flashing as the cursor crosses a border.
      const target = _cellAtPoint(e.clientX, e.clientY);
      body.querySelectorAll('.cal-drag-over').forEach(c => {
        if (c !== target) c.classList.remove('cal-drag-over');
      });
      if (target) target.classList.add('cal-drag-over');
    });
    cell.addEventListener('dragleave', (e) => {
      // Only clear if the cursor really left this cell (dragleave fires when
      // entering a child too — that's the flicker bug).
      const target = _cellAtPoint(e.clientX, e.clientY);
      if (target !== cell) cell.classList.remove('cal-drag-over');
    });
    cell.addEventListener('drop', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      body.querySelectorAll('.cal-drag-over').forEach(c => c.classList.remove('cal-drag-over'));
      if (!_dragUid) return;
      // Drop target = whichever cell is actually under the cursor at release,
      // not the bubbling target. Fixes "drops on wrong day" reports.
      const target = _cellAtPoint(e.clientX, e.clientY) || cell;
      const nd = target.dataset.date;
      const ev = _events.find(e => e.uid === _dragUid);
      if (!ev || !nd) return;
      const od = _localDateOf(ev.dtstart);
      if (od === nd) return;
      const diff = Math.round((new Date(nd + 'T00:00:00') - new Date(od + 'T00:00:00')) / 86400000);
      // Snapshot the original times for undo BEFORE we mutate.
      const undoSnap = { uid: ev.uid, dtstart: ev.dtstart, dtend: ev.dtend };
      _pushCalUndo({ label: 'move', run: () => _updateEvent(undoSnap.uid, { dtstart: undoSnap.dtstart, dtend: undoSnap.dtend || undefined }).then(_render) });
      await _updateEvent(ev.uid, { dtstart: _shiftDT(ev.dtstart, diff), dtend: ev.dtend ? _shiftDT(ev.dtend, diff) : undefined });
      _render();
      uiModule.showToast?.('Moved', { duration: 4000, action: 'Undo', actionHint: 'Ctrl+Z', onAction: _popAndRunCalUndo });
    });
  });
}

// ── Undo stack (calendar) ──
const _calUndoStack = [];
function _pushCalUndo(entry) {
  _calUndoStack.push(entry);
  if (_calUndoStack.length > 20) _calUndoStack.shift();
}
function _popAndRunCalUndo() {
  const entry = _calUndoStack.pop();
  if (entry && typeof entry.run === 'function') {
    try { entry.run(); } catch {}
  }
}
// Ctrl/Cmd+Z anywhere inside the calendar modal undoes the last drag-move.
if (typeof window !== 'undefined' && !window._calUndoBound) {
  window._calUndoBound = true;
  document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.key !== 'z' || e.shiftKey) return;
    // Skip if the user's typing in a real field — let the browser's text undo run.
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    const modal = document.getElementById('calendar-modal');
    if (!modal || modal.classList.contains('hidden') || !_calUndoStack.length) return;
    e.preventDefault();
    _popAndRunCalUndo();
  });
}

// ── Calendar Settings ──

async function _showCalSettings() {
  const existing = document.getElementById('cal-settings-panel');
  if (existing) { existing.remove(); return; }

  const cals = _calendars;
  const COLORS = ['#5b8abf','#4caf50','#ff9800','#e91e63','#9c27b0','#00bcd4','#795548','#607d8b','#f44336','#7c4dff'];

  const overlay = document.createElement('div');
  overlay.id = 'cal-settings-panel';
  overlay.className = 'modal';
  overlay.style.display = 'flex';
  overlay.style.zIndex = '999';
  overlay.innerHTML = `
    <div class="modal-content" style="width:420px;max-width:92vw;">
      <div class="modal-header">
        <h4>Calendar Settings</h4>
        <button class="close-btn" id="cal-settings-close">\u2716</button>
      </div>
      <div class="modal-body" style="padding:16px;display:flex;flex-direction:column;gap:16px;">
        <div>
          <div style="font-size:11px;opacity:0.5;margin-bottom:6px;">Your calendars</div>
          <div id="cal-settings-list" style="display:flex;flex-direction:column;gap:4px;">
            ${cals.map(c => `
              <div class="cal-settings-row" data-id="${_e(c.href)}" style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;background:color-mix(in srgb, var(--fg) 4%, transparent);">
                <input type="color" value="${c.color || '#5b8abf'}" class="cal-s-color" style="width:24px;height:24px;border:none;background:none;cursor:pointer;padding:0;border-radius:50%;overflow:hidden;" />
                <input type="text" value="${_e(c.name)}" class="cal-s-name" style="flex:1;background:none;border:1px solid var(--border);border-radius:4px;padding:3px 6px;color:var(--fg);font-size:12px;" />
                <button class="cal-s-del" title="Delete calendar" style="background:none;border:none;color:var(--accent, var(--red));opacity:0.75;cursor:pointer;padding:2px;display:flex;position:relative;top:4px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></button>
              </div>
            `).join('')}
          </div>
          <button class="memory-toolbar-btn" id="cal-settings-add" style="margin-top:8px;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--accent, var(--red))" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New calendar
          </button>
        </div>
        <div style="border-top:1px solid var(--border);padding-top:12px;">
          <div style="font-size:11px;opacity:0.5;margin-bottom:6px;">Import calendar</div>
          <div style="display:flex;gap:8px;align-items:center;">
            <label class="memory-toolbar-btn" style="cursor:pointer;">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="position:relative;top:5px;margin-right:3px;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
              <span style="position:relative;top:4px;">Import .ics</span>
              <input type="file" accept=".ics,.ical" id="cal-import-file" style="display:none;" />
            </label>
            <span id="cal-import-status" style="font-size:11px;opacity:0.6;"></span>
          </div>
          <div style="font-size:10px;opacity:0.4;margin-top:4px;">Upload a .ics file to import events. Google Calendar, Apple Calendar, and Outlook all export .ics files.</div>
        </div>
        <div style="border-top:1px solid var(--border);padding-top:12px;">
          <div style="font-size:11px;opacity:0.5;margin-bottom:6px;">Export calendar</div>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
            ${cals.map(c => `
              <button class="memory-toolbar-btn cal-s-export-chip" data-id="${_e(c.href)}" title="Download ${_e(c.name)}.ics" style="cursor:pointer;">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="position:relative;top:2px;margin-right:3px;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                <span style="position:relative;top:1px;">${_e(c.name)}</span>
              </button>
            `).join('')}
          </div>
          <div style="font-size:10px;opacity:0.4;margin-top:4px;">Download a calendar as .ics for backup or to import into another app.</div>
        </div>
        <div style="border-top:1px solid var(--border);padding-top:12px;">
          <div style="font-size:11px;opacity:0.5;margin-bottom:6px;">Sync</div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <button class="memory-toolbar-btn" id="cal-settings-sync-now" style="cursor:pointer;">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="position:relative;top:2px;margin-right:3px;"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
              <span style="position:relative;top:1px;">Sync now</span>
            </button>
            <span id="cal-settings-sync-status" style="font-size:11px;opacity:0.6;"></span>
          </div>
          <div style="font-size:10px;opacity:0.4;margin-top:4px;">Pulls events from your CalDAV server. To connect or change CalDAV credentials, open <a href="#" id="cal-settings-open-caldav" style="color:var(--accent, var(--red));text-decoration:none;font-weight:600;">Settings → Integrations</a>.</div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const cleanup = () => overlay.remove();
  overlay.querySelector('#cal-settings-close').addEventListener('click', cleanup);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(); });

  // Create a new (local) calendar. Defaults the name + next palette color, then
  // reopens the panel so the user can rename it inline and pick a color.
  overlay.querySelector('#cal-settings-add')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const color = COLORS[_calendars.length % COLORS.length];
    try {
      const r = await fetch(`${API_BASE}/api/calendar/calendars?name=${encodeURIComponent('New calendar')}&color=${encodeURIComponent(color)}`, { method: 'POST', credentials: 'same-origin' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) throw new Error(d.error || 'Failed to create calendar');
      _calendars.push({ name: d.name, href: d.id, color: d.color });
      _allEvents = {}; _fetchedRanges = []; localStorage.removeItem(LS_KEY);
      _render();
      cleanup();
      _showCalSettings();
      // Focus the new row's name field so it's ready to rename.
      setTimeout(() => {
        const rows = document.querySelectorAll('#cal-settings-list .cal-settings-row');
        const last = rows[rows.length - 1];
        const nm = last?.querySelector('.cal-s-name');
        if (nm) { nm.focus(); nm.select(); }
      }, 30);
    } catch (err) {
      btn.disabled = false;
      if (window.showError) window.showError(err.message || 'Failed to create calendar');
      else console.error(err);
    }
  });

  // Color + name changes
  overlay.querySelectorAll('.cal-settings-row').forEach(row => {
    const id = row.dataset.id;
    const colorInput = row.querySelector('.cal-s-color');
    const nameInput = row.querySelector('.cal-s-name');
    const delBtn = row.querySelector('.cal-s-del');

    let saveTimer;
    const save = () => {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(async () => {
        await fetch(`${API_BASE}/api/calendar/calendars/${id}?name=${encodeURIComponent(nameInput.value)}&color=${encodeURIComponent(colorInput.value)}`, { method: 'PUT' });
        if (uiModule?.showToast) uiModule.showToast(`Saved “${nameInput.value || 'calendar'}”`);
        // Update local calendar list
        const c = _calendars.find(c => c.href === id);
        if (c) { c.name = nameInput.value; c.color = colorInput.value; }
        // Update colors on cached events
        for (const uid of Object.keys(_allEvents)) {
          if (_allEvents[uid].calendar_href === id) {
            _allEvents[uid].color = colorInput.value;
            _allEvents[uid].calendar = nameInput.value;
          }
        }
        localStorage.removeItem(LS_KEY);
        _fetchedRanges = [];
        _render();
      }, 300);
    };
    colorInput.addEventListener('input', save);
    nameInput.addEventListener('change', save);
    // Upgrade the native color box into the app's themed color picker.
    try { attachColorPicker(colorInput); } catch (_) {}

    delBtn.addEventListener('click', async () => {
      const name = nameInput.value;
      if (!await window.styledConfirm(`Delete calendar "${name}" and all its events?`, { confirmText: 'Delete', danger: true })) return;
      await fetch(`${API_BASE}/api/calendar/calendars/${id}`, { method: 'DELETE' });
      row.remove();
      _allEvents = {}; _fetchedRanges = []; localStorage.removeItem(LS_KEY);
      _calendars = _calendars.filter(c => c.href !== id);
      _render();
    });
  });

  // ICS import
  overlay.querySelector('#cal-import-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const status = overlay.querySelector('#cal-import-status');
    status.textContent = 'Importing...';
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch(`${API_BASE}/api/calendar/import`, { method: 'POST', body: fd, credentials: 'same-origin' });
      // Try JSON first; fall back to text so HTML auth-walls and bare
      // 500s surface something the user can act on instead of the
      // generic "Import failed".
      let data = null, raw = '';
      try { data = await res.clone().json(); } catch (_) { raw = await res.text().catch(() => ''); }
      if (res.ok && data && data.ok) {
        status.textContent = `${data.imported} events imported to "${data.calendar}"` + (data.skipped ? ` (${data.skipped} skipped)` : '');
        _allEvents = {}; _fetchedRanges = []; localStorage.removeItem(LS_KEY);
        await _fetchCalendars();
        _render();
      } else {
        // FastAPI HTTPException → {detail}; some routes use {error}.
        const reason = (data && (data.detail || data.error)) || raw.slice(0, 200) || `HTTP ${res.status}`;
        status.textContent = `Import failed: ${reason}`;
        console.error('Calendar import failed', res.status, data || raw);
      }
    } catch (err) {
      status.textContent = `Import failed: ${err.message || err}`;
      console.error('Calendar import threw', err);
    }
    e.target.value = '';
  });

  // Export chips — one per calendar; downloads that calendar's .ics.
  overlay.querySelectorAll('.cal-s-export-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      window.open(`${API_BASE}/api/calendar/export/${chip.dataset.id}`, '_blank');
    });
  });

  // Sync now — fires the CalDAV pull synchronously so we can show the
  // result inline, then refreshes the panel + calendar grid.
  overlay.querySelector('#cal-settings-sync-now')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const status = overlay.querySelector('#cal-settings-sync-status');
    btn.disabled = true;
    status.textContent = 'Syncing…';
    const data = await _syncCaldav(true) || {};
    if (data.errors && data.errors.length) {
      status.textContent = `Sync failed: ${data.errors[0]}`;
    } else {
      const parts = [];
      if (data.events) parts.push(`${data.events} events`);
      if (data.deleted) parts.push(`${data.deleted} removed`);
      status.textContent = parts.length ? `Synced — ${parts.join(', ')}` : 'Synced — no changes';
      _allEvents = {}; _fetchedRanges = [];
      try { localStorage.removeItem(LS_KEY); } catch (_) {}
      await _fetchCalendars();
      _render();
      // Reopen the panel so the calendars list reflects any new ones.
      const reopenWith = !!document.getElementById('cal-settings-panel');
      cleanup();
      if (reopenWith) _showCalSettings();
    }
    btn.disabled = false;
  });

  // Integrations link — close this overlay and open Settings → Integrations.
  overlay.querySelector('#cal-settings-open-caldav')?.addEventListener('click', (e) => {
    e.preventDefault();
    cleanup();
    if (window.settingsModule && typeof window.settingsModule.open === 'function') {
      try { window.settingsModule.open('integrations'); return; } catch (_) {}
    }
    const modal = document.getElementById('settings-modal');
    if (modal) {
      modal.classList.remove('hidden');
      const tabBtn = modal.querySelector('[data-settings-tab="integrations"]');
      if (tabBtn) tabBtn.click();
    }
  });
}

// ── Event Form ──

// Pull an explicit clock time out of a free-text title so it can overrule the
// time pickers on save (e.g. title "Standup 10am" wins over a 9pm picker).
// Returns {h, m} in 24h, or null when the title has no unambiguous time.
function _parseTitleTime(text) {
  if (!text) return null;
  // 12-hour with am/pm — "10am", "10:30 pm", "at 7 p.m."
  let m = text.match(/\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\b/i);
  if (m) {
    let h = parseInt(m[1], 10);
    const mm = m[2] ? parseInt(m[2], 10) : 0;
    if (h < 1 || h > 12 || mm > 59) return null;
    const pm = m[3].toLowerCase() === 'p';
    if (pm && h !== 12) h += 12;
    if (!pm && h === 12) h = 0;
    return { h, m: mm };
  }
  // 24-hour HH:MM — "15:00", "at 9:30" (needs the colon to avoid matching
  // bare numbers like "room 5" or years).
  m = text.match(/\b([01]?\d|2[0-3]):([0-5]\d)\b/);
  if (m) return { h: parseInt(m[1], 10), m: parseInt(m[2], 10) };
  return null;
}

function _showEventForm(existing, defaultDate, defaultEndDate) {
  const body = document.getElementById('cal-body');
  if (!body) return;
  const isEdit = !!existing;
  const ds = existing ? _localDateOf(existing.dtstart) : (defaultDate || _today());
  const de = existing && existing.dtend ? _localDateOf(existing.dtend) : (defaultEndDate || ds);
  const isMultiDay = ds !== de;
  const st = existing && !existing.all_day ? _fmtTime(existing.dtstart) : '09:00';
  const et = existing && !existing.all_day && existing.dtend ? _fmtTime(existing.dtend) : '10:00';
  // Default to all-day when dragging across multiple days
  const ad = existing ? existing.all_day : (defaultEndDate && defaultEndDate !== defaultDate);

  let calOpts = _calendars.filter(c => !_hiddenCals.has(c.href)).map(c =>
    `<option value="${_e(c.href)}" ${existing && existing.calendar_href === c.href ? 'selected' : ''}>${_e(c.name)}</option>`
  ).join('');

  // "Bespoke" event form: a big clock-face hero (time + date) and a single
  // title input. Everything else (location, description, recurrence,
  // reminder, color, calendar) is folded behind a click — focusing the
  // title or clicking "Add details" reveals it. Empty drafts feel like a
  // sticky-note; full-detail editing is one keystroke away.
  const _hasDetails = !!(existing && (
    existing.location || existing.description || existing.rrule ||
    (existing.color && existing.color.length) ||
    isMultiDay
  ));
  const _expandedAtStart = isEdit && _hasDetails;

  body.innerHTML = `<div class="cal-form cal-form-bespoke${_expandedAtStart ? ' is-expanded' : ''}">
    <button type="button" class="cal-form-mobile-cancel" id="cal-form-mobile-cancel" title="Cancel" aria-label="Cancel event">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
    <div class="cal-form-today" id="cal-form-today">Today is <span id="cal-form-today-text">${_clockDate(_today())} · ${_nowClock()}</span></div>
    <div class="cal-hero">
      <button type="button" class="cal-hero-time" id="cal-hero-time" title="Change time">
        <span class="cal-hero-clock" id="cal-hero-clock">${_clockFace(ad ? '' : st)}</span>
        <span class="cal-hero-ampm" id="cal-hero-ampm">${_clockAmpm(ad ? '' : st)}</span>
      </button>
      <button type="button" class="cal-hero-date" id="cal-hero-date" title="Change date">${_clockDate(ds)}</button>
    </div>

    <div class="cal-title-wrap">
      <input type="text" id="cal-f-sum" placeholder=" " value="${_e(existing?.summary || '')}" class="cal-input cal-hero-title" autocomplete="off" />
      <span class="cal-title-hint" aria-hidden="true">${isEdit ? 'Event title' : 'What’s happening?'}<svg class="cal-title-enter-ico" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg></span>
    </div>

    <div class="cal-form-details" id="cal-form-details" aria-hidden="${_expandedAtStart ? 'false' : 'true'}">
      <div class="cal-form-row">
        <input type="date" id="cal-f-date" value="${ds}" class="cal-input" />
        <span style="opacity:0.3">to</span>
        <input type="date" id="cal-f-date-end" value="${de}" class="cal-input" />
        <div class="cal-allday-ctrl">
          <span class="cal-allday-label">All day</span>
          <label class="admin-switch cal-allday-switch"><input type="checkbox" id="cal-f-allday" ${ad ? 'checked' : ''} /><span class="admin-slider"></span></label>
        </div>
      </div>
      <div class="cal-form-row" id="cal-time-row" style="${ad ? 'display:none' : ''}">
        <input type="time" id="cal-f-start" value="${st}" class="cal-input cal-input-time" />
        <span style="opacity:0.3">–</span>
        <input type="time" id="cal-f-end" value="${et}" class="cal-input cal-input-time" />
      </div>
      <div class="cal-loc-row">
        <input type="text" id="cal-f-loc" placeholder="Location" value="${_e(existing?.location || '')}" class="cal-input" />
        <a id="cal-f-loc-map" class="cal-loc-map" href="#" target="_blank" rel="noopener noreferrer" title="Open in Maps" aria-label="Open in Apple Maps" tabindex="-1">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 1 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
        </a>
      </div>
      <select id="cal-f-rrule" class="cal-input">
        <option value="" ${!existing?.rrule ? 'selected' : ''}>Does not repeat</option>
        <option value="FREQ=DAILY" ${existing?.rrule === 'FREQ=DAILY' ? 'selected' : ''}>Daily</option>
        <option value="FREQ=WEEKLY" ${existing?.rrule === 'FREQ=WEEKLY' ? 'selected' : ''}>Weekly</option>
        <option value="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" ${existing?.rrule === 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR' ? 'selected' : ''}>Weekdays</option>
        <option value="FREQ=MONTHLY" ${existing?.rrule === 'FREQ=MONTHLY' ? 'selected' : ''}>Monthly</option>
        <option value="FREQ=YEARLY" ${existing?.rrule === 'FREQ=YEARLY' ? 'selected' : ''}>Yearly</option>
      </select>
      <textarea id="cal-f-desc" placeholder="Description" class="cal-input" rows="2">${_e(existing?.description || '')}</textarea>
      <div class="cal-form-row" style="align-items:center;gap:8px;">
        <label style="font-size:11px;display:flex;align-items:center;gap:4px;"><svg class="cal-remind-bell" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--accent, var(--red))" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg><span style="opacity:0.5;">Reminder</span></label>
        <select id="cal-f-remind" class="cal-input" style="flex:1;">
          <option value="" ${isEdit ? 'selected' : ''}>No reminder</option>
          <option value="0">At event time</option>
          <option value="5">5 minutes before</option>
          <option value="10">10 minutes before</option>
          <option value="15" ${!isEdit ? 'selected' : ''}>15 minutes before</option>
          <option value="30">30 minutes before</option>
          <option value="60">1 hour before</option>
          <option value="120">2 hours before</option>
          <option value="1440">1 day before</option>
          <option value="custom">Exact time...</option>
        </select>
        <input type="datetime-local" id="cal-f-remind-custom" class="cal-input" style="flex:1;display:none;" />
      </div>
      <div class="cal-form-row" style="align-items:center;gap:8px;">
        <label style="font-size:11px;opacity:0.5;">Color</label>
        <div class="note-color-picker" id="cal-f-colors">
          ${CAL_COLORS.map(c => {
            const cur = existing?.color || '';
            const isCustom = c.hex === 'custom';
            const isActive = isCustom ? _isCalBgImage(cur) : (cur === c.hex || (!cur && !c.hex));
            let bg;
            if (isCustom) {
              const url = _calBgImageUrl(cur);
              bg = url ? `center/cover no-repeat url('${url}')` : _CAL_CUSTOM_GRADIENT;
            } else {
              bg = c.hex || 'var(--border)';
            }
            return `<span class="note-color-dot${isActive ? ' active' : ''}" data-color="${c.hex}" style="background:${bg}" title="${c.name}"></span>`;
          }).join('')}
        </div>
      </div>
      ${_calendars.length > 1 ? `<select id="cal-f-cal" class="cal-input cal-f-cal-select">${calOpts}</select>` : ''}
    </div>

    <div class="cal-form-actions">
      ${isEdit ? `<button id="cal-f-del" class="cal-btn cal-btn-danger" style="display:inline-flex;align-items:center;gap:5px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>Delete</button>` : ''}
      <button id="cal-f-cancel" class="cal-btn" style="display:inline-flex;align-items:center;gap:5px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Cancel</button>
      <button id="cal-f-save" class="cal-btn cal-btn-primary" style="display:inline-flex;align-items:center;gap:5px;">${isEdit
        ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>Save'
        : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Create'}</button>
    </div>
  </div>`;

  document.getElementById('cal-f-allday')?.addEventListener('change', (e) => {
    document.getElementById('cal-time-row').style.display = e.target.checked ? 'none' : '';
  });
  // Keep end date >= start date
  document.getElementById('cal-f-date')?.addEventListener('change', () => {
    const s = document.getElementById('cal-f-date').value;
    const eEl = document.getElementById('cal-f-date-end');
    if (eEl && eEl.value < s) eEl.value = s;
  });
  // Color dot picker — also live-tints the form card (border, focus
   // rings, primary button) so the user sees the choice immediately.
  const _formCard = document.querySelector('.cal-form-bespoke');
  // Dismiss the keyboard by pressing Enter in a single-line text field — the
  // ↵ glyph next to the title hints at this.
  if (_formCard) {
    _formCard.querySelectorAll('input[type="text"]').forEach(inp => {
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
      });
    });
  }
  // Tint the calendar-picker select with the chosen calendar's colour so it's
  // clear which calendar the event lands in.
  const _calSel = document.getElementById('cal-f-cal');
  if (_calSel) {
    const _tintCalSel = () => {
      const c = _calendars.find(x => x.href === _calSel.value);
      const col = (c && c.color && !_isCalBgImage(c.color)) ? c.color : 'var(--accent, var(--red))';
      // Soft full-width background tint only — no side bar/border highlight.
      _calSel.style.background = `color-mix(in srgb, ${col} 16%, var(--bg))`;
    };
    _calSel.addEventListener('change', _tintCalSel);
    _tintCalSel();
  }
  const _applyFormTint = (hex) => {
    if (!_formCard) return;
    if (_isCalBgImage(hex)) {
      // Paint the form card with the uploaded image (mirrors how the notes
      // form previews a custom-bg note), plus a translucent overlay so text
      // stays readable. Chrome accent falls back to the theme accent.
      const url = _calBgImageUrl(hex);
      _formCard.style.setProperty('--ev-color', 'var(--accent)');
      _formCard.style.backgroundImage = `linear-gradient(color-mix(in srgb, var(--panel) 65%, transparent), color-mix(in srgb, var(--panel) 65%, transparent)), url('${url.replace(/'/g, "\\'")}')`;
      _formCard.style.backgroundSize = 'cover';
      _formCard.style.backgroundPosition = 'center';
      _formCard.classList.add('cal-form-bg-image');
      return;
    }
    // Clear any prior custom-bg styling.
    _formCard.classList.remove('cal-form-bg-image');
    _formCard.style.backgroundImage = '';
    _formCard.style.backgroundSize = '';
    _formCard.style.backgroundPosition = '';
    if (hex) _formCard.style.setProperty('--ev-color', hex);
    else _formCard.style.removeProperty('--ev-color');
  };
  document.querySelectorAll('#cal-f-colors .note-color-dot').forEach(dot => {
    dot.addEventListener('click', async () => {
      // Custom dot: prompt for an image upload. Empty input → no-op.
      if (dot.dataset.color === 'custom') {
        const url = await _pickCalBgImage();
        if (!url) return;
        const sentinel = 'bg:' + url;
        dot.dataset.color = sentinel;
        dot.style.background = `center/cover no-repeat url('${url}')`;
        document.querySelectorAll('#cal-f-colors .note-color-dot').forEach(d => d.classList.remove('active'));
        dot.classList.add('active');
        _applyFormTint(sentinel);
        return;
      }
      document.querySelectorAll('#cal-f-colors .note-color-dot').forEach(d => d.classList.remove('active'));
      dot.classList.add('active');
      _applyFormTint(dot.dataset.color || '');
    });
  });
  // Initial tint for edit-an-existing-event so the card already reflects
  // the saved color when the form opens.
  _applyFormTint(existing?.color || '');
  // When the user changes the start time, shift the end time by the same
  // delta so the event keeps its original duration (or a 1-hour default if
  // start == end). Skipped if the user has already nudged the end input
  // since opening the form — we don't want to clobber a deliberate edit.
  (function _wireStartShiftsEnd() {
    const startEl = document.getElementById('cal-f-start');
    const endEl = document.getElementById('cal-f-end');
    if (!startEl || !endEl) return;
    const _toMin = (v) => {
      if (!v || !/^\d{2}:\d{2}$/.test(v)) return null;
      const [h, m] = v.split(':').map(n => parseInt(n, 10));
      return h * 60 + m;
    };
    const _toHHMM = (mins) => {
      let m = ((mins % 1440) + 1440) % 1440;
      const hh = String(Math.floor(m / 60)).padStart(2, '0');
      const mm = String(m % 60).padStart(2, '0');
      return `${hh}:${mm}`;
    };
    let prevStartMin = _toMin(startEl.value);
    endEl.addEventListener('input', () => { endEl.dataset.userEdited = '1'; });
    startEl.addEventListener('change', () => {
      const newStartMin = _toMin(startEl.value);
      const endMin = _toMin(endEl.value);
      if (newStartMin == null) { prevStartMin = newStartMin; return; }
      // Compute the duration before the change. Use the user's existing
      // start→end gap, fallback to 1 hour.
      let durationMin = 60;
      if (prevStartMin != null && endMin != null && endMin > prevStartMin) {
        durationMin = endMin - prevStartMin;
      } else if (endMin != null && newStartMin != null && endMin > newStartMin && endEl.dataset.userEdited === '1') {
        // User already set a custom end before changing start — leave it.
        prevStartMin = newStartMin;
        return;
      }
      endEl.value = _toHHMM(newStartMin + durationMin);
      prevStartMin = newStartMin;
    });
  })();
  // Custom reminder picker
  document.getElementById('cal-f-remind')?.addEventListener('change', (e) => {
    const customInput = document.getElementById('cal-f-remind-custom');
    if (e.target.value === 'custom') {
      customInput.style.display = '';
      // Default to 1 hour before event
      const dv = document.getElementById('cal-f-date')?.value || _today();
      const st = document.getElementById('cal-f-start')?.value || '09:00';
      const eventDt = new Date(`${dv}T${st}:00`);
      eventDt.setHours(eventDt.getHours() - 1);
      const pad = n => String(n).padStart(2, '0');
      customInput.value = `${eventDt.getFullYear()}-${pad(eventDt.getMonth()+1)}-${pad(eventDt.getDate())}T${pad(eventDt.getHours())}:${pad(eventDt.getMinutes())}`;
      customInput.focus();
    } else {
      customInput.style.display = 'none';
    }
    // Jingle the bell whenever a non-empty reminder is picked. CSS handles the
    // animation; we just toggle the class so it re-fires on every change.
    const _bell = document.querySelector('.cal-remind-bell');
    if (_bell && e.target.value) {
      _bell.classList.remove('jingling');
      void _bell.offsetWidth;
      _bell.classList.add('jingling');
      setTimeout(() => _bell.classList.remove('jingling'), 700);
    }
  });
  const _cancelEventForm = () => _render();
  document.getElementById('cal-f-cancel')?.addEventListener('click', _cancelEventForm);
  document.getElementById('cal-form-mobile-cancel')?.addEventListener('click', _cancelEventForm);
  document.getElementById('cal-f-save')?.addEventListener('click', async () => {
    const summary = document.getElementById('cal-f-sum').value.trim();
    if (!summary) { uiModule.showToast('Title required'); return; }
    const dv = document.getElementById('cal-f-date').value;
    const dvEnd = document.getElementById('cal-f-date-end').value || dv;
    const isAD = document.getElementById('cal-f-allday').checked;
    // Title overrules: if the title states a time, apply it to the start
    // (keeping the current duration) so the picker can't silently disagree.
    if (!isAD) {
      const tt = _parseTitleTime(summary);
      const startEl = document.getElementById('cal-f-start');
      const endEl = document.getElementById('cal-f-end');
      const newStart = tt ? `${String(tt.h).padStart(2, '0')}:${String(tt.m).padStart(2, '0')}` : null;
      if (newStart && startEl && startEl.value !== newStart) {
        const toMin = (v) => { const p = (v || '').split(':'); return p.length === 2 ? (+p[0]) * 60 + (+p[1]) : null; };
        const s0 = toMin(startEl.value), e0 = toMin(endEl?.value);
        const dur = (s0 != null && e0 != null && e0 > s0) ? e0 - s0 : 60;
        startEl.value = newStart;
        const endMin = (tt.h * 60 + tt.m + dur) % 1440;
        if (endEl) endEl.value = `${String(Math.floor(endMin / 60)).padStart(2, '0')}:${String(endMin % 60).padStart(2, '0')}`;
        startEl.dispatchEvent(new Event('input'));
      }
    }
    const activeDot = document.querySelector('#cal-f-colors .note-color-dot.active');
    const colorVal = activeDot?.dataset.color || '';
    // Append the user's current UTC offset so the backend stores events as
    // proper UTC instants (is_utc=True). Without this, naive "10:00" gets
    // re-interpreted as local elsewhere — the timezone-misfire bug.
    const _tz = _tzOffset();
    const payload = {
      summary,
      dtstart: isAD ? dv : `${dv}T${document.getElementById('cal-f-start').value}:00${_tz}`,
      dtend: isAD ? dvEnd : `${dvEnd}T${document.getElementById('cal-f-end').value}:00${_tz}`,
      all_day: isAD,
      description: document.getElementById('cal-f-desc').value,
      location: document.getElementById('cal-f-loc').value,
      rrule: document.getElementById('cal-f-rrule').value || undefined,
      calendar_href: document.getElementById('cal-f-cal')?.value || (_calendars[0]?.href || ''),
      color: colorVal || undefined,
    };
    try {
      if (isEdit) await _updateEvent(existing.uid, payload);
      else await _createEvent(payload);
      // Create reminder if selected
      const remindVal = document.getElementById('cal-f-remind')?.value;
      if (remindVal) {
        let remindAt;
        if (remindVal === 'custom') {
          const customVal = document.getElementById('cal-f-remind-custom')?.value;
          remindAt = customVal ? new Date(customVal) : null;
        } else {
          const eventStart = isAD ? new Date(dv + 'T00:00:00') : new Date(`${dv}T${document.getElementById('cal-f-start').value}:00`);
          remindAt = new Date(eventStart.getTime() - parseInt(remindVal) * 60 * 1000);
        }
        if (remindAt && remindAt > new Date()) {
          await _createEventReminder({ summary, dtstart: payload.dtstart, all_day: isAD, location: payload.location }, remindAt);
        }
      }
      _selectedDay = dv; _render();
    } catch (e) { uiModule.showToast('Failed to save'); }
  });
  document.getElementById('cal-f-del')?.addEventListener('click', async () => {
    const name = existing && existing.summary ? `"${existing.summary}"` : 'this event';
    const ok = await uiModule.styledConfirm(`Delete ${name}?`, { confirmText: 'Delete', danger: true });
    if (!ok) return;
    try { await _deleteEvent(existing.uid); _render(); }
    catch (e) { uiModule.showToast('Failed to delete'); }
  });
  // ── Bespoke-form behavior ──────────────────────────────────────────
  const formEl = body.querySelector('.cal-form');
  const detailsEl = document.getElementById('cal-form-details');
  const titleInput = document.getElementById('cal-f-sum');

  const setExpanded = (on) => {
    if (!formEl) return;
    formEl.classList.toggle('is-expanded', on);
    if (detailsEl) detailsEl.setAttribute('aria-hidden', on ? 'false' : 'true');
  };

  // Focusing the title input unfolds the details once (new events). Edit
  // mode opens already expanded when there's any detail content to see.
  titleInput?.addEventListener('focus', () => setExpanded(true), { once: true });

  // Location → Apple Maps. The pin button next to the input is enabled
  // only when there's a non-empty location, and its href tracks the live
  // input value. Apple's universal URL opens the native Maps app on
  // iOS/macOS and falls back to a web view on everything else.
  const locInput = document.getElementById('cal-f-loc');
  const locMap = document.getElementById('cal-f-loc-map');
  const _syncLocMap = () => {
    if (!locMap) return;
    const v = (locInput?.value || '').trim();
    if (!v) {
      locMap.classList.add('is-disabled');
      locMap.removeAttribute('href');
      locMap.setAttribute('tabindex', '-1');
      locMap.setAttribute('aria-disabled', 'true');
    } else {
      locMap.classList.remove('is-disabled');
      locMap.setAttribute('href', 'https://maps.apple.com/?q=' + encodeURIComponent(v));
      locMap.setAttribute('tabindex', '0');
      locMap.removeAttribute('aria-disabled');
    }
  };
  locInput?.addEventListener('input', _syncLocMap);
  _syncLocMap();

  // Hero is clickable — clicking the time or date opens the matching
  // native picker. Expands the details panel first so the input has been
  // laid out (showPicker fails on display:none / 0-height inputs in some
  // browsers).
  const _openPicker = (inputId, { uncheckAllDay = false } = {}) => {
    setExpanded(true);
    const input = document.getElementById(inputId);
    if (!input) return;
    if (uncheckAllDay) {
      const allday = document.getElementById('cal-f-allday');
      if (allday && allday.checked) {
        allday.checked = false;
        document.getElementById('cal-time-row').style.display = '';
        _syncHero();
      }
    }
    // Wait one frame for the reveal layout to settle.
    requestAnimationFrame(() => {
      input.focus();
      try { if (typeof input.showPicker === 'function') input.showPicker(); } catch {}
    });
  };
  document.getElementById('cal-hero-time')?.addEventListener('click', (e) => {
    // Detect which segment of the visible clock was clicked (hh, mm, or
    // somewhere else) so clicking the minutes digits puts the caret right
    // on the minute field of the picker.
    const seg = e.target?.closest('[data-seg]')?.dataset?.seg;
    _openPicker('cal-f-start', { uncheckAllDay: true });
    if (seg === 'mm') {
      // `<input type="time">` accepts setSelectionRange in Chromium for
      // selecting the minute segment; Firefox/Safari are no-ops but the
      // picker still opens, so nothing is lost.
      requestAnimationFrame(() => {
        const inp = document.getElementById('cal-f-start');
        if (!inp) return;
        try { inp.setSelectionRange(3, 5); } catch {}
      });
    }
  });
  document.getElementById('cal-hero-date')?.addEventListener('click', () => {
    _openPicker('cal-f-date');
  });

  // Live hero clock — keep the big time/date in sync with the inputs the
  // user can still tweak inside the details panel.
  const _syncHero = () => {
    const allday = document.getElementById('cal-f-allday')?.checked;
    const startVal = document.getElementById('cal-f-start')?.value || '';
    const dateVal = document.getElementById('cal-f-date')?.value || ds;
    const clockEl = document.getElementById('cal-hero-clock');
    const ampmEl = document.getElementById('cal-hero-ampm');
    const dateEl = document.getElementById('cal-hero-date');
    if (clockEl) clockEl.innerHTML = allday ? '<span class="cal-hero-clock-allday">All day</span>' : _clockFace(startVal);
    if (ampmEl) ampmEl.textContent = allday ? '' : _clockAmpm(startVal);
    if (dateEl) dateEl.textContent = _clockDate(dateVal);
  };
  document.getElementById('cal-f-start')?.addEventListener('input', _syncHero);
  document.getElementById('cal-f-allday')?.addEventListener('change', _syncHero);
  document.getElementById('cal-f-date')?.addEventListener('change', _syncHero);
  _syncHero();

  // New events: expand the details up front (don't rely on the title's focus
  // event — programmatic .focus() is often a no-op on mobile, which would leave
  // the form showing only the title + buttons), then focus the title.
  if (!isEdit) { setExpanded(true); titleInput?.focus(); }

  // Live "Today is …" tick. Updates every 30s; auto-stops the moment the
  // header element disappears (any _render() call swaps #cal-body's HTML).
  const _todayTextEl = document.getElementById('cal-form-today-text');
  if (_todayTextEl) {
    const _tick = () => {
      const el = document.getElementById('cal-form-today-text');
      if (!el) { clearInterval(_todayInterval); return; }
      el.textContent = `${_clockDate(_today())} · ${_nowClock()}`;
    };
    const _todayInterval = setInterval(_tick, 30000);
  }
}

// ── Helpers ──

function _fmtDate(s) { return new Date(s + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }); }

// Hero clock helpers — used by the bespoke event form.
// _clockFace returns the colon-separated digits ("HH : MM"), _clockAmpm
// returns "AM"/"PM"/"" (empty for all-day), _clockDate is a long form
// "Sat · May 10, 2026". 24-h time stays without an AM/PM marker.
function _clockFace(hhmm) {
  // Return the clock split into hh / separator / mm sub-spans so each
  // segment is individually clickable. The wrapping #cal-hero-clock has
  // its innerHTML re-set by _syncHero, so the spans round-trip cleanly.
  if (!hhmm) {
    return '<span class="cal-hero-clock-hh" data-seg="hh">—</span><span class="cal-hero-sep"> : </span><span class="cal-hero-clock-mm" data-seg="mm">—</span>';
  }
  const [h, m] = hhmm.split(':');
  const use12 = (new Date()).toLocaleString().toLowerCase().match(/am|pm/);
  let hh = parseInt(h, 10);
  if (use12) { hh = ((hh + 11) % 12) + 1; }
  const hhStr = String(hh).padStart(2, '0');
  return `<span class="cal-hero-clock-hh" data-seg="hh">${hhStr}</span><span class="cal-hero-sep"> : </span><span class="cal-hero-clock-mm" data-seg="mm">${m}</span>`;
}
function _clockAmpm(hhmm) {
  if (!hhmm) return '';
  const use12 = (new Date()).toLocaleString().toLowerCase().match(/am|pm/);
  if (!use12) return '';
  const h = parseInt(hhmm.split(':')[0], 10);
  return h < 12 ? 'AM' : 'PM';
}
function _clockDate(ds) {
  if (!ds) return '';
  return new Date(ds + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}
function _nowClock() {
  // Live wall-clock string for the "Today is …" header. Locale-aware so
  // 24-h users don't see AM/PM.
  return new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}
function _fmtTime(s) {
  if (!s || s.length < 16) return '';
  // Tz-aware timestamps from CalDAV/import are stored as UTC instants and
  // serialized with Z/offset. Display them in the browser's local timezone;
  // legacy naive timestamps keep their written wall-clock time.
  if (/[Zz]$|[+\-]\d{2}:?\d{2}$/.test(s)) {
    const d = new Date(s);
    if (!isNaN(d)) {
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
  }
  return s.slice(11, 16);
}
function _e(s) { return uiModule.esc ? uiModule.esc(s || '') : (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

// Linkify a location string: URLs become clickable, plain addresses get a Maps link.
function _locHTML(loc) {
  if (!loc) return '';
  const urlRe = /(https?:\/\/[^\s]+)/gi;
  if (urlRe.test(loc)) {
    return loc.replace(urlRe, (url) => {
      const safe = _e(url);
      return `<a href="${safe}" target="_blank" rel="noopener" onclick="event.stopPropagation();">${safe}</a>`;
    }).replace(/\n/g, '<br>');
  }
  // No URL — link the whole thing to OpenStreetMap.
  const mapUrl = 'https://www.openstreetmap.org/search?query=' + encodeURIComponent(loc);
  return `<a href="${mapUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" title="Open in OpenStreetMap">${_e(loc)}</a>`;
}

// ── Open / Close ──

let _wheelDebounce = 0;
function _wheelNav(e) {
  if (!_open) return;
  // Don't intercept scroll inside the day-detail panel or any other inner scroll area
  if (e.target.closest('.cal-day-detail') || e.target.closest('.cal-form')) return;
  const body = document.getElementById('cal-body');
  if (!body) return;
  const now = Date.now();
  if (now - _wheelDebounce < 300) { e.preventDefault(); return; }
  if (Math.abs(e.deltaY) < 30) return;
  _wheelDebounce = now;
  e.preventDefault();
  if (e.deltaY > 0) {
    _slideDir = 1;
    if (_view === 'year') _currentDate = new Date(_currentDate.getFullYear() + 1, 0, 1);
    else if (_view === 'week') _currentDate.setDate(_currentDate.getDate() + 7);
    else _currentDate = new Date(_currentDate.getFullYear(), _currentDate.getMonth() + 1, 1);
  } else {
    _slideDir = -1;
    if (_view === 'year') _currentDate = new Date(_currentDate.getFullYear() - 1, 0, 1);
    else if (_view === 'week') _currentDate.setDate(_currentDate.getDate() - 7);
    else _currentDate = new Date(_currentDate.getFullYear(), _currentDate.getMonth() - 1, 1);
  }
  _selectedDay = null;
  _render();
}

function openCalendar() {
  if (_open) return;
  // If currently minimized — restore in place, preserve all state
  if (Modals.isMinimized('calendar-modal')) {
    Modals.restore('calendar-modal');
    _open = true;
    return;
  }
  _open = true;
  if (_todayCount() > 0) { _markBadgeSeen(); _updateBadge(); }
  _collapseSidebar();
  const modal = _getModal();
  // Clean up any leftover state from a previous swipe-dismiss
  modal.classList.remove('hidden', 'modal-minimized');
  const _content = modal.querySelector('.modal-content');
  if (_content) {
    _content.classList.remove('modal-closing', 'sheet-ready');
    _content.style.transform = '';
    _content.style.transition = '';
    _content.style.animation = '';
    _content.style.opacity = '';
  }
  modal.style.display = 'flex';
  Modals.register('calendar-modal', {
    railBtnId: 'rail-calendar',
    sidebarBtnId: 'tool-calendar-btn',
    closeFn: () => _doCloseCalendar(),
    restoreFn: () => {},
  });
  _currentDate = new Date();
  _selectedDay = _today();  // auto-show today's events on open
  _view = 'month';
  _scrollToTodayOnOpen = true;  // first render lands on today's row
  _escHandler = (e) => {
    if (e.key === 'Escape') {
      // Layer Esc: close the topmost calendar surface first, only fall through
      // to closing the whole calendar when nothing else is on top.
      const settings = document.getElementById('cal-settings-panel');
      if (settings) { settings.remove(); return; }
      if (document.querySelector('.cal-form')) { _render(); return; }
      closeCalendar();
    }
    else if (e.key === 'ArrowLeft') document.getElementById('cal-prev')?.click();
    else if (e.key === 'ArrowRight') document.getElementById('cal-next')?.click();
    else if (e.key === 't' || e.key === 'T') document.getElementById('cal-today')?.click();
    // Cmd/Ctrl+Z is handled by the module-level `_calUndoBound` listener,
    // which consumes the shared `_calUndoStack`. Don't duplicate here.
  };
  document.addEventListener('keydown', _escHandler);
  const body = document.getElementById('cal-body');
  if (body) {
    body.innerHTML = '<div class="cal-loading"></div>';
    const wp = spinnerModule.createWhirlpool(28);
    wp.element.style.margin = '40px auto';
    body.querySelector('.cal-loading').appendChild(wp.element);
    body.addEventListener('wheel', _wheelNav, { passive: false });
  }
  _fetchCalendars().then(() => _render());
}

// Open the calendar focused on a specific event (by uid) or date.
// Used by the chat anchor-link delegate so `[Wake up](#event-<uid>)`
// opens the calendar on that day with the event highlighted.
async function openCalendarTo(target) {
  openCalendar();
  if (!target) return;
  try {
    await _fetchCalendars();
    // If target looks like an ISO date (YYYY-MM-DD...), go straight there.
    let dt = null;
    const isoMatch = /^\d{4}-\d{2}-\d{2}/.test(String(target));
    if (isoMatch) {
      dt = new Date(target);
    } else {
      // Treat as an event uid — find it among loaded events.
      const ev = (_events || []).find(e => e.uid === target || (e.uid || '').startsWith(target));
      if (ev && ev.dtstart) dt = new Date(ev.dtstart);
      if (ev) _highlightEventUid = ev.uid;
    }
    if (dt && !isNaN(dt.getTime())) {
      _currentDate = new Date(dt);
      _selectedDay = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
      _view = 'month';
      _render();
    }
  } catch (e) { /* best-effort focus */ }
}

let _highlightEventUid = null;

function _doCloseCalendar() {
  _open = false;
  _restoreSidebar();
  if (_modal) {
    _modal.style.display = 'none';
    _modal.classList.add('hidden');
  }
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
  // Drop any pending undo — closures captured event uids/state that may
  // no longer be valid by the time the user reopens. A reopened calendar
  // starts with a clean slate.
  _calUndoStack.length = 0;
}

function closeCalendar() {
  if (!_open && !Modals.isMinimized('calendar-modal')) return;
  if (Modals.isRegistered('calendar-modal')) {
    Modals.close('calendar-modal');
  } else {
    _doCloseCalendar();
  }
}

function isCalendarOpen() {
  // Treat minimized as "not open" so toggle handler will restore via Modals.toggle
  if (Modals.isMinimized('calendar-modal')) return false;
  return _open;
}

// ── Persistent cache (localStorage) ──
const LS_KEY = 'odysseus-calendar-cache';
const LS_TTL = 10 * 60 * 1000; // 10 min

function _saveCache() {
  try {
    const data = {
      ts: Date.now(),
      calendars: _calendars,
      events: Object.values(_allEvents),
      ranges: _fetchedRanges,
    };
    localStorage.setItem(LS_KEY, JSON.stringify(data));
  } catch (e) {}
}

function _loadCache() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (!data.ts || Date.now() - data.ts > LS_TTL) return false;
    if (data.calendars) _calendars = data.calendars;
    if (data.events) data.events.forEach(ev => { _allEvents[ev.uid] = ev; });
    // Don't restore _fetchedRanges — always re-fetch from API to pick up
    // external changes (e.g. TimeTree sync adding events)
    return true;
  } catch (e) { return false; }
}

// Boot: load cache, refresh badge, prefetch current month
(async () => {
  _loadCache();
  _updateBadge();
  try {
    await _fetchCalendars();
    _saveCache();
    const [s, e] = _monthRange(new Date());
    await _fetchEvents(s, e);
    _saveCache();
    _updateBadge();
  } catch (e) {}
})();

// Live-refresh when the AI agent adds/edits/deletes events. chat.js dispatches
// `calendar-refresh` after a manage_calendar tool call, so a new event shows up
// without the user hard-refreshing. Drop the cache (so adds/edits/deletes all
// reflect), refetch the visible range, re-render if open, and update the badge.
window.addEventListener('calendar-refresh', () => {
  _allEvents = {};
  _fetchedRanges = [];
  const range = (_view === 'year')
    ? [`${_currentDate.getFullYear()}-01-01`, `${_currentDate.getFullYear() + 1}-01-01`]
    : (_view === 'week') ? _weekRange(_currentDate) : _monthRange(_currentDate);
  _fetchEvents(range[0], range[1], /*force*/ true)
    .then(() => { if (_open) _render(); _updateBadge(); })
    .catch(() => {});
});

// Calendar reminders are stored as Notes. The Notes reminder loop owns
// notification dispatch so calendar reminders do not fire twice.

const calendarModule = { openCalendar, closeCalendar, isCalendarOpen };
export { openCalendar, openCalendarTo, closeCalendar, isCalendarOpen };
export default calendarModule;
