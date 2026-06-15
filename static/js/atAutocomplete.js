// static/js/atAutocomplete.js
// @-mention autocomplete for attaching library context to the current chat.

const POPUP_ID = 'at-autocomplete';
const DEBOUNCE_MS = 200;
const MAX_PER_CATEGORY = 8;

let API_BASE = '';

function _esc(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

function _ensurePopup() {
  let el = document.getElementById(POPUP_ID);
  if (el) return el;
  el = document.createElement('div');
  el.id = POPUP_ID;
  el.className = 'at-autocomplete-popup';
  el.setAttribute('role', 'listbox');
  el.setAttribute('aria-label', 'Attach library context');
  document.body.appendChild(el);
  return el;
}

function _position(popup, textarea) {
  const r = textarea.getBoundingClientRect();
  const maxH = Math.min(window.innerHeight * 0.5, 360);
  popup.style.maxHeight = maxH + 'px';
  popup.style.left = Math.round(r.left) + 'px';
  popup.style.width = Math.max(280, Math.round(Math.min(r.width, 520))) + 'px';
  const aboveSpace = r.top;
  if (aboveSpace > maxH + 20) {
    popup.style.bottom = (window.innerHeight - r.top + 6) + 'px';
    popup.style.top = '';
  } else {
    popup.style.top = (r.bottom + 6) + 'px';
    popup.style.bottom = '';
  }
}

function _render(popup, items, selectedIdx, query) {
  if (!items.length) {
    popup.innerHTML = `<div class="at-ac-empty">No results for <code>${_esc(query)}</code></div>`;
    return;
  }
  let html = '';
  let lastCat = null;
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    if (it.category !== lastCat) {
      html += `<div class="at-ac-cat">${_esc(it.category)}</div>`;
      lastCat = it.category;
    }
    const sel = i === selectedIdx ? ' at-ac-row-sel' : '';
    const badge = `<span class="at-ac-badge">${_esc(it.typeLabel)}</span>`;
    html += `<div class="at-ac-row${sel}" role="option" data-idx="${i}" data-type="${_esc(it.type)}" data-id="${_esc(it.id)}" data-title="${_esc(it.title)}">`
         +    `<span class="at-ac-title">${_esc(it.title)}</span>`
         +    badge
         + `</div>`;
  }
  popup.innerHTML = html;
  const selEl = popup.querySelector('.at-ac-row-sel');
  if (selEl) selEl.scrollIntoView({ block: 'nearest' });
}

async function _searchDocuments(q) {
  if (!API_BASE) return [];
  try {
    const res = await fetch(`${API_BASE}/api/documents/library?search=${encodeURIComponent(q)}&limit=${MAX_PER_CATEGORY}`, { credentials: 'same-origin' });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.documents || []).map(d => ({
      type: 'document',
      id: d.id,
      title: d.title || 'Untitled',
      category: 'Documents',
      typeLabel: 'Doc',
    }));
  } catch {
    return [];
  }
}

async function _searchResearch(q) {
  if (!API_BASE) return [];
  try {
    const res = await fetch(`${API_BASE}/api/research/library?search=${encodeURIComponent(q)}&limit=${MAX_PER_CATEGORY}`, { credentials: 'same-origin' });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.items || []).map(r => ({
      type: 'research',
      id: r.id,
      title: r.query || 'Research',
      category: 'Research',
      typeLabel: 'Research',
    }));
  } catch {
    return [];
  }
}

function _searchSessions(q, getSessions) {
  const sessions = typeof getSessions === 'function' ? getSessions() : [];
  if (!Array.isArray(sessions)) return [];
  const qlow = q.toLowerCase();
  return sessions
    .filter(s => s && s.id && (s.name || '').toLowerCase().includes(qlow))
    .slice(0, MAX_PER_CATEGORY)
    .map(s => ({
      type: 'session',
      id: s.id,
      title: s.name || 'Chat',
      category: 'Chats',
      typeLabel: 'Chat',
    }));
}

function _parseQuery(textarea) {
  const value = textarea.value;
  const start = textarea.selectionStart || 0;
  const before = value.slice(0, start);
  // Find the nearest @ before the cursor
  const atIdx = before.lastIndexOf('@');
  if (atIdx === -1) return null;
  // Must be preceded by whitespace/start
  if (atIdx > 0 && !/\s/.test(before[atIdx - 1])) return null;
  const query = before.slice(atIdx + 1);
  // Stop at whitespace or newline inside the query
  if (/\s/.test(query)) return null;
  return { atIdx, query };
}

export function initAtAutocomplete(textarea, { apiBase, getCurrentSessionId, getSessions, onPick }) {
  if (!textarea || textarea._atAcWired) return;
  textarea._atAcWired = true;
  API_BASE = apiBase || '';

  let popup = null;
  let visible = false;
  let items = [];
  let selectedIdx = 0;
  let debounce = null;

  const hide = () => {
    if (!visible) return;
    visible = false;
    if (popup) popup.style.display = 'none';
  };

  const show = () => {
    if (!popup) popup = _ensurePopup();
    visible = true;
    popup.style.display = 'block';
    _position(popup, textarea);
  };

  const refresh = async () => {
    const parsed = _parseQuery(textarea);
    if (!parsed) { hide(); return; }
    const { atIdx, query } = parsed;
    if (!query.length) {
      items = [];
      _render(popup || _ensurePopup(), items, 0, query);
      show();
      return;
    }
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(async () => {
      const [docs, research, chats] = await Promise.all([
        _searchDocuments(query),
        _searchResearch(query),
        Promise.resolve(_searchSessions(query, getSessions)),
      ]);
      items = [...docs, ...research, ...chats];
      selectedIdx = 0;
      if (!visible) show();
      _render(popup, items, selectedIdx, query);
    }, DEBOUNCE_MS);
  };

  const pick = (item) => {
    const parsed = _parseQuery(textarea);
    if (!parsed) return;
    const { atIdx } = parsed;
    const before = textarea.value.slice(0, atIdx);
    const after = textarea.value.slice(textarea.selectionStart || 0);
    textarea.value = before + after;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
    const pos = before.length;
    textarea.setSelectionRange(pos, pos);
    hide();
    if (typeof onPick === 'function') {
      const sessionId = typeof getCurrentSessionId === 'function' ? getCurrentSessionId() : null;
      onPick({ type: item.type, id: item.id, title: item.title }, sessionId);
    }
  };

  textarea.addEventListener('input', refresh);
  textarea.addEventListener('focus', refresh);
  textarea.addEventListener('blur', () => { setTimeout(hide, 120); });

  textarea.addEventListener('keydown', (e) => {
    if (!visible || !items.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIdx = (selectedIdx + 1) % items.length;
      _render(popup, items, selectedIdx, '');
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIdx = (selectedIdx - 1 + items.length) % items.length;
      _render(popup, items, selectedIdx, '');
    } else if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
      e.preventDefault();
      pick(items[selectedIdx]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      hide();
    }
  });

  window.addEventListener('resize', () => { if (visible) _position(popup, textarea); });

  document.addEventListener('mousedown', (e) => {
    if (!visible || !popup) return;
    const row = e.target.closest?.('.at-ac-row');
    if (row && popup.contains(row)) {
      e.preventDefault();
      const item = items[Number(row.dataset.idx)];
      if (item) pick(item);
    }
  });
}

export default { initAtAutocomplete };
