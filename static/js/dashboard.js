// static/js/dashboard.js
// Homescreen dashboard rendered inside #welcome-screen (the landing view
// shown before a chat starts). Lazily imported by chatRenderer.js's
// showWelcomeScreen(), same convention as notes.js/tasks.js/calendar.js.
//
// Each widget fetches independently (Promise-per-card, not Promise.all) and
// fails silently into its own empty/error state, so one broken data source
// never blanks the rest of the dashboard.

const API_BASE = window.location.origin;
let _clockTimer = null;

function _el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

async function _getJSON(path) {
  const r = await fetch(`${API_BASE}${path}`, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function _empty(body, text) {
  body.appendChild(_el('div', 'dash-empty', text));
}

// To-do text is free-form and often a full sentence; cut it so one item stays
// a couple of lines instead of dominating the card. Full text goes in title.
const _TODO_MAX_CHARS = 64;
function _truncate(text, max = _TODO_MAX_CHARS) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max - 1).trimEnd() + '…' : s;
}

const ICONS = {
  clock: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  weather: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19a4.5 4.5 0 0 0 0-9 6 6 0 0 0-11.4-2A5 5 0 0 0 6 18h11.5z"/></svg>',
  tasks: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M9 16l2 2 4-4"/></svg>',
  chats: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  calendar: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  email: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>',
  notes: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h5"/></svg>',
};

function _card(container, title, iconKey) {
  const card = _el('div', 'dash-card');
  const header = _el('div', 'dash-card-header');
  const icon = _el('span', 'dash-card-icon');
  icon.innerHTML = ICONS[iconKey] || '';
  header.appendChild(icon);
  header.appendChild(_el('span', 'dash-card-title', title));
  card.appendChild(header);
  const body = _el('div', 'dash-card-body');
  card.appendChild(body);
  container.appendChild(card);
  body.appendChild(_el('div', 'dash-loading', 'Loading…'));
  return body;
}

// ── Clock ──
function _renderClock(container) {
  const body = _card(container, 'Now', 'clock');
  body.textContent = '';
  const time = _el('div', 'dash-clock-time');
  const date = _el('div', 'dash-clock-date');
  body.appendChild(time);
  body.appendChild(date);
  const tick = () => {
    // Stop once the welcome screen is gone (user opened a chat), otherwise
    // this interval outlives the widgets it updates.
    if (!document.body.contains(time)) {
      if (_clockTimer) { clearInterval(_clockTimer); _clockTimer = null; }
      return;
    }
    const now = new Date();
    time.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    date.textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  };
  tick();
  if (_clockTimer) clearInterval(_clockTimer);
  _clockTimer = setInterval(tick, 30000);
}

// ── Weather ──
function _renderWeatherCard(container) {
  const body = _card(container, 'Weather', 'weather');
  _loadWeather(body);
}

function _appendChangeLocationLink(body) {
  const link = _el('button', 'dash-link-btn', 'Change location');
  link.type = 'button';
  link.addEventListener('click', () => _renderWeatherSetup(body));
  body.appendChild(link);
}

async function _loadWeather(body) {
  let location = null;
  try {
    const pref = await _getJSON('/api/prefs/dashboard_weather_location');
    location = pref && pref.value ? String(pref.value) : null;
  } catch (_) { /* prefs unavailable — fall through to setup */ }

  if (!location) {
    _renderWeatherSetup(body);
    return;
  }

  try {
    const data = await _getJSON(`/api/weather?location=${encodeURIComponent(location)}`);
    body.textContent = '';
    if (!data.ok) {
      _empty(body, data.error === 'location_not_found' ? 'Location not found.' : 'Weather unavailable.');
      _appendChangeLocationLink(body);
      return;
    }
    const top = _el('div', 'dash-weather-top');
    top.appendChild(_el('span', 'dash-weather-temp', data.temp != null ? `${Math.round(data.temp)}°C` : '—'));
    top.appendChild(_el('span', 'dash-weather-cond', data.condition_label || ''));
    body.appendChild(top);
    if (data.high != null && data.low != null) {
      body.appendChild(_el('div', 'dash-weather-hilo', `H:${Math.round(data.high)}°  L:${Math.round(data.low)}°`));
    }
    body.appendChild(_el('div', 'dash-weather-loc', data.location_label || location));
    _appendChangeLocationLink(body);
  } catch (_) {
    body.textContent = '';
    _empty(body, 'Weather unavailable.');
    _appendChangeLocationLink(body);
  }
}

function _renderWeatherSetup(body) {
  body.textContent = '';
  _empty(body, 'Set a location to see weather.');
  const row = _el('div', 'dash-weather-setup-row');
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'dash-weather-input';
  input.placeholder = 'City, region, or ZIP';
  const saveBtn = _el('button', 'dash-link-btn', 'Save');
  saveBtn.type = 'button';
  const save = async () => {
    const val = input.value.trim();
    if (!val) return;
    saveBtn.disabled = true;
    try {
      await fetch(`${API_BASE}/api/prefs/dashboard_weather_location`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: val }),
      });
      await _loadWeather(body);
    } finally {
      saveBtn.disabled = false;
    }
  };
  saveBtn.addEventListener('click', save);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
  row.appendChild(input);
  row.appendChild(saveBtn);
  body.appendChild(row);
}

// ── Today: automations due today + open to-do items ──
function _isToday(iso) {
  if (!iso) return false;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return false;
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

function _renderTodayCard(container) {
  const body = _card(container, 'Today', 'tasks');
  _loadToday(body);
}

async function _loadToday(body) {
  const items = [];
  try {
    const data = await _getJSON('/api/tasks');
    (data.tasks || []).forEach(t => {
      if (t.status === 'active' && _isToday(t.next_run)) {
        items.push({ kind: 'task', id: t.id, label: t.name || t.prompt || 'Automation' });
      }
    });
  } catch (_) { /* automations unavailable */ }
  try {
    const data = await _getJSON('/api/notes');
    (data.notes || []).forEach(n => {
      if (!['todo', 'checklist', 'goal'].includes(n.note_type)) return;
      (n.items || []).forEach((it, idx) => {
        if (!it.done) items.push({ kind: 'todo', noteId: n.id, index: idx, label: it.text || '(untitled)' });
      });
    });
  } catch (_) { /* notes unavailable */ }

  body.textContent = '';
  if (!items.length) {
    _empty(body, 'Nothing urgent today.');
    return;
  }
  items.slice(0, 8).forEach(item => {
    if (item.kind === 'todo') {
      // Same markup/classes as the Notes panel's own checklist rows
      // (note.js / .note-checkbox, .note-check-dot, .note-check-text) so
      // this widget's tick style matches the Notes app exactly.
      const row = _el('div', 'note-checkbox dash-today-item');
      row.title = item.label;
      row.appendChild(_el('span', 'note-check-dot'));
      row.appendChild(_el('span', 'note-check-text', _truncate(item.label)));
      row.addEventListener('click', async () => {
        if (row.classList.contains('done')) return;
        row.classList.add('done');
        try {
          await fetch(`${API_BASE}/api/notes/${encodeURIComponent(item.noteId)}/items/${item.index}/toggle`, {
            method: 'POST',
            credentials: 'same-origin',
          });
        } catch (_) { /* best-effort */ }
        setTimeout(() => {
          row.remove();
          if (!body.querySelector('.dash-today-item')) _empty(body, 'Nothing urgent today.');
        }, 320);
      });
      body.appendChild(row);
    } else {
      const row = _el('div', 'dash-list-item dash-today-item');
      row.classList.add('dash-clickable');
      row.title = item.label;
      row.appendChild(_el('span', 'dash-today-badge', 'Task'));
      row.appendChild(_el('span', 'dash-today-label', _truncate(item.label)));
      row.addEventListener('click', () => {
        import('./tasks.js').then(m => { if (m.openTasks) m.openTasks(item.id); }).catch(() => {});
      });
      body.appendChild(row);
    }
  });
}

// ── Last 3 chats ──
function _renderChatsCard(container) {
  const body = _card(container, 'Recent Chats', 'chats');
  _loadChats(body);
}

async function _loadChats(body) {
  let sessions = [];
  try {
    const mod = await import('./sessions.js');
    sessions = mod.getSessions ? mod.getSessions() : [];
  } catch (_) { /* sessions module unavailable */ }

  const filtered = (sessions || [])
    .filter(s => !s.archived && s.folder !== 'Assistant'
      && (s.name || '').trim() !== 'Nobody' && (s.name || '').trim() !== 'Incognito')
    .sort((a, b) => {
      const av = a.last_message_at || a.updated_at || a.created_at || '';
      const bv = b.last_message_at || b.updated_at || b.created_at || '';
      return bv.localeCompare(av);
    })
    .slice(0, 3);

  body.textContent = '';
  if (!filtered.length) {
    _empty(body, 'No recent chats.');
    return;
  }
  filtered.forEach(s => {
    const row = _el('div', 'dash-list-item dash-clickable', s.name || 'Untitled chat');
    row.addEventListener('click', () => {
      import('./sessions.js').then(m => { if (m.selectSession) m.selectSession(s.id); }).catch(() => {});
    });
    body.appendChild(row);
  });
}

// ── Upcoming calendar events ──
function _renderCalendarCard(container) {
  const body = _card(container, 'Upcoming', 'calendar');
  _loadCalendar(body);
}

function _formatEventWhen(dtstart) {
  if (!dtstart) return '';
  const isAllDay = /^\d{4}-\d{2}-\d{2}$/.test(dtstart);
  const d = new Date(dtstart);
  if (isNaN(d.getTime())) return '';
  return isAllDay
    ? d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

async function _loadCalendar(body) {
  try {
    const start = new Date();
    const end = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
    const data = await _getJSON(`/api/calendar/events?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`);
    const events = (data.events || []).slice(0, 5);
    body.textContent = '';
    if (!events.length) {
      _empty(body, 'No upcoming events.');
      return;
    }
    events.forEach(ev => {
      const row = _el('div', 'dash-list-item dash-clickable');
      row.appendChild(_el('span', 'dash-event-when', _formatEventWhen(ev.dtstart)));
      row.appendChild(_el('span', 'dash-event-label', ev.summary || '(untitled event)'));
      row.addEventListener('click', () => {
        import('./calendar.js').then(m => { if (m.openCalendarTo) m.openCalendarTo(ev.uid); }).catch(() => {});
      });
      body.appendChild(row);
    });
  } catch (_) {
    body.textContent = '';
    _empty(body, 'Calendar unavailable.');
  }
}

// ── New email ──
function _renderEmailCard(container) {
  const body = _card(container, 'Email', 'email');
  _loadEmail(body);
}

async function _loadEmail(body) {
  try {
    const data = await _getJSON('/api/email/unread-state?folder=INBOX');
    const count = Number(data.unread_count || 0);
    body.textContent = '';
    if (!count) {
      _empty(body, 'Inbox zero.');
      return;
    }
    const row = _el('div', 'dash-list-item dash-clickable dash-email-row');
    row.appendChild(_el('span', 'dash-email-count', String(count)));
    row.appendChild(_el('span', 'dash-today-label', count === 1 ? 'new email' : 'new emails'));
    row.addEventListener('click', () => {
      document.getElementById('rail-email')?.click();
    });
    body.appendChild(row);
  } catch (_) {
    body.textContent = '';
    _empty(body, 'Email unavailable.');
  }
}

// ── Pinned notes ──
function _renderNotesCard(container) {
  const body = _card(container, 'Pinned Notes', 'notes');
  _loadNotes(body);
}

async function _loadNotes(body) {
  try {
    const data = await _getJSON('/api/notes');
    const pinned = (data.notes || []).filter(n => n.pinned).slice(0, 5);
    body.textContent = '';
    if (!pinned.length) {
      _empty(body, 'No pinned notes.');
      return;
    }
    pinned.forEach(n => {
      const row = _el('div', 'dash-list-item dash-clickable', n.title || (n.content || '').slice(0, 60) || '(untitled note)');
      row.addEventListener('click', () => {
        import('./notes.js').then(m => { if (m.openPanel) m.openPanel(); }).catch(() => {});
      });
      body.appendChild(row);
    });
  } catch (_) {
    body.textContent = '';
    _empty(body, 'Notes unavailable.');
  }
}

export function refreshDashboard() {
  const container = document.getElementById('dashboard-grid');
  if (!container) return;
  container.textContent = '';
  _renderClock(container);
  _renderWeatherCard(container);
  _renderTodayCard(container);
  _renderCalendarCard(container);
  _renderEmailCard(container);
  _renderNotesCard(container);
  _renderChatsCard(container);
}

