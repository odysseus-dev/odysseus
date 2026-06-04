// shell.js — Terminal workspace with file explorer
// Each tab gets a real PTY session via WebSocket + xterm.js.
// Tab state (name, cwd) persists in localStorage; the terminal session itself
// is a new PTY connection on each page load.

const _STORAGE_KEY = 'odysseus-shell-tabs';
const _EXPLORER_WIDTH_KEY = 'odysseus-shell-explorer-width';

let _tabs = [];          // [{id, name, cwd}]
let _activeId = null;
let _explorerWidth = parseInt(localStorage.getItem(_EXPLORER_WIDTH_KEY) || '280', 10);
let _pickerPath = '';
let _pickerSelected = '';
let _pickerCallback = null;
let _treeCache = {};
let _activeExplorerTab = 'explorer';  // 'explorer' | 'scm'
let _scmRefreshTimer = null;          // debounce handle for auto-refresh

// PTY sessions: tabId → {term, ws, fitAddon, container, resizeObs}
const _pty = new Map();

// ── Helpers ───────────────────────────────────────────────────────────────────

function _uid() { return 'sh-' + Math.random().toString(36).slice(2, 9); }
function _el(id) { return document.getElementById(id); }

function _escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Persistence ───────────────────────────────────────────────────────────────

function _save() {
  const data = _tabs.map(t => ({
    id: t.id, name: t.name, cwd: t.cwd,
    termTabs: t.termTabs.map(tt => ({ id: tt.id, label: tt.label })),
    activeTermTabId: t.activeTermTabId,
  }));
  try { localStorage.setItem(_STORAGE_KEY, JSON.stringify(data)); } catch {}
}

function _loadTabs() {
  try {
    const raw = localStorage.getItem(_STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw).map(d => {
      // Migrate old format (no termTabs) to new
      const termTabs = (d.termTabs && d.termTabs.length)
        ? d.termTabs.map(tt => ({ id: tt.id || _uid(), label: tt.label || '1' }))
        : [{ id: _uid(), label: '1' }];
      return {
        id: d.id || _uid(), name: d.name || 'shell', cwd: d.cwd || '',
        termTabs,
        activeTermTabId: d.activeTermTabId || termTabs[0].id,
      };
    });
  } catch { return []; }
}

// ── PTY sessions ──────────────────────────────────────────────────────────────

function _ptyTheme() {
  const s = getComputedStyle(document.documentElement);
  const v = n => s.getPropertyValue(n).trim();
  return {
    background:  v('--bg')      || '#1a1a1a',
    foreground:  v('--fg')      || '#abb2bf',
    cursor:      v('--accent')  || '#528bff',
    selectionBackground: 'rgba(128,128,255,0.25)',
    black:'#4e4e4e', red:'#e06c75', green:'#98c379', yellow:'#e5c07b',
    blue:'#61afef', magenta:'#c678dd', cyan:'#56b6c2', white:'#abb2bf',
    brightBlack:'#5c6370', brightRed:'#ff7b72', brightGreen:'#7ee787',
    brightYellow:'#f0b429', brightBlue:'#79c0ff', brightMagenta:'#d2a8ff',
    brightCyan:'#39c5cf', brightWhite:'#ffffff',
  };
}

function _getWorkspaceForTermTab(termTabId) {
  return _tabs.find(ws => ws.termTabs && ws.termTabs.some(tt => tt.id === termTabId));
}

function _openPtyForTab(tabId) {
  if (_pty.has(tabId)) return;
  const body = _el('shell-terminal-body');
  if (!body) return;

  // Reuse existing container or create a new one
  let container = body.querySelector(`[data-tabId="${tabId}"]`);
  if (!container) {
    container = document.createElement('div');
    container.className = 'shell-xterm-container';
    container.dataset.tabId = tabId;
    body.appendChild(container);
  }

  const XTerm = window.Terminal;
  // xterm@5 addon-fit exports as window.FitAddon (the class directly)
  const FitAddon = window.FitAddon?.FitAddon ?? window.FitAddon;
  if (!XTerm || !FitAddon) {
    container.innerHTML = '<div style="padding:20px;color:#e06c75;font-family:monospace;font-size:12px">xterm.js failed to load.<br>Check browser console (F12) for errors.</div>';
    return;
  }

  const term = new XTerm({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: '"Fira Code", "Cascadia Code", "Consolas", monospace',
    theme: _ptyTheme(),
    scrollback: 5000,
    convertEol: false,
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(container);
  fit.fit();

  // Look up the workspace that owns this terminal tab to get the cwd
  const workspace = _getWorkspaceForTermTab(tabId);
  const cwd = workspace?.cwd || '';

  // WebSocket connection to the backend PTY
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(
    `${proto}//${location.host}/api/shell/pty-ws?cwd=${encodeURIComponent(cwd)}&cols=${term.cols}&rows=${term.rows}`
  );
  ws.onmessage = e => term.write(e.data);
  ws.onclose   = () => term.write('\r\n\x1b[2m[session ended]\x1b[0m\r\n');
  ws.onerror   = () => term.write('\r\n\x1b[31m[connection error]\x1b[0m\r\n');

  // Keyboard input → PTY
  term.onData(data => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }));
      if (data === '\r' || data === '\n') {
        const ownerWs = _getWorkspaceForTermTab(tabId);
        if (ownerWs?.cwd) _scheduleScmRefresh(ownerWs.cwd);
      }
    }
  });

  // Resize → PTY
  const resizeObs = new ResizeObserver(() => {
    try { fit.fit(); } catch {}
    if (ws.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
  });
  resizeObs.observe(container);

  _pty.set(tabId, { term, ws, fit, container, resizeObs });
}

function _closePtyForTab(tabId) {
  const p = _pty.get(tabId);
  if (!p) return;
  try { p.resizeObs.disconnect(); } catch {}
  try { p.ws.close(); } catch {}
  try { p.term.dispose(); } catch {}
  try { p.container.remove(); } catch {}
  _pty.delete(tabId);
}

function _showPtyForTab(tabId) {
  _pty.forEach((p, id) => {
    p.container.style.display = id === tabId ? 'flex' : 'none';
  });
  const p = _pty.get(tabId);
  if (p) setTimeout(() => { try { p.fit.fit(); p.term.focus(); } catch {} }, 60);
}

// ── Workspace tab management ──────────────────────────────────────────────────

function _getTab(id) { return _tabs.find(t => t.id === (id ?? _activeId)); }

function _addTab(cwd, name) {
  const shortName = name || cwd.split(/[\\/]/).filter(Boolean).pop() || 'shell';
  const firstTermTab = { id: _uid(), label: '1' };
  const tab = { id: _uid(), name: shortName, cwd, termTabs: [firstTermTab], activeTermTabId: firstTermTab.id };
  _tabs.push(tab);
  _save();
  return tab;
}

function _removeTab(id) {
  const ws = _getTab(id);
  if (ws) ws.termTabs.forEach(tt => _closePtyForTab(tt.id));
  _tabs = _tabs.filter(t => t.id !== id);
  if (_activeId === id) _activeId = _tabs.length ? _tabs[_tabs.length - 1].id : null;
  _save();
  _renderSidebarList();
  if (_activeId) _switchTab(_activeId); else _renderEmpty();
}

function _switchTab(id) {
  _activeId = id;
  _renderSidebarList();
  const ws = _getTab();
  if (!ws) return;
  const cwdBar = _el('shell-cwd-bar');
  if (cwdBar) cwdBar.textContent = ws.cwd || '~';
  _renderTermTabs(id);
  const activeTermId = ws.activeTermTabId;
  if (!_pty.has(activeTermId)) _openPtyForTab(activeTermId);
  _showPtyForTab(activeTermId);
  if (ws.cwd) {
    _loadTree(ws.cwd);
    if (_activeExplorerTab === 'scm') _loadScm(ws.cwd);
    _loadGitBar(ws.cwd);
  }
}

// ── Terminal sub-tab management ───────────────────────────────────────────────

function _renderTermTabs(workspaceId) {
  const bar = _el('shell-term-tabbar');
  if (!bar) return;
  const ws = _getTab(workspaceId);
  if (!ws) return;

  bar.innerHTML = '';
  ws.termTabs.forEach((tt, i) => {
    const tab = document.createElement('div');
    tab.className = 'shell-term-tab' + (tt.id === ws.activeTermTabId ? ' shell-term-tab-active' : '');
    tab.title = `Terminal ${i + 1}`;

    const label = document.createElement('span');
    label.className = 'shell-term-tab-label';
    label.textContent = tt.label || String(i + 1);
    tab.appendChild(label);

    // Rename on double-click
    label.addEventListener('dblclick', e => {
      e.stopPropagation();
      const inp = document.createElement('input');
      inp.type = 'text'; inp.value = tt.label; inp.className = 'shell-term-tab-input';
      label.replaceWith(inp); inp.focus(); inp.select();
      const commit = () => {
        tt.label = inp.value.trim() || tt.label;
        _save(); _renderTermTabs(workspaceId);
      };
      inp.addEventListener('blur', commit);
      inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') commit(); if (ev.key === 'Escape') _renderTermTabs(workspaceId); });
    });

    // Close button — only show when more than one terminal
    if (ws.termTabs.length > 1) {
      const closeBtn = document.createElement('button');
      closeBtn.className = 'shell-term-tab-close';
      closeBtn.title = 'Close terminal';
      closeBtn.innerHTML = '×';
      closeBtn.addEventListener('click', e => { e.stopPropagation(); _closeTermTab(workspaceId, tt.id); });
      tab.appendChild(closeBtn);
    }

    tab.addEventListener('click', e => {
      if (e.target.closest('.shell-term-tab-close')) return;
      _switchTermTab(workspaceId, tt.id);
    });
    bar.appendChild(tab);
  });

  // New terminal button
  const addBtn = document.createElement('button');
  addBtn.className = 'shell-term-tab-add';
  addBtn.title = 'New terminal (same directory)';
  addBtn.textContent = '+';
  addBtn.addEventListener('click', () => _addTermTab(workspaceId));
  bar.appendChild(addBtn);
}

function _addTermTab(workspaceId) {
  const ws = _getTab(workspaceId);
  if (!ws) return;
  const label = String(ws.termTabs.length + 1);
  const newTt = { id: _uid(), label };
  ws.termTabs.push(newTt);
  ws.activeTermTabId = newTt.id;
  _save();
  _renderTermTabs(workspaceId);
  _openPtyForTab(newTt.id);
  _showPtyForTab(newTt.id);
}

function _switchTermTab(workspaceId, termTabId) {
  const ws = _getTab(workspaceId);
  if (!ws) return;
  ws.activeTermTabId = termTabId;
  _save();
  _renderTermTabs(workspaceId);
  if (!_pty.has(termTabId)) _openPtyForTab(termTabId);
  _showPtyForTab(termTabId);
}

function _closeTermTab(workspaceId, termTabId) {
  const ws = _getTab(workspaceId);
  if (!ws || ws.termTabs.length <= 1) return;
  _closePtyForTab(termTabId);
  ws.termTabs = ws.termTabs.filter(tt => tt.id !== termTabId);
  if (ws.activeTermTabId === termTabId) {
    ws.activeTermTabId = ws.termTabs[ws.termTabs.length - 1].id;
  }
  _save();
  _renderTermTabs(workspaceId);
  _switchTermTab(workspaceId, ws.activeTermTabId);
}

// ── Render helpers ────────────────────────────────────────────────────────────

function _renderSidebarList() {
  const list = _el('shell-tab-list');
  if (!list) return;
  list.innerHTML = '';
  for (const t of _tabs) {
    const div = document.createElement('div');
    div.className = 'list-item shell-sidebar-tab' + (t.id === _activeId ? ' active-session' : '');
    div.dataset.tabId = t.id;
    div.title = t.cwd || t.name;
    div.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5">
        <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
      </svg>
      <span class="grow shell-tab-name-label">${_escHtml(t.name)}</span>
      <button class="shell-tab-close-btn" data-tab-id="${t.id}" title="Close">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
             stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>`;
    div.addEventListener('click', e => {
      if (e.target.closest('.shell-tab-close-btn')) return;
      openWorkspace();
      _switchTab(t.id);
    });
    div.querySelector('.shell-tab-close-btn').addEventListener('click', e => {
      e.stopPropagation();
      _removeTab(t.id);
    });
    div.querySelector('.shell-tab-name-label').addEventListener('dblclick', e => {
      e.stopPropagation();
      const inp = document.createElement('input');
      inp.type = 'text'; inp.value = t.name; inp.className = 'shell-tab-rename-input';
      e.target.replaceWith(inp); inp.focus(); inp.select();
      const commit = () => { t.name = inp.value.trim() || t.name; _save(); _renderSidebarList(); };
      inp.addEventListener('blur', commit);
      inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') commit(); });
    });
    list.appendChild(div);
  }
}

function _renderEmpty() {
  const body = _el('shell-terminal-body');
  if (body) body.innerHTML = '<div class="shell-empty">No terminal open — click + to create one.</div>';
  const cwdBar = _el('shell-cwd-bar');
  if (cwdBar) cwdBar.textContent = '';
}

// ── File tree ─────────────────────────────────────────────────────────────────

async function _loadTree(path) {
  const tree = _el('shell-tree');
  if (!tree) return;

  if (_treeCache[path]) { _renderTree(path, _treeCache[path]); return; }

  tree.innerHTML = '<div class="shell-tree-loading">Loading...</div>';
  try {
    const r = await fetch(`/api/shell/browse?path=${encodeURIComponent(path)}`);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    _treeCache[path] = data;
    _renderTree(path, data);
  } catch (e) {
    tree.innerHTML = `<div class="shell-tree-err">${_escHtml(String(e))}</div>`;
  }
}

function _renderTree(rootPath, data) {
  const tree = _el('shell-tree');
  if (!tree) return;
  tree.innerHTML = '';

  // Breadcrumb navigation bar
  const crumb = document.createElement('div');
  crumb.className = 'shell-tree-crumb';
  const parts = rootPath.replace(/\\/g, '/').split('/').filter(Boolean);
  parts.forEach((part, i) => {
    const seg = document.createElement('span');
    seg.className = 'shell-tree-crumb-seg';
    seg.textContent = part;
    seg.title = 'Navigate here';
    seg.addEventListener('click', () => {
      // Reconstruct Windows path up to this segment
      const winPath = parts.slice(0, i + 1).join('\\').replace(/^([a-zA-Z])$/, '$1:\\') || rootPath;
      // Handle drive letter
      const rebuilt = /^[a-zA-Z]$/.test(parts[0]) && i === 0 ? parts[0] + ':\\' : parts.slice(0, i + 1).join('\\');
      delete _treeCache[rootPath];
      _loadTree(rebuilt);
    });
    crumb.appendChild(seg);
    if (i < parts.length - 1) { const sep = document.createElement('span'); sep.className = 'shell-crumb-sep'; sep.textContent = '/'; crumb.appendChild(sep); }
  });
  tree.appendChild(crumb);

  // Dirs
  for (const dir of data.dirs) {
    const row = document.createElement('div');
    row.className = 'shell-tree-dir';
    row.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span>${_escHtml(dir.name)}</span>`;
    row.title = dir.path;
    row.addEventListener('click', () => { delete _treeCache[dir.path]; _loadTree(dir.path); });
    tree.appendChild(row);
  }

  // Files
  for (const file of data.files) {
    const row = document.createElement('div');
    row.className = 'shell-tree-file';
    row.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg><span>${_escHtml(file.name)}</span>`;
    row.title = file.path;
    row.addEventListener('click', () => _openFile(file.path, file.name, file.ext));
    tree.appendChild(row);
  }
}

async function _openFile(path, name, ext) {
  const viewer = _el('shell-viewer');
  const nameEl = _el('shell-viewer-name');
  const codeEl = _el('shell-viewer-code');
  if (!viewer || !nameEl || !codeEl) return;

  nameEl.textContent = name;
  codeEl.textContent = 'Loading…';
  codeEl.removeAttribute('class');
  viewer.style.display = 'flex';

  try {
    const r = await fetch(`/api/shell/readfile?path=${encodeURIComponent(path)}`);
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }
    const data = await r.json();
    codeEl.textContent = data.content;
    // Map ext to hljs language
    const langMap = { '.py':'python','.js':'javascript','.ts':'typescript','.jsx':'javascript',
      '.tsx':'typescript','.html':'html','.css':'css','.json':'json','.yaml':'yaml','.yml':'yaml',
      '.md':'markdown','.sql':'sql','.sh':'bash','.bash':'bash','.rs':'rust','.go':'go',
      '.java':'java','.c':'c','.cpp':'cpp','.rb':'ruby','.php':'php','.toml':'toml' };
    const lang = langMap[ext] || 'plaintext';
    if (lang !== 'plaintext') codeEl.className = `language-${lang}`;
    if (window.hljs) { try { window.hljs.highlightElement(codeEl); } catch {} }
  } catch (e) {
    codeEl.textContent = String(e);
  }
}

// ── Folder picker modal ───────────────────────────────────────────────────────

// ── Explorer tab switching ────────────────────────────────────────────────────

function _switchExplorerTab(tab) {
  _activeExplorerTab = tab;
  ['explorer', 'scm'].forEach(t => {
    const btn   = _el(`shell-etab-${t}`);
    const panel = _el(`shell-panel-${t}`);
    if (btn)   btn.classList.toggle('shell-etab-active', t === tab);
    if (panel) panel.style.display = t === tab ? 'flex' : 'none';
  });
  if (tab === 'scm') {
    const active = _getTab();
    if (active?.cwd) _loadScm(active.cwd);
  }
}

// ── Source Control ────────────────────────────────────────────────────────────

async function _loadScm(cwd) {
  const list = _el('shell-scm-list');
  if (!list) return;
  list.innerHTML = '<div class="scm-loading">Loading…</div>';
  try {
    const r = await fetch(`/api/shell/gitstatus?path=${encodeURIComponent(cwd)}`);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    _renderScm(data);
    // Badge
    const badge = _el('shell-scm-badge');
    if (badge) {
      const n = data.files?.length || 0;
      badge.textContent = n;
      badge.style.display = n ? 'inline' : 'none';
    }
  } catch (e) {
    if (list) list.innerHTML = `<div class="scm-error">${_escHtml(String(e))}</div>`;
  }
}

function _renderScm(data) {
  const list = _el('shell-scm-list');
  if (!list) return;

  if (!data.is_git) {
    list.innerHTML = '<div class="scm-note">Not a git repository</div>';
    return;
  }
  if (!data.files?.length) {
    list.innerHTML = '<div class="scm-note">Working tree clean — no changes</div>';
    return;
  }

  const staged    = data.files.filter(f => f.xy[0] !== ' ' && f.xy !== '??');
  const unstaged  = data.files.filter(f => f.xy[1] !== ' ' && f.xy !== '??');
  const untracked = data.files.filter(f => f.xy === '??');

  let html = '';
  if (staged.length)    html += _scmSection('STAGED CHANGES',  staged,    'staged',    data.cwd);
  if (unstaged.length)  html += _scmSection('CHANGES',          unstaged,  'unstaged',  data.cwd);
  if (untracked.length) html += _scmSection('UNTRACKED',         untracked, 'untracked', data.cwd);
  list.innerHTML = html;

  // Wire all item clicks and action buttons via delegation
  list.querySelectorAll('.scm-item').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.scm-actions')) return;
      const { file, cwd, staged } = row.dataset;
      _loadScmDiff(cwd, file, staged === 'true');
    });
  });

  list.querySelectorAll('[data-git-action]').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const action = btn.dataset.gitAction;
      const file   = btn.dataset.file;
      const cwd    = btn.dataset.cwd;
      if (action === 'revert') {
        if (!btn.dataset.confirming) {
          btn.dataset.confirming = '1';
          const orig = btn.textContent;
          btn.textContent = '?';
          btn.title = 'Click again to confirm';
          setTimeout(() => { delete btn.dataset.confirming; btn.textContent = orig; btn.title = 'Discard changes'; }, 3000);
          return;
        }
      }
      await _gitAction(action, cwd, file);
      _loadScm(cwd);
    });
  });

  // Section-level buttons
  list.querySelectorAll('[data-git-section]').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const { gitSection: action, cwd } = btn.dataset;
      await _gitAction(action, cwd, '');
      _loadScm(cwd);
    });
  });
}

const _SCM_LABELS = { M:'M', A:'A', D:'D', R:'R', C:'C', '?':'?' };
const _SCM_CLS    = { M:'scm-m', A:'scm-a', D:'scm-d', R:'scm-r', '?':'scm-u' };

function _scmSection(title, files, kind, cwd) {
  const isStaged    = kind === 'staged';
  const isUntracked = kind === 'untracked';

  const sectionActions = isStaged
    ? `<button class="scm-sec-btn" data-git-section="unstage-all" data-cwd="${_escHtml(cwd)}" title="Unstage all">↩ all</button>`
    : isUntracked ? ''
    : `<button class="scm-sec-btn" data-git-section="stage-all" data-cwd="${_escHtml(cwd)}" title="Stage all">+ all</button>
       <button class="scm-sec-btn scm-sec-danger" data-git-section="revert-all" data-cwd="${_escHtml(cwd)}" title="Discard all">↩ all</button>`;

  const rows = files.map(f => {
    const statusChar = isStaged ? f.xy[0] : isUntracked ? '?' : f.xy[1];
    const cls        = _SCM_CLS[statusChar] || 'scm-u';
    const short      = f.file.split('/').pop();
    const dir        = f.file.includes('/') ? f.file.split('/').slice(0, -1).join('/') : '';
    const efile      = _escHtml(f.file);
    const ecwd       = _escHtml(cwd);

    let actions = '';
    if (isStaged) {
      actions = `<button class="scm-btn" data-git-action="unstage" data-file="${efile}" data-cwd="${ecwd}" title="Unstage">↓</button>`;
    } else if (isUntracked) {
      actions = `<button class="scm-btn scm-btn-add" data-git-action="stage" data-file="${efile}" data-cwd="${ecwd}" title="Stage file">+</button>`;
    } else {
      actions = `<button class="scm-btn scm-btn-add" data-git-action="stage"  data-file="${efile}" data-cwd="${ecwd}" title="Stage">+</button>
                 <button class="scm-btn scm-btn-danger" data-git-action="revert" data-file="${efile}" data-cwd="${ecwd}" title="Discard changes">↩</button>`;
    }

    return `<div class="scm-item" data-file="${efile}" data-cwd="${ecwd}" data-staged="${isStaged}" title="${efile}">
      <span class="scm-status ${cls}">${statusChar}</span>
      <span class="scm-fname">${_escHtml(short)}</span>
      ${dir ? `<span class="scm-fdir">${_escHtml(dir)}</span>` : ''}
      <div class="scm-actions">${actions}</div>
    </div>`;
  }).join('');

  return `<div class="scm-section">
    <div class="scm-section-hdr">
      <span class="scm-section-title">${title} <span class="scm-section-count">${files.length}</span></span>
      <div class="scm-section-btns">${sectionActions}</div>
    </div>
    ${rows}
  </div>`;
}

async function _loadScmDiff(cwd, file, staged) {
  const diffArea  = _el('shell-scm-diff');
  const diffTitle = _el('shell-scm-diff-title');
  const diffPre   = _el('shell-scm-diff-pre');
  if (!diffArea) return;

  diffTitle.textContent = file.split('/').pop() + (staged ? ' (staged)' : '');
  diffPre.innerHTML = '<span class="scm-loading">Loading diff…</span>';
  diffArea.style.display = 'flex';

  try {
    const r = await fetch(`/api/shell/gitdiff?path=${encodeURIComponent(cwd)}&file=${encodeURIComponent(file)}${staged ? '&staged=true' : ''}`);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    diffPre.innerHTML = _renderDiff(data.diff || '');
  } catch (e) {
    diffPre.innerHTML = `<span style="color:#e06c75">${_escHtml(String(e))}</span>`;
  }
}

function _renderDiff(raw) {
  if (!raw.trim()) return '<span class="scm-note">No changes</span>';
  return raw.split('\n').map(line => {
    const esc = _escHtml(line);
    if (line.startsWith('+++') || line.startsWith('---'))
      return `<span class="diff-meta">${esc}</span>\n`;
    if (line.startsWith('@@'))
      return `<span class="diff-hunk">${esc}</span>\n`;
    if (line.startsWith('+'))
      return `<span class="diff-add">${esc}</span>\n`;
    if (line.startsWith('-'))
      return `<span class="diff-del">${esc}</span>\n`;
    return `<span class="diff-ctx">${esc}</span>\n`;
  }).join('');
}

async function _gitAction(action, cwd, file) {
  const epMap = {
    'stage':       '/api/shell/gitstage',
    'unstage':     '/api/shell/gitunstage',
    'revert':      '/api/shell/gitrestore',
    'stage-all':   '/api/shell/gitstage',
    'unstage-all': '/api/shell/gitunstage',
    'revert-all':  '/api/shell/gitrestore',
  };
  const url = epMap[action];
  if (!url) return;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: cwd, file }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    console.error('git action failed:', err.detail || action);
  }
}

async function _gitCommit() {
  const tab    = _getTab();
  const msgEl  = _el('shell-scm-msg');
  const btn    = _el('shell-scm-commit-btn');
  if (!tab?.cwd || !msgEl) return;
  const msg = msgEl.value.trim();
  if (!msg) { msgEl.focus(); return; }

  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await fetch('/api/shell/gitcommit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: tab.cwd, message: msg }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Commit failed');
    msgEl.value = '';
    const diff = _el('shell-scm-diff');
    if (diff) diff.style.display = 'none';
    await _loadScm(tab.cwd);
    _loadGitBar(tab.cwd);
  } catch (e) {
    alert('Commit failed:\n' + String(e));
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✓ Commit'; }
  }
}

// Debounced SCM refresh — called after PTY input to catch Claude's file edits
function _scheduleScmRefresh(cwd) {
  clearTimeout(_scmRefreshTimer);
  _scmRefreshTimer = setTimeout(() => {
    if (_activeExplorerTab === 'scm') _loadScm(cwd);
    _loadGitBar(cwd);
  }, 1500);
}

// ── Git status bar ────────────────────────────────────────────────────────────

async function _loadGitBar(cwd) {
  if (!cwd) return;
  try {
    const r = await fetch(`/api/shell/gitinfo?path=${encodeURIComponent(cwd)}`);
    if (!r.ok) return;
    const d = await r.json();
    _renderGitBar(d);
  } catch (_) {}
}

function _renderGitBar(d) {
  const branchText = _el('git-bar-branch-text');
  const syncEl     = _el('git-bar-sync');
  const aheadEl    = _el('git-bar-ahead');
  const behindEl   = _el('git-bar-behind');
  const changesEl  = _el('git-bar-changes');
  const changesTxt = _el('git-bar-changes-text');
  const commitEl   = _el('git-bar-commit');
  const commitTxt  = _el('git-bar-commit-text');
  const sepCommit  = document.querySelector('.git-bar-sep-commit');
  if (!branchText) return;

  if (!d.is_git) {
    branchText.textContent = 'not a git repo';
    if (syncEl)    syncEl.style.display    = 'none';
    if (changesEl) changesEl.style.display = 'none';
    if (commitEl)  commitEl.style.display  = 'none';
    if (sepCommit) sepCommit.style.display = 'none';
    return;
  }

  // Branch
  const branch = d.branch === '(detached)' ? '(detached HEAD)' : (d.branch || '?');
  branchText.textContent = branch;

  // Ahead / behind — only shown when tracking a remote
  if (d.upstream && (d.ahead > 0 || d.behind > 0)) {
    if (aheadEl)  aheadEl.textContent  = d.ahead  > 0 ? `↑ ${d.ahead}` : '';
    if (behindEl) behindEl.textContent = d.behind > 0 ? ` ↓ ${d.behind}` : '';
    if (syncEl)   syncEl.style.display = 'flex';
  } else {
    if (syncEl) syncEl.style.display = 'none';
  }

  // Change count
  if (d.changes > 0) {
    if (changesTxt) changesTxt.textContent = `${d.changes} change${d.changes !== 1 ? 's' : ''}`;
    if (changesEl)  changesEl.style.display = 'flex';
  } else {
    if (changesEl) changesEl.style.display = 'none';
  }

  // Last commit
  if (d.last_commit) {
    // Truncate long subject lines
    const txt = d.last_commit.length > 48 ? d.last_commit.slice(0, 46) + '…' : d.last_commit;
    if (commitTxt) commitTxt.textContent = txt;
    if (commitEl)  commitEl.style.display = 'flex';
    if (sepCommit) sepCommit.style.display = 'inline-block';
  } else {
    if (commitEl)  commitEl.style.display  = 'none';
    if (sepCommit) sepCommit.style.display = 'none';
  }
}

// ── Vertical resize (tree ↕ viewer) ──────────────────────────────────────────

function _initVerticalResize() {
  const handle = _el('shell-v-resize');
  if (!handle) return;
  let startY = 0, startH = 0;

  handle.addEventListener('mousedown', e => {
    const tree = _el('shell-tree');
    if (!tree) return;
    startY = e.clientY;
    startH = tree.getBoundingClientRect().height;
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });

  function onMove(e) {
    const tree = _el('shell-tree');
    if (!tree) return;
    const delta = e.clientY - startY;
    const newH = Math.max(60, startH + delta);
    tree.style.height = newH + 'px';
    tree.style.flex   = 'none';
    try { localStorage.setItem('odysseus-shell-tree-h', newH); } catch {}
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  }

  // Restore saved height
  const saved = parseInt(localStorage.getItem('odysseus-shell-tree-h') || '0', 10);
  if (saved > 0) {
    const tree = _el('shell-tree');
    if (tree) { tree.style.height = saved + 'px'; tree.style.flex = 'none'; }
  }
}

// ── Folder picker ─────────────────────────────────────────────────────────────

async function _openPicker(callback) {
  _pickerSelected = '';
  _pickerCallback = callback;

  const modal = _el('shell-picker-modal');
  if (!modal) return;
  modal.classList.remove('hidden');

  // Fetch root path from backend
  const r = await fetch('/api/shell/browse?path=').catch(() => null);
  if (!r || !r.ok) return;
  const data = await r.json();
  _pickerPath = data.path;
  _renderPicker(data);
}

async function _pickerNav(path) {
  const okBtn = _el('shell-picker-ok');
  if (okBtn) okBtn.disabled = true;
  const list = _el('shell-picker-list');
  if (list) list.innerHTML = '<div style="padding:12px;font-size:12px;opacity:0.5">Loading…</div>';
  const r = await fetch(`/api/shell/browse?path=${encodeURIComponent(path)}`).catch(() => null);
  if (!r || !r.ok) {
    if (list) list.innerHTML = '<div style="padding:12px;font-size:12px;color:#e06c75">Could not open folder</div>';
    return;
  }
  const data = await r.json();
  _pickerPath = data.path;
  _renderPicker(data);
}

function _renderPicker(data) {
  const crumb = _el('shell-picker-crumb');
  const list = _el('shell-picker-list');
  const selLabel = _el('shell-picker-sel');
  const okBtn = _el('shell-picker-ok');
  if (!crumb || !list) return;

  // Breadcrumb — clicking a segment navigates to that directory
  crumb.innerHTML = '';
  const parts = data.path.replace(/\\/g, '/').split('/').filter(Boolean);
  parts.forEach((part, i) => {
    const seg = document.createElement('span');
    seg.className = 'picker-crumb-seg';
    seg.textContent = part;
    seg.addEventListener('click', e => {
      e.stopPropagation();
      let rebuilt;
      if (i === 0 && /^[a-zA-Z]:?$/.test(part)) {
        rebuilt = part.replace(/:?$/, '') + ':\\';
      } else {
        rebuilt = parts.slice(0, i + 1).join('\\');
      }
      _pickerNav(rebuilt);
    });
    crumb.appendChild(seg);
    if (i < parts.length - 1) {
      const sep = document.createElement('span');
      sep.className = 'picker-crumb-sep';
      sep.textContent = ' / ';
      crumb.appendChild(sep);
    }
  });

  // Folder list — single click navigates INTO the folder
  list.innerHTML = '';
  if (data.parent) {
    const up = document.createElement('div');
    up.className = 'picker-item picker-up';
    up.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg> ..`;
    up.addEventListener('click', e => { e.stopPropagation(); _pickerNav(data.parent); });
    list.appendChild(up);
  }
  for (const dir of data.dirs) {
    const item = document.createElement('div');
    item.className = 'picker-item picker-dir';
    item.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg> <span>${_escHtml(dir.name)}</span>`;
    item.addEventListener('click', e => { e.stopPropagation(); _pickerNav(dir.path); });
    list.appendChild(item);
  }

  // Footer: always shows current path; OK always enabled (opens current dir)
  if (selLabel) selLabel.textContent = data.path;
  if (okBtn) { okBtn.disabled = false; okBtn.textContent = 'Open here'; }
}

function _closePicker(confirm) {
  const modal = _el('shell-picker-modal');
  if (modal) modal.classList.add('hidden');
  if (confirm && _pickerPath && _pickerCallback) _pickerCallback(_pickerPath);
  _pickerCallback = null;
  _pickerSelected = '';
}

// ── Resize handle ─────────────────────────────────────────────────────────────

function _initResize() {
  const handle = _el('shell-divider');
  if (!handle) return;
  let startX = 0, startW = 0;

  handle.addEventListener('mousedown', e => {
    startX = e.clientX;
    startW = _explorerWidth;
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  function onMove(e) {
    const delta = startX - e.clientX; // dragging left = wider explorer
    _explorerWidth = Math.max(180, Math.min(600, startW + delta));
    const explorer = _el('shell-explorer');
    if (explorer) explorer.style.width = _explorerWidth + 'px';
    localStorage.setItem(_EXPLORER_WIDTH_KEY, _explorerWidth);
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  }
}

// ── Open / close workspace ────────────────────────────────────────────────────

function openWorkspace() {
  const ws = _el('shell-workspace');
  if (ws) ws.style.display = 'flex';
  const explorer = _el('shell-explorer');
  if (explorer) explorer.style.width = _explorerWidth + 'px';
  // Focus the input after paint
  setTimeout(() => { const i = _el('shell-cmd'); if (i && !_running) i.focus(); }, 80);
}

function closeWorkspace() {
  const ws = _el('shell-workspace');
  if (ws) ws.style.display = 'none';
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init() {
  _tabs = _loadTabs();
  _activeId = _tabs.length ? _tabs[_tabs.length - 1].id : null;
  _renderSidebarList();
  _initResize();
  _initVerticalResize();

  // Close the shell workspace when the user navigates anywhere else in the
  // sidebar or icon rail. Using CAPTURE phase (3rd arg = true) so this fires
  // BEFORE any element's own handler — the workspace display is still 'none'
  // when the user first clicks Shell, guaranteeing a clean early-return with
  // no false-close. Clicks inside the workspace or shell section are ignored.
  document.addEventListener('click', e => {
    const ws = _el('shell-workspace');
    if (!ws || ws.style.display === 'none') return;
    if (e.target.closest('#shell-workspace, #shell-section, #shell-picker-modal')) return;
    if (e.target.closest('nav.sidebar, .icon-rail')) closeWorkspace();
  }, true);

  // Explorer tabs
  const etabExplorer = _el('shell-etab-explorer');
  const etabScm      = _el('shell-etab-scm');
  if (etabExplorer) etabExplorer.addEventListener('click', () => _switchExplorerTab('explorer'));
  if (etabScm)      etabScm.addEventListener('click',      () => _switchExplorerTab('scm'));

  // SCM diff close
  // Git bar — changes count click opens SCM tab
  const gitBarChanges = _el('git-bar-changes');
  if (gitBarChanges) gitBarChanges.addEventListener('click', () => _switchExplorerTab('scm'));

  const scmDiffClose = _el('shell-scm-diff-close');
  if (scmDiffClose) scmDiffClose.addEventListener('click', () => {
    const d = _el('shell-scm-diff'); if (d) d.style.display = 'none';
  });

  // Commit
  const commitBtn = _el('shell-scm-commit-btn');
  if (commitBtn) commitBtn.addEventListener('click', _gitCommit);
  const commitMsg = _el('shell-scm-msg');
  if (commitMsg) commitMsg.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); _gitCommit(); }
  });

  // Sidebar section title click → open workspace
  const title = _el('shell-section-title');
  if (title) {
    title.addEventListener('click', () => {
      if (!_tabs.length) { _promptNewTab(); return; }
      openWorkspace();
      if (_activeId) _switchTab(_activeId);
    });
  }

  // Sidebar + button → new tab
  const newTabBtn = _el('shell-new-tab-btn');
  if (newTabBtn) newTabBtn.addEventListener('click', e => { e.stopPropagation(); _promptNewTab(); });

  // File viewer close
  const viewerClose = _el('shell-viewer-close');
  if (viewerClose) viewerClose.addEventListener('click', () => {
    const v = _el('shell-viewer'); if (v) v.style.display = 'none';
  });

  // Refresh active panel (tree or SCM)
  const refresh = _el('shell-explorer-refresh');
  if (refresh) refresh.addEventListener('click', () => {
    const tab = _getTab();
    if (!tab?.cwd) return;
    if (_activeExplorerTab === 'scm') {
      _loadScm(tab.cwd);
    } else {
      delete _treeCache[tab.cwd];
      _loadTree(tab.cwd);
    }
  });

  // Picker ok/cancel
  const pickerOk = _el('shell-picker-ok');
  const pickerCancel = _el('shell-picker-cancel');
  const pickerX = _el('shell-picker-x');
  if (pickerOk) pickerOk.addEventListener('click', () => _closePicker(true));
  if (pickerCancel) pickerCancel.addEventListener('click', () => _closePicker(false));
  if (pickerX) pickerX.addEventListener('click', () => _closePicker(false));
  const pickerModal = _el('shell-picker-modal');
  if (pickerModal) pickerModal.addEventListener('click', e => { if (e.target === pickerModal) _closePicker(false); });

  // Show active tab if workspace was already open
  if (_activeId) {
    const tab = _getTab();
    if (tab?.cwd) setTimeout(() => { _loadTree(tab.cwd); _loadGitBar(tab.cwd); }, 200);
  }
}

function _promptNewTab() {
  _openPicker(cwd => {
    const tab = _addTab(cwd);
    _renderSidebarList();
    openWorkspace();
    _switchTab(tab.id);
    _loadTree(cwd);
  });
}

export default { init, openWorkspace, closeWorkspace };
