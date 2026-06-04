// static/js/projects.js
// Projects sidebar section — stores project list in localStorage,
// validates folder paths against the server, and can create folders.

import Storage from './storage.js';

const STORAGE_KEY = 'odysseus-projects';

// ── Helpers ───────────────────────────────────────────────────────────────

function _load() {
  return Storage.getJSON(STORAGE_KEY) || [];
}

function _save(projects) {
  Storage.setJSON(STORAGE_KEY, projects);
}

function _genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

// ── DOM refs (resolved lazily) ────────────────────────────────────────────

function _el(id) { return document.getElementById(id); }

// ── File-type constants ───────────────────────────────────────────

const _IGNORED_DIRS = new Set([
  '.git','__pycache__','node_modules','.venv','venv','env',
  'dist','build','.idea','.vscode','.pytest_cache','.mypy_cache',
  'target','vendor','.cache','.tox','coverage','.next','.nuxt',
  '.svelte-kit','out','.output','.gradle','.settings',
]);

const _ALLOWED_DOTS = new Set([
  '.env','.gitignore','.gitattributes','.editorconfig',
  '.nvmrc','.npmrc','.prettierrc','.eslintrc','.babelrc','.env.example',
]);

const _TEXT_EXTS = new Set([
  'py','js','ts','jsx','tsx','mjs','cjs',
  'html','htm','css','scss','sass','less',
  'json','jsonc','yaml','yml','toml','ini',
  'cfg','conf','env','md','mdx','rst','txt','csv','tsv',
  'sh','bash','zsh','fish','ps1','bat','cmd',
  'sql','go','rs','c','cpp','cc','cxx',
  'h','hpp','hxx','java','kt','kts','swift',
  'php','rb','pl','r','m','lua',
  'ex','exs','erl','hs','clj','cljs',
  'xml','svg','vue','svelte',
  'graphql','gql','diff','patch',
]);

const _TEXT_NAMES = new Set([
  'Makefile','makefile','Dockerfile','dockerfile',
  'Jenkinsfile','Procfile','Vagrantfile','Gemfile',
  'Rakefile','.env','.gitignore','.gitattributes',
  '.editorconfig','.nvmrc','.npmrc',
  'requirements.txt','setup.cfg','pyproject.toml',
  'package.json','tsconfig.json','jsconfig.json',
  'README','LICENSE','CHANGELOG','AUTHORS',
]);

function _isReadable(name) {
  const dotIdx = name.lastIndexOf('.');
  const ext = dotIdx >= 0 ? name.slice(dotIdx + 1).toLowerCase() : '';
  return _TEXT_EXTS.has(ext) || _TEXT_NAMES.has(name);
}

// ── IndexedDB handle store ──────────────────────────────────────────

const _IDB_NAME  = 'odysseus-fs-handles';
const _IDB_STORE = 'handles';

function _openHandleDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_IDB_NAME, 2);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(_IDB_STORE)) db.createObjectStore(_IDB_STORE);
      if (!db.objectStoreNames.contains('snapshots'))  db.createObjectStore('snapshots');
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror  = () => reject(req.error);
  });
}

async function _saveHandle(projectId, handle) {
  const db = await _openHandleDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readwrite');
    tx.objectStore(_IDB_STORE).put(handle, projectId);
    tx.oncomplete = resolve;
    tx.onerror    = () => reject(tx.error);
  });
}

async function _loadHandle(projectId) {
  const db = await _openHandleDB();
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(_IDB_STORE, 'readonly');
    const req = tx.objectStore(_IDB_STORE).get(projectId);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror   = () => reject(req.error);
  });
}

async function _removeHandle(projectId) {
  const db = await _openHandleDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readwrite');
    tx.objectStore(_IDB_STORE).delete(projectId);
    tx.oncomplete = resolve;
    tx.onerror    = () => reject(tx.error);
  });
}

// ── Diff / workspace state ──────────────────────────────────────────────────

let _currentProject = null;     // project currently open in diff view
let _wsHandle = null;           // alias kept for access-banner compat
let _pendingHandle = null;      // handle chosen in the add/edit modal
const _wsSelected = new Set();  // (kept for compat, no longer used)

let _diffHandle   = null;   // FileSystemDirectoryHandle for diff view
let _diffSnapshot = null;   // snapshot loaded for diff view
let _diffChanges  = [];     // detected changes in diff view
let _expandedProjects = new Set();  // project IDs expanded in sidebar
const _changeCounts = {};           // projectId → changed file count

// ── Snapshot IDB functions ────────────────────────────────────────────────

async function _saveSnapshot(projectId, snapshot) {
  const db = await _openHandleDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('snapshots', 'readwrite');
    tx.objectStore('snapshots').put(snapshot, projectId);
    tx.oncomplete = resolve;
    tx.onerror    = () => reject(tx.error);
  });
}

async function _loadSnapshot(projectId) {
  const db = await _openHandleDB();
  return new Promise((resolve, reject) => {
    const tx  = db.transaction('snapshots', 'readonly');
    const req = tx.objectStore('snapshots').get(projectId);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror   = () => reject(req.error);
  });
}

// Recursively collect readable text files: { relPath: {content,size,mtime} }
async function _collectSnapshotFiles(dirHandle, baseRel, result, depth) {
  if (depth > 6) return;
  for await (const [name, entry] of dirHandle) {
    if (name.startsWith('.') && !_ALLOWED_DOTS.has(name)) continue;
    const rel = baseRel ? baseRel + '/' + name : name;
    if (entry.kind === 'directory') {
      if (_IGNORED_DIRS.has(name)) continue;
      await _collectSnapshotFiles(entry, rel, result, depth + 1);
    } else if (_isReadable(name)) {
      try {
        const file = await entry.getFile();
        const content = file.size < 150_000 ? await file.text() : null;
        result[rel] = { content, size: file.size, mtime: file.lastModified };
      } catch (_e) {}
    }
  }
}

async function _takeSnapshotForProject(projectId) {
  const handle = await _loadHandle(projectId);
  if (!handle) return null;
  let perm = 'denied';
  try { perm = await handle.queryPermission({ mode: 'read' }); } catch { return null; }
  if (perm !== 'granted') return null;
  const files = {};
  await _collectSnapshotFiles(handle, '', files, 0);
  const snapshot = { timestamp: Date.now(), files };
  await _saveSnapshot(projectId, snapshot);
  return snapshot;
}

// Returns [{path, type:'added'|'modified'|'deleted', oldContent, newContent}]
async function _detectChanges(projectId, handle, snapshot) {
  if (!handle || !snapshot) return [];
  let perm = 'denied';
  try { perm = await handle.queryPermission({ mode: 'read' }); } catch { return []; }
  if (perm !== 'granted') return [];
  const current = {};
  await _collectSnapshotFiles(handle, '', current, 0);
  const snap = snapshot.files || {};
  const changes = [];
  for (const [path, curr] of Object.entries(current)) {
    if (!snap[path]) {
      changes.push({ path, type: 'added', oldContent: '', newContent: curr.content || '' });
    } else if (snap[path].size !== curr.size || snap[path].mtime !== curr.mtime) {
      changes.push({ path, type: 'modified', oldContent: snap[path].content || '', newContent: '' });
    }
  }
  for (const path of Object.keys(snap)) {
    if (!current[path]) changes.push({ path, type: 'deleted', oldContent: snap[path].content || '', newContent: '' });
  }
  return changes.sort((a, b) => a.path.localeCompare(b.path));
}

// ── Diff algorithm ────────────────────────────────────────────────────────

// Returns [{type:'='|'+'|'-', line}] using LCS.
function _computeLineDiff(oldText, newText) {
  if (oldText === newText) return [];
  const aLines = oldText.split('\n'), bLines = newText.split('\n');
  const M = aLines.length, N = bLines.length;
  if (M * N > 200000) {
    return [
      ...aLines.map(l => ({ type: '-', line: l })),
      ...bLines.map(l => ({ type: '+', line: l })),
    ];
  }
  const dp = Array.from({ length: M + 1 }, () => new Int32Array(N + 1));
  for (let i = 1; i <= M; i++)
    for (let j = 1; j <= N; j++)
      dp[i][j] = aLines[i-1] === bLines[j-1] ? dp[i-1][j-1]+1 : Math.max(dp[i-1][j], dp[i][j-1]);
  const ops = []; let i = M, j = N;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && aLines[i-1] === bLines[j-1]) { ops.unshift({type:'=',line:aLines[i-1]}); i--; j--; }
    else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) { ops.unshift({type:'+',line:bLines[j-1]}); j--; }
    else { ops.unshift({type:'-',line:aLines[i-1]}); i--; }
  }
  return ops;
}

function _renderDiffHtml(ops) {
  const CTX = 3;
  const shown = new Set();
  for (let k = 0; k < ops.length; k++)
    if (ops[k].type !== '=') for (let c = Math.max(0, k-CTX); c <= Math.min(ops.length-1, k+CTX); c++) shown.add(c);
  let html = '', skipped = 0, prevShown = true;
  for (let k = 0; k < ops.length && k < 600; k++) {
    if (!shown.has(k)) { if (ops[k].type === '=') { skipped++; } prevShown = false; continue; }
    if (!prevShown && skipped > 0) {
      html += `<div style="padding:1px 6px;opacity:0.38;font-size:11px;background:color-mix(in srgb,var(--fg) 4%,transparent);">@@ ${skipped} unchanged line${skipped!==1?'s':''} @@</div>`;
      skipped = 0;
    }
    prevShown = true;
    const {type, line} = ops[k];
    const esc = _escape(line);
    if (type === '=')  html += `<div style="padding:1px 6px;opacity:0.5;"><span style="display:inline-block;width:18px;user-select:none;"> </span>${esc}</div>`;
    else if (type==='-') html += `<div style="padding:1px 6px;background:color-mix(in srgb,#e06c75 14%,transparent);color:color-mix(in srgb,#e06c75 80%,var(--fg));"><span style="display:inline-block;width:18px;user-select:none;">-</span>${esc}</div>`;
    else                 html += `<div style="padding:1px 6px;background:color-mix(in srgb,#98c379 14%,transparent);color:color-mix(in srgb,#98c379 80%,var(--fg));"><span style="display:inline-block;width:18px;user-select:none;">+</span>${esc}</div>`;
  }
  return html || '<div style="opacity:0.4;padding:4px 6px;">No text differences.</div>';
}

// ── Render sidebar list ───────────────────────────────────────────────────

function _renderList() {
  const list = _el('projects-list');
  if (!list) return;
  const projects = _load();
  list.innerHTML = '';
  if (projects.length === 0) {
    list.insertAdjacentHTML('beforeend',
      '<div style="font-size:11px;opacity:0.4;padding:4px 8px 6px;">No projects yet</div>');
    return;
  }

  const sessions = (window.sessionModule && window.sessionModule.getSessions)
    ? window.sessionModule.getSessions() : [];

  projects.forEach(p => {
    const wrap = document.createElement('div');
    wrap.dataset.projectWrap = p.id;

    // ── Project row ──
    const row = document.createElement('div');
    row.className = 'list-item';
    row.dataset.projectId = p.id;
    row.title = p.folderName || '';
    row.style.cssText = 'gap:4px;';

    // Chevron (expand/collapse)
    const chevron = document.createElement('span');
    chevron.style.cssText = 'flex-shrink:0;opacity:0.35;display:flex;align-items:center;transition:transform 0.12s;';
    chevron.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
    if (_expandedProjects.has(p.id)) chevron.style.transform = 'rotate(90deg)';
    row.appendChild(chevron);

    // Folder icon
    row.insertAdjacentHTML('beforeend',
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;">' +
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>' +
      '</svg>');

    // Name
    const nameEl = document.createElement('span');
    nameEl.className = 'grow';
    nameEl.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
    nameEl.textContent = p.name;
    row.appendChild(nameEl);

    // Change count badge
    const count = _changeCounts[p.id] || 0;
    if (count > 0) {
      const badge = document.createElement('span');
      badge.style.cssText = 'flex-shrink:0;background:var(--red,#e06c75);color:#fff;border-radius:8px;padding:0 5px;font-size:10px;font-weight:600;cursor:pointer;line-height:16px;';
      badge.title = count + ' changed file' + (count !== 1 ? 's' : '') + ' — click to view';
      badge.textContent = count;
      badge.addEventListener('click', e => { e.stopPropagation(); const pp = _load().find(x => x.id === p.id); if (pp) _openDiffView(pp); });
      row.appendChild(badge);
    }

    // Diff/changes icon button (shown when folder is linked)
    if (p.folderName) {
      const diffBtn = document.createElement('button');
      diffBtn.type = 'button';
      diffBtn.title = 'View file changes';
      diffBtn.style.cssText = 'flex-shrink:0;padding:2px;border:none;background:transparent;cursor:pointer;opacity:0.35;color:var(--fg);display:flex;align-items:center;border-radius:3px;';
      diffBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="15" x2="15" y2="15"/><line x1="12" y1="12" x2="12" y2="18"/></svg>';
      diffBtn.addEventListener('mouseenter', () => { diffBtn.style.opacity = '0.75'; });
      diffBtn.addEventListener('mouseleave', () => { diffBtn.style.opacity = '0.35'; });
      diffBtn.addEventListener('click', e => { e.stopPropagation(); const pp = _load().find(x => x.id === p.id); if (pp) _openDiffView(pp); });
      row.appendChild(diffBtn);
    }

    // Left-click → toggle expand/collapse (NOT open workspace)
    row.addEventListener('click', () => {
      if (_expandedProjects.has(p.id)) {
        _expandedProjects.delete(p.id);
        chevron.style.transform = '';
        subList.style.display = 'none';
      } else {
        _expandedProjects.add(p.id);
        chevron.style.transform = 'rotate(90deg)';
        subList.style.display = '';
      }
    });

    // Right-click → context menu
    row.addEventListener('contextmenu', e => {
      e.preventDefault();
      e.stopPropagation();
      _showContextMenu(p.id, e.clientX, e.clientY);
    });

    wrap.appendChild(row);

    // ── Sessions sub-list ──
    const subList = document.createElement('div');
    subList.style.cssText = 'display:' + (_expandedProjects.has(p.id) ? '' : 'none') + ';';
    subList.dataset.projectSessions = p.id;

    const projectSessions = sessions.filter(s => s.folder === p.name);
    if (projectSessions.length === 0) {
      const empty = document.createElement('div');
      empty.style.cssText = 'font-size:11px;opacity:0.35;padding:2px 8px 4px 30px;';
      empty.textContent = 'No chats yet';
      subList.appendChild(empty);
    } else {
      projectSessions.forEach(s => {
        const sRow = document.createElement('div');
        sRow.className = 'list-item';
        sRow.style.cssText = 'padding-left:28px;font-size:12px;';
        sRow.dataset.sessionId = s.id;
        sRow.innerHTML =
          '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.4;"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>' +
          '<span class="grow" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _escape(s.name || 'Untitled') + '</span>';
        sRow.addEventListener('click', e => {
          e.stopPropagation();
          if (window.sessionModule && window.sessionModule.selectSession) window.sessionModule.selectSession(s.id);
        });
        subList.appendChild(sRow);
      });
    }

    // "New chat" at end of sub-list
    const newChatRow = document.createElement('div');
    newChatRow.className = 'list-item';
    newChatRow.style.cssText = 'padding-left:28px;font-size:11px;opacity:0.5;';
    newChatRow.innerHTML =
      '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>' +
      '<span>New chat</span>';
    newChatRow.addEventListener('click', e => {
      e.stopPropagation();
      const pp = _load().find(x => x.id === p.id);
      if (pp) _launchProjectChat(pp, []);
    });
    subList.appendChild(newChatRow);

    wrap.appendChild(subList);
    list.appendChild(wrap);
  });
}

function _escape(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Context menu ──────────────────────────────────────────────────────────

let _ctxTargetId = null;

function _showContextMenu(projectId, x, y) {
  _ctxTargetId = projectId;
  const menu = _el('project-context-menu');
  if (!menu) return;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.classList.remove('hidden');

  // Close on next click anywhere
  setTimeout(() => {
    document.addEventListener('click', _closeContextMenu, { once: true });
  }, 0);
}

function _closeContextMenu() {
  const menu = _el('project-context-menu');
  if (menu) menu.classList.add('hidden');
  _ctxTargetId = null;
}

// ── Modal ─────────────────────────────────────────────────────────────────

let _editId = null; // null → new project, string → editing existing

function _openModal(project) {
  _editId = project ? project.id : null;
  _pendingHandle = null;
  const titleEl = _el('project-modal-title');
  if (titleEl) titleEl.innerHTML =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>' +
    (project ? 'Edit Project' : 'New Project');

  const nameInput  = _el('project-name-input');
  const pickLabel  = _el('project-pick-folder-label');
  const instrInput = _el('project-instructions-input');
  const errorEl    = _el('project-modal-error');

  if (nameInput)  nameInput.value  = project ? project.name : '';
  if (instrInput) instrInput.value = project ? (project.instructions || '') : '';
  if (pickLabel) {
    pickLabel.textContent = project && project.folderName ? project.folderName : 'Choose a folder…';
    pickLabel.style.opacity = project && project.folderName ? '1' : '0.5';
  }
  if (errorEl) { errorEl.textContent = ''; errorEl.style.display = 'none'; }

  // Populate agent dropdown
  const agentSelect = _el('project-agent-select');
  if (agentSelect) {
    agentSelect.innerHTML = '<option value="">None</option>';
    agentSelect.value = project ? (project.agentId || '') : '';
    fetch('/api/tokens', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : [])
      .catch(() => [])
      .then(tokens => {
        if (!Array.isArray(tokens)) return;
        const agentTokens = tokens.filter(t => {
          const n = (t.name || '').toLowerCase();
          return n.startsWith('codex agent') || n.startsWith('claude agent') ||
            (t.scopes || []).some(s => String(s || '').startsWith('shell:'));
        });
        agentTokens.forEach(t => {
          const opt = document.createElement('option');
          opt.value = t.id;
          opt.textContent = t.name || (t.token_prefix + '…');
          agentSelect.appendChild(opt);
        });
        agentSelect.value = project ? (project.agentId || '') : '';
      });
  }

  const modal = _el('project-modal');
  if (modal) modal.classList.remove('hidden');
  if (nameInput) setTimeout(() => nameInput.focus(), 50);
}

function _closeModal() {
  const modal = _el('project-modal');
  if (modal) modal.classList.add('hidden');
  _editId = null;
  _pendingHandle = null;
}

// ── Save project ──────────────────────────────────────────────────────────

async function _saveProject() {
  const nameInput = _el('project-name-input');
  const errorEl   = _el('project-modal-error');
  const name = (nameInput ? nameInput.value.trim() : '');
  const instrInput  = _el('project-instructions-input');
  const agentSelect = _el('project-agent-select');
  const instructions = instrInput  ? instrInput.value.trim()  : '';
  const agentId      = agentSelect ? agentSelect.value.trim() : '';

  function _showError(msg) {
    if (errorEl) { errorEl.textContent = msg; errorEl.style.display = 'block'; }
  }

  if (!name) { _showError('Name is required.'); return; }
  if (errorEl) { errorEl.style.display = 'none'; }

  const projects = _load();
  if (_editId) {
    const idx = projects.findIndex(p => p.id === _editId);
    if (idx !== -1) {
      projects[idx].name = name;
      projects[idx].instructions = instructions;
      projects[idx].agentId = agentId;
      if (_pendingHandle) {
        projects[idx].folderName = _pendingHandle.name;
        try { await _saveHandle(_editId, _pendingHandle); } catch (_e) {}
      }
    }
  } else {
    if (!_pendingHandle) { _showError('Please choose a folder first.'); return; }
    const id = _genId();
    projects.unshift({ id, name, folderName: _pendingHandle.name, instructions, agentId });
    try { await _saveHandle(id, _pendingHandle); } catch (_e) {}
  }

  _pendingHandle = null;
  _save(projects);
  _renderList();
  _closeModal();
}

// ── Delete project ────────────────────────────────────────────────────────

function _deleteProject(id) {
  const projects = _load().filter(p => p.id !== id);
  _save(projects);
  _removeHandle(id).catch(() => {});
  _renderList();
}

// ── Diff view (repurposed workspace modal) ────────────────────────────────

async function _openDiffView(project) {
  _currentProject = project;
  _diffHandle = null;
  _diffSnapshot = null;
  _diffChanges = [];

  const modal    = _el('project-workspace');
  const nameEl   = _el('workspace-name');
  const pathEl   = _el('workspace-path');
  const treeEl   = _el('workspace-file-tree');
  const panel    = _el('workspace-diff-panel');
  const banner   = _el('workspace-access-banner');
  const statusEl = _el('workspace-sel-status');

  if (nameEl)   nameEl.textContent = project.name;
  if (pathEl)   pathEl.textContent = project.folderName || '';
  if (treeEl)   treeEl.innerHTML = '';
  if (panel)    { panel.innerHTML = ''; panel.style.display = 'none'; }
  if (banner)   banner.style.display = 'none';
  if (statusEl) statusEl.textContent = 'Loading…';
  if (modal)    modal.classList.remove('hidden');
  _updateDiffBtns(false);

  let handle = null;
  try { handle = await _loadHandle(project.id); } catch (_e) {}
  if (!handle) { _showWorkspaceAccessBanner('no-handle', null); return; }

  let perm = 'prompt';
  try { perm = await handle.queryPermission({ mode: 'read' }); } catch (_e) {}
  if (perm !== 'granted') { _showWorkspaceAccessBanner('need-permission', handle); return; }

  _diffHandle = handle;
  _wsHandle = handle;
  await _loadAndShowDiff(project, handle);
}

function _closeDiffView() {
  const modal = _el('project-workspace');
  if (modal) modal.classList.add('hidden');
  _currentProject = null;
  _diffHandle = null;
  _wsHandle = null;
  _diffSnapshot = null;
  _diffChanges = [];
}

// Keep old name as alias so _launchProjectChat still compiles
const _closeWorkspace = _closeDiffView;

function _showWorkspaceAccessBanner(reason, handle) {
  const banner   = _el('workspace-access-banner');
  const msgEl    = _el('workspace-access-msg');
  const grantBtn = _el('workspace-grant-btn');
  const treeEl   = _el('workspace-file-tree');

  if (!banner) return;
  if (treeEl) treeEl.innerHTML = '';
  banner.style.display = '';

  if (reason === 'no-handle') {
    if (msgEl) msgEl.textContent = 'No folder linked. Edit this project (right-click \u2192 Settings) to re-pick a folder.';
    if (grantBtn) grantBtn.style.display = 'none';
  } else {
    const folderName = (handle && handle.name) || (_currentProject && _currentProject.folderName) || 'this folder';
    if (msgEl) msgEl.textContent = `Grant read access to "${folderName}" to view file changes.`;
    if (grantBtn) {
      grantBtn.style.display = '';
      grantBtn.textContent = 'Grant Access';
      grantBtn.onclick = async () => {
        if (!handle) return;
        let perm = 'denied';
        try { perm = await handle.requestPermission({ mode: 'read' }); } catch (_e) {}
        if (perm === 'granted') {
          banner.style.display = 'none';
          _diffHandle = handle;
          _wsHandle = handle;
          if (_currentProject) await _loadAndShowDiff(_currentProject, handle);
        } else {
          if (msgEl) msgEl.textContent = 'Access denied. You can try again or re-pick the folder.';
          grantBtn.textContent = 'Try Again';
        }
      };
    }
  }
}

function _updateDiffBtns(hasSnapshot) {
  const snapshotBtn = _el('workspace-snapshot-btn');
  const refreshBtn  = _el('workspace-refresh-btn');
  if (snapshotBtn) snapshotBtn.textContent = hasSnapshot ? 'Reset baseline' : 'Take snapshot';
  if (refreshBtn)  refreshBtn.style.display = hasSnapshot ? '' : 'none';
}

async function _loadAndShowDiff(project, handle) {
  const treeEl   = _el('workspace-file-tree');
  const panel    = _el('workspace-diff-panel');
  const statusEl = _el('workspace-sel-status');

  if (treeEl) treeEl.innerHTML = '<div style="opacity:0.45;padding:12px 8px;">Scanning files\u2026</div>';
  if (panel)  { panel.innerHTML = ''; panel.style.display = 'none'; }

  let snapshot = null;
  try { snapshot = await _loadSnapshot(project.id); } catch (_e) {}
  _diffSnapshot = snapshot;
  _updateDiffBtns(!!snapshot);

  if (!snapshot) {
    if (treeEl) treeEl.innerHTML =
      '<div style="opacity:0.5;padding:14px 12px;font-size:12px;line-height:1.6;">' +
      'No baseline snapshot yet.<br>' +
      'Click <strong>Take snapshot</strong> to record the current state. ' +
      'After your agent makes changes, return here to see the diff.</div>';
    if (statusEl) statusEl.textContent = 'No baseline \u2014 take a snapshot to start tracking changes';
    return;
  }

  let changes = [];
  try { changes = await _detectChanges(project.id, handle, snapshot); } catch (e) {
    if (treeEl) treeEl.innerHTML = '<div style="color:var(--red,#e06c75);padding:8px;font-size:12px;">Error: ' + _escape(String(e)) + '</div>';
    return;
  }

  _diffChanges = changes;
  _changeCounts[project.id] = changes.length;
  _renderList();

  if (panel) { panel.innerHTML = ''; panel.style.display = 'none'; }

  if (changes.length === 0) {
    if (treeEl) treeEl.innerHTML =
      '<div style="opacity:0.5;padding:14px 12px;font-size:12px;">' +
      'No changes since last snapshot.<br><span style="opacity:0.7;">Baseline: ' + _fmtTimeAgo(snapshot.timestamp) + '</span></div>';
    if (statusEl) statusEl.textContent = 'No changes \u00b7 Baseline: ' + _fmtTimeAgo(snapshot.timestamp);
    return;
  }

  if (treeEl) _renderDiffTree(treeEl, changes, handle, panel);
  if (statusEl) statusEl.textContent =
    changes.length + ' file' + (changes.length !== 1 ? 's' : '') + ' changed \u00b7 Baseline: ' + _fmtTimeAgo(snapshot.timestamp);
}

function _fmtTimeAgo(ts) {
  const d = Date.now() - ts;
  if (d < 60000)    return 'just now';
  if (d < 3600000)  return Math.floor(d / 60000) + 'm ago';
  if (d < 86400000) return Math.floor(d / 3600000) + 'h ago';
  return new Date(ts).toLocaleDateString();
}

function _renderDiffTree(container, changes, handle, panel) {
  container.innerHTML = '';
  const order = { modified: 0, added: 1, deleted: 2 };
  const sorted = [...changes].sort((a, b) => (order[a.type] - order[b.type]) || a.path.localeCompare(b.path));
  for (const change of sorted) {
    const col = change.type === 'added' ? '#98c379' : change.type === 'deleted' ? '#e06c75' : '#e5c07b';
    const lbl = change.type === 'added' ? 'A' : change.type === 'deleted' ? 'D' : 'M';
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:7px;padding:4px 10px;border-radius:4px;cursor:pointer;user-select:none;';
    row.innerHTML =
      `<span style="flex-shrink:0;width:16px;height:16px;border-radius:3px;display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;background:${col}1a;color:${col};">${lbl}</span>` +
      `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;">${_escape(change.path)}</span>` +
      `<span style="flex-shrink:0;font-size:10px;opacity:0.45;">${change.type}</span>`;
    row.addEventListener('mouseenter', () => { if (!row.classList.contains('diff-sel')) row.style.background = 'color-mix(in srgb,var(--fg) 6%,transparent)'; });
    row.addEventListener('mouseleave', () => { if (!row.classList.contains('diff-sel')) row.style.background = ''; });
    row.addEventListener('click', async () => {
      container.querySelectorAll('.diff-sel').forEach(r => { r.classList.remove('diff-sel'); r.style.background = ''; });
      row.classList.add('diff-sel');
      row.style.background = 'color-mix(in srgb,var(--red,#e06c75) 8%,transparent)';
      await _showFileDiff(change, handle, panel);
    });
    container.appendChild(row);
  }
}

async function _showFileDiff(change, handle, panel) {
  if (!panel) return;
  panel.style.display = '';
  panel.innerHTML = '<div style="opacity:0.45;padding:8px 10px;font-size:12px;">Loading\u2026</div>';
  const col = change.type === 'added' ? '#98c379' : change.type === 'deleted' ? '#e06c75' : '#e5c07b';
  let newContent = '';
  if (handle && change.type !== 'deleted') {
    try { newContent = await _readFileViaHandle(handle, change.path); } catch (_e) {}
  }
  let ops;
  if (change.type === 'deleted') ops = change.oldContent.split('\n').map(l => ({ type: '-', line: l }));
  else if (change.type === 'added') ops = newContent.split('\n').map(l => ({ type: '+', line: l }));
  else ops = _computeLineDiff(change.oldContent || '', newContent);

  panel.innerHTML =
    `<div style="font-size:11px;opacity:0.55;padding:5px 10px;border-bottom:1px solid var(--border);font-family:monospace;">${_escape(change.path)} <span style="color:${col};">[${change.type}]</span></div>` +
    `<div style="font-family:monospace;font-size:12px;white-space:pre;overflow-x:auto;max-height:340px;overflow-y:auto;">${_renderDiffHtml(ops)}</div>`;
}

// Called after each chat stream completes to check for agent-made file changes
async function _checkForChanges(sessionId) {
  try {
    const sessions = (window.sessionModule && window.sessionModule.getSessions) ? window.sessionModule.getSessions() : [];
    const sess = sessions.find(s => s.id === sessionId);
    if (!sess || !sess.folder) return;
    const project = getProjectForFolder(sess.folder);
    if (!project) return;
    const handle = await _loadHandle(project.id);
    if (!handle) return;
    let perm = 'denied';
    try { perm = await handle.queryPermission({ mode: 'read' }); } catch { return; }
    if (perm !== 'granted') return;
    const snapshot = await _loadSnapshot(project.id);
    if (!snapshot) return;
    const changes = await _detectChanges(project.id, handle, snapshot);
    const prevCount = _changeCounts[project.id] || 0;
    _changeCounts[project.id] = changes.length;
    if (changes.length !== prevCount) _renderList();
    if (changes.length > 0 && changes.length > prevCount) {
      const modal = _el('project-workspace');
      const isOpen = modal && !modal.classList.contains('hidden');
      if (!isOpen) {
        _openDiffView(project);
      } else if (_currentProject && _currentProject.id === project.id) {
        await _loadAndShowDiff(project, _diffHandle || handle);
      }
    }
  } catch (_e) {}
}

async function _loadTreeFromHandle(container, dirHandle, baseRel) {
  try {
    const items = await _readDirHandle(dirHandle, baseRel, 1, 2);
    container.innerHTML = '';
    if (items.length === 0) {
      container.innerHTML = '<div style="opacity:0.4;padding:8px;font-size:12px;">Empty folder</div>';
      return;
    }
    _renderTreeItems(container, items, 0);
  } catch (err) {
    container.innerHTML =
      '<div style="color:var(--red,#e06c75);padding:8px;font-size:12px;">Error: ' +
      _escape(err.message || String(err)) + '</div>';
  }
}

async function _readDirHandle(dirHandle, baseRel, depth, maxDepth) {
  const entries = [];
  for await (const [name, entry] of dirHandle) {
    entries.push([name, entry]);
  }
  entries.sort(([a, ae], [b, be]) => {
    if (ae.kind !== be.kind) return ae.kind === 'directory' ? -1 : 1;
    return a.localeCompare(b, undefined, { sensitivity: 'base' });
  });
  const items = [];
  let count = 0;
  for (const [name, entry] of entries) {
    if (count++ >= 100) { items.push({ name: '…', type: 'truncated', rel: '' }); break; }
    if (name.startsWith('.') && !_ALLOWED_DOTS.has(name)) continue;
    const rel = baseRel ? baseRel + '/' + name : name;
    if (entry.kind === 'directory') {
      if (_IGNORED_DIRS.has(name)) continue;
      const item = { name, type: 'dir', rel, _handle: entry };
      if (depth < maxDepth) {
        item.children = await _readDirHandle(entry, rel, depth + 1, maxDepth);
      } else {
        item.collapsed = true;
      }
      items.push(item);
    } else {
      let size = 0;
      try { size = (await entry.getFile()).size; } catch (_e) {}
      items.push({ name, type: 'file', rel, size, readable: _isReadable(name), _handle: entry });
    }
  }
  return items;
}

async function _getSubHandle(rootHandle, relPath) {
  let current = rootHandle;
  for (const part of relPath.split('/').filter(Boolean)) {
    current = await current.getDirectoryHandle(part);
  }
  return current;
}

async function _readFileViaHandle(rootHandle, relPath) {
  const MAX_BYTES = 150_000;
  let current = rootHandle;
  const parts = relPath.split('/').filter(Boolean);
  for (let i = 0; i < parts.length - 1; i++) {
    current = await current.getDirectoryHandle(parts[i]);
  }
  const fileHandle = await current.getFileHandle(parts[parts.length - 1]);
  const file = await fileHandle.getFile();
  if (file.size > MAX_BYTES) throw new Error(`File too large (${file.size.toLocaleString()} B)`);
  return await file.text();
}


function _fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function _folderIconSvg() {
  return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.65;"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
}

function _fileIconSvg() {
  return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.35;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
}

function _renderTreeItems(container, items, indent) {
  const pad = 12 + indent * 16;

  for (const item of items) {
    if (item.type === 'truncated') {
      const el = document.createElement('div');
      el.style.cssText = `padding:3px 8px 3px ${pad}px;opacity:0.38;font-size:11px;`;
      el.textContent = '… more files';
      container.appendChild(el);
      continue;
    }

    if (item.type === 'dir') {
      const wrap = document.createElement('div');

      const row = document.createElement('div');
      row.style.cssText = `display:flex;align-items:center;gap:6px;padding:3px 8px 3px ${pad}px;cursor:pointer;border-radius:4px;user-select:none;`;

      const chevron = document.createElement('span');
      chevron.style.cssText = 'flex-shrink:0;opacity:0.35;display:flex;align-items:center;transition:transform 0.12s;';
      chevron.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';

      row.appendChild(chevron);
      row.insertAdjacentHTML('beforeend', _folderIconSvg());

      const nameEl = document.createElement('span');
      nameEl.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      nameEl.textContent = item.name + '/';
      row.appendChild(nameEl);

      const children = document.createElement('div');
      children.style.display = 'none';

      if (item.children && item.children.length > 0) {
        _renderTreeItems(children, item.children, indent + 1);
      }

      let lazyLoaded = false;
      row.addEventListener('click', () => {
        const isOpen = children.style.display !== 'none';
        if (isOpen) {
          children.style.display = 'none';
          chevron.style.transform = '';
        } else {
          children.style.display = '';
          chevron.style.transform = 'rotate(90deg)';
          if (!lazyLoaded && (item.collapsed || !item.children || item.children.length === 0)) {
            lazyLoaded = true;
            children.innerHTML = '<div style="opacity:0.4;padding:4px 8px;font-size:12px;">Loading…</div>';
            if (item._handle) {
              _loadTreeFromHandle(children, item._handle, item.rel);
            } else if (_wsHandle) {
              _getSubHandle(_wsHandle, item.rel)
                .then(h => _loadTreeFromHandle(children, h, item.rel))
                .catch(e => {
                  children.innerHTML = '<div style="color:var(--red,#e06c75);font-size:12px;padding:4px 8px;">' +
                    _escape(String(e)) + '</div>';
                });
            }
          }
        }
      });
      row.addEventListener('mouseenter', () => { row.style.background = 'color-mix(in srgb,var(--fg) 6%,transparent)'; });
      row.addEventListener('mouseleave', () => { row.style.background = ''; });

      wrap.appendChild(row);
      wrap.appendChild(children);
      container.appendChild(wrap);

    } else if (item.type === 'file') {
      const row = document.createElement('div');
      row.style.cssText = `display:flex;align-items:center;gap:6px;padding:3px 8px 3px ${pad}px;border-radius:4px;`;

      if (item.readable) {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.style.cssText = 'flex-shrink:0;cursor:pointer;accent-color:var(--red,#e06c75);';
        cb.title = 'Select for chat';
        cb.checked = _wsSelected.has(item.rel);
        cb.addEventListener('change', () => {
          if (cb.checked) {
            _wsSelected.add(item.rel);
            row.style.background = 'color-mix(in srgb,var(--red,#e06c75) 8%,transparent)';
          } else {
            _wsSelected.delete(item.rel);
            row.style.background = '';
          }
          _updateWorkspaceBtns();
        });
        row.addEventListener('click', e => {
          if (e.target === cb) return;
          cb.checked = !cb.checked;
          cb.dispatchEvent(new Event('change'));
        });
        row.style.cursor = 'pointer';
        row.addEventListener('mouseenter', () => {
          if (!_wsSelected.has(item.rel)) row.style.background = 'color-mix(in srgb,var(--fg) 6%,transparent)';
        });
        row.addEventListener('mouseleave', () => {
          if (!_wsSelected.has(item.rel)) row.style.background = '';
        });
        row.appendChild(cb);
      } else {
        const spacer = document.createElement('span');
        spacer.style.cssText = 'width:16px;flex-shrink:0;';
        row.appendChild(spacer);
        row.style.opacity = '0.45';
      }

      row.insertAdjacentHTML('beforeend', _fileIconSvg());

      const nameEl = document.createElement('span');
      nameEl.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      nameEl.textContent = item.name;
      row.appendChild(nameEl);

      const sizeEl = document.createElement('span');
      sizeEl.style.cssText = 'font-size:10px;opacity:0.38;flex-shrink:0;';
      sizeEl.textContent = _fmtSize(item.size);
      row.appendChild(sizeEl);

      container.appendChild(row);
    }
  }
}

async function _launchProjectChat(project, relPaths) {
  const handle = _wsHandle;
  _closeWorkspace();

  // Link the new session to this project's folder
  if (window.sessionModule && window.sessionModule.setPendingFolder) {
    window.sessionModule.setPendingFolder(project.name);
  }

  const newChatBtn = _el('sidebar-new-chat-btn');
  if (newChatBtn) newChatBtn.click();

  // If files were selected, read and inject them into the message
  if (handle && relPaths && relPaths.length > 0) {
    await new Promise(r => setTimeout(r, 200));
    const input = document.getElementById('message');
    if (!input) return;

    const limit = 5;
    const toRead = relPaths.slice(0, limit);
    const blocks = [];

    for (const rel of toRead) {
      try {
        const content = await _readFileViaHandle(handle, rel);
        const dotIdx = rel.lastIndexOf('.');
        const ext = dotIdx >= 0 ? rel.slice(dotIdx + 1) : '';
        blocks.push('**' + rel + '**\n```' + ext + '\n' + content + '\n```');
      } catch (_err) { /* skip unreadable files */ }
    }

    if (relPaths.length > limit) {
      const extra = relPaths.length - limit;
      blocks.push(`*(${extra} more file${extra !== 1 ? 's' : ''} not shown)*`);
    }

    if (blocks.length > 0) {
      input.value = blocks.join('\n\n');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }
}

// ── Init ──────────────────────────────────────────────────────────────────

export function initProjects() {
  _renderList();

  // "+" button in section header
  const addBtn = _el('projects-add-btn');
  if (addBtn) addBtn.addEventListener('click', () => _openModal(null));

  // Modal close
  const closeBtn = _el('close-project-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closeModal);

  const cancelBtn = _el('project-modal-cancel-btn');
  if (cancelBtn) cancelBtn.addEventListener('click', _closeModal);

  // Close on backdrop click
  const modal = _el('project-modal');
  if (modal) {
    modal.addEventListener('click', e => {
      if (e.target === modal) _closeModal();
    });
  }

  // Folder picker button
  const pickBtn = _el('project-pick-folder-btn');
  if (pickBtn) {
    pickBtn.addEventListener('click', async () => {
      if (!window.showDirectoryPicker) {
        alert('Your browser does not support folder picking.\nPlease use Chrome, Edge, or another Chromium-based browser.');
        return;
      }
      try {
        const handle = await window.showDirectoryPicker({ mode: 'read' });
        _pendingHandle = handle;
        const label = _el('project-pick-folder-label');
        if (label) { label.textContent = handle.name; label.style.opacity = '1'; }
        // Auto-fill name if empty
        const nameInput = _el('project-name-input');
        if (nameInput && !nameInput.value.trim()) nameInput.value = handle.name;
      } catch (err) {
        if (err.name !== 'AbortError') {
          const errorEl = _el('project-modal-error');
          if (errorEl) { errorEl.textContent = 'Could not open folder picker: ' + err.message; errorEl.style.display = 'block'; }
        }
      }
    });
  }

  // Save
  const saveBtn = _el('project-modal-save-btn');
  if (saveBtn) saveBtn.addEventListener('click', _saveProject);

  // Save on Enter in name input
  const nameInput = _el('project-name-input');
  if (nameInput) {
    nameInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') _saveProject();
    });
  }

  // Context menu: settings
  const ctxSettings = _el('project-ctx-settings');
  if (ctxSettings) {
    ctxSettings.addEventListener('click', () => {
      if (!_ctxTargetId) return;
      const project = _load().find(p => p.id === _ctxTargetId);
      if (project) _openModal(project);
      _closeContextMenu();
    });
  }

  // Context menu: rename
  const ctxRename = _el('project-ctx-rename');
  if (ctxRename) {
    ctxRename.addEventListener('click', () => {
      if (!_ctxTargetId) return;
      const project = _load().find(p => p.id === _ctxTargetId);
      if (project) _openModal(project);
      _closeContextMenu();
    });
  }

  // Context menu: delete
  const ctxDelete = _el('project-ctx-delete');
  if (ctxDelete) {
    ctxDelete.addEventListener('click', () => {
      if (!_ctxTargetId) return;
      const id = _ctxTargetId;
      _closeContextMenu();
      _deleteProject(id);
    });
  }

  // Context menu: view changes
  const ctxChanges = _el('project-ctx-changes');
  if (ctxChanges) {
    ctxChanges.addEventListener('click', () => {
      if (!_ctxTargetId) return;
      const project = _load().find(p => p.id === _ctxTargetId);
      if (project) _openDiffView(project);
      _closeContextMenu();
    });
  }

  // Workspace (diff view) close
  const closeWs = _el('close-project-workspace');
  if (closeWs) closeWs.addEventListener('click', _closeDiffView);

  const wsModal = _el('project-workspace');
  if (wsModal) {
    wsModal.addEventListener('click', e => {
      if (e.target === wsModal) _closeDiffView();
    });
  }

  // Take/reset snapshot button
  const snapshotBtn = _el('workspace-snapshot-btn');
  if (snapshotBtn) {
    snapshotBtn.addEventListener('click', async () => {
      if (!_currentProject || !_diffHandle) return;
      snapshotBtn.disabled = true;
      snapshotBtn.textContent = 'Taking snapshot…';
      try {
        await _takeSnapshotForProject(_currentProject.id, _diffHandle);
        _diffSnapshot = await _loadSnapshot(_currentProject.id);
        _diffChanges = [];
        _changeCounts[_currentProject.id] = 0;
        _renderList();
        await _loadAndShowDiff(_currentProject, _diffHandle);
      } catch (e) {
        alert('Snapshot failed: ' + String(e));
      } finally {
        snapshotBtn.disabled = false;
      }
    });
  }

  // Refresh diff button
  const refreshBtn = _el('workspace-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      if (!_currentProject || !_diffHandle) return;
      refreshBtn.disabled = true;
      try {
        await _loadAndShowDiff(_currentProject, _diffHandle);
      } finally {
        refreshBtn.disabled = false;
      }
    });
  }

  // Close context menu / modals on Escape
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      _closeContextMenu();
      _closeModal();
      _closeDiffView();
    }
  });
}

/** Open the project modal for an existing project by id (for the badge link). */
function _openSettings(projectId) {
  const project = _load().find(p => p.id === projectId);
  if (project) _openModal(project);
}

export default { initProjects, renderList: _renderList, getProjectForFolder, openSettings: _openSettings, checkForChanges: _checkForChanges };

/** Return the project whose name matches folderName, or null. */
function getProjectForFolder(folderName) {
  if (!folderName) return null;
  return _load().find(p => p.name === folderName) || null;
}
