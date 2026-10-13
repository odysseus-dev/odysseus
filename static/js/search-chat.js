// Search Chat Module — Ctrl+K command palette for searching conversations

import uiModule from './ui.js?v=20260916largetoolscroll1';
import sessionModule from './sessions.js';

let API_BASE = '';
let debounceTimer = null;
let selectedIndex = -1;
let results = [];

function el(id) { return document.getElementById(id); }

function hideMobileSidebarForSearch() {
  if (window.innerWidth >= 768) return;
  const sidebar = el('sidebar');
  const rail = el('icon-rail');
  const backdrop = el('sidebar-backdrop');
  let changed = false;
  if (sidebar && !sidebar.classList.contains('hidden')) {
    sidebar.classList.add('hidden');
    changed = true;
  }
  if (rail && rail.classList.contains('mobile-mini')) {
    rail.classList.remove('mobile-mini');
    rail.style.cssText = '';
    changed = true;
  }
  if (backdrop) backdrop.classList.remove('visible');
  if (changed && typeof window.syncRailSide === 'function') {
    try { window.syncRailSide(); } catch (_) {}
  }
}

export function openSearch() {
  hideMobileSidebarForSearch();
  const overlay = el('search-overlay');
  if (!overlay) return;
  // Freeze the opening offset before focus summons the mobile keyboard.
  // A live `vh` value shrinks during the keyboard animation and visibly
  // pulls the search popup upward even though it is already unobscured.
  const openingHeight = window.innerHeight || document.documentElement.clientHeight;
  const openingTop = Math.max(56, Math.min(140, Math.round(openingHeight * 0.15)));
  overlay.style.setProperty('--search-overlay-top', `${openingTop}px`);
  overlay.classList.remove('hidden');
  const input = el('search-input');
  if (input) {
    input.value = '';
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

function renderResults(data, query) {
  results = data;
  selectedIndex = -1;
  const container = el('search-results');
  if (!container) return;

  if (!data || data.length === 0) {
    container.innerHTML = query
      ? '<div class="search-empty">No results found</div>'
      : '';
    return;
  }

  // Group by session
  const grouped = {};
  for (const r of data) {
    if (!grouped[r.session_id]) {
      grouped[r.session_id] = { name: r.session_name, items: [] };
    }
    grouped[r.session_id].items.push(r);
  }

  let html = '';
  let idx = 0;
  for (const [sessionId, group] of Object.entries(grouped)) {
    html += `<div class="search-group-header">${escapeHtml(group.name)}</div>`;
    for (const item of group.items) {
      const roleLabel = item.role === 'user' ? 'You' : 'AI';
      html += `<div class="search-result-item" data-index="${idx}" data-session="${escapeHtml(sessionId)}">
        <div class="search-result-role">${roleLabel}</div>
        <div class="search-result-snippet">${highlightMatch(item.content_snippet, query)}</div>
        <div class="search-result-time">${formatTimestamp(item.timestamp)}</div>
      </div>`;
      idx++;
    }
  }
  container.innerHTML = html;

  // Click handlers
  container.querySelectorAll('.search-result-item').forEach(item => {
    item.addEventListener('click', () => {
      const sid = item.dataset.session;
      navigateToSession(sid);
    });
  });
}

function navigateToSession(sessionId) {
  closeSearch();
  if (sessionModule && sessionModule.selectSession) {
    sessionModule.selectSession(sessionId);
  }
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
      const sid = items[selectedIndex].dataset.session;
      navigateToSession(sid);
    }
  }
}

function handleInput(e) {
  const query = e.target.value.trim();
  if (debounceTimer) clearTimeout(debounceTimer);

  if (!query) {
    renderResults([], '');
    return;
  }

  debounceTimer = setTimeout(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=20`);
      if (!res.ok) return;
      const data = await res.json();
      renderResults(data, query);
    } catch (err) {
      console.error('Search error:', err);
    }
  }, 300);
}

export function init(apiBase) {
  API_BASE = apiBase || '';

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
