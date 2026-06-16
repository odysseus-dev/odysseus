// atlas.js — Atlas, an Obsidian-style markdown vault.
//
// A modal feature (same lifecycle as Gallery/Calendar) with three panes:
//   • left   — note list + search + new-note + import/export
//   • center — split markdown editor / live preview ([[wikilinks]] clickable)
//   • right  — backlinks for the open note
// Graph is a dockable panel that slides in on the right (widening the dialog)
// next to the open note: a dependency-free force-directed view of the link
// network on a <canvas>. Clicking a node opens that note. Templates live under
// templates/ and inject at the cursor; import is a drag-and-drop dialog.
//
// The vault lives as real .md files on disk; this module is a thin client over
// the /api/atlas/* endpoints (routes/atlas_routes.py).

import * as Modals from './modalManager.js';
import { mdToHtml } from './markdown.js';
import { showToast, showError, styledPrompt } from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';
import { applyRightDock, clearDockSide } from './modalSnap.js';

// Width (px) the graph panel adds to the dialog when docked open.
const GRAPH_PANEL_W = 460;

// Notes under this folder act as reusable templates (Obsidian convention).
const TEMPLATE_DIR = 'templates/';

const API = '/api/atlas';

// ── module state ────────────────────────────────────────────────────────
let _modal = null;
let _open = false;
let _notes = [];          // [{path, title, tags, size, mtime}]
let _current = null;      // path of the open note
let _dirty = false;
let _saveTimer = null;
let _graphOpen = false;   // graph docked as a right-side panel
let _graph = null;        // GraphView instance (lazy)
let _docked = false;      // edge-docked to the right (reserves chat space)

// ── tiny fetch helpers ──────────────────────────────────────────────────
async function apiGet(path, params) {
  const url = new URL(API + path, location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}
async function apiSend(path, method, body) {
  const r = await fetch(API + path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

// ── modal lifecycle ───────────────────────────────────────────────────────
function isAtlasOpen() { return _open; }

function _buildModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'atlas-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <style>
      #atlas-modal .modal-content { width: min(1200px, 96vw); height: 88vh; display:flex; flex-direction:column; }
      #atlas-modal .atlas-head { display:flex; align-items:center; gap:8px; padding:8px 12px; border-bottom:1px solid var(--bubble-border,#333); }
      #atlas-modal .atlas-head .grow { flex:1; }
      #atlas-modal .atlas-body { flex:1; display:flex; min-height:0; overflow:hidden; }
      #atlas-modal .atlas-left { width:240px; border-right:1px solid var(--bubble-border,#333); display:flex; flex-direction:column; min-height:0; }
      #atlas-modal .atlas-list { overflow:auto; flex:1; padding:2px 0; }
      #atlas-modal .atlas-row { display:flex; align-items:center; gap:4px; padding:4px 8px; cursor:pointer; font-size:13px; white-space:nowrap; overflow:hidden; border-radius:5px; }
      #atlas-modal .atlas-row:hover { background:rgba(127,127,127,.12); }
      #atlas-modal .atlas-row.active { background:var(--section-accent,rgba(120,140,255,.18)); }
      #atlas-modal .atlas-row.folder { opacity:.85; font-weight:500; }
      #atlas-modal .atlas-row.drop-target { outline:1px dashed var(--brand-color,#48f); outline-offset:-1px; }
      #atlas-modal .atlas-row .twisty { width:12px; flex:0 0 auto; opacity:.6; font-size:10px; }
      #atlas-modal .atlas-row .label { overflow:hidden; text-overflow:ellipsis; flex:1; }
      #atlas-modal .atlas-row .base-badge { font-size:9px; opacity:.55; border:1px solid currentColor; border-radius:3px; padding:0 3px; letter-spacing:.5px; }
      #atlas-modal .atlas-menu { position:fixed; z-index:1000; background:var(--input-bg,#222); border:1px solid var(--input-border,#444); border-radius:8px; padding:4px; min-width:170px; box-shadow:0 6px 20px rgba(0,0,0,.4); }
      #atlas-modal .atlas-menu div { padding:7px 10px; border-radius:6px; cursor:pointer; font-size:13px; }
      #atlas-modal .atlas-menu div:hover { background:rgba(127,127,127,.18); }
      #atlas-modal .atlas-preview a.atlas-tag { color:var(--brand-color,#6cf); background:rgba(110,140,255,.14); border-radius:10px; padding:0 7px; font-size:.9em; text-decoration:none; white-space:nowrap; }
      #atlas-modal .atlas-preview a.atlas-tag:hover { background:rgba(110,140,255,.28); }
      #atlas-modal .atlas-center { position:relative; }
      #atlas-modal .atlas-center.atlas-pulse::after { content:''; position:absolute; inset:0; pointer-events:none; border-radius:6px; z-index:5; animation: atlasPulse 1.4s cubic-bezier(0.22,1,0.36,1); }
      @keyframes atlasPulse {
        0%   { box-shadow: inset 0 0 0 2px var(--brand-color,#6c8cff), 0 0 0 0 color-mix(in srgb, var(--brand-color,#6c8cff) 55%, transparent); background: color-mix(in srgb, var(--brand-color,#6c8cff) 14%, transparent); }
        60%  { box-shadow: inset 0 0 0 1px var(--brand-color,#6c8cff); background: transparent; }
        100% { box-shadow: inset 0 0 0 0 transparent; background: transparent; }
      }
      #atlas-modal .atlas-base-view { flex:1; display:flex; flex-direction:column; min-width:0; overflow:hidden; }
      #atlas-modal .atlas-base-bar { display:flex; align-items:center; gap:6px; padding:8px 12px; border-bottom:1px solid var(--bubble-border,#333); flex-wrap:wrap; }
      #atlas-modal .atlas-base-bar .title { font-weight:600; margin-right:auto; }
      #atlas-modal .atlas-base-content { flex:1; overflow:auto; padding:12px; }
      #atlas-modal table.atlas-table { border-collapse:collapse; width:100%; font-size:13px; }
      #atlas-modal table.atlas-table th, #atlas-modal table.atlas-table td { border:1px solid var(--bubble-border,#333); padding:5px 8px; text-align:left; vertical-align:top; }
      #atlas-modal table.atlas-table th { cursor:pointer; position:sticky; top:0; background:var(--input-bg,#222); user-select:none; }
      #atlas-modal table.atlas-table td.editable { cursor:text; }
      #atlas-modal table.atlas-table td.editable:hover { background:rgba(127,127,127,.1); }
      #atlas-modal table.atlas-table a.cellopen { color:var(--brand-color,#6cf); text-decoration:none; cursor:pointer; }
      #atlas-modal .atlas-cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:10px; }
      #atlas-modal .atlas-card { border:1px solid var(--bubble-border,#333); border-radius:8px; padding:10px; cursor:pointer; }
      #atlas-modal .atlas-card:hover { background:rgba(127,127,127,.08); }
      #atlas-modal .atlas-card .ttl { font-weight:600; margin-bottom:6px; }
      #atlas-modal .atlas-card .kv { font-size:11px; opacity:.75; }
      #atlas-modal .atlas-board { display:flex; gap:12px; align-items:flex-start; height:100%; }
      #atlas-modal .atlas-col { background:rgba(127,127,127,.06); border-radius:8px; padding:8px; min-width:200px; max-width:260px; }
      #atlas-modal .atlas-col h5 { margin:2px 4px 8px; font-size:12px; text-transform:uppercase; opacity:.7; }
      #atlas-modal .atlas-col .atlas-card { background:var(--input-bg,#222); margin-bottom:8px; }
      #atlas-modal .atlas-filter-row { display:flex; gap:6px; margin-bottom:6px; align-items:center; }
      #atlas-modal .atlas-filter-row select, #atlas-modal .atlas-filter-row input { background:var(--input-bg,#222); border:1px solid var(--input-border,#444); color:inherit; border-radius:5px; padding:4px 6px; font-size:12px; }
      #atlas-modal .atlas-builder { padding:12px; border-bottom:1px solid var(--bubble-border,#333); }
      #atlas-modal .atlas-builder label { font-size:11px; opacity:.7; margin-right:6px; }
      #atlas-modal .atlas-center { flex:1; display:flex; min-height:0; }
      #atlas-modal .atlas-editor, #atlas-modal .atlas-preview { flex:1; overflow:auto; padding:14px; min-width:0; }
      #atlas-modal .atlas-editor { display:flex; flex-direction:column; border-right:1px solid var(--bubble-border,#333); }
      #atlas-modal .atlas-editor textarea { flex:1; width:100%; resize:none; border:0; background:transparent; color:inherit; font-family:var(--code-font,monospace); font-size:13px; line-height:1.55; outline:none; }
      #atlas-modal .atlas-preview a.atlas-wikilink { color:var(--brand-color,#6cf); text-decoration:none; border-bottom:1px dashed currentColor; cursor:pointer; }
      #atlas-modal .atlas-preview a.atlas-wikilink.missing { color:var(--accent-error,#e66); opacity:.8; }
      #atlas-modal .atlas-right { width:220px; border-left:1px solid var(--bubble-border,#333); overflow:auto; padding:10px; font-size:12px; }
      #atlas-modal .atlas-right h4 { margin:6px 0; font-size:11px; text-transform:uppercase; opacity:.6; }
      #atlas-modal .atlas-bl { padding:4px 6px; cursor:pointer; border-radius:5px; }
      #atlas-modal .atlas-bl:hover { background:rgba(127,127,127,.15); }
      #atlas-modal .atlas-graph-wrap { width:460px; flex:0 0 460px; border-left:1px solid var(--bubble-border,#333); position:relative; display:flex; flex-direction:column; overflow:hidden; }
      #atlas-modal .atlas-graph-bar { display:flex; align-items:center; gap:8px; padding:6px 10px; border-bottom:1px solid var(--bubble-border,#333); font-size:11px; text-transform:uppercase; opacity:.7; }
      #atlas-modal canvas.atlas-graph { flex:1; width:100%; display:block; cursor:grab; }
      #atlas-modal .atlas-ed-bar { display:flex; align-items:center; gap:8px; padding:6px 12px 8px; }
      #atlas-modal .atlas-drop { border:2px dashed var(--input-border,#555); border-radius:10px; padding:28px 18px; text-align:center; display:flex; flex-direction:column; gap:10px; align-items:center; }
      #atlas-modal .atlas-drop.dragover, #atlas-import-overlay .atlas-drop.dragover { border-color:var(--brand-color,#48f); background:rgba(80,120,255,.08); }
      #atlas-import-overlay .atlas-drop { border:2px dashed var(--input-border,#555); border-radius:10px; padding:28px 18px; text-align:center; display:flex; flex-direction:column; gap:10px; align-items:center; }
      .atlas-btn { background:var(--input-bg,#222); border:1px solid var(--input-border,#444); color:inherit; border-radius:6px; padding:4px 9px; cursor:pointer; font-size:12px; }
      .atlas-btn:hover { background:rgba(127,127,127,.2); }
      .atlas-pick-item { padding:8px 10px; cursor:pointer; border-radius:6px; font-size:13px; }
      .atlas-pick-item:hover { background:rgba(127,127,127,.15); }
      #atlas-modal .atlas-btn { background:var(--input-bg,#222); border:1px solid var(--input-border,#444); color:inherit; border-radius:6px; padding:4px 9px; cursor:pointer; font-size:12px; }
      #atlas-modal .atlas-btn:hover { background:rgba(127,127,127,.2); }
      #atlas-modal .atlas-btn.active { background:var(--brand-color,#48f); color:#fff; border-color:transparent; }
      #atlas-modal input.atlas-search { width:100%; box-sizing:border-box; background:var(--input-bg,#222); border:1px solid var(--input-border,#444); color:inherit; border-radius:6px; padding:5px 8px; font-size:12px; }
      #atlas-modal .atlas-status { font-size:11px; opacity:.6; padding:0 12px 6px; }
    </style>
    <div class="modal-content">
      <div class="atlas-head">
        <strong style="font-size:14px;">Atlas</strong>
        <span class="grow"></span>
        <button class="atlas-btn" data-act="refresh" title="Refresh from disk">↻</button>
        <button class="atlas-btn" data-act="graph-toggle" title="Show the link graph alongside your note">Graph</button>
        <button class="atlas-btn" data-act="import">Import</button>
        <button class="atlas-btn" data-act="export">Export</button>
        <button class="atlas-btn" id="atlas-close" title="Close">&#x2715;</button>
      </div>
      <div class="atlas-body">
        <div class="atlas-left">
          <div style="padding:8px; display:flex; gap:6px;">
            <input class="atlas-search" placeholder="Search… (#tag)" />
            <button class="atlas-btn" data-act="new" title="New note">+</button>
          </div>
          <div class="atlas-list"></div>
        </div>
        <div class="atlas-center">
          <div class="atlas-editor">
            <div class="atlas-ed-bar">
              <span class="atlas-status" data-el="path">No note open</span>
            </div>
            <textarea spellcheck="false" placeholder="# Write markdown…  link with [[Other note]]"></textarea>
          </div>
          <div class="atlas-preview"></div>
          <div class="atlas-base-view" style="display:none;"></div>
        </div>
        <div class="atlas-right">
          <h4>Backlinks</h4>
          <div data-el="backlinks"><div style="opacity:.5;">—</div></div>
        </div>
        <div class="atlas-graph-wrap" style="display:none;">
          <div class="atlas-graph-bar"><span>Graph</span><span style="flex:1"></span><button class="atlas-btn" data-act="graph-close" title="Hide graph">&#x2715;</button></div>
          <canvas class="atlas-graph"></canvas>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(_modal);

  // header actions
  _modal.querySelector('#atlas-close').onclick = closeAtlas;
  _modal.querySelector('[data-act="new"]').onclick = (e) => _openPlusMenu(e.currentTarget);
  _modal.querySelector('[data-act="refresh"]').onclick = () => _refresh();
  _modal.querySelector('[data-act="graph-toggle"]').onclick = () => _toggleGraph();
  _modal.querySelector('[data-act="graph-close"]').onclick = () => _toggleGraph(false);
  _modal.querySelector('[data-act="export"]').onclick = () => {
    window.open(API + '/export', '_blank');
  };
  _modal.querySelector('[data-act="import"]').onclick = _openImportDialog;

  // search
  const search = _modal.querySelector('.atlas-search');
  let st;
  search.oninput = () => { clearTimeout(st); st = setTimeout(() => _runSearch(search.value), 200); };

  // editor: debounced save
  const ta = _modal.querySelector('textarea');
  ta.addEventListener('input', () => {
    _dirty = true;
    _renderPreview(ta.value);
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(_saveCurrent, 600);
  });

  // preview: clicking a [[wikilink]] navigates
  _modal.querySelector('.atlas-preview').addEventListener('click', (e) => {
    const tag = e.target.closest('a.atlas-tag');
    if (tag) {
      e.preventDefault();
      const search = _modal.querySelector('.atlas-search');
      search.value = '#' + tag.dataset.tag;
      _runSearch(search.value);
      return;
    }
    const a = e.target.closest('a.atlas-wikilink');
    if (!a) return;
    e.preventDefault();
    const target = a.dataset.target;
    const resolved = a.dataset.resolved;
    if (resolved) openNote(resolved);
    else _newNote(target);  // ghost link → create it
  });

  return _modal;
}

async function openAtlas() {
  _buildModal();
  _modal.classList.remove('hidden');
  _modal.style.display = 'flex';
  _open = true;
  Modals.register('atlas-modal', {
    railBtnId: 'rail-atlas',
    sidebarBtnId: 'tool-atlas-btn',
    closeFn: () => _doClose(),
    restoreFn: () => {},
  });
  // Hook into Odysseus window management: one call gives drag, edge/corner
  // resize, and tile/dock-snap — same as Gallery/Calendar. Bound once.
  if (!_modal._dragWired) {
    const content = _modal.querySelector('.modal-content');
    const header = _modal.querySelector('.atlas-head');
    makeWindowDraggable(_modal, { content, header, minWidth: 720, minHeight: 420 });
    _modal._dragWired = true;
  }
  document.addEventListener('keydown', _escHandler);
  window.addEventListener('atlas-refresh', _onExternalRefresh);
  await _reloadNotes();
  // Restart the graph sim if the panel was left open from a previous session.
  if (_graphOpen) _openGraph();
}

// Open Atlas docked to the right edge (the composer "Atlas" entry, and the
// target for chat note-links). Uses Odysseus's edge-dock — the same mechanism
// a manual drag-to-right-edge triggers — so the chat/composer REFLOW to reserve
// space (body.right-dock-active + --right-dock-w) instead of being clipped by
// an overlay. Idempotent.
async function openAtlasDocked() {
  if (!_open) await openAtlas();
  // Let the modal lay out before docking so the snapshot/geometry is real.
  requestAnimationFrame(() => {
    try { applyRightDock(_modal); _docked = true; } catch (_) {}
  });
}

// Open a specific note in the docked panel and pulse-highlight it — used by the
// chat [title](#atlas-<path>) links the agent emits.
async function openAtlasNote(path) {
  await openAtlasDocked();
  await openNote(path);
  _pulseEditor();
}

// Theme-matched attention pulse on the editor/preview when a note is opened
// from a chat link.
function _pulseEditor() {
  const center = _modal && _modal.querySelector('.atlas-center');
  if (!center) return;
  center.classList.remove('atlas-pulse');
  void center.offsetWidth;  // restart the animation
  center.classList.add('atlas-pulse');
  setTimeout(() => center.classList.remove('atlas-pulse'), 1400);
}

// Fired (debounced) when the agent's manage_atlas tool writes/edits a note, so
// the vault reflects the agent's changes without a manual reload.
function _onExternalRefresh() { if (_open) _refresh(); }

// Re-read everything from disk: explorer, the open note (unless it has unsaved
// edits), the open base's query, and the graph.
async function _refresh() {
  await _reloadNotes();
  if (_base) await _runBase();
  else if (_current && !_dirty) {
    try {
      const data = await apiGet('/note', { path: _current });
      _modal.querySelector('textarea').value = data.content || '';
      _renderPreview(data.content || '', data.outlinks);
      _renderBacklinks(data.backlinks || []);
    } catch { /* note may have been deleted */ }
  }
  if (_graphOpen) _openGraph();
}

function _escHandler(e) { if (e.key === 'Escape' && _open) closeAtlas(); }

function _doClose() {
  if (_saveTimer) { clearTimeout(_saveTimer); _saveCurrent(); }
  if (_graph) { _graph.stop(); }
  _open = false;
  document.removeEventListener('keydown', _escHandler);
  window.removeEventListener('atlas-refresh', _onExternalRefresh);
  // Release the reserved right-dock space so the chat/composer reflow back.
  if (_docked && _modal) {
    try { clearDockSide('right', _modal); } catch (_) {}
    _modal.classList.remove('modal-right-docked');
    _docked = false;
  }
  if (_modal) { _modal.style.display = 'none'; _modal.classList.add('hidden'); }
}
function closeAtlas() { _doClose(); }

// ── note list ─────────────────────────────────────────────────────────────
async function _reloadNotes() {
  let notes = [];
  let bases = [];
  try { notes = (await apiGet('/notes')).notes || []; } catch { notes = []; }
  try { bases = (await apiGet('/bases')).bases || []; } catch { bases = []; }
  // Merge .base files into the explorer so they appear (with a BASE badge)
  // alongside notes — /notes only returns markdown.
  _notes = notes.concat(bases.map((b) => ({ path: b.path, title: b.name })));
  _renderList(_notes);
}

// Folder collapse state + the folder the user last clicked (drives the
// default location for "New note" / "New folder").
const _collapsed = new Set();
// Just-created folders that are still empty — the /notes API only lists files,
// so without this a new empty folder would vanish until it holds a note.
const _emptyFolders = new Set();
let _selectedFolder = '';

function _ensureDir(root, dirPath) {
  let node = root;
  const parts = dirPath.split('/');
  for (let i = 0; i < parts.length; i++) {
    const seg = parts[i];
    if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), files: [], path: parts.slice(0, i + 1).join('/') });
    node = node.dirs.get(seg);
  }
  return node;
}

// Build a nested tree { dirs: Map<name, node>, files: [item] } from flat paths.
function _buildTree(items) {
  const root = { dirs: new Map(), files: [] };
  for (const it of items) {
    const parts = it.path.split('/');
    const dir = parts.slice(0, -1).join('/');
    const node = dir ? _ensureDir(root, dir) : root;
    node.files.push(it);
  }
  for (const f of _emptyFolders) if (f) _ensureDir(root, f);
  return root;
}

function _renderList(items) {
  const list = _modal.querySelector('.atlas-list');
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div style="padding:14px; opacity:.5; font-size:12px;">No notes yet. Click + to create one.</div>';
    return;
  }
  const tree = _buildTree(items);
  _renderTreeLevel(list, tree, 0);
  // Dropping on empty list space moves a note to the vault root.
  _wireDropTarget(list, '');
}

function _renderTreeLevel(container, node, depth) {
  // Folders first (alphabetical), then files.
  const folders = [...node.dirs.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  for (const [name, child] of folders) {
    const collapsed = _collapsed.has(child.path);
    const row = document.createElement('div');
    row.className = 'atlas-row folder' + (_selectedFolder === child.path ? ' active' : '');
    row.style.paddingLeft = (8 + depth * 12) + 'px';
    row.innerHTML = `<span class="twisty">${collapsed ? '▸' : '▾'}</span><span class="label"></span>`;
    row.querySelector('.label').textContent = name;
    row.onclick = () => {
      _selectedFolder = (_selectedFolder === child.path && !collapsed) ? child.path : child.path;
      if (collapsed) _collapsed.delete(child.path); else _collapsed.add(child.path);
      _renderList(_notes);
    };
    _wireDropTarget(row, child.path);
    container.appendChild(row);
    if (!collapsed) _renderTreeLevel(container, child, depth + 1);
  }
  for (const it of node.files.sort((a, b) => (a.title || a.path).localeCompare(b.title || b.path))) {
    const row = document.createElement('div');
    const isBase = it.path.endsWith('.base');
    row.className = 'atlas-row' + (it.path === _current ? ' active' : '');
    row.style.paddingLeft = (8 + depth * 12 + 14) + 'px';
    row.draggable = true;
    row.innerHTML = `<span class="label"></span>${isBase ? '<span class="base-badge">BASE</span>' : ''}`;
    row.querySelector('.label').textContent = it.title || it.path.split('/').pop();
    row.title = it.path;
    row.onclick = () => isBase ? openBase(it.path) : openNote(it.path);
    row.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/atlas-path', it.path); });
    container.appendChild(row);
  }
}

// Make `el` accept a dropped note, moving it into `folder` via /rename.
function _wireDropTarget(el, folder) {
  el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('drop-target'); });
  el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
  el.addEventListener('drop', async (e) => {
    e.preventDefault(); e.stopPropagation();
    el.classList.remove('drop-target');
    const src = e.dataTransfer.getData('text/atlas-path');
    if (!src) return;
    const base = src.split('/').pop();
    const dest = folder ? `${folder}/${base}` : base;
    if (dest === src) return;
    try {
      const res = await apiSend('/rename', 'POST', { path: src, new_path: dest });
      if (_current === src) _current = res.path;
      await _reloadNotes();
    } catch (err) { showError('Move failed: ' + err.message); }
  });
}

async function _runSearch(q) {
  q = (q || '').trim();
  if (!q) { _renderList(_notes); return; }
  try {
    const data = await apiGet('/search', { q });
    _renderList((data.results || []).map((r) => ({ path: r.path, title: r.title })));
  } catch { /* keep current list */ }
}

// Swap the centre pane between the note editor/preview and the Bases view.
function _showCenter(mode) {
  const note = mode !== 'base';
  _modal.querySelector('.atlas-editor').style.display = note ? '' : 'none';
  _modal.querySelector('.atlas-preview').style.display = note ? '' : 'none';
  _modal.querySelector('.atlas-base-view').style.display = note ? 'none' : '';
}

// ── open / edit / save a note ───────────────────────────────────────────────
async function openNote(path) {
  if (_dirty) { clearTimeout(_saveTimer); await _saveCurrent(); }
  try {
    const data = await apiGet('/note', { path });
    _current = data.path;
    _selectedFolder = data.path.includes('/') ? data.path.slice(0, data.path.lastIndexOf('/')) : '';
    _dirty = false;
    _showCenter('note');
    _modal.querySelector('textarea').value = data.content || '';
    _modal.querySelector('[data-el="path"]').textContent = data.path;
    _renderPreview(data.content || '', data.outlinks);
    _renderBacklinks(data.backlinks || []);
    _renderList(_notes);
  } catch (e) { console.error('openNote', e); }
}

// The "+" button: a small menu so creating a note no longer demands you
// decide its folder up front, and template insertion lives here too.
function _openPlusMenu(anchor) {
  const existing = _modal.querySelector('.atlas-menu');
  if (existing) { existing.remove(); return; }
  const menu = document.createElement('div');
  menu.className = 'atlas-menu';
  const here = _selectedFolder ? ` (in ${_selectedFolder}/)` : '';
  menu.innerHTML = `
    <div data-m="note">New note${here}</div>
    <div data-m="folder">New folder${here}</div>
    <div data-m="base">New base (query)…</div>
    <div data-m="template">Insert template…</div>`;
  const r = anchor.getBoundingClientRect();
  menu.style.top = (r.bottom + 4) + 'px';
  menu.style.left = Math.max(8, r.right - 175) + 'px';
  _modal.appendChild(menu);
  const close = () => { menu.remove(); document.removeEventListener('click', onDoc, true); };
  const onDoc = (e) => { if (!menu.contains(e.target) && e.target !== anchor) close(); };
  setTimeout(() => document.addEventListener('click', onDoc, true), 0);
  menu.querySelector('[data-m="note"]').onclick = () => { close(); _newNote(); };
  menu.querySelector('[data-m="folder"]').onclick = () => { close(); _newFolder(); };
  menu.querySelector('[data-m="base"]').onclick = () => { close(); _newBase(); };
  menu.querySelector('[data-m="template"]').onclick = () => { close(); _insertTemplate(); };
}

// New note: default into the selected folder so there's no "which folder?"
// friction — the prompt is pre-filled and you can still edit the subpath.
async function _newNote(name) {
  const folder = _selectedFolder ? _selectedFolder + '/' : '';
  const def = typeof name === 'string' && name ? name : folder;
  const path = await styledPrompt('', {
    title: 'New note',
    defaultValue: def,
    placeholder: 'e.g. Ideas/Big Idea',
    confirmText: 'Create',
    maxLength: 200,
  });
  if (!path || !path.replace(/\/+$/, '')) return;
  try {
    const res = await apiSend('/note', 'PUT', { path, content: `# ${path.split('/').pop()}\n\n` });
    await _reloadNotes();
    openNote(res.path);
  } catch (e) { showError('Could not create note: ' + e.message); }
}

async function _newFolder() {
  const folder = _selectedFolder ? _selectedFolder + '/' : '';
  const path = await styledPrompt('', {
    title: 'New folder',
    defaultValue: folder,
    placeholder: 'e.g. Projects/2026',
    confirmText: 'Create',
    maxLength: 200,
  });
  if (!path || !path.replace(/\/+$/, '')) return;
  try {
    const res = await apiSend('/folder', 'POST', { path });
    _selectedFolder = res.path;
    _collapsed.delete(res.path);
    _emptyFolders.add(res.path);
    await _reloadNotes();
  } catch (e) { showError('Could not create folder: ' + e.message); }
}

async function _saveCurrent() {
  if (!_current || !_dirty) return;
  const content = _modal.querySelector('textarea').value;
  try {
    await apiSend('/note', 'PUT', { path: _current, content });
    _dirty = false;
    // Refresh the cached list (title/mtime may have changed) without stealing focus.
    apiGet('/notes').then((d) => { _notes = d.notes || []; }).catch(() => {});
  } catch (e) { console.error('save', e); }
}

// ── preview (with [[wikilink]] + ![[embed]] rendering) ───────────────────────
// Map of raw target → resolved relpath for the OPEN note, so wikilinks know
// whether they point at a real note or a ghost.
let _outlinkMap = {};

function _renderPreview(src, outlinks) {
  if (Array.isArray(outlinks)) {
    _outlinkMap = {};
    for (const o of outlinks) _outlinkMap[o.target] = o.resolved;
  }
  const prepared = _preprocessMarkdown(src || '');
  const box = _modal.querySelector('.atlas-preview');
  box.innerHTML = mdToHtml(prepared, { sanitize: true });
  // mdToHtml turns [label](#atlas-link-<target>) into a normal <a>. Tag those
  // as wikilinks and mark whether they resolve to a real note (vs a ghost).
  box.querySelectorAll('a[href^="#atlas-link-"]').forEach((a) => {
    const target = decodeURIComponent(a.getAttribute('href').slice('#atlas-link-'.length));
    const resolved = _outlinkMap[target] || '';
    a.classList.add('atlas-wikilink');
    if (!resolved) a.classList.add('missing');
    a.dataset.target = target;
    a.dataset.resolved = resolved;
  });
  // #tag anchors → pills.
  box.querySelectorAll('a[href^="#atlas-tag-"]').forEach((a) => {
    a.classList.add('atlas-tag');
    a.dataset.tag = decodeURIComponent(a.getAttribute('href').slice('#atlas-tag-'.length));
  });
}

// Mirror of src/atlas_links: a fenced/inline code span we must NOT scan for
// links or tags (so `# heading` and example snippets stay literal).
const _CODE_SPAN = /```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`/g;

function _preprocessMarkdown(src) {
  // Protect code spans from the link/tag passes by stashing them behind
  // placeholders, then restoring verbatim at the end.
  const stash = [];
  src = src.replace(_CODE_SPAN, (m) => { stash.push(m); return ` ${stash.length - 1} `; });

  // Image embeds: ![[pic.png]] → markdown image served from the raw endpoint.
  src = src.replace(/!\[\[([^\]|#]+?)\]\]/g, (m, target) => {
    const t = target.trim();
    if (/\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(t)) {
      const path = t.includes('/') ? t : `_attachments/${t}`;
      return `![${t}](${API}/raw?path=${encodeURIComponent(path)})`;
    }
    return `[[${target}]]`; // non-image embed → render as a normal link
  });
  // [[Target|alias]] / [[Target#heading]] / [[Target]] → a standard markdown
  // link with an #atlas-link- href (decoded + classified after render).
  src = src.replace(/\[\[([^\]]+?)\]\]/g, (m, inner) => {
    const [left, alias] = inner.split('|');
    const target = left.split('#')[0].trim();
    const label = (alias || left).trim().replace(/[[\]]/g, '');
    return `[${label}](#atlas-link-${encodeURIComponent(target)})`;
  });
  // #tag / #parent/child → a pill link. Anchored to line-start or whitespace
  // (so `# Heading` and `url#frag` are skipped) and not purely numeric — same
  // rules as the Python parser.
  src = src.replace(/(^|\s)#([A-Za-z0-9_][\w/-]*)/g, (m, pre, raw) => {
    const tag = raw.replace(/\/+$/, '');
    if (!tag || tag.replace(/\//g, '').match(/^\d+$/)) return m;
    return `${pre}[#${tag}](#atlas-tag-${encodeURIComponent(tag)})`;
  });

  return src.replace(/ (\d+) /g, (m, i) => stash[+i]);
}

function _renderBacklinks(list) {
  const box = _modal.querySelector('[data-el="backlinks"]');
  if (!list.length) { box.innerHTML = '<div style="opacity:.5;">None</div>'; return; }
  box.innerHTML = '';
  for (const path of list) {
    const d = document.createElement('div');
    d.className = 'atlas-bl';
    d.textContent = (_notes.find((n) => n.path === path) || {}).title || path;
    d.title = path;
    d.onclick = () => openNote(path);
    box.appendChild(d);
  }
}

// ── graph: a dockable panel on the right that widens the dialog ───────────────
// Width is adjusted in JS (not via a CSS class) so it composes with the inline
// width the resize handles set — the dialog grows by the panel width on open
// and shrinks back on close, capped to the viewport.
function _toggleGraph(force) {
  _graphOpen = (typeof force === 'boolean') ? force : !_graphOpen;
  const wrap = _modal.querySelector('.atlas-graph-wrap');
  const content = _modal.querySelector('.modal-content');
  const w = content.getBoundingClientRect().width;
  if (_graphOpen) {
    content.style.width = Math.min(window.innerWidth * 0.98, w + GRAPH_PANEL_W) + 'px';
    wrap.style.display = '';
    _openGraph();
  } else {
    wrap.style.display = 'none';
    content.style.width = Math.max(720, w - GRAPH_PANEL_W) + 'px';
    if (_graph) _graph.stop();
  }
  _modal.querySelector('[data-act="graph-toggle"]').classList.toggle('active', _graphOpen);
}

async function _openGraph() {
  const canvas = _modal.querySelector('canvas.atlas-graph');
  let data;
  try { data = await apiGet('/graph'); } catch { data = { nodes: [], links: [] }; }
  if (!_graph) _graph = new GraphView(canvas, (id) => { if (!id.startsWith('::missing::')) openNote(id); });
  // Wait one frame so the freshly-shown panel has its real width before the
  // simulation seeds node positions / sizes the canvas.
  requestAnimationFrame(() => {
    _graph.setData(data.nodes, data.links);
    _graph.start();
  });
}

// ── templates ─────────────────────────────────────────────────────────────
function _insertAtCursor(ta, text) {
  const s = ta.selectionStart ?? ta.value.length;
  const e = ta.selectionEnd ?? ta.value.length;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
  ta.selectionStart = ta.selectionEnd = s + text.length;
  ta.focus();
  ta.dispatchEvent(new Event('input'));   // re-render preview + queue save
}

async function _insertTemplate() {
  if (!_current) { showToast('Open or create a note first.'); return; }
  const templates = _notes.filter((n) => n.path.startsWith(TEMPLATE_DIR));
  if (!templates.length) {
    showToast(`No templates yet — make a note under "${TEMPLATE_DIR}" and it shows up here.`);
    return;
  }
  const pick = await _listDialog('Insert template', templates.map((t) => ({
    label: t.title || t.path.slice(TEMPLATE_DIR.length),
    value: t.path,
  })));
  if (!pick) return;
  try {
    const data = await apiGet('/note', { path: pick });
    const ta = _modal.querySelector('textarea');
    _insertAtCursor(ta, _expandTemplate(data.content || ''));
  } catch (e) { showError('Could not load template: ' + e.message); }
}

// Tiny placeholder expansion so templates can carry a live date / title,
// mirroring the most-used Obsidian core-template tokens.
function _expandTemplate(text) {
  const now = new Date();
  const iso = now.toISOString().slice(0, 10);
  const title = _current ? _current.split('/').pop().replace(/\.md$/i, '') : '';
  return text
    .replace(/\{\{\s*date\s*\}\}/gi, iso)
    .replace(/\{\{\s*time\s*\}\}/gi, now.toTimeString().slice(0, 5))
    .replace(/\{\{\s*title\s*\}\}/gi, title);
}

// ── import (drop-in dialog) / export ─────────────────────────────────────────
let _importOverlay = null;

function _buildImportOverlay() {
  if (_importOverlay) return _importOverlay;
  const ov = document.createElement('div');
  ov.id = 'atlas-import-overlay';
  ov.className = 'modal';
  ov.style.display = 'none';
  ov.innerHTML = `
    <div class="modal-content" style="width:min(520px,92vw);">
      <div class="modal-header"><h4>Import to Atlas</h4></div>
      <div class="modal-body">
        <div class="atlas-drop" data-el="drop">
          <div style="font-size:13px;">Drop Markdown files or a <code>.zip</code> here</div>
          <div style="opacity:.55;font-size:11px;">a single .md works too</div>
          <div style="display:flex;gap:8px;">
            <button class="atlas-btn" data-act="pick-files">Choose files…</button>
            <button class="atlas-btn" data-act="pick-folder">Choose folder…</button>
          </div>
        </div>
      </div>
      <div class="modal-footer"><button class="confirm-btn confirm-btn-secondary" data-act="cancel">Close</button></div>
    </div>
    <input type="file" data-el="files-input" accept=".md,.markdown,.txt,.csv,.json,.zip,.png,.jpg,.jpeg,.gif,.webp,.svg" multiple style="display:none;">
    <input type="file" data-el="folder-input" webkitdirectory multiple style="display:none;">
  `;
  document.body.appendChild(ov);
  _importOverlay = ov;

  const close = () => { ov.style.display = 'none'; ov.classList.add('hidden'); };
  ov.querySelector('[data-act="cancel"]').onclick = close;
  ov.addEventListener('click', (e) => { if (e.target === ov) close(); });

  const filesInput = ov.querySelector('[data-el="files-input"]');
  const folderInput = ov.querySelector('[data-el="folder-input"]');
  ov.querySelector('[data-act="pick-files"]').onclick = () => filesInput.click();
  ov.querySelector('[data-act="pick-folder"]').onclick = () => folderInput.click();
  filesInput.onchange = (e) => { close(); _doImport(e.target.files); };
  folderInput.onchange = (e) => { close(); _doImport(e.target.files); };

  const drop = ov.querySelector('[data-el="drop"]');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove('dragover');
  }));
  drop.addEventListener('drop', (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) { close(); _doImport(files); }
  });
  return ov;
}

function _openImportDialog() {
  const ov = _buildImportOverlay();
  ov.classList.remove('hidden');
  ov.style.display = 'flex';
}

async function _doImport(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.webkitRelativePath || f.name);
  try {
    const r = await fetch(API + '/import', { method: 'POST', body: fd, credentials: 'same-origin' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const res = await r.json();
    showToast(`Imported ${res.imported || 0} file(s)` + (res.skipped ? `, skipped ${res.skipped}.` : '.'));
    await _reloadNotes();
  } catch (e) { showError('Import failed: ' + e.message); }
}

// Small styled single-choice picker (used for templates). Resolves the chosen
// item's value, or null on cancel. Reuses the app's modal classes so it matches
// the rest of the UI.
function _listDialog(title, items) {
  return new Promise((resolve) => {
    const ov = document.createElement('div');
    ov.className = 'modal';
    ov.style.display = 'flex';
    ov.innerHTML = `
      <div class="modal-content" style="width:min(420px,90vw);max-height:70vh;display:flex;flex-direction:column;">
        <div class="modal-header"><h4>${title}</h4></div>
        <div class="modal-body" data-el="list" style="overflow:auto;"></div>
        <div class="modal-footer"><button class="confirm-btn confirm-btn-secondary" data-act="cancel">Cancel</button></div>
      </div>`;
    document.body.appendChild(ov);
    const done = (v) => { ov.remove(); resolve(v); };
    const list = ov.querySelector('[data-el="list"]');
    for (const it of items) {
      const row = document.createElement('div');
      row.className = 'atlas-pick-item';
      row.textContent = it.label;
      row.onclick = () => done(it.value);
      list.appendChild(row);
    }
    ov.querySelector('[data-act="cancel"]').onclick = () => done(null);
    ov.addEventListener('click', (e) => { if (e.target === ov) done(null); });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Bases — saved structured queries rendered as table / cards / board, with a
// filter builder and inline property editing. Mirrors the backend query model
// (src/atlas_query.py); the agent/MCP produce the very same JSON.
// ═══════════════════════════════════════════════════════════════════════════
let _base = null;   // { path, query, result } for the open base

const FIELD_OPTS = [
  'file.title', 'file.path', 'file.tags', 'file.folder', 'file.mtime',
  'section.heading', 'section.body', 'section.level',
];
const OP_OPTS = ['eq', 'ne', 'contains', 'startswith', 'endswith', 'regex',
  'gt', 'lt', 'gte', 'lte', 'exists', 'empty', 'in'];

async function _newBase() {
  const folder = _selectedFolder ? _selectedFolder + '/' : '';
  const path = await styledPrompt('', {
    title: 'New base', defaultValue: folder, placeholder: 'e.g. Tracking/Open items',
    confirmText: 'Create', maxLength: 200,
  });
  if (!path || !path.replace(/\/+$/, '')) return;
  const query = { from: 'notes', where: { join: 'and', filters: [] }, view: 'table' };
  try {
    const res = await apiSend('/base', 'PUT', { path, query });
    await _reloadNotes();
    openBase(res.path, query);
  } catch (e) { showError('Could not create base: ' + e.message); }
}

async function openBase(path, presetQuery) {
  try {
    let query, result;
    if (presetQuery) {
      query = presetQuery;
      result = await apiSend('/query', 'POST', { query });
    } else {
      const data = await apiGet('/base', { path });
      query = data.query || {}; result = data;
    }
    _base = { path, query, result };
    _current = null;
    _showCenter('base');
    _renderBase();
    _renderList(_notes);
  } catch (e) { showError('Could not open base: ' + e.message); }
}

async function _runBase() {
  try {
    _base.result = await apiSend('/query', 'POST', { query: _base.query });
    _renderBase();
  } catch (e) { showError('Query failed: ' + e.message); }
}

async function _saveBase() {
  try {
    await apiSend('/base', 'PUT', { path: _base.path, query: _base.query });
    showToast('Base saved.');
  } catch (e) { showError('Save failed: ' + e.message); }
}

function _renderBase() {
  const host = _modal.querySelector('.atlas-base-view');
  const q = _base.query;
  const view = q.view || 'table';
  host.innerHTML = '';

  // Toolbar.
  const bar = document.createElement('div');
  bar.className = 'atlas-base-bar';
  bar.innerHTML = `
    <span class="title"></span>
    <button class="atlas-btn" data-v="table">Table</button>
    <button class="atlas-btn" data-v="cards">Cards</button>
    <button class="atlas-btn" data-v="board">Board</button>
    <button class="atlas-btn" data-act="filters">Filters</button>
    <button class="atlas-btn" data-act="save">Save</button>`;
  bar.querySelector('.title').textContent = _base.path.split('/').pop().replace(/\.base$/, '');
  bar.querySelectorAll('[data-v]').forEach((b) => {
    b.classList.toggle('active', b.dataset.v === view);
    b.onclick = () => { _base.query.view = b.dataset.v; _renderBase(); };
  });
  bar.querySelector('[data-act="filters"]').onclick = () => _toggleBuilder(host);
  bar.querySelector('[data-act="save"]').onclick = _saveBase;
  host.appendChild(bar);

  const content = document.createElement('div');
  content.className = 'atlas-base-content';
  host.appendChild(content);

  const res = _base.result || { columns: [], rows: [] };
  if (view === 'cards') _renderCards(content, res);
  else if (view === 'board') _renderBoard(content, res, q.group_by);
  else _renderTable(content, res);
}

function _openRowNote(path) { if (path) openNote(path); }

function _renderTable(host, res) {
  if (!res.rows.length) { host.innerHTML = '<div style="opacity:.5">No matching notes.</div>'; return; }
  const cols = res.columns;
  const table = document.createElement('table');
  table.className = 'atlas-table';
  const thead = document.createElement('tr');
  thead.innerHTML = '<th>Note</th>' + cols.map((c) => `<th data-c="${_attr(c)}"></th>`).join('');
  thead.querySelectorAll('th[data-c]').forEach((th, i) => {
    th.textContent = cols[i];
    th.onclick = () => { _base.query.sort = [{ field: cols[i], dir: _flipDir(cols[i]) }]; _runBase(); };
  });
  table.appendChild(thead);
  for (const row of res.rows) {
    const tr = document.createElement('tr');
    const open = document.createElement('td');
    const a = document.createElement('a');
    a.className = 'cellopen'; a.textContent = (row['file.path'] || '').split('/').pop().replace(/\.md$/, '');
    a.onclick = () => _openRowNote(row['file.path']);
    open.appendChild(a); tr.appendChild(open);
    for (const c of cols) {
      const td = document.createElement('td');
      const v = row[c];
      td.textContent = Array.isArray(v) ? v.join(', ') : (v == null ? '' : String(v));
      // Inline edit for frontmatter property columns.
      if (c.startsWith('prop.')) {
        td.classList.add('editable');
        td.title = 'Click to edit';
        td.onclick = () => _editCell(td, row['file.path'], c.slice(5), v);
      }
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  host.appendChild(table);
}

function _editCell(td, notePath, key, current) {
  if (td.querySelector('input')) return;
  const input = document.createElement('input');
  input.value = current == null ? '' : (Array.isArray(current) ? current.join(', ') : String(current));
  input.style.width = '100%';
  td.textContent = ''; td.appendChild(input); input.focus();
  const commit = async () => {
    const val = input.value.trim();
    try {
      await apiSend('/property', 'POST', { path: notePath, key, value: val });
      await _runBase();
    } catch (e) { showError('Update failed: ' + e.message); _renderBase(); }
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    else if (e.key === 'Escape') _renderBase();
  });
  input.addEventListener('blur', commit);
}

function _renderCards(host, res) {
  if (!res.rows.length) { host.innerHTML = '<div style="opacity:.5">No matching notes.</div>'; return; }
  const wrap = document.createElement('div'); wrap.className = 'atlas-cards';
  for (const row of res.rows) wrap.appendChild(_card(row, res.columns));
  host.appendChild(wrap);
}

function _card(row, cols) {
  const card = document.createElement('div'); card.className = 'atlas-card';
  card.onclick = () => _openRowNote(row['file.path']);
  const ttl = document.createElement('div'); ttl.className = 'ttl';
  ttl.textContent = row['file.title'] || (row['file.path'] || '').split('/').pop();
  card.appendChild(ttl);
  for (const c of cols) {
    if (c === 'file.title') continue;
    const v = row[c]; if (v == null || v === '') continue;
    const kv = document.createElement('div'); kv.className = 'kv';
    kv.textContent = `${c.replace(/^prop\./, '')}: ${Array.isArray(v) ? v.join(', ') : v}`;
    card.appendChild(kv);
  }
  return card;
}

function _renderBoard(host, res, groupBy) {
  if (!groupBy) {
    host.innerHTML = '<div style="opacity:.6">Board view needs a "group by" field — open Filters to set one.</div>';
    return;
  }
  const cols = new Map();
  for (const row of res.rows) {
    const key = (row[groupBy] == null || row[groupBy] === '') ? '(none)' : String(row[groupBy]);
    if (!cols.has(key)) cols.set(key, []);
    cols.get(key).push(row);
  }
  const board = document.createElement('div'); board.className = 'atlas-board';
  for (const [key, rows] of cols) {
    const col = document.createElement('div'); col.className = 'atlas-col';
    const h = document.createElement('h5'); h.textContent = `${key} (${rows.length})`; col.appendChild(h);
    for (const row of rows) col.appendChild(_card(row, res.columns));
    board.appendChild(col);
  }
  host.appendChild(board);
}

// Inline filter builder, toggled above the results.
function _toggleBuilder(host) {
  const existing = host.querySelector('.atlas-builder');
  if (existing) { existing.remove(); return; }
  const q = _base.query;
  const b = document.createElement('div'); b.className = 'atlas-builder';
  b.innerHTML = `
    <div style="margin-bottom:8px;">
      <label>From</label>
      <select data-f="from"><option value="notes">notes</option><option value="sections">sections</option></select>
      <label style="margin-left:12px;">Match</label>
      <select data-f="join"><option value="and">all (AND)</option><option value="or">any (OR)</option></select>
      <label style="margin-left:12px;">Group by</label>
      <input data-f="group" placeholder="e.g. prop.status" style="width:130px;">
    </div>
    <div class="rows"></div>
    <button class="atlas-btn" data-act="add">+ condition</button>
    <button class="atlas-btn" data-act="run">Run</button>`;
  b.querySelector('[data-f="from"]').value = q.from || 'notes';
  b.querySelector('[data-f="join"]').value = (q.where && q.where.join) || 'and';
  b.querySelector('[data-f="group"]').value = q.group_by || '';
  const rows = b.querySelector('.rows');
  const filters = (q.where && q.where.filters) || [];
  filters.forEach((f, i) => rows.appendChild(_filterRow(f, i)));
  b.querySelector('[data-act="add"]').onclick = () => {
    rows.appendChild(_filterRow({ field: 'prop.status', op: 'eq', value: '' }, rows.children.length));
  };
  b.querySelector('[data-act="run"]').onclick = () => {
    q.from = b.querySelector('[data-f="from"]').value;
    q.group_by = b.querySelector('[data-f="group"]').value.trim() || undefined;
    const newFilters = [...rows.querySelectorAll('.atlas-filter-row')].map((r) => ({
      field: r.querySelector('[data-k="field"]').value,
      op: r.querySelector('[data-k="op"]').value,
      value: r.querySelector('[data-k="value"]').value,
    })).filter((f) => f.field && f.op);
    q.where = { join: b.querySelector('[data-f="join"]').value, filters: newFilters };
    _runBase();
  };
  host.querySelector('.atlas-base-content').before(b);
}

function _filterRow(f) {
  const row = document.createElement('div'); row.className = 'atlas-filter-row';
  const fieldSel = `<select data-k="field">${FIELD_OPTS.map((o) => `<option ${o === f.field ? 'selected' : ''}>${o}</option>`).join('')}<option ${(f.field || '').startsWith('prop.') ? 'selected' : ''} value="${_attr(f.field)}">${_esc(f.field || 'prop.…')}</option></select>`;
  const opSel = `<select data-k="op">${OP_OPTS.map((o) => `<option ${o === f.op ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
  row.innerHTML = `${fieldSel}${opSel}<input data-k="value" placeholder="value" value="${_attr(f.value == null ? '' : f.value)}"><button class="atlas-btn" data-act="rm">✕</button>`;
  // Let the user type a custom prop.<name> field.
  const fieldEl = row.querySelector('[data-k="field"]');
  fieldEl.addEventListener('dblclick', async () => {
    const custom = await styledPrompt('', { title: 'Field', defaultValue: 'prop.', confirmText: 'Set' });
    if (custom) { const o = document.createElement('option'); o.value = custom; o.textContent = custom; o.selected = true; fieldEl.appendChild(o); }
  });
  row.querySelector('[data-act="rm"]').onclick = () => row.remove();
  return row;
}

function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function _attr(s) { return _esc(s).replace(/"/g, '&quot;'); }
function _flipDir(field) {
  const cur = (_base.query.sort && _base.query.sort[0]);
  return (cur && cur.field === field && cur.dir === 'asc') ? 'desc' : 'asc';
}

// ═══════════════════════════════════════════════════════════════════════════
// GraphView — a small dependency-free force-directed graph on <canvas>.
// O(n²) repulsion is fine for typical personal vaults (hundreds of notes);
// the simulation cools via `alpha` and is paused when not visible.
// ═══════════════════════════════════════════════════════════════════════════
class GraphView {
  constructor(canvas, onNodeClick) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onNodeClick = onNodeClick;
    this.nodes = [];
    this.links = [];
    this.alpha = 1;
    this.raf = null;
    this.scale = 1;
    this.offset = { x: 0, y: 0 };
    this.drag = null;       // node being dragged
    this.pan = null;        // background pan start
    this.hover = null;
    this._bind();
  }

  setData(nodes, links) {
    const w = this.canvas.clientWidth || 600, h = this.canvas.clientHeight || 400;
    const byId = {};
    this.nodes = nodes.map((n) => {
      const node = { ...n, x: w / 2 + (Math.random() - 0.5) * 200, y: h / 2 + (Math.random() - 0.5) * 200, vx: 0, vy: 0, deg: 0 };
      byId[n.id] = node;
      return node;
    });
    this.links = links
      .map((l) => ({ source: byId[l.source], target: byId[l.target] }))
      .filter((l) => l.source && l.target);
    this.links.forEach((l) => { l.source.deg++; l.target.deg++; });
    this.alpha = 1;
  }

  start() {
    this._resize();
    if (!this.raf) this._loop();
  }
  stop() { if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; } }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.canvas.width = w * dpr; this.canvas.height = h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  _tick() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const REPULSE = 2200, SPRING = 0.02, LEN = 70;
    const nodes = this.nodes;
    // Repulsion (every pair).
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const f = (REPULSE / d2) * this.alpha;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    // Spring along links.
    for (const l of this.links) {
      let dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - LEN) * SPRING * this.alpha;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      l.source.vx += fx; l.source.vy += fy; l.target.vx -= fx; l.target.vy -= fy;
    }
    // Gentle centering + integrate.
    for (const n of nodes) {
      if (n === this.drag) { n.vx = 0; n.vy = 0; continue; }
      n.vx += (w / 2 - n.x) * 0.0008 * this.alpha;
      n.vy += (h / 2 - n.y) * 0.0008 * this.alpha;
      n.vx *= 0.85; n.vy *= 0.85;
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= 0.995;
    if (this.alpha < 0.02) this.alpha = 0.02; // keep a little life for dragging
  }

  _loop() {
    this._tick();
    this._draw();
    this.raf = requestAnimationFrame(() => this._loop());
  }

  _toScreen(n) { return { x: n.x * this.scale + this.offset.x, y: n.y * this.scale + this.offset.y }; }
  _toWorld(px, py) { return { x: (px - this.offset.x) / this.scale, y: (py - this.offset.y) / this.scale }; }

  _draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);
    const cs = getComputedStyle(this.canvas);
    const fg = cs.color || '#ccc';
    // edges
    ctx.strokeStyle = 'rgba(127,127,127,.35)';
    ctx.lineWidth = 1;
    for (const l of this.links) {
      const a = this._toScreen(l.source), b = this._toScreen(l.target);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    // nodes
    for (const n of this.nodes) {
      const p = this._toScreen(n);
      const r = (4 + Math.min(n.deg, 8)) * this.scale;
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = n.missing ? 'rgba(230,100,100,.65)' : (n === this.hover ? '#6cf' : 'rgba(120,150,255,.85)');
      ctx.fill();
      if (this.scale > 0.6 || n === this.hover) {
        ctx.fillStyle = fg; ctx.font = `${11}px sans-serif`; ctx.textAlign = 'center';
        ctx.fillText(n.title || n.id, p.x, p.y - r - 3);
      }
    }
  }

  _nodeAt(px, py) {
    const wp = this._toWorld(px, py);
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      const r = 6 + Math.min(n.deg, 8);
      if ((n.x - wp.x) ** 2 + (n.y - wp.y) ** 2 <= r * r) return n;
    }
    return null;
  }

  _bind() {
    const c = this.canvas;
    const pos = (e) => { const r = c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    c.addEventListener('mousedown', (e) => {
      const p = pos(e); const n = this._nodeAt(p.x, p.y);
      if (n) { this.drag = n; this._moved = false; }
      else { this.pan = { x: p.x - this.offset.x, y: p.y - this.offset.y }; }
    });
    window.addEventListener('mousemove', (e) => {
      if (!this.drag && !this.pan) {
        const p = pos(e); this.hover = this._nodeAt(p.x, p.y);
        c.style.cursor = this.hover ? 'pointer' : 'grab';
        return;
      }
      const p = pos(e); this._moved = true;
      if (this.drag) { const wp = this._toWorld(p.x, p.y); this.drag.x = wp.x; this.drag.y = wp.y; this.alpha = Math.max(this.alpha, 0.3); }
      else if (this.pan) { this.offset.x = p.x - this.pan.x; this.offset.y = p.y - this.pan.y; }
    });
    window.addEventListener('mouseup', (e) => {
      if (this.drag && !this._moved) this.onNodeClick(this.drag.id);
      this.drag = null; this.pan = null;
    });
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const p = pos(e); const before = this._toWorld(p.x, p.y);
      this.scale *= e.deltaY < 0 ? 1.1 : 0.9;
      this.scale = Math.max(0.2, Math.min(4, this.scale));
      const after = this._toWorld(p.x, p.y);
      this.offset.x += (after.x - before.x) * this.scale;
      this.offset.y += (after.y - before.y) * this.scale;
    }, { passive: false });
  }
}

const atlasModule = { openAtlas, openAtlasDocked, openAtlasNote, closeAtlas, isAtlasOpen };
export { openAtlas, openAtlasDocked, openAtlasNote, closeAtlas, isAtlasOpen };
export default atlasModule;
