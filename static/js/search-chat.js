// Search Chat Module — Ctrl+K command palette for searching conversations

import uiModule from './ui.js';
import sessionModule from './sessions.js';

let API_BASE = '';
let debounceTimer = null;
let selectedIndex = -1;
let results = [];
let activeTypes = new Set();

const TYPE_FILTERS = [
  { key: 'chat', label: 'Chats' },
  { key: 'email', label: 'Email' },
  { key: 'document', label: 'Docs' },
  { key: 'note', label: 'Notes' },
  { key: 'task', label: 'Tasks' },
  { key: 'event', label: 'Calendar' },
  { key: 'memory', label: 'Memory' },
  { key: 'contact', label: 'Contacts' },
  { key: 'research', label: 'Research' },
];

const TYPE_LABELS = {
  chat: 'Chat',
  email: 'Email',
  document: 'Doc',
  note: 'Note',
  task: 'Task',
  event: 'Event',
  memory: 'Memory',
  contact: 'Contact',
  research: 'Research',
};

const GROUP_LABELS = {
  chat: 'Chats',
  email: 'Email',
  document: 'Documents',
  note: 'Notes',
  task: 'Tasks',
  event: 'Calendar',
  memory: 'Memory',
  contact: 'Contacts',
  research: 'Research',
};

function el(id) { return document.getElementById(id); }

export function openSearch() {
  const overlay = el('search-overlay');
  if (!overlay) return;
  activeTypes = new Set();
  ensureFilterRow();
  renderFilters();
  overlay.classList.remove('hidden');
  const input = el('search-input');
  if (input) {
    input.value = '';
    input.placeholder = 'Search workspace...';
    input.focus();
  }
  selectedIndex = -1;
  results = [];
  el('search-results').innerHTML = '';
}

export function closeSearch() {
  const overlay = el('search-overlay');
  if (!overlay) return;
  overlay.classList.add('hidden');
  el('search-results').innerHTML = '';
  selectedIndex = -1;
  results = [];
}

export function isOpen() {
  const overlay = el('search-overlay');
  return overlay && !overlay.classList.contains('hidden');
}

var escapeHtml = uiModule.esc;

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(String(value));
  return String(value).replace(/["\\]/g, '\\$&');
}

function highlightMatch(text, query) {
  if (!query) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const regex = new RegExp('(' + query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return escaped.replace(regex, '<mark class="search-highlight">$1</mark>');
}

function formatTimestamp(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  if (diff < 86400000) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (diff < 604800000) {
    return d.toLocaleDateString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function ensureFilterRow() {
  const popup = document.querySelector('.search-popup');
  const resultsEl = el('search-results');
  if (!popup || !resultsEl || el('search-type-filters')) return;
  const row = document.createElement('div');
  row.id = 'search-type-filters';
  row.className = 'search-type-filters';
  popup.insertBefore(row, resultsEl);
}

function renderFilters() {
  const row = el('search-type-filters');
  if (!row) return;
  const allActive = activeTypes.size === 0 ? ' active' : '';
  let html = `<button type="button" class="search-type-chip${allActive}" data-type="">All</button>`;
  for (const filter of TYPE_FILTERS) {
    const active = activeTypes.has(filter.key) ? ' active' : '';
    html += `<button type="button" class="search-type-chip${active}" data-type="${filter.key}">${filter.label}</button>`;
  }
  row.innerHTML = html;
  row.querySelectorAll('.search-type-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const type = btn.dataset.type;
      if (!type) {
        activeTypes.clear();
      } else {
        if (activeTypes.has(type)) activeTypes.delete(type);
        else activeTypes.add(type);
      }
      renderFilters();
      const input = el('search-input');
      const query = input ? input.value.trim() : '';
      if (query) scheduleSearch(query, 0);
    });
  });
}

function normalizeResult(item) {
  if (!item) return null;
  if (!item.type && item.session_id) {
    return {
      type: 'chat',
      id: item.message_id || item.id || item.session_id,
      title: item.session_name || 'Chat',
      snippet: item.content_snippet || item.snippet || '',
      timestamp: item.timestamp,
      subtitle: item.role === 'user' ? 'You' : 'AI',
      source_ref: {
        session_id: item.session_id,
        message_id: item.message_id || item.id,
        role: item.role,
      },
    };
  }
  return {
    type: item.type || 'chat',
    id: item.id,
    title: item.title || item.session_name || TYPE_LABELS[item.type] || 'Result',
    snippet: item.snippet || item.content_snippet || '',
    timestamp: item.timestamp,
    subtitle: item.subtitle || '',
    source_ref: item.source_ref || {},
  };
}

function renderResults(data, query) {
  const flat = Array.isArray(data) ? data : (data && Array.isArray(data.results) ? data.results : []);
  const normalized = flat.map(normalizeResult).filter(Boolean);
  results = [];
  selectedIndex = -1;
  const container = el('search-results');
  if (!container) return;

  if (normalized.length === 0) {
    container.innerHTML = query
      ? '<div class="search-empty">No results found</div>'
      : '';
    return;
  }

  const grouped = new Map();
  for (const item of normalized) {
    if (!grouped.has(item.type)) grouped.set(item.type, []);
    grouped.get(item.type).push(item);
  }

  let html = '';
  for (const type of TYPE_FILTERS.map(f => f.key)) {
    const group = grouped.get(type);
    if (!group || group.length === 0) continue;
    html += `<div class="search-group-header">${escapeHtml(GROUP_LABELS[type] || type)}</div>`;
    for (const item of group) {
      const idx = results.length;
      results.push(item);
      const meta = [item.subtitle, formatTimestamp(item.timestamp)].filter(Boolean).join(' · ');
      html += `<div class="search-result-item" data-index="${idx}">
        <div class="search-result-badge search-result-badge-${escapeHtml(type)}">${escapeHtml(TYPE_LABELS[type] || type)}</div>
        <div class="search-result-body">
          <div class="search-result-title">${highlightMatch(item.title, query)}</div>
          <div class="search-result-snippet">${highlightMatch(item.snippet, query)}</div>
        </div>
        <div class="search-result-time">${escapeHtml(meta)}</div>
      </div>`;
    }
  }
  container.innerHTML = html;

  // Click handlers
  container.querySelectorAll('.search-result-item').forEach(item => {
    item.addEventListener('click', () => {
      navigateToResult(results[Number(item.dataset.index)]);
    });
  });
}

function navigateToSession(sessionId) {
  closeSearch();
  if (sessionModule && sessionModule.selectSession) {
    sessionModule.selectSession(sessionId);
  }
}

async function navigateToResult(result) {
  if (!result) return;
  const ref = result.source_ref || {};
  closeSearch();

  if (result.type === 'chat') {
    if (ref.session_id && sessionModule && sessionModule.selectSession) {
      await sessionModule.selectSession(ref.session_id);
    }
    return;
  }

  if (result.type === 'document') {
    if (ref.session_id && sessionModule && sessionModule.selectSession) {
      await sessionModule.selectSession(ref.session_id);
    }
    const docModule = await import('./document.js');
    const load = docModule.loadDocument || docModule.default?.loadDocument;
    if (load && ref.document_id) await load(ref.document_id);
    return;
  }

  if (result.type === 'email') {
    const emailModule = await import('./emailLibrary.js');
    const open = emailModule.openEmailLibrary || emailModule.default?.openEmailLibrary;
    if (open) open({ folder: ref.folder || 'INBOX', uid: ref.uid, account_id: ref.account_id || null });
    return;
  }

  if (result.type === 'task') {
    const taskModule = await import('./tasks.js');
    const open = taskModule.openTasks || taskModule.default?.openTasks;
    if (open) open(ref.task_id);
    return;
  }

  if (result.type === 'event') {
    const calModule = await import('./calendar.js');
    const open = calModule.openCalendarTo || calModule.default?.openCalendarTo;
    if (open) open(ref.event_uid || ref.date);
    return;
  }

  if (result.type === 'note') {
    const notesModule = await import('./notes.js');
    const open = notesModule.openNotes || notesModule.default?.openNotes;
    if (open) open();
    focusLater(`.note-card[data-note-id="${cssEscape(ref.note_id)}"]`, 'note-card-reminder-fired');
    return;
  }

  if (result.type === 'memory') {
    document.getElementById('tool-memory-btn')?.click();
    focusLater(`.memory-item[data-memory-id="${cssEscape(ref.memory_id)}"]`, 'memory-tidy-editing');
    const input = document.getElementById('memory-search');
    if (input) {
      input.value = result.snippet || result.title || '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    return;
  }

  if (result.type === 'research') {
    const doclib = await import('./documentLibrary.js');
    const open = doclib.openLibrary || doclib.default?.openLibrary;
    if (open) open();
    setTimeout(() => {
      document.querySelector('[data-doclib-tab="research"]')?.click();
      const input = document.getElementById('doclib-research-search');
      if (input) {
        input.value = result.title || '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }, 120);
    return;
  }

  if (result.type === 'contact') {
    document.getElementById('rail-settings')?.click();
    setTimeout(() => {
      document.querySelector('[data-settings-tab="integrations"]')?.click();
    }, 120);
  }
}

function focusLater(selector, className) {
  setTimeout(() => {
    const target = document.querySelector(selector);
    if (!target) return;
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (className) {
      target.classList.add(className);
      setTimeout(() => target.classList.remove(className), 1800);
    }
  }, 350);
}

function updateSelection() {
  const container = el('search-results');
  if (!container) return;
  const items = container.querySelectorAll('.search-result-item');
  items.forEach((item, i) => {
    item.classList.toggle('selected', i === selectedIndex);
  });
  // Scroll selected into view
  if (selectedIndex >= 0 && items[selectedIndex]) {
    items[selectedIndex].scrollIntoView({ block: 'nearest' });
  }
}

function handleKeydown(e) {
  if (!isOpen()) return;

  const container = el('search-results');
  const items = container ? container.querySelectorAll('.search-result-item') : [];
  const count = items.length;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    selectedIndex = count > 0 ? Math.min(selectedIndex + 1, count - 1) : -1;
    updateSelection();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    selectedIndex = Math.max(selectedIndex - 1, 0);
    updateSelection();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (selectedIndex >= 0 && items[selectedIndex]) {
      navigateToResult(results[Number(items[selectedIndex].dataset.index)]);
    }
  }
}

function handleInput(e) {
  const query = e.target.value.trim();
  scheduleSearch(query, 300);
}

function scheduleSearch(query, delay) {
  if (debounceTimer) clearTimeout(debounceTimer);

  if (!query) {
    renderResults([], '');
    return;
  }

  debounceTimer = setTimeout(() => performSearch(query), delay);
}

function typeQueryParam() {
  return activeTypes.size ? Array.from(activeTypes).join(',') : '';
}

async function performSearch(query) {
  try {
    const params = new URLSearchParams({ q: query, limit: '30' });
    const typeParam = typeQueryParam();
    if (typeParam) params.set('types', typeParam);
    const res = await fetch(`${API_BASE}/api/search/all?${params.toString()}`);
    if (!res.ok) throw new Error(`Search failed: ${res.status}`);
    const data = await res.json();
    renderResults(data, query);
  } catch (err) {
    console.error('Search error:', err);
    const chatOnly = activeTypes.size === 0 || (activeTypes.size === 1 && activeTypes.has('chat'));
    if (!chatOnly) {
      renderResults({ results: [] }, query);
      return;
    }
    try {
      const fallback = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=20`);
      if (fallback.ok) renderResults(await fallback.json(), query);
    } catch (_) {
      renderResults({ results: [] }, query);
    }
  }
}

export function init(apiBase) {
  API_BASE = apiBase || '';
  ensureFilterRow();
  renderFilters();

  const input = el('search-input');
  if (input) {
    input.addEventListener('input', handleInput);
    input.addEventListener('keydown', handleKeydown);
  }

  // Close on overlay click (not popup click)
  const overlay = el('search-overlay');
  if (overlay) {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeSearch();
    });
  }
}

const searchChatModule = {
  init,
  openSearch,
  closeSearch,
  isOpen,
};

export default searchChatModule;
