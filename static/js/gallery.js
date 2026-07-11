/**
 * Gallery Module — photo backup + AI-generated image library.
 */

import uiModule from './ui.js';
import { openEditor, closeEditor, isEditorOpen } from './galleryEditor.js';
import spinnerModule from './spinner.js';
import { makeWindowDraggable } from './windowDrag.js';
import { bindMenuDismiss, dismissOrRemove } from './escMenuStack.js';
import { topPortalZ } from './toolWindowZOrder.js';
import sessionModule from './sessions.js';
import fileHandlerModule from './fileHandler.js';

const API_BASE = window.location.origin;
let _open = false;
let _galleryResizeHandler = null;

// Auto-refresh gallery when new image is generated
window.addEventListener('gallery-refresh', (e) => {
  if (e?.detail?.source === 'chat-upload' && _sort !== 'recent') {
    _sort = 'recent';
    const sortSel = document.getElementById('gallery-sort');
    if (sortSel) sortSel.value = 'recent';
  }
  if (_open) _fetchLibrary(false);
});
let _items = [];
let _total = 0;
let _totalTagged = 0;

// Update the "X/Y tagged" badge in the AI-tagging settings header.
function _updateTagCount() {
  const el = document.getElementById('gallery-tag-count');
  if (el) el.textContent = _total ? `${_totalTagged}/${_total} tagged` : '';
}
let _search = '';
// Stack of active tag filters. Multiple tags AND together — the user
// builds this up by clicking tag chips or by hitting Enter in the
// search box, and tears it down with the × on each pill.
let _activeTags = [];
let _activeModel = null;
let _activeAlbum = null;
let _galleryCascaded = false;   // play the domino-in cascade once per open
let _favoritesOnly = false;
let _sort = 'recent';
let _shuffleSeed = Math.floor(Math.random() * 2 ** 31);
let _offset = 0;
// Page size — computed from the grid's visible area so taller / wider
// windows (fullscreen) fetch enough photos to fill the screen instead
// of leaving blank space below a fixed 24-photo page. Capped at the
// backend's max (100).
let _limit = 24;
function _computeFetchLimit() {
  const grid = document.getElementById('gallery-grid');
  const COL_W = 168; // 160px min column + 8px gap
  const ROW_H = 200; // ~160px image + caption + gap
  const gridW = (grid && grid.clientWidth) || Math.min(window.innerWidth * 0.9, 1100);
  const cols = Math.max(2, Math.floor(gridW / COL_W));
  // The grid scroll viewport is max-height:60vh.
  const gridH = window.innerHeight * 0.6;
  const rows = Math.ceil(gridH / ROW_H) + 2; // +2 buffer rows for scroll
  return Math.min(100, Math.max(24, cols * rows));
}
let _searchDebounce = null;
let _escHandler = null;
let _albums = [];
// Albums tab — search filter + multi-select state. Mirrors what the
// Photos tab does (_search, _selectMode) but scoped to the albums grid.
let _albumSearch = '';
let _albumSelectMode = false;
const _albumSelected = new Set();

// ---- API helpers ----

async function _fetchLibrary(append) {
  // Recompute the page size each fetch so resizing / fullscreening the
  // window between loads pulls the right number of photos.
  _limit = _computeFetchLimit();
  // First load with nothing on screen → show skeleton tiles instead of a blank
  // grid that then snaps to full. BUT: if the last successful load returned
  // zero items, skip the skeleton entirely — otherwise empty accounts flash
  // 8-20 placeholder tiles for ~200ms before snapping to the "No photos yet"
  // message, which read as glitchy.
  if (!append && _items.length === 0) {
    let _knownEmpty = false;
    try { _knownEmpty = localStorage.getItem('gallery-known-empty') === '1'; } catch (_) {}
    if (!_knownEmpty) _renderSkeletons(_limit);
  }
  if (!append) {
    _offset = 0;
    // Leave _items untouched until the response arrives — that's the
    // stale-while-revalidate trick that lets the gallery feel instant on
    // re-open. The new list replaces _items on success below; if the fetch
    // fails, the previous photos stay visible.
  }
  const params = new URLSearchParams({ sort: _sort, offset: _offset, limit: _limit });
  if (_sort === 'shuffle') params.set('seed', String(_shuffleSeed));
  if (_search) params.set('search', _search);
  if (_activeTags.length) params.set('tag', _activeTags.join(','));
  if (_activeModel) params.set('model', _activeModel);
  if (_activeAlbum) params.set('album', _activeAlbum);
  if (_favoritesOnly) params.set('favorites', 'true');
  try {
    const res = await fetch(`${API_BASE}/api/gallery/library?${params}`, { credentials: 'same-origin' });
    const data = await res.json();
    if (append) {
      _items = _items.concat(data.items || []);
    } else {
      _items = data.items || [];
    }
    // Cache an "empty" verdict so the next open of an empty gallery doesn't
    // flash skeleton tiles before the real "No photos yet" message.
    try {
      const _noFilters = !_search && !_activeTags.length && !_activeModel && !_activeAlbum && !_favoritesOnly;
      if (_noFilters) {
        if (_items.length === 0) localStorage.setItem('gallery-known-empty', '1');
        else localStorage.removeItem('gallery-known-empty');
      }
    } catch (_) {}
    _total = data.total || 0;
    if (typeof data.total_tagged === 'number') _totalTagged = data.total_tagged;
    _updateTagCount();
    _renderGrid();
    _renderTags(data.tags || []);
    _renderModels(data.models || []);
    _renderStats();
  } catch (e) {
    console.error('Gallery fetch error:', e);
  }
}

async function _fetchAlbums() {
  try {
    const res = await fetch(`${API_BASE}/api/gallery/albums`, { credentials: 'same-origin' });
    const data = await res.json();
    _albums = data.albums || [];
    _renderAlbums();
  } catch (e) { console.error('Albums fetch error:', e); }
}


// v2 review HIGH-7: return a boolean so callers can stop showing
// "Tags saved" / "Photo deleted" toasts when the server actually
// returned 4xx/5xx. The previous swallow-and-return-undefined caused
// silent UI lies on permission failures.
async function _patchImage(id, patch) {
  try {
    const r = await fetch(`${API_BASE}/api/gallery/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(patch),
    });
    if (!r.ok) {
      console.warn('Gallery patch returned', r.status);
      return false;
    }
    return true;
  } catch (e) {
    console.error('Gallery patch error:', e);
    return false;
  }
}

async function _deleteImage(id) {
  try {
    const r = await fetch(`${API_BASE}/api/gallery/${id}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    if (!r.ok) {
      console.warn('Gallery delete returned', r.status);
      return false;
    }
    return true;
  } catch (e) {
    console.error('Gallery delete error:', e);
    return false;
  }
}

// ---- Bulk upload with progress ----

// Accepts either File[] (uploads all into fallbackAlbumId) or
// {file, albumId}[] (per-file album targeting — used for folder drops).
async function _bulkUpload(filesOrItems, fallbackAlbumId) {
  const bar = document.getElementById('gallery-upload-bar');
  const progress = document.getElementById('gallery-upload-progress');
  const status = document.getElementById('gallery-upload-status');
  if (!bar) return;

  const items = filesOrItems.map(it =>
    it instanceof File ? { file: it, albumId: fallbackAlbumId } : it
  );

  bar.style.display = '';
  let done = 0, dupes = 0, errors = 0;
  const total = items.length;

  // Concurrency pool — N workers pulling from the queue. 4 is a reasonable
  // default for a local server: enough to overlap network + EXIF + disk
  // without flooding SQLite (which serializes writes anyway). Videos in
  // particular benefit because they're large enough to be I/O-bound.
  const CONCURRENCY = 4;
  let cursor = 0;
  async function worker() {
    while (true) {
      const idx = cursor++;
      if (idx >= items.length) return;
      const it = items[idx];
      const fd = new FormData();
      fd.append('file', it.file);
      if (it.albumId) fd.append('album_id', it.albumId);
      try {
        const res = await fetch(`${API_BASE}/api/gallery/upload`, {
          method: 'POST', body: fd, credentials: 'same-origin',
        });
        const data = await res.json();
        if (data.duplicate) dupes++;
        else if (!data.ok) errors++;
      } catch (e) { errors++; }
      done++;
      if (progress) progress.style.width = `${(done / total) * 100}%`;
      if (status) status.textContent = `${done}/${total}${dupes ? ` (${dupes} duplicates)` : ''}`;
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, total) }, worker));

  const msg = `${done - dupes - errors} imported` +
    (dupes ? `, ${dupes} duplicates skipped` : '') +
    (errors ? `, ${errors} errors` : '');
  if (status) status.textContent = msg;
  uiModule.showToast(msg);
  setTimeout(() => { bar.style.display = 'none'; }, 3000);
  // Auto-switch to Recent so the just-uploaded photos are immediately
  // visible at the top (otherwise Shuffle would scatter them).
  if (done - dupes - errors > 0 && _sort !== 'recent') {
    _sort = 'recent';
    const sortSel = document.getElementById('gallery-sort');
    if (sortSel) sortSel.value = 'recent';
  }
  _fetchLibrary(false);
  _fetchAlbums();
}

// True if this File / filename should be uploaded — images and common videos.
function _isMediaFile(f) {
  const t = (f?.type || '').toLowerCase();
  if (t.startsWith('image/') || t.startsWith('video/')) return true;
  // Some Linux file managers and older browsers leave .type blank; fall
  // back to the extension.
  const ext = (f?.name || '').toLowerCase().split('.').pop() || '';
  return ['png','jpg','jpeg','webp','gif','mp4','mov','webm','mkv','m4v'].includes(ext);
}

// True if a URL/filename refers to a video — used to pick <video> vs <img>.
function _isVideoUrl(url) {
  const ext = (url || '').toLowerCase().split('?')[0].split('.').pop();
  return ['mp4','mov','webm','mkv','m4v'].includes(ext);
}

// Recursively walk a webkit FileSystemEntry, returning all media Files under it.
async function _walkEntryForImages(entry) {
  if (entry.isFile) {
    return new Promise(res => {
      entry.file(
        f => res(_isMediaFile(f) ? [f] : []),
        () => res([])
      );
    });
  }
  if (!entry.isDirectory) return [];
  const reader = entry.createReader();
  const out = [];
  while (true) {
    const batch = await new Promise(res => reader.readEntries(res, () => res([])));
    if (!batch.length) break;
    const subs = await Promise.all(batch.map(_walkEntryForImages));
    subs.forEach(s => out.push(...s));
  }
  return out;
}

// Handle a native drop: split into folders (→ new/existing albums) and loose files
// (→ current album). Returns when the whole upload is complete.
async function _handleGalleryDrop(e) {
  const dtItems = [...(e.dataTransfer?.items || [])];
  const entries = dtItems
    .map(it => (typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null))
    .filter(Boolean);

  const uploadItems = [];
  let sawFolderEntry = false;

  for (const entry of entries) {
    if (entry.isDirectory) {
      sawFolderEntry = true;
      let album = _albums.find(a => a.name === entry.name);
      if (!album) {
        try {
          const res = await fetch(`${API_BASE}/api/gallery/albums`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ name: entry.name }),
          });
          const data = await res.json();
          if (data && data.id) album = { id: data.id, name: data.name || entry.name };
        } catch (err) { console.error('Failed to create album for', entry.name, err); }
      }
      if (!album) continue;
      const files = await _walkEntryForImages(entry);
      files.forEach(f => uploadItems.push({ file: f, albumId: album.id }));
    } else if (entry.isFile) {
      const f = await new Promise(res => entry.file(res, () => res(null)));
      if (f && _isMediaFile(f)) {
        uploadItems.push({ file: f, albumId: _activeAlbum });
      }
    }
  }

  // Fallback: some drag sources (Linux file managers like Thunar/Nautilus,
  // or older browsers) don't populate FileSystemEntry but DO populate
  // dataTransfer.files for loose files. Pick those up too.
  if (!uploadItems.length) {
    const files = [...(e.dataTransfer?.files || [])].filter(_isMediaFile);
    files.forEach(f => uploadItems.push({ file: f, albumId: _activeAlbum }));
  }

  if (uploadItems.length) {
    await _bulkUpload(uploadItems);
    return;
  }

  // Nothing usable — either an empty folder, an unreadable folder URI, or a
  // non-image drop. If the dataTransfer types hint at a folder/URI drop,
  // explain the limitation and point at the Upload album button.
  const types = [...(e.dataTransfer?.types || [])];
  const looksLikeFolderUri = !sawFolderEntry && (
    types.includes('text/uri-list') ||
    types.includes('text/x-moz-url') ||
    dtItems.some(it => it.kind === 'string')
  );
  if (looksLikeFolderUri) {
    uiModule.showError('Browsers can’t read folders dropped from native file managers (Thunar/Nautilus). Use the "Upload album" tile in the Albums tab instead.');
  } else if (entries.length || dtItems.length) {
    uiModule.showToast('No images found in that drop');
  }
}

// ---- Render helpers ----

function _renderStats() {
  const el = document.getElementById('gallery-stats');
  if (el) el.textContent = `${_total} photo${_total !== 1 ? 's' : ''}`;
}

function _renderTags(tags) {
  // The global "every tag in the gallery" chip row under the search is gone —
  // it just piled up every user-added tag with no way to remove it. Filter by
  // tapping a tag on a photo (→ a removable pill in the header) or via search.
  const container = document.getElementById('gallery-tag-chips');
  if (!container) return;
  container.innerHTML = '';
  container.style.display = 'none';
}

function _renderModels(models) {
  const sel = document.getElementById('gallery-model-filter');
  if (!sel) return;
  let html = '<option value="">All sources</option>';
  models.forEach(m => {
    const selected = _activeModel === m ? ' selected' : '';
    html += `<option value="${_esc(m)}"${selected}>${_esc(m)}</option>`;
  });
  sel.innerHTML = html;
}

function _renderAlbums() {
  const container = document.getElementById('gallery-album-chips');   // above search: active-filter indicators
  const filterC = document.getElementById('gallery-filter-chips');    // below search: All / Favorites
  if (!container && !filterC) return;
  // Below the search bar: the All / Favorites filters PLUS any active tag chips
  // (so a tag you searched/clicked sits right next to All and the heart).
  if (filterC) {
    // Order: All, then the heart, then any active tag chips (to the right of
    // both), then the active-album chip. No favorites-within-an-album view, so
    // the heart is hidden while an album is active.
    let fhtml = `<button class="gallery-chip${!_activeAlbum && !_favoritesOnly ? ' active' : ''}" data-album="">All</button>`;
    if (!_activeAlbum) {
      fhtml += `<button class="gallery-chip gallery-chip-fav${_favoritesOnly ? ' active' : ''}" data-fav="true" title="Favorites">&#9829;</button>`;
    }
    _activeTags.forEach(t => {
      fhtml += `<span class="gallery-chip gallery-chip-active-album" title="Filtered to tag — click × to remove"><span>#${_esc(t)}</span><button class="gallery-chip-clear" data-clear-tag="${_esc(t)}" aria-label="Remove tag filter">&times;</button></span>`;
    });
    if (_activeAlbum) {
      const a = _albums.find(x => x.id === _activeAlbum);
      if (a) {
        fhtml += `<span class="gallery-chip gallery-chip-active-album" title="Currently showing this album — click X to clear"><span>${_esc(a.name)}</span><button class="gallery-chip-clear" data-clear="album" aria-label="Clear album filter">&times;</button></span>`;
      }
    }
    filterC.innerHTML = fhtml;
    filterC.querySelector('.gallery-chip[data-album=""]')?.addEventListener('click', () => {
      _favoritesOnly = false;
      _activeAlbum = null;
      _activeTags = [];
      _fetchLibrary(false);
      _renderAlbums();
    });
    filterC.querySelector('.gallery-chip-fav')?.addEventListener('click', () => {
      _favoritesOnly = !_favoritesOnly;
      _activeAlbum = null;
      _fetchLibrary(false);
      _renderAlbums();
    });
    filterC.querySelector('.gallery-chip-clear[data-clear="album"]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      _activeAlbum = null;
      _fetchLibrary(false);
      _renderAlbums();
    });
    filterC.querySelectorAll('.gallery-chip-clear[data-clear-tag]').forEach(x => {
      x.addEventListener('click', (e) => {
        e.stopPropagation();
        const tag = x.dataset.clearTag;
        _activeTags = _activeTags.filter(t => t !== tag);
        _fetchLibrary(false);
        _renderAlbums();
      });
    });
  }
  // The above-search row is no longer used — all filter chips live below now.
  if (container) container.innerHTML = '';
}

// Albums tab — renders the album list as a grid of cover-thumbnailed cards.
// Clicking an album switches to the Photos tab filtered by that album.
//
// Structure mirrors the Photos tab: persistent toolbar (search + Select)
// and bulk bar built once, only the inner #gallery-albums-grid-wrap
// re-renders so the search input keeps focus while typing.
function _renderAlbumsTab() {
  const container = document.getElementById('gallery-albums-container');
  if (!container) return;
  _ensureAlbumsToolbar(container);
  _renderAlbumsGrid();
}

function _filteredAlbums() {
  const q = _albumSearch.trim().toLowerCase();
  if (!q) return _albums;
  return _albums.filter(a => (a.name || '').toLowerCase().includes(q));
}

function _ensureAlbumsToolbar(container) {
  if (container.querySelector('#gallery-albums-toolbar')) return;
  container.innerHTML = `
    <div class="gallery-toolbar" id="gallery-albums-toolbar">
      <div class="gallery-search-wrap">
        <input type="text" class="gallery-search" id="gallery-albums-search" placeholder="Search albums..." />
      </div>
      <button class="gallery-select-btn gallery-toolbar-action" id="gallery-albums-select-btn" title="Select for bulk actions" style="position:relative;top:2px;"><span style="position:relative;top:1px;">Select</span></button>
    </div>
    <div class="memory-bulk-bar hidden" id="gallery-albums-bulk-bar">
      <label class="memory-bulk-check-all" style="position:relative;top:-1px;"><input type="checkbox" id="gallery-albums-bulk-all"> All</label>
      <span id="gallery-albums-bulk-count" style="position:relative;top:-1px;">0 selected</span>
      <button class="memory-toolbar-btn" id="gallery-albums-bulk-delete" title="Delete selected" style="margin-left:auto;color:var(--color-error, #f44);position:relative;top:-3px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>Delete</button>
      <button class="memory-toolbar-btn" id="gallery-albums-bulk-cancel" title="Cancel (Esc)" style="margin-left:4px;padding:3px 6px;position:relative;top:-3px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
    </div>
    <div id="gallery-albums-grid-wrap"></div>
  `;

  // Wire search — debounced re-render, same pattern as Photos.
  const searchInput = container.querySelector('#gallery-albums-search');
  let _albumSearchDebounce = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(_albumSearchDebounce);
    _albumSearchDebounce = setTimeout(() => {
      _albumSearch = searchInput.value;
      _renderAlbumsGrid();
    }, 150);
  });

  // Wire Select + bulk bar — Cancel restores the normal click-to-open
  // behavior; Actions opens a dropdown anchored on the button.
  container.querySelector('#gallery-albums-select-btn').addEventListener('click', () => {
    _setAlbumSelectMode(!_albumSelectMode);
  });
  container.querySelector('#gallery-albums-bulk-cancel').addEventListener('click', () => {
    _setAlbumSelectMode(false);
  });
  container.querySelector('#gallery-albums-bulk-all').addEventListener('change', (e) => {
    const on = e.target.checked;
    const list = _filteredAlbums();
    if (on) list.forEach(a => _albumSelected.add(a.id));
    else _albumSelected.clear();
    _renderAlbumsGrid();
  });
  container.querySelector('#gallery-albums-bulk-delete').addEventListener('click', (e) => {
    e.stopPropagation();
    if (!_albumSelected.size) { uiModule.showToast('Select albums first'); return; }
    _bulkDeleteAlbums([..._albumSelected]);
  });
}

function _setAlbumSelectMode(on) {
  _albumSelectMode = on;
  if (!on) _albumSelected.clear();
  const container = document.getElementById('gallery-albums-container');
  container.querySelector('#gallery-albums-select-btn span').textContent = on ? 'Cancel' : 'Select';
  container.querySelector('#gallery-albums-select-btn').classList.toggle('active', on);
  container.querySelector('#gallery-albums-bulk-bar').classList.toggle('hidden', !on);
  _renderAlbumsGrid();
}

function _updateAlbumBulkCount() {
  const container = document.getElementById('gallery-albums-container');
  if (!container) return;
  const sel = _albumSelected.size;
  const cnt = container.querySelector('#gallery-albums-bulk-count');
  if (cnt) cnt.textContent = sel + ' selected';
  const all = container.querySelector('#gallery-albums-bulk-all');
  const total = _filteredAlbums().length;
  if (all) { all.checked = total > 0 && sel === total; all.indeterminate = sel > 0 && sel < total; }
  const del = container.querySelector('#gallery-albums-bulk-delete');
  if (del) del.style.opacity = sel > 0 ? '1' : '0.5';
}

function _renderAlbumsGrid() {
  const container = document.getElementById('gallery-albums-container');
  if (!container) return;
  const wrap = container.querySelector('#gallery-albums-grid-wrap');
  if (!wrap) return;

  const albums = _filteredAlbums();
  if (!_albums.length) {
    wrap.innerHTML = `
      <div class="gallery-albums-empty">
        <p>No albums yet.</p>
        <button class="gallery-select-btn" id="gallery-albums-new">+ New album</button>
      </div>`;
    _wireAlbumsEvents(wrap);
    return;
  }
  if (!albums.length) {
    wrap.innerHTML = `<div class="gallery-albums-empty"><p>No albums match "${_esc(_albumSearch)}".</p></div>`;
    return;
  }

  let html = '<div class="gallery-albums-grid">';
  // Action tiles (New / Upload) — hidden in select mode so they don't
  // visually compete with the selection dots and can't be accidentally
  // toggled like real albums.
  if (!_albumSelectMode) {
    html += `
      <div class="gallery-album-card gallery-album-card-add" id="gallery-albums-new">
        <div class="gallery-album-cover">
          <div class="gallery-album-placeholder">+</div>
        </div>
        <div class="gallery-album-info">
          <div class="gallery-album-name">New album</div>
        </div>
      </div>
      <div class="gallery-album-card gallery-album-card-add" id="gallery-albums-upload">
        <div class="gallery-album-cover">
          <div class="gallery-album-placeholder">
            <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          </div>
        </div>
        <div class="gallery-album-info">
          <div class="gallery-album-name">Upload album</div>
          <div class="gallery-album-count">Pick a folder</div>
        </div>
      </div>`;
  }
  albums.forEach(a => {
    // Empty albums get the placeholder icon even if cover_url is set —
    // a stale cover from before the album was emptied looks like the
    // album still has photos in it.
    const cover = (a.cover_url && a.count > 0)
      ? `<img src="${_esc(a.cover_url)}" alt="" loading="lazy" />`
      : `<div class="gallery-album-placeholder">
           <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
         </div>`;
    const isSel = _albumSelected.has(a.id);
    const dot = _albumSelectMode
      ? `<span class="gallery-select-dot${isSel ? ' selected' : ''}" style="display:flex;"></span>`
      : '';
    const cls = 'gallery-album-card' + (_albumSelectMode ? ' gallery-card-selectable' : '') + (isSel ? ' selected' : '');
    html += `
      <div class="${cls}" data-album="${_esc(a.id)}">
        ${dot}
        <button class="gallery-album-menu-btn" data-album="${_esc(a.id)}" title="Options" aria-label="Album options"${_albumSelectMode ? ' style="display:none"' : ''}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="position:relative;top:2px;"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
        <div class="gallery-album-menu-pop dropdown" data-album="${_esc(a.id)}" hidden>
          <div class="dropdown-item-compact" data-action="upload">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></span>
            <span>Upload here</span>
          </div>
          <div class="dropdown-item-compact" data-action="rename">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg></span>
            <span>Rename</span>
          </div>
          <div class="dropdown-item-compact dropdown-item-danger" data-action="delete">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></span>
            <span>Delete</span>
          </div>
        </div>
        <div class="gallery-album-cover">${cover}</div>
        <div class="gallery-album-info">
          <div class="gallery-album-name">${_esc(a.name)}</div>
          <div class="gallery-album-count">${a.count} photo${a.count === 1 ? '' : 's'}</div>
        </div>
      </div>`;
  });
  html += '</div>';
  wrap.innerHTML = html;
  _updateAlbumBulkCount();
  _wireAlbumsEvents(wrap);
}

// Per-card / per-popmenu event wiring — extracted so both the empty
// state and the real grid can reuse it.
function _wireAlbumsEvents(scope) {
  const container = document.getElementById('gallery-albums-container');
  if (!container) return;

  container.querySelectorAll('.gallery-album-card[data-album]').forEach(card => {
    card.addEventListener('click', (e) => {
      // Clicks on the menu button or any pop-menu item are handled below;
      // don't navigate into the album in that case.
      if (e.target.closest('.gallery-album-menu-btn')) return;
      if (e.target.closest('.gallery-album-menu-pop')) return;
      // In select mode, clicking a card toggles its selection instead
      // of opening it. Mirrors the Photos tab's behaviour.
      if (_albumSelectMode) {
        const id = card.dataset.album;
        if (_albumSelected.has(id)) _albumSelected.delete(id);
        else _albumSelected.add(id);
        const dot = card.querySelector('.gallery-select-dot');
        if (dot) dot.classList.toggle('selected', _albumSelected.has(id));
        card.classList.toggle('selected', _albumSelected.has(id));
        _updateAlbumBulkCount();
        return;
      }
      _activeAlbum = card.dataset.album || null;
      _favoritesOnly = false;
      // Hide any open photo detail before swapping context — otherwise the
      // previously-viewed photo lingers on top when the user lands back on
      // the Photos tab.
      const _detail = document.getElementById('gallery-detail');
      if (_detail) _detail.style.display = 'none';
      _renderAlbums();
      _fetchLibrary(false);
      // Switch back to the Photos tab so they immediately see the contents.
      const modal = document.getElementById('gallery-modal');
      const photosTab = modal?.querySelector('.gallery-tab[data-tab="images"]');
      photosTab?.click();
    });
  });

  // Hover menu: toggle the per-card pop on ⋯ click, close any others.
  container.querySelectorAll('.gallery-album-menu-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.album;
      const pop = container.querySelector(`.gallery-album-menu-pop[data-album="${CSS.escape(id)}"]`);
      const wasOpen = pop && !pop.hidden;
      container.querySelectorAll('.gallery-album-menu-pop').forEach(p => { p.hidden = true; });
      if (pop && !wasOpen) pop.hidden = false;
    });
  });
  // Click anywhere else closes any open pop.
  if (!container._popDismissWired) {
    document.addEventListener('click', (e) => {
      if (e.target.closest('.gallery-album-menu-btn')) return;
      if (e.target.closest('.gallery-album-menu-pop')) return;
      container.querySelectorAll('.gallery-album-menu-pop').forEach(p => { p.hidden = true; });
    });
    container._popDismissWired = true;
  }

  container.querySelectorAll('.gallery-album-menu-pop').forEach(pop => {
    const id = pop.dataset.album;
    pop.querySelector('[data-action="upload"]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      pop.hidden = true;
      // Spawn an ephemeral file picker scoped to this album.
      const picker = document.createElement('input');
      picker.type = 'file';
      picker.accept = 'image/*,video/*';
      picker.multiple = true;
      picker.style.display = 'none';
      picker.addEventListener('change', async () => {
        const files = [...(picker.files || [])];
        if (files.length) await _bulkUpload(files, id);
        picker.remove();
      });
      document.body.appendChild(picker);
      picker.click();
    });
    pop.querySelector('[data-action="rename"]')?.addEventListener('click', async (e) => {
      e.stopPropagation();
      pop.hidden = true;
      const album = _albums.find(a => a.id === id);
      const newName = prompt('Rename album:', album?.name || '');
      if (!newName || !newName.trim() || newName.trim() === album?.name) return;
      const r = await fetch(`${API_BASE}/api/gallery/albums/${id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin', body: JSON.stringify({ name: newName.trim() }),
      });
      if (r.ok) {
        await _fetchAlbums();
        _renderAlbumsTab();
        if (uiModule) uiModule.showToast('Album renamed');
      } else if (uiModule) {
        uiModule.showError('Rename failed');
      }
    });
    pop.querySelector('[data-action="delete"]')?.addEventListener('click', async (e) => {
      e.stopPropagation();
      pop.hidden = true;
      const album = _albums.find(a => a.id === id);
      const ok = await uiModule.styledConfirm(
        `Delete album "${album?.name || ''}"? Photos inside will stay in your library.`,
        { confirmText: 'Delete', danger: true },
      );
      if (!ok) return;
      const r = await fetch(`${API_BASE}/api/gallery/albums/${id}`, {
        method: 'DELETE', credentials: 'same-origin',
      });
      if (r.ok) {
        if (_activeAlbum === id) _activeAlbum = null;
        await _fetchAlbums();
        _renderAlbumsTab();
        _renderAlbums();
        if (uiModule) uiModule.showToast('Album deleted');
      } else if (uiModule) {
        uiModule.showError('Delete failed');
      }
    });
  });

  document.getElementById('gallery-albums-new')?.addEventListener('click', async () => {
    const name = (uiModule.styledPrompt
      ? await uiModule.styledPrompt('Name your new album.', { title: 'New album', placeholder: 'e.g. Vacation 2026', confirmText: 'Create' })
      : prompt('Album name:'));
    if (!name?.trim()) return;
    await fetch(`${API_BASE}/api/gallery/albums`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin', body: JSON.stringify({ name: name.trim() }),
    });
    await _fetchAlbums();
    _renderAlbumsTab();
  });

  document.getElementById('gallery-albums-upload')?.addEventListener('click', () => {
    // <input webkitdirectory> picks a folder; we create an album with the
    // folder name and upload every image inside.
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.multiple = true;
    picker.webkitdirectory = true;
    picker.style.display = 'none';
    picker.addEventListener('change', async () => {
      const all = [...(picker.files || [])];
      const images = all.filter(_isMediaFile);
      picker.remove();
      if (!images.length) {
        if (uiModule) uiModule.showToast('No images or videos in that folder');
        return;
      }
      // Derive folder name from the first file's relative path (e.g.
      // "MyTrip/photo.jpg" → "MyTrip"). Fall back to a prompt.
      const rel = images[0].webkitRelativePath || '';
      let folderName = rel.split('/')[0] || '';
      if (!folderName) {
        folderName = prompt('Album name for these photos:') || '';
        if (!folderName.trim()) return;
      }
      // Reuse an existing album with the same name; otherwise create one.
      let album = _albums.find(a => a.name === folderName);
      if (!album) {
        const r = await fetch(`${API_BASE}/api/gallery/albums`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin', body: JSON.stringify({ name: folderName }),
        });
        const data = await r.json().catch(() => ({}));
        if (data?.id) {
          album = { id: data.id, name: folderName, count: 0 };
          _albums.push(album);
        }
      }
      if (!album) {
        if (uiModule) uiModule.showError('Could not create album');
        return;
      }
      await _bulkUpload(images, album.id);
      await _fetchAlbums();
      _renderAlbumsTab();
    });
    document.body.appendChild(picker);
    picker.click();
  });
}

async function _bulkDeleteAlbums(ids) {
  if (!ids.length) return;
  const ok = await uiModule.styledConfirm(
    `Delete ${ids.length} album${ids.length > 1 ? 's' : ''}? Photos inside will stay in your library.`,
    { confirmText: 'Delete', danger: true },
  );
  if (!ok) return;
  let failed = 0;
  for (const id of ids) {
    const r = await fetch(`${API_BASE}/api/gallery/albums/${id}`, {
      method: 'DELETE', credentials: 'same-origin',
    });
    if (!r.ok) failed++;
    else if (_activeAlbum === id) _activeAlbum = null;
  }
  if (failed) uiModule.showError(`Failed to delete ${failed} of ${ids.length} albums`);
  else if (uiModule) uiModule.showToast(`Deleted ${ids.length} album${ids.length > 1 ? 's' : ''}`);
  _setAlbumSelectMode(false);
  await _fetchAlbums();
  _renderAlbumsTab();
  _renderAlbums();
}

// Fetch the user's persisted editor drafts and render them as a thumbnail
// grid under the new-canvas / browse buttons. Each card resumes the draft
// in the editor on click; the × trashes it server-side.
// Frosted whirlpool overlay over the drafts area while fetching the
// list. Lives inside the drafts section so it sits above the grid.
let _draftsSpinner = null;
function _draftsShowLoading(section) {
  if (!section) return;
  let ov = section.querySelector('.gallery-editor-drafts-loading');
  if (!ov) {
    ov = document.createElement('div');
    ov.className = 'gallery-editor-drafts-loading';
    try {
      _draftsSpinner = spinnerModule.createWhirlpool(28);
      _draftsSpinner.element.style.cssText = 'width:28px;height:28px;margin:0;';
      ov.appendChild(_draftsSpinner.element);
    } catch (_) {
      ov.textContent = 'Loading…';
    }
    section.appendChild(ov);
  }
  // Start the overlay exactly at the grid's top so it covers ONLY the projects
  // list — not the header's search/select above it (the old fixed 30px offset
  // assumed a short header and ended up covering half the search/select).
  const _grid = section.querySelector('.gallery-editor-drafts-grid');
  const _hdr = section.querySelector('.gallery-editor-drafts-header');
  const _top = _grid ? _grid.offsetTop : (_hdr ? _hdr.offsetHeight : 30);
  ov.style.top = _top + 'px';
  ov.style.display = '';
}
function _draftsHideLoading(section) {
  if (!section) return;
  const ov = section.querySelector('.gallery-editor-drafts-loading');
  if (ov) ov.style.display = 'none';
}

// Held between renders so search + select state survive a re-render.
let _draftsCache = [];
let _draftsSearch = '';
let _draftsSelectMode = false;
let _draftsSelected = new Set();

async function _renderEditorDrafts() {
  const section = document.getElementById('gallery-editor-drafts');
  const grid = document.getElementById('gallery-editor-drafts-grid');
  if (!section || !grid) return;
  // Show a frosted whirlpool overlay over the drafts area while the
  // list is fetching. The section becomes visible BEFORE the fetch so
  // the user sees the loading indicator instead of a blank space.
  section.hidden = false;
  _draftsShowLoading(section);
  try {
    const res = await fetch(`${API_BASE}/api/editor-drafts`, { credentials: 'same-origin' });
    if (res.ok) {
      const out = await res.json();
      _draftsCache = Array.isArray(out.drafts) ? out.drafts : [];
    }
  } catch (_) {
    _draftsCache = [];
  }
  _draftsHideLoading(section);
  if (!_draftsCache.length) {
    section.hidden = true;
    grid.innerHTML = '';
    _draftsSelected.clear();
    _draftsSelectMode = false;
    _draftsSyncBulkBar();
    return;
  }
  section.hidden = false;
  // Drop selections for drafts that no longer exist.
  const present = new Set(_draftsCache.map(d => d.id));
  for (const id of [..._draftsSelected]) if (!present.has(id)) _draftsSelected.delete(id);
  _draftsPaint();
  _draftsWireOnce();
}

// Re-render only the grid (and bulk bar) from cached drafts + search +
// selection state. Used by search/select-mode/checkbox updates.
function _draftsPaint() {
  const grid = document.getElementById('gallery-editor-drafts-grid');
  if (!grid) return;
  const q = _draftsSearch.trim().toLowerCase();
  const filtered = _draftsCache.filter(d => {
    if (!q) return true;
    const name = String(d.name || '').toLowerCase();
    return name.includes(q);
  });
  grid.innerHTML = filtered.map(d => {
    const updated = d.updated_at ? _humanRelativeDate(new Date(d.updated_at)) : '';
    const dims = (d.width && d.height) ? `${d.width}×${d.height}` : '';
    const thumb = d.thumbnail
      ? `<img class="gallery-editor-draft-thumb" src="${_esc(d.thumbnail)}" alt="" />`
      : '<div class="gallery-editor-draft-thumb gallery-editor-draft-thumb-empty"></div>';
    const checked = _draftsSelected.has(d.id);
    const checkbox = _draftsSelectMode
      ? `<span class="gallery-select-dot${checked ? ' selected' : ''}" data-draft-id="${_esc(d.id)}"></span>`
      : '';
    return `
      <div class="gallery-editor-draft-card${checked ? ' selected' : ''}${_draftsSelectMode ? ' select-mode' : ''}" data-draft-id="${_esc(d.id)}" tabindex="0" title="Resume ${_esc(d.name || 'project')}">
        ${checkbox}
        ${thumb}
        <div class="gallery-editor-draft-info">
          <div class="gallery-editor-draft-name">${_esc(d.name || 'Untitled')}</div>
          <div class="gallery-editor-draft-meta">${_esc([dims, updated].filter(Boolean).join(' · '))}</div>
        </div>
        <button class="gallery-editor-draft-delete" data-draft-id="${_esc(d.id)}" title="Delete project" aria-label="Delete project">×</button>
      </div>`;
  }).join('');
  grid.querySelectorAll('.gallery-editor-draft-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.gallery-editor-draft-delete')) return;
      const id = card.dataset.draftId;
      if (!id) return;
      if (_draftsSelectMode) {
        if (_draftsSelected.has(id)) _draftsSelected.delete(id);
        else _draftsSelected.add(id);
        _draftsPaint();
        _draftsSyncBulkBar();
        return;
      }
      // Pass the cached dims as the preset size so the editor can show a
      // correctly-proportioned placeholder while the draft loads.
      const draft = _draftsCache.find(d => d.id === id);
      const presetSize = (draft && draft.width && draft.height)
        ? { w: draft.width, h: draft.height }
        : null;
      openEditor(null, null, presetSize, draft?.name || null, id);
    });
  });
  grid.querySelectorAll('.gallery-editor-draft-delete').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.dataset.draftId;
      if (!id) return;
      const ok = await uiModule.styledConfirm('Delete this project?', {
        confirmText: 'Delete', cancelText: 'Cancel', danger: true,
      });
      if (!ok) return;
      // Graceful exit: fade + shrink the card before the grid re-renders.
      const card = btn.closest('.gallery-editor-draft-card');
      if (card) card.classList.add('gallery-draft-removing');
      try {
        await fetch(`${API_BASE}/api/editor-drafts/${encodeURIComponent(id)}`, {
          method: 'DELETE', credentials: 'same-origin',
        });
      } catch (_) { /* swallow — refresh below */ }
      await new Promise(r => setTimeout(r, 240));   // let the animation finish
      _draftsSelected.delete(id);
      _renderEditorDrafts();
    });
  });
  _draftsSyncBulkBar();
}

function _draftsSyncBulkBar() {
  const bar = document.getElementById('gallery-editor-drafts-bulk');
  const countEl = document.getElementById('gallery-editor-drafts-bulk-count');
  const selectBtn = document.getElementById('gallery-editor-drafts-select');
  if (bar) bar.classList.toggle('hidden', !_draftsSelectMode);
  if (countEl) countEl.textContent = `${_draftsSelected.size} selected`;
  if (selectBtn) {
    selectBtn.textContent = _draftsSelectMode ? 'Cancel' : 'Select';
    selectBtn.classList.toggle('active', _draftsSelectMode);
  }
  // "All" checkbox state — checked when all visible drafts are selected,
  // indeterminate when only some (matches the Photos tab).
  const all = document.getElementById('gallery-editor-drafts-select-all');
  if (all) {
    const q = _draftsSearch.trim().toLowerCase();
    const visible = _draftsCache.filter(d => !q || String(d.name || '').toLowerCase().includes(q));
    const selVis = visible.filter(d => _draftsSelected.has(d.id)).length;
    all.checked = visible.length > 0 && selVis === visible.length;
    all.indeterminate = selVis > 0 && selVis < visible.length;
  }
}

let _draftsWired = false;
function _draftsWireOnce() {
  if (_draftsWired) return;
  _draftsWired = true;
  document.getElementById('gallery-editor-drafts-search')?.addEventListener('input', (e) => {
    _draftsSearch = e.target.value || '';
    _draftsPaint();
  });
  document.getElementById('gallery-editor-drafts-select')?.addEventListener('click', () => {
    _draftsSelectMode = !_draftsSelectMode;
    if (!_draftsSelectMode) _draftsSelected.clear();
    _draftsPaint();
  });
  document.getElementById('gallery-editor-drafts-select-all')?.addEventListener('change', (e) => {
    // Same "All" checkbox behavior as Photos: checked selects every visible
    // draft, unchecked clears them (respects the search filter).
    const q = _draftsSearch.trim().toLowerCase();
    const visible = _draftsCache.filter(d => !q || String(d.name || '').toLowerCase().includes(q));
    if (e.target.checked) for (const d of visible) _draftsSelected.add(d.id);
    else for (const d of visible) _draftsSelected.delete(d.id);
    _draftsPaint();
  });
  document.getElementById('gallery-editor-drafts-bulk-cancel')?.addEventListener('click', () => {
    _draftsSelectMode = false;
    _draftsSelected.clear();
    _draftsPaint();
  });
  document.getElementById('gallery-editor-drafts-bulk-delete')?.addEventListener('click', async () => {
    if (!_draftsSelected.size) return;
    const n = _draftsSelected.size;
    const ok = await uiModule.styledConfirm(`Delete ${n} project${n === 1 ? '' : 's'}?`, {
      confirmText: 'Delete', cancelText: 'Cancel', danger: true,
    });
    if (!ok) return;
    const ids = [..._draftsSelected];
    // Graceful exit on the selected cards before they're removed.
    const grid = document.getElementById('gallery-editor-drafts-grid');
    if (grid) ids.forEach(id => grid.querySelector(`.gallery-editor-draft-card[data-draft-id="${(window.CSS && CSS.escape) ? CSS.escape(id) : id}"]`)?.classList.add('gallery-draft-removing'));
    await new Promise(r => setTimeout(r, 240));
    await Promise.allSettled(ids.map(id =>
      fetch(`${API_BASE}/api/editor-drafts/${encodeURIComponent(id)}`, {
        method: 'DELETE', credentials: 'same-origin',
      })
    ));
    _draftsSelected.clear();
    _draftsSelectMode = false;
    _renderEditorDrafts();
  });
}

// Human-readable "x minutes ago" / "y days ago" for the drafts list.
function _humanRelativeDate(when) {
  const diff = (Date.now() - when.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  if (diff < 86400 * 30) return Math.floor(diff / 86400) + 'd ago';
  return when.toLocaleDateString();
}

// Edit tab empty state — shown when the user clicks the tab without a photo
// loaded. Lets them start a blank canvas or jump back to pick a photo.
function _renderEditorLanding() {
  const container = document.getElementById('gallery-editor-container');
  if (!container) return;
  // openEditor()/closeEditor() may have left the container hidden; the Edit
  // tab is still active so make sure the landing is actually visible.
  container.style.display = 'flex';
  // Templates rendered as a native <select>. Browsers handle all the layout
  // and styling natively — no custom flex grid, no clipping, no empty boxes.
  // Picking an option fires `change` and goes straight into the editor.
  const presets = [
    { w: 1024, h: 1024, label: 'Square HD — 1024 × 1024' },
    { w: 1920, h: 1080, label: 'Widescreen — 1920 × 1080' },
    { w: 1080, h: 1920, label: 'Portrait — 1080 × 1920' },
    { w: 1080, h: 1080, label: 'Instagram — 1080 × 1080' },
    { w: 1500, h: 1050, label: 'Postcard — 1500 × 1050' },
    { w: 2480, h: 3508, label: 'A4 (300dpi) — 2480 × 3508' },
    { w: 2550, h: 3300, label: 'Letter (300dpi) — 2550 × 3300' },
    { w: 3840, h: 2160, label: '4K — 3840 × 2160' },
  ];
  const optionsHtml = presets
    .map((p, i) => `<option value="${i}">${p.label}</option>`)
    .join('');
  container.innerHTML = `
    <div class="gallery-editor-landing">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.6"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>
      <h3>Image Editor <span class="ge-alpha-tag">Alpha</span></h3>
      <p>Start a blank canvas, or open a photo from your gallery to edit it.</p>
      <div class="gallery-editor-landing-actions">
        <button class="gallery-select-btn" id="gallery-editor-new">New canvas...</button>
        <button class="gallery-select-btn" id="gallery-editor-pick">Browse photos</button>
      </div>
      <label class="gallery-editor-template-label">
        Or pick a template
        <select class="gallery-editor-template-select" id="gallery-editor-template">
          <option value="">Select a size…</option>
          ${optionsHtml}
        </select>
      </label>
      <div class="gallery-editor-drafts" id="gallery-editor-drafts" hidden>
        <div class="gallery-editor-drafts-header">
          <h4 class="gallery-editor-drafts-title">Saved projects</h4>
          <input type="search" class="gallery-editor-drafts-search" id="gallery-editor-drafts-search" placeholder="Search projects…" autocomplete="off" />
          <button class="gallery-select-btn" id="gallery-editor-drafts-select" title="Toggle multi-select">Select</button>
        </div>
        <div class="gallery-bulk-bar hidden" id="gallery-editor-drafts-bulk">
          <label class="memory-bulk-check-all"><input type="checkbox" id="gallery-editor-drafts-select-all"> All</label>
          <span class="gallery-bulk-count" id="gallery-editor-drafts-bulk-count">0 selected</span>
          <button class="gallery-bulk-delete" id="gallery-editor-drafts-bulk-delete"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>Delete selected</button>
          <button class="memory-toolbar-btn" id="gallery-editor-drafts-bulk-cancel" title="Cancel (Esc)" style="margin-left:4px;padding:3px 6px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div class="gallery-editor-drafts-grid" id="gallery-editor-drafts-grid"></div>
      </div>
    </div>`;
  // Each remount of the editor landing rebuilds the drafts header
  // markup, so the cached event listener references are stale. Reset.
  _draftsWired = false;
  _renderEditorDrafts();
  document.getElementById('gallery-editor-template')?.addEventListener('change', (e) => {
    const idx = parseInt(e.target.value, 10);
    if (Number.isNaN(idx)) return;
    const p = presets[idx];
    if (p) openEditor(null, null, { w: p.w, h: p.h }, `${p.w}×${p.h}`);
  });
  document.getElementById('gallery-editor-new')?.addEventListener('click', async () => {
    // openEditor() now returns a Promise — it's async because the size
    // prompt is a styled modal. Await it before checking whether the
    // editor actually opened (the user may have cancelled).
    await openEditor(null, null, null, 'New canvas');
    if (!isEditorOpen()) _renderEditorLanding();
  });
  document.getElementById('gallery-editor-pick')?.addEventListener('click', () => {
    document.querySelector('#gallery-modal .gallery-tab[data-tab="images"]')?.click();
  });
}

// Wire the first-tile Upload affordance in the Photos grid. Opens the same
// multi-file picker the old Import button used.
function _wireUploadTile() {
  const tile = document.getElementById('gallery-upload-tile');
  if (!tile) return;
  tile.addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*,video/*';
    input.multiple = true;
    input.addEventListener('change', async () => {
      if (!input.files.length) return;
      const files = [];
      for (const file of [...input.files]) {
        const nextFile = await fileHandlerModule.cropForMobileUpload(file);
        if (nextFile) files.push(nextFile);
      }
      if (files.length) _bulkUpload(files, _activeAlbum);
    });
    input.click();
  });
}

// Shimmer placeholder tiles shown while the FIRST page loads, so the grid
// doesn't pop from empty → full (re-opens keep the old photos via
// stale-while-revalidate, so skeletons only show when there's nothing yet).
function _renderSkeletons(n) {
  const grid = document.getElementById('gallery-grid');
  if (!grid) return;
  const count = Math.max(8, Math.min(n || 12, 20));
  let html = '';
  for (let i = 0; i < count; i++) html += '<div class="gallery-card gallery-card-skeleton" aria-hidden="true"></div>';
  grid.innerHTML = html;
  const lm = document.getElementById('gallery-load-more');
  if (lm) lm.style.display = 'none';
}

function _renderGrid() {
  const grid = document.getElementById('gallery-grid');
  const loadMore = document.getElementById('gallery-load-more');
  if (!grid) return;

  // First tile: always-visible "Upload" affordance. Mirrors the Upload album
  // tile in the Albums tab so the upload entry point is consistent across
  // both grids.
  const uploadTile = `
    <div class="gallery-card gallery-card-upload" id="gallery-upload-tile" title="Upload photos or videos">
      <div class="gallery-card-upload-inner">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <div class="gallery-card-upload-label">Upload</div>
      </div>
    </div>`;

  if (_items.length === 0) {
    grid.innerHTML = uploadTile + '<div class="gallery-empty">No photos yet. Click Upload or drag-and-drop to get started!</div>';
    _wireUploadTile();
    if (loadMore) loadMore.style.display = 'none';
    return;
  }

  let html = uploadTile;
  _items.forEach(img => {
    const date = img.taken_at
      ? new Date(img.taken_at).toLocaleDateString()
      : (img.created_at ? new Date(img.created_at).toLocaleDateString() : '');
    // Card label: prefer the prompt (which doubles as the user-editable
    // name for uploaded photos). Fall back to a cleaned filename so
    // imported photos with empty prompts still show something useful
    // instead of a blank row.
    const fallbackName = (img.filename || '')
      .replace(/^\d{4,}[_-]/, '')   // drop date-prefix on uploads
      .replace(/\.[^.]+$/, '')       // drop extension
      .replace(/[_-]+/g, ' ')
      .trim();
    const labelText = (img.caption || '').trim() || (img.prompt || '').trim() || fallbackName || 'Photo';
    const promptPreview = labelText.length > 60 ? labelText.substring(0, 58) + '...' : labelText;
    const favCls = img.favorite ? ' gallery-fav-active' : '';
    html += `
      <div class="gallery-card" data-id="${_esc(img.id)}">
        <span class="gallery-select-dot" style="display:none;"></span>
        <button class="gallery-fav-btn${favCls}" data-id="${_esc(img.id)}" title="Favorite">&#9829;</button>
        <button class="gallery-dl-btn" data-id="${_esc(img.id)}" data-url="${_esc(img.url)}" data-filename="${_esc(img.filename || '')}" title="Download">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        </button>
        ${_isVideoUrl(img.url)
          ? `<video src="${_esc(img.url)}" preload="metadata" muted playsinline></video>
             <span class="gallery-card-play" aria-hidden="true">
               <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
             </span>`
          : `<img src="${_esc(img.url)}" alt="${_esc(img.prompt)}" loading="lazy" />`}
        <div class="gallery-card-info">
          <div class="gallery-card-prompt">${_esc(promptPreview)}</div>
          <div class="gallery-card-meta">
            ${img.model ? `<span class="gallery-card-model">${_esc(img.model)}</span>` : ''}
            <span class="gallery-card-date">${date}</span>
          </div>
        </div>
      </div>`;
  });
  grid.innerHTML = html;
  _wireUploadTile();

  // Domino-in cascade the first render after opening (not on filter/sort/
  // load-more re-renders) — mirrors the document library.
  if (!_galleryCascaded) {
    _galleryCascaded = true;
    grid.classList.add('gallery-just-opened');
    setTimeout(() => grid.classList.remove('gallery-just-opened'), 900);
  }

  if (loadMore) {
    loadMore.style.display = _items.length < _total ? 'block' : 'none';
  }

  // Card click → detail (skip the upload tile, it has its own handler)
  grid.querySelectorAll('.gallery-card[data-id]').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.gallery-fav-btn')) return;
      if (e.target.closest('.gallery-dl-btn')) return;
      const selectBtn = document.getElementById('gallery-select-btn');
      if (selectBtn && selectBtn.classList.contains('active')) return;
      const img = _items.find(i => i.id === card.dataset.id);
      if (img) _openDetail(img);
    });
  });

  // Download buttons
  grid.querySelectorAll('.gallery-dl-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const url = btn.dataset.url;
      const filename = btn.dataset.filename || `image-${btn.dataset.id}.png`;
      try {
        const res = await fetch(url, { credentials: 'same-origin' });
        const blob = await res.blob();
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
      } catch (_) {
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    });
  });

  // Favorite buttons
  grid.querySelectorAll('.gallery-fav-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      const res = await fetch(`${API_BASE}/api/gallery/${id}/favorite`, {
        method: 'POST', credentials: 'same-origin',
      });
      const data = await res.json();
      if (data.ok) {
        btn.classList.toggle('gallery-fav-active', data.favorite);
        const item = _items.find(i => i.id === id);
        if (item) item.favorite = data.favorite;
      }
    });
  });
}

// ---- Detail overlay ----

function _openDetail(img) {
  const detail = document.getElementById('gallery-detail');
  if (!detail) return;
  // Drop any face-overlay resize listener from the previous photo
  // before the new render attaches its own.

  const _dateSrc = img.taken_at || img.created_at || null;
  const _dateObj = _dateSrc ? new Date(_dateSrc) : null;
  const _relAgo = (d) => {
    if (!d || isNaN(d.getTime())) return '';
    const secs = (Date.now() - d.getTime()) / 1000;
    if (secs < 0) return '';
    if (secs < 60) return 'just now';
    if (secs < 3600) { const m = Math.floor(secs / 60); return `${m} minute${m !== 1 ? 's' : ''} ago`; }
    if (secs < 86400) { const h = Math.floor(secs / 3600); return `${h} hour${h !== 1 ? 's' : ''} ago`; }
    if (secs < 86400 * 7) { const d2 = Math.floor(secs / 86400); return `${d2} day${d2 !== 1 ? 's' : ''} ago`; }
    if (secs < 86400 * 30) { const w = Math.floor(secs / (86400 * 7)); return `${w} week${w !== 1 ? 's' : ''} ago`; }
    if (secs < 86400 * 365) { const mo = Math.floor(secs / (86400 * 30)); return `${mo} month${mo !== 1 ? 's' : ''} ago`; }
    const y = Math.floor(secs / (86400 * 365));
    return `${y} year${y !== 1 ? 's' : ''} ago`;
  };
  const date = _dateObj
    ? `${_dateObj.toLocaleString()}<span class="gallery-date-rel"> (${_relAgo(_dateObj)})</span>`
    : 'Unknown';
  const userTags = img.user_tags || img.tags || '';
  const aiTags = img.ai_tags || '';
  const dims = img.width && img.height ? `${img.width} x ${img.height}` : (img.size || 'Unknown');
  const fileSize = img.file_size ? _humanSize(img.file_size) : '';
  // "Edited" row: only show when updated_at is meaningfully later than
  // created_at (>10s). Every photo bumps updated_at on insert via the
  // ORM timestamp mixin, so the gap filters out the trivial case.
  let editedHtml = '';
  if (img.updated_at && img.created_at) {
    const u = new Date(img.updated_at);
    const c = new Date(img.created_at);
    if (!isNaN(u) && !isNaN(c) && (u.getTime() - c.getTime() > 10000)) {
      editedHtml = `<div class="gallery-detail-section"><label>Edited</label><div>${u.toLocaleString()}<span class="gallery-date-rel"> (${_relAgo(u)})</span></div></div>`;
    }
  }

  detail.innerHTML = `
    <div class="gallery-detail-header">
      <button class="gallery-detail-back" id="gallery-detail-back">&larr; Back</button>
      <div style="flex:1"></div>
      <button class="gallery-detail-back" id="gallery-edit-direct-btn" title="Edit (E)" aria-label="Edit photo" style="display:inline-flex;align-items:center;gap:4px;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
        Edit
      </button>
      <button class="gallery-detail-back" id="gallery-chat-photo-btn" title="${img.session_id ? 'Open source chat' : 'Start a new chat with this photo'}" aria-label="${img.session_id ? 'Open source chat' : 'Discuss photo'}" style="display:inline-flex;align-items:center;gap:4px;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>
        ${img.session_id ? 'Open chat' : 'Discuss'}
      </button>
      <button class="gallery-detail-back gallery-detail-fav-header${img.favorite ? ' active' : ''}" id="gallery-detail-fav-header" title="${img.favorite ? 'Unfavorite' : 'Favorite'}" aria-label="Favorite" aria-pressed="${img.favorite ? 'true' : 'false'}" style="display:inline-flex;align-items:center;justify-content:center;padding:4px 8px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="${img.favorite ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
      </button>
      <div class="gallery-detail-menu-wrap">
        <button class="gallery-detail-action gallery-detail-menu-btn" id="gallery-detail-menu-btn" title="Actions" aria-label="Photo actions">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
        <div class="gallery-detail-menu dropdown" id="gallery-detail-menu" hidden>
          <button class="dropdown-item-compact" id="gallery-fav-detail">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="${img.favorite ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg></span>
            ${img.favorite ? 'Favorited' : 'Favorite'}
          </button>
          <button class="dropdown-item-compact" id="gallery-ai-tag-btn" data-mode="${aiTags ? 'clear' : 'tag'}">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg></span>
            ${aiTags ? 'Clear AI tags' : 'AI Tag'}
          </button>
          <button class="dropdown-item-compact" id="gallery-download-btn">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>
            Download
          </button>
          ${img.album_id ? `<button class="dropdown-item-compact" id="gallery-set-cover-btn">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></span>
            Set as album cover
          </button>` : ''}
          <button class="dropdown-item-compact dropdown-item-danger" id="gallery-delete-btn">
            <span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></span>
            Delete
          </button>
        </div>
      </div>
    </div>
    <div class="gallery-detail-body">
      <div class="gallery-detail-image" id="gallery-detail-image-wrap" style="position:relative">
        <button class="gallery-detail-rotate gallery-detail-rotate-ccw" id="gallery-rotate-ccw-btn" title="Rotate 90° counter-clockwise" aria-label="Rotate left">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
        </button>
        <button class="gallery-detail-rotate gallery-detail-rotate-cw" id="gallery-rotate-btn" title="Rotate 90° clockwise" aria-label="Rotate right">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        </button>
        <button class="gallery-detail-nav gallery-detail-nav-prev" id="gallery-detail-prev" title="Previous (←)" aria-label="Previous">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <div class="gallery-detail-img-frame">
          ${_isVideoUrl(img.url)
            ? `<video id="gallery-detail-img" src="${_esc(img.url)}" controls preload="metadata" playsinline></video>`
            : `<img id="gallery-detail-img" src="${_esc(img.url)}" alt="${_esc(img.prompt)}" />`}
          <div id="gallery-detail-face-overlay" style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none"></div>
        </div>
        <button class="gallery-detail-nav gallery-detail-nav-next" id="gallery-detail-next" title="Next (→)" aria-label="Next">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
      <div class="gallery-detail-sidebar">
        <div class="gallery-detail-section">
          <label>Name</label>
          <div class="gallery-name-wrap">
            <input type="text" class="gallery-detail-name-input" id="gallery-detail-name-input"
              value="${_esc(img.prompt || '')}" placeholder="Untitled photo (press Enter to save)" />
            <svg class="gallery-name-enter" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>
          </div>
        </div>
        ${img.caption ? `<div class="gallery-detail-section"><label>OCR Caption</label><div class="gallery-detail-prompt">${_esc(img.caption)}</div></div>` : ''}
        ${img.prompt && img.model !== 'imported' ? `<div class="gallery-detail-section"><label>Prompt</label><div class="gallery-detail-prompt">${_esc(img.prompt)}</div></div>` : ''}
        <div class="gallery-detail-section gallery-detail-section-date">
          <label>Date</label>
          <div>${date}</div>
        </div>
        ${editedHtml}
        <div class="gallery-detail-section">
          <label>Dimensions</label>
          <div>${dims}${fileSize ? ` (${fileSize})` : ''}</div>
        </div>
        ${img.camera ? `<div class="gallery-detail-section"><label>Camera</label><div>${_esc(img.camera)}</div></div>` : ''}
        ${img.gps ? `<div class="gallery-detail-section"><label>Location</label><div>${img.gps.lat}, ${img.gps.lng}</div></div>` : ''}
        ${img.model ? `<div class="gallery-detail-section"><label>Source</label><div>${_esc(img.model)}</div></div>` : ''}
        ${img.session_name ? `<div class="gallery-detail-section"><label>Session</label><div>${_esc(img.session_name)}</div></div>` : ''}
        ${aiTags ? `<div class="gallery-detail-section"><label>AI Tags</label><div class="gallery-ai-tags">${aiTags.split(',').map(t => t.trim()).filter(Boolean).map(t => `<button class="gallery-ai-chip gallery-aitag-chip" data-tag-filter="${_esc(t)}" title="AI-generated tag — click to filter to photos tagged “${_esc(t)}”"><span class="gallery-aitag-mark" aria-hidden="true">✦</span>${_esc(t)}</button>`).join('')}</div></div>` : ''}
        <div class="gallery-detail-section">
          <label>Tags</label>
          <div class="gallery-ai-tags" id="gallery-user-tag-chips">${userTags.split(',').map(t => t.trim()).filter(Boolean).map(t => `<button class="gallery-ai-chip gallery-user-chip" data-tag-filter="${_esc(t)}" title="Filter to photos tagged “${_esc(t)}”">${_esc(t)}<span class="gallery-tag-x" title="Remove tag" aria-label="Remove tag">×</span></button>`).join('')}</div>
          <div class="gallery-tag-input-wrap">
            <input type="text" class="gallery-tag-input" id="gallery-tag-input"
              value="" placeholder="Add a tag" title="Type a tag and press Enter to add it" />
            <span class="gallery-tag-enter-hint" aria-hidden="true">↵</span>
          </div>
        </div>
        <div class="gallery-detail-section">
          <label>Album</label>
          <select id="gallery-detail-album" class="gallery-tag-input" style="padding:4px 6px;">
            <option value="">None</option>
            ${_albums.map(a => `<option value="${a.id}" ${img.album_id === a.id ? 'selected' : ''}>${_esc(a.name)}</option>`).join('')}
          </select>
        </div>
        <div class="gallery-detail-section" id="gallery-detail-people-section" style="display:none">
          <label>People in this photo</label>
          <div id="gallery-detail-people-list" class="gallery-detail-people"></div>
        </div>
      </div>
    </div>
  `;
  detail.style.display = 'flex';

  document.getElementById('gallery-detail-back').addEventListener('click', () => {
    detail.style.display = 'none';
  });

  document.getElementById('gallery-chat-photo-btn')?.addEventListener('click', async () => {
    if (img.session_id) {
      closeGallery();
      try {
        await sessionModule.selectSession(img.session_id);
      } catch (e) {
        console.error('Open source chat failed:', e);
        uiModule.showError && uiModule.showError('Could not open source chat');
      }
      return;
    }

    try {
      const dcRes = await fetch('/api/default-chat', { credentials: 'same-origin' });
      const dc = await dcRes.json();
      if (!dc?.endpoint_url || !dc?.model) {
        uiModule.showError && uiModule.showError('Pick a chat model first');
        return;
      }
      sessionModule.createDirectChat(dc.endpoint_url, dc.model, dc.endpoint_id);
      closeGallery();
      const res = await fetch(img.url, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('image fetch ' + res.status);
      const blob = await res.blob();
      const name = img.filename || 'gallery-photo.jpg';
      const file = new File([blob], name, { type: blob.type || 'image/jpeg' });
      fileHandlerModule.addFiles([file]);
      const input = document.getElementById('message');
      if (input) {
        input.value = 'Let’s discuss this photo.';
        input.dispatchEvent(new Event('input'));
        input.focus();
      }
    } catch (e) {
      console.error('Discuss photo failed:', e);
      uiModule.showError && uiModule.showError('Could not start photo chat');
    }
  });

  // Clickable tag chips — both AI Tags and User Tags. Clicking a chip
  // closes the detail, sets the tag filter on the main grid, and
  // re-fetches so the user sees other photos with that tag.
  // Remove a user tag from this photo (the × on a tag chip).
  const _removeUserTag = async (tag, chip) => {
    const existing = (img.user_tags || img.tags || '').split(',').map(t => t.trim()).filter(Boolean);
    const remaining = existing.filter(e => e.toLowerCase() !== String(tag).toLowerCase());
    const cleaned = remaining.join(', ');
    const ok = await _patchImage(img.id, { tags: cleaned });
    if (!ok) { if (uiModule) uiModule.showError('Failed to remove tag'); return; }
    img.tags = cleaned;
    img.user_tags = cleaned;
    chip.remove();
  };
  detail.querySelectorAll('[data-tag-filter]').forEach(chip => {
    chip.addEventListener('click', (e) => {
      e.stopPropagation();
      // × removes the tag (user chips only) instead of filtering.
      if (e.target.closest('.gallery-tag-x')) { _removeUserTag(chip.dataset.tagFilter, chip); return; }
      const tag = chip.dataset.tagFilter;
      if (!tag) return;
      if (!_activeTags.includes(tag)) _activeTags.push(tag);
      _activeAlbum = null;
      _favoritesOnly = false;
      detail.style.display = 'none';
      // Ensure we're looking at the Photos tab.
      const photosTab = document.querySelector('#gallery-modal .gallery-tab[data-tab="images"]');
      photosTab?.click();
      _fetchLibrary(false);
      _renderAlbums();
    });
  });

  // Overflow menu — single ⋮ button on the right that hosts all the action
  // items. Clicking any item closes the menu (per-item handlers also fire).
  const menuBtn = document.getElementById('gallery-detail-menu-btn');
  const menu = document.getElementById('gallery-detail-menu');
  if (menuBtn && menu) {
    // `.dropdown { display:none }` isn't tied to [hidden] — set inline display.
    const _setMenu = (show) => { menu.hidden = !show; menu.style.display = show ? 'block' : 'none'; };
    _setMenu(false);
    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _setMenu(menu.hidden);
    });
    menu.addEventListener('click', () => { _setMenu(false); });
    // Click outside closes the menu.
    document.addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== menuBtn) _setMenu(false);
    });
  }

  const _toggleDetailFavorite = async () => {
    const res = await fetch(`${API_BASE}/api/gallery/${img.id}/favorite`, {
      method: 'POST', credentials: 'same-origin',
    });
    const data = await res.json();
    if (!data.ok) return;
    img.favorite = data.favorite;
    const menuItem = document.getElementById('gallery-fav-detail');
    if (menuItem) menuItem.innerHTML = data.favorite ? '&#9829; Favorited' : '&#9825; Favorite';
    const headerBtn = document.getElementById('gallery-detail-fav-header');
    if (headerBtn) {
      headerBtn.setAttribute('aria-pressed', data.favorite ? 'true' : 'false');
      headerBtn.setAttribute('title', data.favorite ? 'Unfavorite' : 'Favorite');
      const svg = headerBtn.querySelector('svg');
      if (svg) svg.setAttribute('fill', data.favorite ? 'currentColor' : 'none');
    }
  };
  document.getElementById('gallery-fav-detail').addEventListener('click', _toggleDetailFavorite);
  document.getElementById('gallery-detail-fav-header')?.addEventListener('click', _toggleDetailFavorite);

  document.getElementById('gallery-ai-tag-btn').addEventListener('click', async (e) => {
    // When the photo already has AI tags this button is "Clear AI tags".
    const clearMode = e.currentTarget.dataset.mode === 'clear';
    // The button lives in the ⋮ menu which closes on click, so its text never
    // shows — surface a whirlpool overlay on the image instead.
    const stage = document.getElementById('gallery-detail-image-wrap') || document.getElementById('gallery-detail-img')?.parentElement;
    let overlay = null, spinner = null;
    if (stage) {
      overlay = document.createElement('div');
      overlay.style.cssText = 'position:absolute;inset:0;display:flex;flex-direction:column;gap:8px;align-items:center;justify-content:center;background:color-mix(in srgb, var(--bg) 55%, transparent);z-index:5;';
      try {
        spinner = spinnerModule.createWhirlpool(36);
        spinner.element.style.cssText = 'width:36px;height:36px;margin:0;';
        overlay.appendChild(spinner.element);
        const label = document.createElement('div');
        label.textContent = clearMode ? 'Clearing…' : 'AI tagging…';
        label.style.cssText = 'font-size:11px;opacity:0.7;';
        overlay.appendChild(label);
      } catch (_) { overlay.textContent = clearMode ? 'Clearing…' : 'AI tagging…'; }
      if (getComputedStyle(stage).position === 'static') stage.style.position = 'relative';
      stage.appendChild(overlay);
    }
    const cleanup = () => { try { spinner?.destroy?.(); } catch {} overlay?.remove(); };
    try {
      const url = clearMode
        ? `${API_BASE}/api/gallery/clear-ai-tags?image_id=${encodeURIComponent(img.id)}`
        : `${API_BASE}/api/gallery/${img.id}/ai-tag`;
      const res = await fetch(url, { method: 'POST', credentials: 'same-origin' });
      const data = await res.json();
      cleanup();
      if (data.ok) {
        img.ai_tags = clearMode ? '' : data.ai_tags;
        uiModule.showToast(clearMode ? 'AI tags cleared' : 'AI tags added');
        _openDetail(img); // re-render detail
      } else {
        uiModule.showError(data.error || (clearMode ? 'Clear failed' : 'AI tagging failed'));
      }
    } catch (e2) {
      cleanup();
      uiModule.showError(clearMode ? 'Clear failed' : 'AI tagging failed');
    }
  });

  document.getElementById('gallery-download-btn').addEventListener('click', async () => {
    try {
      const res = await fetch(img.url, { credentials: 'same-origin' });
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = img.filename || `image-${img.id}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
    } catch (e) {
      // Fallback: direct link
      const a = document.createElement('a');
      a.href = img.url;
      a.download = img.filename || `image-${img.id}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  });

  // Whirlpool while the (newly opened/navigated) image loads — cached images
  // report `complete` immediately, so no spinner flash for those.
  const _imgEl = document.getElementById('gallery-detail-img');
  const _frame = detail.querySelector('.gallery-detail-img-frame');
  if (_imgEl && _frame && _imgEl.tagName === 'IMG' && !_imgEl.complete) {
    const ld = document.createElement('div');
    ld.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, var(--bg) 40%, transparent);z-index:4;pointer-events:none;border-radius:6px;';
    let _sp = null;
    try { _sp = spinnerModule.createWhirlpool(34); _sp.element.style.cssText = 'width:34px;height:34px;margin:0;'; ld.appendChild(_sp.element); } catch (_) {}
    _frame.appendChild(ld);
    const _done = () => { try { _sp?.destroy?.(); } catch {} ld.remove(); };
    _imgEl.addEventListener('load', _done, { once: true });
    _imgEl.addEventListener('error', _done, { once: true });
  }

  // Prev/Next navigation
  const curIdx = _items.findIndex(i => i.id === img.id);
  const prevBtn = document.getElementById('gallery-detail-prev');
  const nextBtn = document.getElementById('gallery-detail-next');
  if (curIdx <= 0) prevBtn.classList.add('gallery-detail-nav-disabled');
  if (curIdx < 0 || curIdx >= _items.length - 1) nextBtn.classList.add('gallery-detail-nav-disabled');

  prevBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (curIdx > 0) _openDetail(_items[curIdx - 1]);
  });
  nextBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (curIdx >= 0 && curIdx < _items.length - 1) _openDetail(_items[curIdx + 1]);
  });

  // Mobile swipe — horizontal one-finger swipe across the image wrap moves
  // between photos. Skips multi-touch (pinch-zoom) and lets the video
  // controls handle their own touches.
  const wrap = document.getElementById('gallery-detail-image-wrap');
  if (wrap) {
    let sx = 0, sy = 0, st = 0, tracking = false;
    wrap.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) { tracking = false; return; }
      if (e.target.closest('video, button')) { tracking = false; return; }
      const t = e.touches[0];
      sx = t.clientX; sy = t.clientY; st = Date.now();
      tracking = true;
    }, { passive: true });
    wrap.addEventListener('touchend', (e) => {
      if (!tracking) return;
      tracking = false;
      const t = e.changedTouches[0];
      const dx = t.clientX - sx;
      const dy = t.clientY - sy;
      const dt = Date.now() - st;
      // Horizontal flick: > 40px, dominantly horizontal, under 800ms.
      if (dt > 800) return;
      if (Math.abs(dx) < 40) return;
      if (Math.abs(dx) < Math.abs(dy) * 1.2) return;
      if (dx < 0 && curIdx < _items.length - 1) _openDetail(_items[curIdx + 1]);
      else if (dx > 0 && curIdx > 0) _openDetail(_items[curIdx - 1]);
    }, { passive: true });
  }

  const _openInEditor = () => {
    try {
      detail.style.display = 'none';
      const modal = document.getElementById('gallery-modal');
      if (modal) {
        modal.querySelectorAll('.gallery-tab').forEach(t => t.classList.remove('active'));
        modal.querySelector('.gallery-tab[data-tab="editor"]')?.classList.add('active');
      }
      const imagesContainer = document.getElementById('gallery-images-container');
      const albumsContainer = document.getElementById('gallery-albums-container');
      if (imagesContainer) imagesContainer.style.display = 'none';
      if (albumsContainer) albumsContainer.style.display = 'none';
      const editorContainer = document.getElementById('gallery-editor-container');
      if (editorContainer) editorContainer.style.display = 'flex';
      const baseFilename = (img.filename || '').replace(/\.[^.]+$/, '');
      const label = img.prompt?.trim() || baseFilename || 'Photo';
      openEditor(img.url, img.id, null, label);
    } catch (e) {
      console.error('[edit] failed:', e);
      if (uiModule) uiModule.showError('Failed to open editor: ' + (e?.message || 'unknown'));
    }
  };
  document.getElementById('gallery-edit-btn')?.addEventListener('click', _openInEditor);
  document.getElementById('gallery-edit-direct-btn')?.addEventListener('click', _openInEditor);

  // Rotate — server-side image rotation. Forces a fresh URL afterwards
  // so the browser doesn't show the old cached version. Shows a
  // whirlpool over the detail image while the request + reload are in
  // flight so the user sees the action is processing.
  const _rotate = async (angle) => {
    const stage = document.querySelector('.gallery-detail-img-stage') || document.getElementById('gallery-detail-img')?.parentElement;
    let overlay = null;
    let spinner = null;
    if (stage) {
      overlay = document.createElement('div');
      overlay.className = 'gallery-detail-rotate-loading';
      overlay.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, var(--bg) 55%, transparent);z-index:5;pointer-events:none;';
      try {
        spinner = spinnerModule.createWhirlpool(36);
        spinner.element.style.cssText = 'width:36px;height:36px;margin:0;';
        overlay.appendChild(spinner.element);
      } catch (_) { overlay.textContent = 'Rotating…'; }
      if (getComputedStyle(stage).position === 'static') stage.style.position = 'relative';
      stage.appendChild(overlay);
    }
    const cleanup = () => {
      try { spinner?.destroy?.(); } catch {}
      overlay?.remove();
    };
    try {
      const r = await fetch(`${API_BASE}/api/gallery/${img.id}/rotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ angle }),
      });
      if (!r.ok) { cleanup(); uiModule.showError('Rotate failed'); return; }
      // Cache-bust the image in the detail view, then wait for the new
      // image to actually load before clearing the spinner so the user
      // doesn't see a flash of the old/blank image.
      const imgEl = document.getElementById('gallery-detail-img');
      if (imgEl) {
        const newSrc = img.url + (img.url.includes('?') ? '&' : '?') + 't=' + Date.now();
        await new Promise((resolve) => {
          imgEl.onload = imgEl.onerror = () => { imgEl.onload = null; imgEl.onerror = null; resolve(); };
          imgEl.src = newSrc;
        });
      }
      cleanup();
      uiModule.showToast('Rotated');
      _fetchLibrary(false);
    } catch (e) {
      cleanup();
      uiModule.showError('Rotate failed');
    }
  };
  document.getElementById('gallery-rotate-btn')?.addEventListener('click', () => _rotate(90));
  document.getElementById('gallery-rotate-ccw-btn')?.addEventListener('click', () => _rotate(-90));

  // Set as album cover — only present if the photo is currently in an album.
  document.getElementById('gallery-set-cover-btn')?.addEventListener('click', async () => {
    if (!img.album_id) return;
    try {
      const r = await fetch(`${API_BASE}/api/gallery/albums/${img.album_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ cover_id: img.id }),
      });
      if (r.ok) {
        uiModule.showToast('Album cover updated');
        await _fetchAlbums();
      } else {
        uiModule.showError('Failed to set cover');
      }
    } catch (e) {
      uiModule.showError('Failed to set cover');
    }
  });

  document.getElementById('gallery-delete-btn').addEventListener('click', async () => {
    if (!await uiModule.styledConfirm('Delete this photo? This cannot be undone.', { confirmText: 'Delete', danger: true })) return;
    const ok = await _deleteImage(img.id);
    if (!ok) {
      uiModule.showError('Failed to delete photo');
      return;
    }
    detail.style.display = 'none';
    _items = _items.filter(i => i.id !== img.id);
    _total = Math.max(0, _total - 1);
    _renderGrid();
    _renderStats();
    if (uiModule) uiModule.showToast('Photo deleted');
  });

  // Tag input — Enter saves; also strips a leading '#' from each tag so
  // typing "#person, #beach" stores as "person, beach".
  // Rename input — saves to the prompt column on Enter/blur via the
  // dedicated rename endpoint.
  const _nameInput = document.getElementById('gallery-detail-name-input');
  if (_nameInput) {
    const _saveName = async () => {
      const newName = _nameInput.value.trim();
      if (newName === (img.prompt || '')) return;
      try {
        const r = await fetch(`${API_BASE}/api/gallery/${img.id}/rename`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ name: newName }),
        });
        if (!r.ok) throw new Error('Failed');
        img.prompt = newName;
        if (uiModule) uiModule.showToast('Renamed');
        window.dispatchEvent(new CustomEvent('gallery-refresh'));
      } catch {
        if (uiModule) uiModule.showError('Failed to rename');
      }
    };
    _nameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _saveName(); _nameInput.blur(); }
    });
    _nameInput.addEventListener('blur', _saveName);
  }
  const _tagInput = document.getElementById('gallery-tag-input');
  if (_tagInput) {
    // Wire a tag chip's click-to-filter (same behavior as the chips rendered at
    // open) so chips we add live still work.
    const _wireTagChip = (chip) => {
      chip.addEventListener('click', (e) => {
        e.stopPropagation();
        if (e.target.closest('.gallery-tag-x')) { _removeUserTag(chip.dataset.tagFilter, chip); return; }
        const tag = chip.dataset.tagFilter;
        if (!tag) return;
        if (!_activeTags.includes(tag)) _activeTags.push(tag);
        _activeAlbum = null;
        _favoritesOnly = false;
        detail.style.display = 'none';
        document.querySelector('#gallery-modal .gallery-tab[data-tab="images"]')?.click();
        _fetchLibrary(false);
        _renderAlbums();
      });
    };
    // The input is an ADD field: type a tag, press Enter → it's appended to the
    // photo's tags, the field clears, and a chip appears immediately. No re-render.
    const _addTags = async () => {
      const newTags = _tagInput.value.split(',').map(t => t.trim().replace(/^#+/, '').trim()).filter(Boolean);
      _tagInput.value = '';
      if (!newTags.length) return;
      const existing = (img.user_tags || img.tags || '').split(',').map(t => t.trim()).filter(Boolean);
      const merged = existing.slice();
      const added = [];
      for (const t of newTags) {
        if (!merged.some(e => e.toLowerCase() === t.toLowerCase())) { merged.push(t); added.push(t); }
      }
      if (!added.length) return;
      const cleaned = merged.join(', ');
      const ok = await _patchImage(img.id, { tags: cleaned });
      if (!ok) { if (uiModule) uiModule.showError('Failed to save tags'); return; }
      img.tags = cleaned;
      img.user_tags = cleaned;
      const chips = document.getElementById('gallery-user-tag-chips');
      if (chips) {
        added.forEach(t => {
          const b = document.createElement('button');
          b.className = 'gallery-ai-chip gallery-user-chip';
          b.dataset.tagFilter = t;
          b.title = `Filter to photos tagged “${t}”`;
          b.textContent = t;
          const x = document.createElement('span');
          x.className = 'gallery-tag-x';
          x.title = 'Remove tag';
          x.setAttribute('aria-label', 'Remove tag');
          x.textContent = '×';
          b.appendChild(x);
          chips.appendChild(b);
          _wireTagChip(b);
        });
      }
    };
    _tagInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _addTags(); }
    });
    // Tap-away on mobile still adds whatever's typed.
    _tagInput.addEventListener('blur', () => { if (_tagInput.value.trim()) _addTags(); });
  }

  document.getElementById('gallery-detail-album').addEventListener('change', async (e) => {
    const albumId = e.target.value;
    const ok = await _patchImage(img.id, { album_id: albumId || '' });
    if (!ok) { uiModule.showError('Failed to update album'); return; }
    img.album_id = albumId || null;
    uiModule.showToast(albumId ? 'Added to album' : 'Removed from album');
  });
}


function _makeGalleryDraggable(content) {
  if (!content) return;
  const header = content.querySelector('.modal-header');
  if (!header) return;
  const modal = content.closest('.modal') || content;
  makeWindowDraggable(modal, { content, header });
}

// ---- Open / Close ----

// Re-export the manager for the rail click handler
import * as Modals from './modalManager.js';

export function openGallery() {
  // If already minimized — restore in place, preserve all state
  if (Modals.isRegistered('gallery-modal') && Modals.isMinimized('gallery-modal')) {
    Modals.restore('gallery-modal');
    return;
  }
  if (_open) return;
  _open = true;
  _galleryCascaded = false;   // replay the domino-in cascade on each open
  let _freshChatUpload = false;
  try {
    const ts = Number(localStorage.getItem('gallery-fresh-chat-upload') || '0');
    _freshChatUpload = ts > 0 && Date.now() - ts < 5 * 60 * 1000;
    if (_freshChatUpload) localStorage.removeItem('gallery-fresh-chat-upload');
  } catch (_) {}
  if (_freshChatUpload) _sort = 'recent';
  // State is preserved across close/reopen — filters, album, sort, items,
  // albums, people — so reopening the gallery feels instant. Use the search
  // input or "All" chip to clear the active filter.
  // Exception: when sort is shuffle, regenerate the seed every open so the
  // user gets a fresh order each visit (the whole point of shuffle). Also
  // CLEAR the cached items so the user doesn't see the stale random order
  // flash up and then swap to the new order when the fetch resolves —
  // skeletons during the brief refetch read as intentional, the swap doesn't.
  if (_sort === 'shuffle') {
    _shuffleSeed = Math.floor(Math.random() * 2 ** 31);
    _items = [];
  }

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'gallery-modal';
  modal.innerHTML = `
    <div class="modal-content gallery-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>Gallery <span id="gallery-stats" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal;margin-left:8px"></span></h4>
        <button class="modal-close" id="gallery-close">&times;</button>
      </div>
      <div class="gallery-tabs">
        <button class="gallery-tab active" data-tab="images">
          <span class="gallery-tab-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg></span>
          <span class="gallery-tab-label">Photos</span>
        </button>
        <button class="gallery-tab" data-tab="albums">
          <span class="gallery-tab-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span>
          <span class="gallery-tab-label">Albums</span>
        </button>
        <button class="gallery-tab" data-tab="editor" id="gallery-editor-tab">
          <span class="gallery-tab-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg></span>
          <span class="gallery-tab-label">Edit</span>
          <span class="gallery-tab-close" id="gallery-editor-tab-close" title="Close edit" aria-label="Close edit">×</span>
        </button>
        <button class="gallery-tab" data-tab="settings">
          <span class="gallery-tab-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span>
          <span class="gallery-tab-label">Settings</span>
        </button>
      </div>
      <div class="modal-body">
        <div id="gallery-upload-bar" style="display:none;padding:4px 8px 0;">
          <div style="background:var(--border);border-radius:4px;overflow:hidden;height:6px;">
            <div id="gallery-upload-progress" style="height:100%;background:var(--accent, var(--red));width:0%;transition:width 0.2s;"></div>
          </div>
          <div id="gallery-upload-status" style="font-size:10px;opacity:0.5;margin-top:2px;"></div>
        </div>
        <div class="gallery-images-container" id="gallery-images-container" style="margin-top:2px">
        <div class="gallery-album-chips" id="gallery-album-chips"></div>
        <div class="gallery-album-chips gallery-people-chips" id="gallery-people-chips" style="display:none"></div>
        <div class="gallery-toolbar">
          <div class="gallery-search-wrap">
            <input type="text" class="gallery-search" id="gallery-search" placeholder="Search photos, tags..." />
            <span class="gallery-search-enter-hint" aria-hidden="true"><svg class="gallery-enter-key" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>to tag</span>
          </div>
          <span class="gallery-toolbar-break" aria-hidden="true"></span>
          <select class="gallery-model-filter" id="gallery-model-filter">
            <option value="">All sources</option>
          </select>
          <select class="gallery-sort" id="gallery-sort">
            <option value="shuffle"${_sort === 'shuffle' ? ' selected' : ''}>↭ Random order</option>
            <option value="recent"${_sort === 'recent' ? ' selected' : ''}>↓ Newest first</option>
            <option value="oldest"${_sort === 'oldest' ? ' selected' : ''}>↑ Oldest first</option>
          </select>
          <button class="gallery-select-btn gallery-toolbar-action" id="gallery-select-btn" title="Select for bulk actions"><span style="position:relative;top:1px;">Select</span></button>
        </div>
        <div class="gallery-album-chips" id="gallery-filter-chips" style="margin-top:0;"></div>
        <div class="memory-bulk-bar hidden" id="gallery-bulk-bar" style="margin-bottom:4px;">
          <label class="memory-bulk-check-all" style="position:relative;top:-1px;"><input type="checkbox" id="gallery-bulk-select-all"> All</label>
          <span id="gallery-bulk-count" style="position:relative;top:-1px;">0 selected</span>
          <button class="memory-toolbar-btn" id="gallery-bulk-actions" style="position:relative;top:-3px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>Actions <span style="opacity:0.55;font-size:9px;">▼</span></button>
          <button class="memory-toolbar-btn" id="gallery-bulk-cancel" title="Cancel (Esc)" style="margin-left:4px;padding:3px 6px;position:relative;top:-3px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div class="gallery-tag-chips" id="gallery-tag-chips"></div>
        <div class="gallery-grid" id="gallery-grid"></div>
        <button class="gallery-load-more" id="gallery-load-more" style="display:none">Load more</button>
        <div class="gallery-detail" id="gallery-detail" style="display:none"></div>
        </div>
        <div class="gallery-albums-container" id="gallery-albums-container" style="display:none;"></div>
        <div class="gallery-editor-container" id="gallery-editor-container" style="display:none;"></div>
        <div class="gallery-settings-container" id="gallery-settings-container" style="display:none;">
          <div class="admin-card">
            <h2>AI Tagging <span id="gallery-tag-count" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal;"></span></h2>
            <p class="memory-desc doclib-desc">Auto-tag photos by content with your <a href="#" id="gallery-vision-link" class="ge-vision-link">vision model</a>. Your own tags are kept.</p>
            <div id="gallery-tag-bar" style="display:none;padding:8px 0 0;">
              <div style="background:var(--border);border-radius:4px;overflow:hidden;height:6px;">
                <div id="gallery-tag-progress" style="height:100%;background:var(--accent, var(--red));width:0%;transition:width 0.2s;"></div>
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;">
                <div id="gallery-tag-status" style="font-size:10px;opacity:0.5;"></div>
                <button id="gallery-tag-cancel" class="gallery-select-btn" style="font-size:10px;padding:1px 6px;">Cancel</button>
              </div>
            </div>
            <div class="memory-toolbar" style="display:flex;flex-direction:row;gap:6px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-top:32px;">
              <button class="memory-toolbar-btn" id="gallery-clear-ai-tags-btn" title="Remove all AI-generated tags from every photo">Clear AI tags</button>
              <button class="memory-toolbar-btn" id="gallery-tag-all-btn" title="AI-tag all untagged photos (in the current album, if any)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px;margin-right:5px;"><path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
                Start AI tag
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  Modals.register('gallery-modal', {
    railBtnId: 'rail-gallery',
    sidebarBtnId: 'tool-gallery-btn',
    closeFn: () => _doCloseGallery(),
    restoreFn: () => {},
  });

  // Allow dragging the modal by its header — same pattern as Email Library,
  // Sessions, etc. The tileManager (corner/edge snap-tiling) listens on
  // pointer events too; it only shows a ghost on move and snaps on release,
  // so the two coexist.
  _makeGalleryDraggable(modal.querySelector('.modal-content'));

  document.getElementById('gallery-close').addEventListener('click', async () => {
    if (isEditorOpen()) {
      const ok = await uiModule.styledConfirm(
        'Close Gallery and the active edit?',
        { confirmText: 'Close', danger: true },
      );
      if (!ok) return;
      window.__galleryAllowCloseEditor = true;
    }
    closeGallery();
  });

  // Double-click the Edit tab to rename what's being edited. The label
  // shows up everywhere it's referenced by id (#gallery-editor-tab), so a
  // simple inline contenteditable swap is enough.
  const editorTab = modal.querySelector('.gallery-tab[data-tab="editor"]');
  // Close × on the Edit tab — appears on hover. Confirms if the editor
  // has an open session (any in-progress edit), otherwise just closes.
  const editorTabClose = modal.querySelector('#gallery-editor-tab-close');
  if (editorTabClose) {
    editorTabClose.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (isEditorOpen()) {
        const ok = await uiModule.styledConfirm(
          'Close the edit? Any unsaved changes will be lost.',
          { confirmText: 'Close', danger: true },
        );
        if (!ok) return;
      }
      window.__galleryAllowCloseEditor = true;
      closeEditor();
      window.__galleryAllowCloseEditor = false;
      // If user is currently on the Edit tab, swap back to Photos.
      const activeTab = modal.querySelector('.gallery-tab.active');
      if (activeTab?.dataset.tab === 'editor') {
        modal.querySelector('.gallery-tab[data-tab="images"]')?.click();
      }
    });
  }
  if (editorTab) {
    editorTab.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      const labelEl = editorTab.querySelector('.gallery-tab-label') || editorTab;
      const current = labelEl.textContent.replace(/^Edit:\s*/, '');
      const oldText = labelEl.textContent;
      const input = document.createElement('input');
      input.type = 'text';
      input.value = current === 'Edit' ? '' : current;
      input.placeholder = 'Edit name';
      input.className = 'gallery-tab-rename-input';
      // Replace only the label span's contents so the icon SVG next to
      // it stays visible during the rename.
      labelEl.textContent = '';
      labelEl.appendChild(input);
      input.focus();
      input.select();
      const finish = (commit) => {
        if (commit && input.value.trim()) {
          labelEl.textContent = `Edit: ${input.value.trim().slice(0, 24)}`;
        } else {
          labelEl.textContent = oldText;
        }
      };
      input.addEventListener('blur', () => finish(true));
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
        if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
      });
    });
  }

  // ── Tab switching ──
  modal.querySelectorAll('.gallery-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      modal.querySelectorAll('.gallery-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.tab;
      // Always close the photo detail when changing tabs — leaving it open
      // means it pops back the next time the user returns to Photos.
      const _detail = document.getElementById('gallery-detail');
      if (_detail) _detail.style.display = 'none';
      const imagesContainer = document.getElementById('gallery-images-container');
      const albumsContainer = document.getElementById('gallery-albums-container');
      const editorContainer = document.getElementById('gallery-editor-container');
      const settingsContainer = document.getElementById('gallery-settings-container');
      if (imagesContainer) imagesContainer.style.display = target === 'images' ? '' : 'none';
      if (albumsContainer) albumsContainer.style.display = target === 'albums' ? '' : 'none';
      if (editorContainer) editorContainer.style.display = target === 'editor' ? 'flex' : 'none';
      if (settingsContainer) settingsContainer.style.display = target === 'settings' ? '' : 'none';
      if (target === 'images') {
        // Keep active edits alive when leaving the Edit tab. The edit
        // session is only torn down by the explicit Edit-tab close.
      } else if (target === 'albums') {
        _renderAlbumsTab();
      } else if (target === 'editor') {
        // If the editor isn't already holding an image, render a chooser so the
        // tab does something useful instead of opening an empty grey pane.
        if (!isEditorOpen()) _renderEditorLanding();
      }
    });
  });

  const searchInput = document.getElementById('gallery-search');
  searchInput.addEventListener('input', () => {
    clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(() => {
      _search = searchInput.value.trim();
      _fetchLibrary(false);
    }, 300);
  });
  // Pressing Enter in the search box converts the current query into a
  // stacked tag-filter pill (strips a leading "#"), clears the box, and
  // re-fetches. Lets the user keep narrowing by tag without having to
  // click chips.
  searchInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const raw = searchInput.value.trim().replace(/^#/, '');
    if (!raw) return;
    if (!_activeTags.includes(raw)) _activeTags.push(raw);
    // Searching a tag swaps the active filter to All (clear Favorites/album) so
    // the heart deactivates and All lights up.
    _favoritesOnly = false;
    _activeAlbum = null;
    // Filtering by a tag reads best newest-first — switch the sort to Recent.
    if (_sort !== 'recent') {
      _sort = 'recent';
      const sortSel = document.getElementById('gallery-sort');
      if (sortSel) sortSel.value = 'recent';
    }
    searchInput.value = '';
    _search = '';
    clearTimeout(_searchDebounce);
    _fetchLibrary(false);
    _renderAlbums();
  });

  document.getElementById('gallery-sort').addEventListener('change', (e) => {
    _sort = e.target.value;
    // New shuffle session each time the user picks Shuffle so it actually
    // re-shuffles, not just re-renders the same seeded order.
    if (_sort === 'shuffle') _shuffleSeed = Math.floor(Math.random() * 2 ** 31);
    _fetchLibrary(false);
  });

  document.getElementById('gallery-model-filter').addEventListener('change', (e) => {
    _activeModel = e.target.value || null;
    _fetchLibrary(false);
  });

  document.getElementById('gallery-load-more').addEventListener('click', () => {
    _offset = _items.length;
    _fetchLibrary(true);
  });

  // Infinite scroll: auto-load the next page when the Load-more button nears the
  // bottom of the gallery's scroll area. The button stays as a manual fallback.
  // Infinite scroll. A capture-phase scroll listener on document catches scroll
  // from WHICHEVER element actually scrolls (desktop modal-body or the mobile
  // scroll wrapper — IntersectionObserver's root was unreliable across the two).
  // We just test the Load-more button against the viewport bottom.
  let _loadingMore = false;
  let _scrollTick = false;
  const _maybeAutoLoad = () => {
    _scrollTick = false;
    if (!_open || _loadingMore || _items.length >= _total) return;
    const btn = document.getElementById('gallery-load-more');
    if (!btn || btn.style.display === 'none' || !btn.offsetParent) return;  // hidden / nothing more
    const r = btn.getBoundingClientRect();
    if (r.top <= window.innerHeight + 600) {   // within 600px of the viewport bottom
      _loadingMore = true;
      _offset = _items.length;
      Promise.resolve(_fetchLibrary(true)).finally(() => { _loadingMore = false; });
    }
  };
  document.addEventListener('scroll', () => {
    if (_scrollTick) return;
    _scrollTick = true;
    requestAnimationFrame(_maybeAutoLoad);
  }, true);

  // When the window grows (e.g. entering fullscreen), the visible grid
  // can hold more photos than the last page fetched — top up so there's
  // no blank space. Debounced; only fires when the freshly-computed
  // page size exceeds what's loaded and the server has more to give.
  let _resizeTopUpTimer = null;
  const _onGalleryResize = () => {
    clearTimeout(_resizeTopUpTimer);
    _resizeTopUpTimer = setTimeout(() => {
      if (!_open) return;
      if (_items.length >= _total) return;        // already have everything
      if (_computeFetchLimit() <= _items.length) return; // viewport not bigger than current load
      _offset = _items.length;
      _fetchLibrary(true);
    }, 300);
  };
  window.addEventListener('resize', _onGalleryResize);
  // Remember the handler so closeGallery can detach it.
  _galleryResizeHandler = _onGalleryResize;

  // ── Import images ──

  // "vision model" link → open Settings on the AI tab (where the vision model
  // is configured).
  const visionLink = document.getElementById('gallery-vision-link');
  if (visionLink) {
    visionLink.addEventListener('click', (e) => {
      e.preventDefault();
      import('./settings.js').then(m => {
        m.open('ai');
        // The gallery modal gets a bumped z-index from modalManager; settings
        // opens with its lower static z-index and lands BEHIND it. Raise it above.
        const sm = document.getElementById('settings-modal');
        const gm = document.getElementById('gallery-modal');
        if (sm) {
          const gz = gm ? (parseInt(getComputedStyle(gm).zIndex) || 0) : 0;
          sm.style.setProperty('z-index', String(Math.max(gz + 1, 10050)), 'important');
        }
      }).catch(() => {});
    });
  }

  // ── Tag All Untagged ──
  let _tagCancelRequested = false;
  let _tagging = false;
  const tagAllBtn = document.getElementById('gallery-tag-all-btn');
  const _tagAllOrigHTML = tagAllBtn ? tagAllBtn.innerHTML : '';
  if (tagAllBtn) {
    tagAllBtn.addEventListener('click', async () => {
      // While a run is active this button acts as Cancel.
      if (_tagging) {
        _tagCancelRequested = true;
        const _se = document.getElementById('gallery-tag-status');
        if (_se) _se.textContent = 'Cancelling…';
        tagAllBtn.textContent = 'Cancelling…';
        tagAllBtn.disabled = true;
        return;
      }
      if (tagAllBtn.disabled) return;
      const scope = _activeAlbum
        ? (_albums.find(a => a.id === _activeAlbum)?.name || 'this album')
        : 'entire gallery';
      const params = new URLSearchParams();
      if (_activeAlbum) params.set('album_id', _activeAlbum);
      let listRes;
      try {
        const r = await fetch(`${API_BASE}/api/gallery/ai-tag-batch?${params.toString()}`, {
          method: 'POST', credentials: 'same-origin',
        });
        listRes = await r.json();
      } catch (e) { uiModule.showError('Failed to fetch tag queue'); return; }
      if (!listRes.ok || !Array.isArray(listRes.image_ids) || listRes.image_ids.length === 0) {
        uiModule.showToast(`No untagged photos in ${scope}`);
        return;
      }
      const total = listRes.image_ids.length;
      const untagged = listRes.total_untagged || total;
      if (!await uiModule.styledConfirm(
        `Tag ${total} of ${untagged} untagged photo${total > 1 ? 's' : ''} in ${scope}?`,
        { confirmText: 'Tag All' }
      )) return;

      const bar = document.getElementById('gallery-tag-bar');
      const progEl = document.getElementById('gallery-tag-progress');
      const statusEl = document.getElementById('gallery-tag-status');
      const cancelBtn = document.getElementById('gallery-tag-cancel');
      bar.style.display = '';
      progEl.style.width = '0%';
      // The Start button becomes the Cancel control for the run (kept enabled so
      // it's clickable; the click handler above routes to cancel via _tagging).
      _tagging = true;
      _tagCancelRequested = false;
      tagAllBtn.classList.add('active', 'gallery-tag-cancelling');
      tagAllBtn.textContent = 'Cancel';
      if (cancelBtn) cancelBtn.style.display = 'none';   // start button covers it now
      cancelBtn.onclick = () => { _tagCancelRequested = true; statusEl.textContent = 'Cancelling...'; };

      let done = 0, failed = 0;
      for (const id of listRes.image_ids) {
        if (_tagCancelRequested) break;
        try {
          const r = await fetch(`${API_BASE}/api/gallery/${id}/ai-tag`, {
            method: 'POST', credentials: 'same-origin',
          });
          const d = await r.json();
          if (!d.ok) failed++;
        } catch (_) { failed++; }
        done++;
        progEl.style.width = `${Math.round((done / total) * 100)}%`;
        statusEl.textContent = `Tagging ${done}/${total}${failed ? ` — ${failed} failed` : ''}`;
      }

      statusEl.textContent = _tagCancelRequested
        ? `Cancelled after ${done}/${total}${failed ? ` (${failed} failed)` : ''}`
        : `Done — tagged ${done - failed}/${total}${failed ? ` (${failed} failed)` : ''}`;
      // Restore the Start button.
      _tagging = false;
      tagAllBtn.disabled = false;
      tagAllBtn.classList.remove('active', 'gallery-tag-cancelling');
      tagAllBtn.innerHTML = _tagAllOrigHTML;
      if (cancelBtn) cancelBtn.style.display = '';
      setTimeout(() => { bar.style.display = 'none'; }, 3000);
      await _fetchLibrary(false);
      if (uiModule) uiModule.showToast(`Tagged ${done - failed} photo${(done - failed) !== 1 ? 's' : ''}`);
    });
  }

  // ── Toolbar overflow (⋮) ──
  const moreBtn = document.getElementById('gallery-toolbar-more-btn');
  const moreMenu = document.getElementById('gallery-toolbar-more-menu');
  if (moreBtn && moreMenu) {
    // `.dropdown { display:none }` isn't tied to [hidden], so toggling the
    // attribute alone won't reveal it — set inline display too (inline wins).
    const _setMore = (show) => { moreMenu.hidden = !show; moreMenu.style.display = show ? 'block' : 'none'; };
    _setMore(false);
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _setMore(moreMenu.hidden);
    });
    document.addEventListener('click', (e) => {
      if (!moreMenu.hidden && !moreMenu.contains(e.target) && e.target !== moreBtn) _setMore(false);
    });
  }

  // ── Clear AI Tags ──
  const clearAiTagsBtn = document.getElementById('gallery-clear-ai-tags-btn');
  if (clearAiTagsBtn) {
    clearAiTagsBtn.addEventListener('click', async () => {
      if (clearAiTagsBtn.disabled) return;
      if (moreMenu) { moreMenu.hidden = true; moreMenu.style.display = 'none'; }
      if (!await uiModule.styledConfirm(
        'Remove all AI-generated tags from every photo? Your own tags are kept.',
        { confirmText: 'Clear AI Tags', danger: true }
      )) return;
      clearAiTagsBtn.disabled = true;
      try {
        const r = await fetch(`${API_BASE}/api/gallery/clear-ai-tags`, {
          method: 'POST', credentials: 'same-origin',
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Clear failed');
        uiModule.showToast(`Cleared AI tags on ${d.cleared} photo${d.cleared === 1 ? '' : 's'}`);
        await _fetchLibrary(false);
      } catch (e) {
        uiModule.showError(`Failed to clear AI tags: ${e.message || e}`);
      } finally {
        clearAiTagsBtn.disabled = false;
      }
    });
  }


  // ── Select mode + bulk delete ──
  let _selectMode = false;
  const selectBtn = document.getElementById('gallery-select-btn');
  const bulkBar = document.getElementById('gallery-bulk-bar');

  const _selectedDots = () => [...document.querySelectorAll('.gallery-select-dot.selected')];
  const _selectedIds = () => _selectedDots().map(d => d.closest('.gallery-card')?.dataset.id).filter(Boolean);

  function _updateBulkCount() {
    const sel = document.querySelectorAll('.gallery-select-dot.selected').length;
    const total = document.querySelectorAll('.gallery-select-dot').length;
    const el = document.getElementById('gallery-bulk-count');
    if (el) el.textContent = sel + ' selected';
    const all = document.getElementById('gallery-bulk-select-all');
    if (all) { all.checked = total > 0 && sel === total; all.indeterminate = sel > 0 && sel < total; }
    // When something's selected, brighten Actions to the same full --fg color as
    // the "N selected" count (the button is a dimmer 60% --fg by default).
    const actions = document.getElementById('gallery-bulk-actions');
    if (actions) actions.style.color = sel > 0 ? 'var(--fg)' : '';
  }

  function _setSelectMode(on) {
    _selectMode = on;
    selectBtn.classList.toggle('active', on);
    // The Select button doubles as Cancel while active (mirrors the library).
    selectBtn.textContent = on ? 'Cancel' : 'Select';
    bulkBar.classList.toggle('hidden', !on);
    // Body-level signal so the CSS rule that hides per-thumbnail overlay
    // buttons (favorite/download) applies to every card — including cards
    // rendered after select mode was already on (load-more etc).
    document.body.classList.toggle('gallery-selecting', on);
    document.querySelectorAll('.gallery-select-dot').forEach(d => {
      d.style.display = on ? '' : 'none';
      if (!on) d.classList.remove('selected');
      d.closest('.gallery-card')?.classList.toggle('gallery-card-selectable', on);
    });
    if (!on) document.querySelectorAll('.gallery-bulk-menu').forEach(m => m.remove());
    _updateBulkCount();
  }

  function _exitSelectMode() { _setSelectMode(false); }

  selectBtn.addEventListener('click', () => _setSelectMode(!_selectMode));
  document.getElementById('gallery-bulk-cancel')?.addEventListener('click', () => _exitSelectMode());

  // Select all / none.
  document.getElementById('gallery-bulk-select-all')?.addEventListener('change', (e) => {
    const on = e.target.checked;
    document.querySelectorAll('.gallery-select-dot').forEach(d => d.classList.toggle('selected', on));
    _updateBulkCount();
  });

  document.getElementById('gallery-grid').addEventListener('click', (e) => {
    if (!_selectMode) return;
    const card = e.target.closest('.gallery-card');
    if (!card) return;
    e.stopPropagation();
    const dot = card.querySelector('.gallery-select-dot');
    if (dot) { dot.classList.toggle('selected'); _updateBulkCount(); }
  });

  // Mobile: long-press on a thumbnail enters select mode and marks that
  // thumbnail as the first selection. Cancelled by movement (so vertical
  // scrolling still works) or by lifting before the timer fires.
  if ('ontouchstart' in window) {
    const gridEl = document.getElementById('gallery-grid');
    let lpTimer = null;
    let lpCard = null;
    let lpStartX = 0, lpStartY = 0;
    let lpFired = false;
    const LONG_PRESS_MS = 380;
    const MOVE_CANCEL_PX = 10;
    const cancel = () => { clearTimeout(lpTimer); lpTimer = null; lpCard = null; };
    gridEl.addEventListener('touchstart', (e) => {
      const card = e.target.closest('.gallery-card');
      if (!card) return;
      const t = e.touches[0];
      lpCard = card;
      lpStartX = t.clientX; lpStartY = t.clientY;
      lpFired = false;
      lpTimer = setTimeout(() => {
        if (!lpCard) return;
        lpFired = true;
        try { if (navigator.vibrate) navigator.vibrate(15); } catch {}
        if (!_selectMode) _setSelectMode(true);
        const dot = lpCard.querySelector('.gallery-select-dot');
        if (dot && !dot.classList.contains('selected')) {
          dot.classList.add('selected');
          _updateBulkCount();
        }
      }, LONG_PRESS_MS);
    }, { passive: true });
    gridEl.addEventListener('touchmove', (e) => {
      if (!lpCard) return;
      const t = e.touches[0];
      if (Math.abs(t.clientX - lpStartX) > MOVE_CANCEL_PX
        || Math.abs(t.clientY - lpStartY) > MOVE_CANCEL_PX) cancel();
    }, { passive: true });
    gridEl.addEventListener('touchend', (e) => {
      if (lpFired) {
        // Swallow the synthesized click that follows the long-press so the
        // tap doesn't immediately toggle the same dot back off.
        e.preventDefault();
      }
      cancel();
    });
    gridEl.addEventListener('touchcancel', cancel);
  }

  // Drag-and-drop import
  const grid = document.getElementById('gallery-grid');
  const imagesContainer = document.getElementById('gallery-images-container');
  ['dragenter', 'dragover'].forEach(ev => {
    imagesContainer.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); grid.classList.add('gallery-dragover'); });
  });
  ['dragleave', 'drop'].forEach(ev => {
    imagesContainer.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); grid.classList.remove('gallery-dragover'); });
  });
  imagesContainer.addEventListener('drop', (e) => {
    _handleGalleryDrop(e).catch(err => console.error('Gallery drop error:', err));
  });

  // Same drop handling on the Albums tab: dropped folders become new albums,
  // loose files go into whatever album is currently active (or stay loose).
  const albumsContainer = document.getElementById('gallery-albums-container');
  if (albumsContainer) {
    ['dragenter', 'dragover'].forEach(ev => {
      albumsContainer.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation();
        albumsContainer.classList.add('gallery-dragover');
      });
    });
    ['dragleave', 'drop'].forEach(ev => {
      albumsContainer.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation();
        albumsContainer.classList.remove('gallery-dragover');
      });
    });
    albumsContainer.addEventListener('drop', (e) => {
      _handleGalleryDrop(e)
        .then(() => _renderAlbumsTab())
        .catch(err => console.error('Gallery drop error:', err));
    });
  }


  // ── Bulk actions menu (Favorite / Add tag / Delete on the selection) ──
  // Built dynamically like the email library's _showBulkActionsMenu so it
  // shares the exact same dropdown style/behaviour.
  const _bulkActionsBtn = document.getElementById('gallery-bulk-actions');
  function _showGalleryBulkMenu(anchor) {
    document.querySelectorAll('.gallery-bulk-menu').forEach(dismissOrRemove);
    // Standard Odysseus dropdown (.dropdown + dropdown-item-compact) so it
    // matches every other menu in the app. Positioned fixed at the button.
    const dropdown = document.createElement('div');
    dropdown.className = 'dropdown gallery-bulk-menu';
    const rect = anchor.getBoundingClientRect();
    const left = Math.min(rect.left, window.innerWidth - 200);
    // Inline the standard dropdown look so it renders correctly even where the
    // `.dropdown` rule is scoped out (e.g. hover-only media queries on mobile).
    dropdown.style.cssText = `position:fixed;display:block;z-index:${topPortalZ()};top:${rect.bottom + 6}px;left:${Math.max(8, left)}px;right:auto;min-width:180px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:6px;font-size:11px;`;
    const _favIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 21s-6.7-4.35-9.33-8.04C.9 10.3 1.4 6.9 4.1 5.6c1.9-.9 4 .03 5 1.7 1-1.67 3.1-2.6 5-1.7 2.7 1.3 3.2 4.7 1.43 7.36C18.7 16.65 12 21 12 21z"/></svg>';
    const _tagIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>';
    const _dlIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    const _delIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
    const _cancelIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    const items = [
      { label: 'Favorite', icon: _favIco, action: () => _bulkFavorite(_selectedIds()) },
      { label: 'Add tag…', icon: _tagIco, action: () => _bulkTag(_selectedIds()) },
      { label: 'Download', icon: _dlIco, action: () => _bulkDownload(_selectedIds()) },
      { label: 'Delete', icon: _delIco, danger: true, action: () => _bulkDelete(_selectedIds()) },
      { separator: true },
      { label: 'Cancel', icon: _cancelIco, action: () => _exitSelectMode() },
    ];
    for (const a of items) {
      if (a.separator) {
        const sep = document.createElement('div');
        sep.className = 'dropdown-divider';
        sep.style.cssText = 'height:1px;background:var(--border);margin:4px 4px;';
        dropdown.appendChild(sep);
        continue;
      }
      const it = document.createElement('div');
      it.className = 'dropdown-item-compact' + (a.danger ? ' dropdown-item-danger' : '');
      it.innerHTML = `<span class="dropdown-icon">${a.icon}</span><span>${a.label}</span>`;
      it.addEventListener('click', (e) => { e.stopPropagation(); close(); a.action(); });
      dropdown.appendChild(it);
    }
    document.body.appendChild(dropdown);
    const close = bindMenuDismiss(dropdown, () => { dropdown.remove(); }, (ev) => !dropdown.contains(ev.target) && ev.target !== anchor);
  }

  _bulkActionsBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    // Toggle: clicking the Actions button while its dropdown is already open
    // should close it. The outside-click handler explicitly skips clicks on
    // the anchor, so the button itself has to do its own dismiss.
    const existing = document.querySelector('.gallery-bulk-menu');
    if (existing) { dismissOrRemove(existing); return; }
    if (!_selectedIds().length) { uiModule.showToast('Select photos first'); return; }
    _showGalleryBulkMenu(e.currentTarget);
  });

  async function _bulkDelete(ids) {
    if (!ids.length) return;
    if (!await uiModule.styledConfirm(`Delete ${ids.length} photo${ids.length > 1 ? 's' : ''}? This cannot be undone.`, { confirmText: 'Delete', danger: true })) return;
    const deleted = [], failed = [];
    for (const id of ids) { const ok = await _deleteImage(id); (ok ? deleted : failed).push(id); }
    if (failed.length) uiModule.showError(`Failed to delete ${failed.length} of ${ids.length} photos`);
    _items = _items.filter(i => !deleted.includes(i.id));
    _total = Math.max(0, _total - deleted.length);
    _exitSelectMode();
    if (uiModule) uiModule.showToast(`${deleted.length} photo${deleted.length > 1 ? 's' : ''} deleted`);
    // If we just emptied a FILTERED view (e.g. deleted every photo under a tag),
    // drop the filters and reload the full library so the user isn't stranded on
    // a blank screen with a now-empty tag/album/favorites filter still active.
    if (_items.length === 0 && (_activeTags.length || _activeAlbum || _favoritesOnly)) {
      _activeTags = [];
      _activeAlbum = null;
      _favoritesOnly = false;
      _fetchLibrary(false);
      _renderAlbums();
      return;
    }
    _renderGrid(); _renderStats();
  }

  async function _bulkDownload(ids) {
    if (!ids.length) return;
    // More than 5 → bundle into a single .zip server-side instead of firing a
    // flood of individual downloads.
    if (ids.length > 5) {
      try {
        if (uiModule) uiModule.showToast(`Zipping ${ids.length} photos…`);
        const res = await fetch(`${API_BASE}/api/gallery/download-zip`, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids }),
        });
        if (!res.ok) throw new Error('zip failed');
        const blob = await res.blob();
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objUrl;
        a.download = 'gallery-photos.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 2000);
        _exitSelectMode();
        if (uiModule) uiModule.showToast(`Downloaded ${ids.length} photos (zip)`);
      } catch (e) {
        if (uiModule) uiModule.showError('Failed to create zip');
      }
      return;
    }
    // 5 or fewer → individual downloads.
    let n = 0;
    for (const id of ids) {
      const it = _items.find(i => i.id === id);
      if (!it) continue;
      try {
        const res = await fetch(it.url, { credentials: 'same-origin' });
        const blob = await res.blob();
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objUrl;
        a.download = it.filename || `image-${it.id}.png`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
        n++;
        // Stagger so the browser doesn't drop simultaneous downloads.
        await new Promise(r => setTimeout(r, 250));
      } catch (_) { /* skip failures */ }
    }
    _exitSelectMode();
    if (uiModule) uiModule.showToast(`Downloading ${n} photo${n === 1 ? '' : 's'}`);
  }

  async function _bulkFavorite(ids) {
    let n = 0;
    for (const id of ids) {
      if (await _patchImage(id, { favorite: true })) {
        n++;
        const it = _items.find(i => i.id === id); if (it) it.favorite = true;
      }
    }
    _renderGrid(); _exitSelectMode();
    if (uiModule) uiModule.showToast(`Favorited ${n} photo${n > 1 ? 's' : ''}`);
  }

  async function _bulkTag(ids) {
    const tag = (await uiModule.styledPrompt('', { title: 'Add tag to selected', placeholder: 'tag', confirmText: 'Add', maxLength: 60 }) || '').trim().replace(/^#+/, '').trim();
    if (!tag) return;
    let n = 0;
    for (const id of ids) {
      const it = _items.find(i => i.id === id);
      const existing = (it?.user_tags || it?.tags || '').split(',').map(t => t.trim()).filter(Boolean);
      if (existing.includes(tag)) { continue; }
      const merged = [...existing, tag].join(', ');
      if (await _patchImage(id, { tags: merged })) {
        n++;
        if (it) { it.tags = merged; it.user_tags = merged; }
      }
    }
    _exitSelectMode();
    if (uiModule) uiModule.showToast(`Tagged ${n} photo${n === 1 ? '' : 's'} “${tag}”`);
  }

  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal()) return;
    if (e.target === modal) closeGallery();
  });

  _escHandler = (e) => {
    if (e.key === 'Escape') {
      // While the image editor is visible, Escape is reserved for the
      // editor (cancel transform/lasso/crop, dismiss size prompt, etc.).
      // Don't close the gallery — users would lose their in-progress edit.
      // We check the editor container's visibility AND the isEditorOpen()
      // flag so a crop popup, transform handles, etc. all keep Esc.
      const editorContainer = document.getElementById('gallery-editor-container');
      const editorVisible = !!(
        editorContainer &&
        getComputedStyle(editorContainer).display !== 'none' &&
        editorContainer.querySelector('.gallery-editor')
      );
      if (editorVisible || isEditorOpen()) {
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
      }
      const detail = document.getElementById('gallery-detail');
      if (detail && detail.style.display !== 'none') {
        // Click Back so Esc and the visible button always do the same thing —
        // future tweaks to Back's teardown automatically apply to Esc too.
        // stopImmediatePropagation blocks app.js's generic dynamic-modal Esc
        // handler that would otherwise close the whole gallery underneath us.
        e.preventDefault();
        e.stopImmediatePropagation();
        const back = document.getElementById('gallery-detail-back');
        if (back) back.click(); else detail.style.display = 'none';
      } else {
        closeGallery();
      }
      return;
    }
    // Arrow-key navigation inside detail view (ignore when typing in inputs)
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const detail = document.getElementById('gallery-detail');
    if (!detail || detail.style.display === 'none') return;
    const tag = (e.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) return;
    const btn = document.getElementById(e.key === 'ArrowLeft' ? 'gallery-detail-prev' : 'gallery-detail-next');
    if (btn && !btn.classList.contains('gallery-detail-nav-disabled')) {
      e.preventDefault();
      btn.click();
    }
  };
  // Capture phase + stopImmediatePropagation (see _escHandler) so app.js's
  // generic dynamic-modal Esc dismiss doesn't close the whole gallery before
  // we get a chance to close just the photo detail.
  document.addEventListener('keydown', _escHandler, true);

  const btn = document.getElementById('tool-gallery-btn');
  if (btn) btn.classList.add('active');

  // 1) Paint cached state immediately so the user sees photos instantly.
  //    Filters, sort, search box, album chips, and the grid all come from
  //    module-scope state that we no longer reset on open.
  if (_items.length || _albums.length) {
    if (_search) searchInput.value = _search;
    const sortSel = document.getElementById('gallery-sort');
    if (sortSel) sortSel.value = _sort;
    _renderAlbums();
    _renderGrid();
    _renderStats();
  }
  // 2) Refresh in the background so the cache stays close-to-fresh. If the
  //    fetch fails or takes a moment, the cached view sticks around.
  _fetchAlbums();
  _fetchLibrary(false);
  searchInput.focus();
}

function _showImagesTab() {
  const modal = document.getElementById('gallery-modal');
  if (!modal) return;
  modal.querySelectorAll('.gallery-tab').forEach(t => t.classList.remove('active'));
  modal.querySelector('.gallery-tab[data-tab="images"]')?.classList.add('active');
  const imagesContainer = document.getElementById('gallery-images-container');
  const albumsContainer = document.getElementById('gallery-albums-container');
  const editorContainer = document.getElementById('gallery-editor-container');
  const settingsContainer = document.getElementById('gallery-settings-container');
  if (imagesContainer) imagesContainer.style.display = '';
  if (albumsContainer) albumsContainer.style.display = 'none';
  if (editorContainer) editorContainer.style.display = 'none';
  if (settingsContainer) settingsContainer.style.display = 'none';
}

export async function openGalleryImage(imageId) {
  if (!imageId) {
    openGallery();
    return;
  }
  openGallery();
  _showImagesTab();
  _search = '';
  _activeTags = [];
  _activeModel = null;
  _activeAlbum = null;
  _favoritesOnly = false;
  _sort = 'recent';
  const searchInput = document.getElementById('gallery-search');
  if (searchInput) searchInput.value = '';
  const sortSel = document.getElementById('gallery-sort');
  if (sortSel) sortSel.value = 'recent';
  const detail = document.getElementById('gallery-detail');
  if (detail) detail.style.display = 'none';

  try {
    await _fetchLibrary(false);
    let img = _items.find(i => String(i.id) === String(imageId));
    if (!img) {
      const res = await fetch(`${API_BASE}/api/gallery/${encodeURIComponent(imageId)}`, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        img = data.image || data;
      }
    }
    if (!img || !img.id) {
      uiModule.showToast?.('Photo not found in gallery', 3000);
      return;
    }
    _openDetail(img);
    const card = document.querySelector(`.gallery-card[data-id="${CSS.escape(String(imageId))}"]`);
    if (card) {
      card.scrollIntoView({ block: 'center', behavior: 'smooth' });
      card.classList.add('gallery-card-focus');
      setTimeout(() => card.classList.remove('gallery-card-focus'), 1600);
    }
  } catch (err) {
    console.error('[gallery] open image failed', err);
    uiModule.showToast?.('Could not open photo in gallery', 3000);
  }
}

function _doCloseGallery() {
  const editorMounted = !!document.querySelector('#gallery-editor-container .gallery-editor');
  if ((window.__galleryEditLive || isEditorOpen() || editorMounted) && !window.__galleryAllowCloseEditor) {
    if (uiModule) uiModule.showToast('Close the edit tab first');
    return;
  }
  _open = false;
  clearTimeout(_searchDebounce);
  if (_galleryResizeHandler) {
    window.removeEventListener('resize', _galleryResizeHandler);
    _galleryResizeHandler = null;
  }
  // Detach the face-overlay resize listener so we don't leak a
  // handler past close (v2 review HIGH-9).
  closeEditor();
  window.__galleryAllowCloseEditor = false;

  const modal = document.getElementById('gallery-modal');
  if (modal) {
    const content = modal.querySelector('.modal-content, .gallery-modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => modal.remove(), { once: true });
      setTimeout(() => { if (modal.parentElement) modal.remove(); }, 250);
    } else {
      modal.remove();
    }
  }

  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler, true);
    _escHandler = null;
  }

  const btn = document.getElementById('tool-gallery-btn');
  if (btn) btn.classList.remove('active');
}

export function closeGallery() {
  if (!_open && !Modals.isMinimized('gallery-modal')) return;
  if (Modals.isRegistered('gallery-modal')) {
    Modals.close('gallery-modal');
  } else {
    _doCloseGallery();
  }
}

export function isGalleryOpen() {
  if (Modals.isMinimized('gallery-modal')) return false;
  return _open;
}

// ---- Utilities ----

function _esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function _humanSize(bytes) {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
  return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

const galleryModule = {
  openGallery,
  openGalleryImage,
  closeGallery,
  isGalleryOpen,
};

export default galleryModule;
