// static/js/bookstack.js
// BookStack Modal — browse, search, read pages from BookStack wiki.
import { makeWindowDraggable } from './windowDrag.js';

const _t = (k, v) => (window.__t || (kk => kk))(k, v);
const API_BASE = '/api/bookstack';

let _open = false;
let _modal = null;
let _currentShelf = null;
let _currentBook = null;
let _currentPage = null;
let _searchQuery = '';

// ---- API ----

async function _api(endpoint, options = {}) {
  try {
    const res = await fetch(`${API_BASE}/${endpoint}`, {
      credentials: 'same-origin',
      ...options,
    });
    if (!res.ok) {
      const err = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}: ${err.slice(0, 200)}`);
    }
    return await res.json();
  } catch (e) {
    console.error('BookStack API error:', e);
    return null;
  }
}

async function _search(query) {
  return _api(`search?query=${encodeURIComponent(query)}&count=20`);
}

async function _listShelves() {
  return _api('shelves');
}

async function _listBooks(shelfId) {
  const endpoint = shelfId ? `books?filter[shelf_id:eq]=${shelfId}` : 'books';
  return _api(endpoint);
}

async function _getBook(bookId) {
  return _api(`books/${bookId}`);
}

async function _getPage(pageId) {
  return _api(`pages/${pageId}`);
}

async function _exportPage(pageId) {
  try {
    const res = await fetch(`${API_BASE}/pages/${pageId}/export/markdown`, { credentials: 'same-origin' });
    if (!res.ok) return null;
    return await res.text();
  } catch (e) {
    return null;
  }
}

// ---- UI Rendering ----

function _createModal() {
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'bookstack-modal';
  modal.innerHTML = `
    <div class="modal-content bs-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
          ${_t('nav.bookstack', 'BookStack')}
        </h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="bs-close-btn" title="${_t('common.close', 'Close')}">&times;</button>
      </div>
      <div class="modal-body bs-modal-body">
        <div class="bs-toolbar">
          <div class="bs-breadcrumb" id="bs-breadcrumb"></div>
          <div class="bs-search">
            <input type="text" id="bs-search-input" class="fb-search-input" placeholder="${_t('bookstack.search', 'Search BookStack...')}" autocomplete="off" />
          </div>
        </div>
        <div class="bs-content">
          <div class="bs-sidebar" id="bs-sidebar"></div>
          <div class="bs-main" id="bs-main">
            <div class="bs-empty">${_t('bookstack.selectItem', 'Select an item to view')}</div>
          </div>
        </div>
      </div>
    </div>`;

  document.body.appendChild(modal);

  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (content && header) {
    makeWindowDraggable(modal, { content, header });
  }

  modal.addEventListener('click', (e) => {
    if (e.target === modal) closePanel();
  });

  return modal;
}

function _renderBreadcrumb() {
  const el = document.getElementById('bs-breadcrumb');
  if (!el) return;

  let html = `<button class="fb-breadcrumb-item" data-action="home">${_t('bookstack.home', 'Home')}</button>`;

  if (_currentShelf) {
    html += `<span class="fb-breadcrumb-sep">/</span>`;
    html += `<button class="fb-breadcrumb-item" data-action="shelf" data-id="${_currentShelf.id}">${_esc(_currentShelf.name)}</button>`;
  }
  if (_currentBook) {
    html += `<span class="fb-breadcrumb-sep">/</span>`;
    html += `<button class="fb-breadcrumb-item" data-action="book" data-id="${_currentBook.id}">${_esc(_currentBook.name)}</button>`;
  }
  if (_currentPage) {
    html += `<span class="fb-breadcrumb-sep">/</span>`;
    html += `<span class="fb-breadcrumb-item">${_esc(_currentPage.name)}</span>`;
  }

  el.innerHTML = html;

  el.querySelectorAll('.fb-breadcrumb-item[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      if (action === 'home') _navigateHome();
      else if (action === 'shelf') _navigateToShelf(parseInt(btn.dataset.id));
      else if (action === 'book') _navigateToBook(parseInt(btn.dataset.id));
    });
  });
}

async function _renderSidebar() {
  const el = document.getElementById('bs-sidebar');
  if (!el) return;

  el.innerHTML = `<div class="bs-loading">${_t('bookstack.loading', 'Loading...')}</div>`;

  const data = await _listShelves();
  if (!data) {
    el.innerHTML = `<div class="bs-empty">${_t('bookstack.notConfigured', 'BookStack not configured. Add it in Settings → Integrations.')}</div>`;
    return;
  }
  if (!data.data) {
    el.innerHTML = `<div class="bs-empty">${_t('bookstack.error', 'Error loading content')}</div>`;
    return;
  }

  let html = '';
  for (const shelf of data.data) {
    html += `<div class="bs-item bs-shelf" data-shelf-id="${shelf.id}">
      <span class="bs-item-icon">📚</span>
      <span class="bs-item-name">${_esc(shelf.name)}</span>
    </div>`;
  }

  el.innerHTML = html;

  el.querySelectorAll('.bs-shelf').forEach(item => {
    item.addEventListener('click', () => _navigateToShelf(parseInt(item.dataset.shelfId)));
  });
}

async function _renderBooks(shelfId) {
  const el = document.getElementById('bs-sidebar');
  if (!el) return;

  el.innerHTML = `<div class="bs-loading">${_t('bookstack.loading', 'Loading...')}</div>`;

  const data = await _listBooks(shelfId);
  if (!data || !data.data) {
    el.innerHTML = `<div class="bs-empty">${_t('bookstack.noBooks', 'No books found')}</div>`;
    return;
  }

  let html = `<button class="bs-back-btn" id="bs-back-shelves">← ${_t('bookstack.backToShelves', 'Back to shelves')}</button>`;
  for (const book of data.data) {
    html += `<div class="bs-item bs-book" data-book-id="${book.id}">
      <span class="bs-item-icon">📖</span>
      <span class="bs-item-name">${_esc(book.name)}</span>
    </div>`;
  }

  el.innerHTML = html;

  document.getElementById('bs-back-shelves')?.addEventListener('click', () => _navigateHome());
  el.querySelectorAll('.bs-book').forEach(item => {
    item.addEventListener('click', () => _navigateToBook(parseInt(item.dataset.bookId)));
  });
}

async function _renderBookContents(bookId) {
  const el = document.getElementById('bs-sidebar');
  if (!el) return;

  el.innerHTML = `<div class="bs-loading">${_t('bookstack.loading', 'Loading...')}</div>`;

  const data = await _getBook(bookId);
  if (!data) {
    el.innerHTML = `<div class="bs-empty">${_t('bookstack.error', 'Error loading book')}</div>`;
    return;
  }

  _currentBook = { id: data.id, name: data.name };

  let html = `<button class="bs-back-btn" id="bs-back-list">← ${_t('bookstack.back', 'Back')}</button>`;
  const contents = data.contents || [];
  for (const item of contents) {
    const icon = item.type === 'chapter' ? '📑' : '📄';
    const indent = item.type === 'page' ? 'padding-left:16px;' : '';
    html += `<div class="bs-item bs-${item.type}" data-type="${item.type}" data-id="${item.id}" style="${indent}">
      <span class="bs-item-icon">${icon}</span>
      <span class="bs-item-name">${_esc(item.name)}</span>
    </div>`;
  }

  el.innerHTML = html;

  document.getElementById('bs-back-list')?.addEventListener('click', () => {
    if (_currentShelf) _renderBooks(_currentShelf.id);
    else _navigateHome();
  });

  el.querySelectorAll('.bs-page').forEach(item => {
    item.addEventListener('click', () => _showPage(parseInt(item.dataset.id)));
  });
  el.querySelectorAll('.bs-chapter').forEach(item => {
    item.addEventListener('click', () => {
      const pageItems = contents.filter(c => c.type === 'page' && c.chapter_id === parseInt(item.dataset.id));
      if (pageItems.length) _showPage(pageItems[0].id);
    });
  });
}

async function _showPage(pageId) {
  const main = document.getElementById('bs-main');
  if (!main) return;

  main.innerHTML = `<div class="bs-loading">${_t('bookstack.loading', 'Loading...')}</div>`;

  const data = await _getPage(pageId);
  if (!data) {
    main.innerHTML = `<div class="bs-empty">${_t('bookstack.error', 'Error loading page')}</div>`;
    return;
  }

  _currentPage = { id: data.id, name: data.name };

  const content = data.html || data.markdown || '';
  main.innerHTML = `
    <div class="bs-page-header">
      <h3>${_esc(data.name)}</h3>
      <div class="bs-page-actions">
        <button class="fb-btn" id="bs-export-btn">${_t('bookstack.export', 'Export')}</button>
      </div>
    </div>
    <div class="bs-page-content">${content}</div>`;

  document.getElementById('bs-export-btn')?.addEventListener('click', async () => {
    const md = await _exportPage(pageId);
    if (md) {
      const blob = new Blob([md], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${data.name.replace(/[^a-z0-9]/gi, '_')}.md`;
      a.click();
      URL.revokeObjectURL(url);
    }
  });

  _renderBreadcrumb();
}

async function _performSearch(query) {
  const main = document.getElementById('bs-main');
  if (!main) return;

  main.innerHTML = `<div class="bs-loading">${_t('bookstack.searching', 'Searching...')}</div>`;

  const data = await _search(query);
  if (!data || !data.data) {
    main.innerHTML = `<div class="bs-empty">${_t('bookstack.noResults', 'No results found')}</div>`;
    return;
  }

  let html = `<div class="bs-search-results"><h3>${_t('bookstack.results', 'Results')}: ${data.total || data.data.length}</h3>`;
  for (const item of data.data) {
    const typeIcon = { page: '📄', book: '📖', chapter: '📑', shelf: '📚' }[item.type] || '📄';
    html += `<div class="bs-result-item" data-type="${item.type}" data-id="${item.id}">
      <span class="bs-result-icon">${typeIcon}</span>
      <div class="bs-result-info">
        <div class="bs-result-name">${_esc(item.name)}</div>
        <div class="bs-result-type">${item.type}</div>
      </div>
    </div>`;
  }
  html += '</div>';

  main.innerHTML = html;

  main.querySelectorAll('.bs-result-item').forEach(item => {
    item.addEventListener('click', () => {
      const type = item.dataset.type;
      const id = parseInt(item.dataset.id);
      if (type === 'page') _showPage(id);
      else if (type === 'book') _navigateToBook(id);
    });
  });
}

// ---- Navigation ----

function _navigateHome() {
  _currentShelf = null;
  _currentBook = null;
  _currentPage = null;
  _renderBreadcrumb();
  _renderSidebar();
  document.getElementById('bs-main').innerHTML = `<div class="bs-empty">${_t('bookstack.selectShelf', 'Select a shelf to browse')}</div>`;
}

async function _navigateToShelf(shelfId) {
  _currentShelf = { id: shelfId, name: '' };
  _currentBook = null;
  _currentPage = null;

  // Get shelf name
  const shelves = await _listShelves();
  if (shelves?.data) {
    const shelf = shelves.data.find(s => s.id === shelfId);
    if (shelf) _currentShelf.name = shelf.name;
  }

  _renderBreadcrumb();
  _renderBooks(shelfId);
  document.getElementById('bs-main').innerHTML = `<div class="bs-empty">${_t('bookstack.selectBook', 'Select a book to view')}</div>`;
}

async function _navigateToBook(bookId) {
  _currentPage = null;
  _renderBreadcrumb();
  _renderBookContents(bookId);
  document.getElementById('bs-main').innerHTML = `<div class="bs-empty">${_t('bookstack.selectPage', 'Select a page to view')}</div>`;
}

function _esc(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---- Event Wiring ----

function _wireEvents() {
  const searchInput = document.getElementById('bs-search-input');
  if (searchInput) {
    let timeout;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(timeout);
      const query = e.target.value.trim();
      if (query.length >= 2) {
        timeout = setTimeout(() => _performSearch(query), 500);
      }
    });
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const query = e.target.value.trim();
        if (query) _performSearch(query);
      }
    });
  }

  document.getElementById('bs-close-btn')?.addEventListener('click', closePanel);
}

// ---- Public API ----

export function openPanel() {
  if (_open) return;
  _open = true;
  _modal = _createModal();
  _wireEvents();
  _navigateHome();
}

export function closePanel() {
  if (!_open) return;
  _open = false;
  _currentShelf = null;
  _currentBook = null;
  _currentPage = null;
  if (_modal) {
    const content = _modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => _modal?.remove(), { once: true });
      setTimeout(() => { if (_modal?.parentElement) _modal.remove(); }, 250);
    } else {
      _modal.remove();
    }
  }
  _modal = null;
}

export async function togglePanel() {
  if (_open) closePanel();
  else await openPanel();
}

export function isPanelOpen() {
  return _open;
}

export default { openPanel, closePanel, togglePanel, isPanelOpen };
