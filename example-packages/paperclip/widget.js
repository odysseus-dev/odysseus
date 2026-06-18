/**
 * Paperclip — widget.js
 * Full-page AI team management app. Opens in the main content area
 * (replaces chat) via registerAppView.
 */
(() => {
  const API = '/api/paperclip';

  // ── State ──────────────────────────────────────────────────────────────────
  let _companies = [];
  let _agents = {};   // { [company_id]: agent[] }
  let _tasks  = {};   // { [company_id]: task[] }
  let _cid  = null;   // selected company id
  let _aid  = null;   // selected agent id
  let _view = 'dashboard'; // 'dashboard' | 'project' | 'agent'
  let _root = null;
  let _navEl = null;
  let _mainEl = null;

  // ── CSS ────────────────────────────────────────────────────────────────────
  const CSS = `
    .pc-shell {
      display: flex; flex: 1; height: 100%; overflow: hidden;
      background: var(--bg); font-family: var(--font-family, 'Fira Code', monospace);
    }
    .pc-nav {
      width: 220px; min-width: 220px; height: 100%;
      border-right: 1px solid var(--border);
      display: flex; flex-direction: column; overflow: hidden;
      background: color-mix(in srgb, var(--panel) 70%, var(--bg));
    }
    .pc-nav-header {
      padding: 13px 12px 11px; display: flex; align-items: center; gap: 8px;
      border-bottom: 1px solid var(--border); flex-shrink: 0;
    }
    .pc-nav-logo {
      width: 26px; height: 26px; border-radius: 6px;
      background: var(--accent, #63b3ed);
      display: flex; align-items: center; justify-content: center;
      color: #000; flex-shrink: 0;
    }
    .pc-nav-brand { font-size: 13px; font-weight: 700; flex: 1; }
    .pc-nav-close {
      cursor: pointer; opacity: .4; padding: 3px;
      display: flex; align-items: center; border-radius: 4px;
      background: none; border: none; color: var(--fg);
    }
    .pc-nav-close:hover { opacity: .9; background: color-mix(in srgb, var(--fg) 10%, transparent); }
    .pc-nav-body { flex: 1; overflow-y: auto; padding: 6px 0 12px; }
    .pc-nav-new {
      margin: 6px 10px 10px; display: flex; align-items: center; gap: 6px;
      padding: 6px 10px; border-radius: 6px; cursor: pointer;
      font-size: 12px; font-weight: 600; font-family: inherit;
      background: var(--accent, #63b3ed); color: #000;
      border: none; width: calc(100% - 20px);
    }
    .pc-nav-new:hover { opacity: .85; }
    .pc-nav-sep { height: 1px; background: var(--border); margin: 6px 0; }
    .pc-nav-section {
      padding: 8px 12px 3px; font-size: 10px; font-weight: 700;
      opacity: .4; letter-spacing: .07em; text-transform: uppercase;
    }
    .pc-nav-item {
      display: flex; align-items: center; gap: 8px;
      padding: 5px 12px; cursor: pointer; font-size: 12px;
      user-select: none; transition: background .1s;
    }
    .pc-nav-item:hover { background: color-mix(in srgb, var(--fg) 6%, transparent); }
    .pc-nav-item.active {
      background: color-mix(in srgb, var(--accent, #63b3ed) 14%, transparent);
      font-weight: 600;
    }
    .pc-nav-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
    .pc-nav-badge {
      margin-left: auto; font-size: 10px; opacity: .4;
      background: color-mix(in srgb, var(--fg) 10%, transparent);
      padding: 1px 5px; border-radius: 10px;
    }
    .pc-nav-add {
      display: flex; align-items: center; gap: 6px;
      padding: 4px 12px; cursor: pointer; font-size: 11px; opacity: .4;
    }
    .pc-nav-add:hover { opacity: .8; }
    .pc-main {
      flex: 1; min-width: 0; height: 100%;
      display: flex; flex-direction: column; overflow: hidden;
    }
    .pc-main-hdr {
      padding: 13px 24px 12px; border-bottom: 1px solid var(--border);
      flex-shrink: 0; display: flex; align-items: center; gap: 12px;
    }
    .pc-main-title {
      font-size: 12px; font-weight: 700; letter-spacing: .08em;
      text-transform: uppercase; flex: 1;
    }
    .pc-main-sub { font-size: 11px; opacity: .4; margin-top: 2px; }
    .pc-main-body { flex: 1; min-height: 0; overflow-y: auto; padding: 24px; }
    .pc-stats {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 12px; margin-bottom: 28px;
    }
    .pc-stat {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 8px; padding: 16px 18px;
    }
    .pc-stat-n { font-size: 30px; font-weight: 700; line-height: 1; }
    .pc-stat-l { font-size: 11px; opacity: .5; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; margin-top: 6px; }
    .pc-stat-s { font-size: 10px; opacity: .35; margin-top: 3px; }
    .pc-section { font-size: 11px; font-weight: 700; opacity: .45; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 10px; }
    .pc-two { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
    @media (max-width: 860px) { .pc-two { grid-template-columns: 1fr; } }
    .pc-task-list { display: flex; flex-direction: column; gap: 4px; }
    .pc-task-row {
      display: flex; align-items: center; gap: 8px;
      padding: 6px 10px; border-radius: 6px;
      background: var(--panel); border: 1px solid var(--border); font-size: 12px;
    }
    .pc-task-row:hover { border-color: color-mix(in srgb, var(--accent, #63b3ed) 45%, var(--border)); }
    .pc-status-btn {
      background: none; border: none; cursor: pointer; font-size: 13px;
      flex-shrink: 0; color: var(--fg); opacity: .55; padding: 0; line-height: 1;
      font-family: inherit;
    }
    .pc-status-btn:hover { opacity: 1; }
    .pc-status-ip { color: var(--yellow, #e5c07b); opacity: 1 !important; }
    .pc-status-done { color: var(--green, #98c379); opacity: 1 !important; }
    .pc-task-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pc-task-title.done { opacity: .35; text-decoration: line-through; }
    .pc-task-tag {
      font-size: 10px; opacity: .4;
      background: color-mix(in srgb, var(--fg) 8%, transparent);
      padding: 1px 6px; border-radius: 10px; flex-shrink: 0;
    }
    .pc-icon-btn {
      background: none; border: none; cursor: pointer; opacity: .3;
      padding: 2px 4px; font-size: 13px; color: var(--fg); flex-shrink: 0;
      font-family: inherit; line-height: 1;
    }
    .pc-icon-btn:hover { opacity: .9; }
    .pc-agent-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(195px, 1fr));
      gap: 12px; margin-bottom: 24px;
    }
    .pc-agent-card {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 8px; padding: 14px;
      display: flex; flex-direction: column; gap: 8px;
      cursor: pointer; transition: border-color .15s;
    }
    .pc-agent-card:hover,
    .pc-agent-card.active { border-color: color-mix(in srgb, var(--accent, #63b3ed) 70%, var(--border)); }
    .pc-avatar {
      width: 36px; height: 36px; border-radius: 50%;
      background: color-mix(in srgb, var(--accent, #63b3ed) 18%, var(--panel));
      display: flex; align-items: center; justify-content: center;
      font-size: 15px; font-weight: 700; flex-shrink: 0;
    }
    .pc-agent-name { font-size: 13px; font-weight: 600; }
    .pc-agent-role { font-size: 11px; opacity: .45; }
    .pc-agent-model { font-size: 10px; opacity: .3; font-style: italic; }
    .pc-agent-actions { display: flex; gap: 6px; margin-top: auto; }
    .pc-btn {
      border: none; border-radius: 5px; cursor: pointer;
      font-size: 11px; padding: 5px 11px; font-weight: 600; font-family: inherit;
    }
    .pc-btn-primary { background: var(--accent, #63b3ed); color: #000; }
    .pc-btn-primary:hover { opacity: .85; }
    .pc-btn-ghost {
      background: none; border: 1px solid var(--border); color: var(--fg);
    }
    .pc-btn-ghost:hover { background: color-mix(in srgb, var(--fg) 8%, transparent); }
    .pc-btn-danger {
      background: none; border: 1px solid color-mix(in srgb, var(--danger,#e55) 40%, transparent);
      color: var(--danger, #e55);
    }
    .pc-btn-danger:hover { background: color-mix(in srgb, var(--danger,#e55) 10%, transparent); }
    .pc-btn:disabled { opacity: .45; cursor: not-allowed; }
    .pc-empty { padding: 40px 24px; text-align: center; opacity: .35; font-size: 13px; }
    .pc-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,.55);
      z-index: 9900; display: flex; align-items: center; justify-content: center;
    }
    .pc-modal {
      background: var(--bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 24px; width: min(480px, 92vw);
      display: flex; flex-direction: column; gap: 14px;
      max-height: 90vh; overflow-y: auto;
    }
    .pc-modal-title { font-size: 14px; font-weight: 700; }
    .pc-fg { display: flex; flex-direction: column; gap: 4px; }
    .pc-fl { font-size: 10px; opacity: .55; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
    .pc-input {
      background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
      padding: 7px 10px; font-size: 13px; color: var(--fg); width: 100%;
      box-sizing: border-box; font-family: inherit;
    }
    .pc-input:focus { outline: none; border-color: var(--accent, #63b3ed); }
    .pc-textarea {
      background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
      padding: 7px 10px; font-size: 12px; color: var(--fg); width: 100%;
      box-sizing: border-box; font-family: inherit; resize: vertical; min-height: 80px;
    }
    .pc-textarea:focus { outline: none; border-color: var(--accent, #63b3ed); }
    .pc-modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 4px; }
    .pc-info-card {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 8px; padding: 18px; margin-bottom: 24px;
    }
  `;

  // ── HTTP ───────────────────────────────────────────────────────────────────
  async function _api(method, path, body) {
    const r = await fetch(API + path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    return r.json();
  }

  // ── Data loading ───────────────────────────────────────────────────────────
  async function _loadAll() {
    const d = await _api('GET', '/companies');
    _companies = d.companies || [];
    if (!_cid && _companies.length) _cid = _companies[0].id;
    if (_cid && !_companies.find(c => c.id === _cid)) {
      _cid = _companies[0]?.id ?? null;
      _aid = null; _view = _cid ? 'project' : 'dashboard';
    }
    await Promise.all(_companies.map(async c => {
      const [ad, td] = await Promise.all([
        _api('GET', `/companies/${c.id}/agents`),
        _api('GET', `/companies/${c.id}/tasks`),
      ]);
      _agents[c.id] = ad.agents || [];
      _tasks[c.id]  = td.tasks  || [];
    }));
  }

  async function _reload() {
    try { await _loadAll(); } catch (e) { console.error('[paperclip]', e); }
    _renderAll();
  }

  // ── Shell ──────────────────────────────────────────────────────────────────
  function _mount(container) {
    _root = container;
    _root.style.cssText = 'display:flex;flex:1;height:100%;overflow:hidden;';

    if (!document.getElementById('pc-styles')) {
      const s = document.createElement('style');
      s.id = 'pc-styles'; s.textContent = CSS;
      document.head.appendChild(s);
    }

    const shell = document.createElement('div');
    shell.className = 'pc-shell';

    _navEl  = document.createElement('div');
    _navEl.className = 'pc-nav';
    _mainEl = document.createElement('div');
    _mainEl.className = 'pc-main';

    shell.appendChild(_navEl);
    shell.appendChild(_mainEl);
    _root.appendChild(shell);

    _reload();
  }

  function _unmount() {
    _root = null; _navEl = null; _mainEl = null;
  }

  function _renderAll() {
    if (!_navEl || !_mainEl) return;
    _renderNav();
    _renderMain();
  }

  // ── Left nav ───────────────────────────────────────────────────────────────
  function _renderNav() {
    _navEl.innerHTML = '';

    const hdr = _mk('div', 'pc-nav-header');
    hdr.innerHTML = `
      <div class="pc-nav-logo">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
        </svg>
      </div>
      <span class="pc-nav-brand">Paperclip</span>
    `;
    const closeBtn = document.createElement('button');
    closeBtn.className = 'pc-nav-close';
    closeBtn.title = 'Back to chats';
    closeBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
    closeBtn.addEventListener('click', () => {
      document.getElementById('pkg-nav-paperclip')?._pkgClose?.();
    });
    hdr.appendChild(closeBtn);
    _navEl.appendChild(hdr);

    const body = _mk('div', 'pc-nav-body');

    const newBtn = document.createElement('button');
    newBtn.className = 'pc-nav-new';
    newBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> New Issue`;
    newBtn.addEventListener('click', () => _modal('task'));
    body.appendChild(newBtn);

    body.appendChild(_navRow(
      `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>`,
      'Dashboard', _view === 'dashboard', () => { _view = 'dashboard'; _aid = null; _renderAll(); }
    ));
    body.appendChild(_navRow(
      `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
      'Inbox', false, null
    ));

    body.appendChild(_mk('div', 'pc-nav-sep'));

    if (_companies.length > 0) {
      const sec = _mk('div', 'pc-nav-section');
      sec.textContent = 'Projects';
      body.appendChild(sec);

      _companies.forEach(c => {
        const isActive = _cid === c.id && _view !== 'dashboard';
        const item = document.createElement('div');
        item.className = 'pc-nav-item' + (isActive ? ' active' : '');
        item.dataset.cid = c.id;
        item.innerHTML = `
          <div class="pc-nav-dot" style="background:${_color(c.id)}"></div>
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(c.name)}</span>
          <span class="pc-nav-badge">${(_agents[c.id] || []).length}</span>
        `;
        item.addEventListener('click', () => {
          _cid = c.id; _view = 'project'; _aid = null; _renderAll();
        });
        body.appendChild(item);
      });
    }

    const addProj = _mk('div', 'pc-nav-add');
    addProj.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Add Project`;
    addProj.addEventListener('click', () => _modal('company'));
    body.appendChild(addProj);

    if (_cid && _view !== 'dashboard' && (_agents[_cid] || []).length > 0) {
      body.appendChild(_mk('div', 'pc-nav-sep'));
      const sec2 = _mk('div', 'pc-nav-section');
      sec2.textContent = 'Agents';
      body.appendChild(sec2);

      (_agents[_cid] || []).forEach(a => {
        const item = document.createElement('div');
        item.className = 'pc-nav-item' + (_aid === a.id ? ' active' : '');
        item.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" opacity=".5"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(a.name)}</span>
          ${a.role ? `<span class="pc-nav-badge">${_esc(a.role.slice(0,8))}</span>` : ''}
        `;
        item.addEventListener('click', () => { _aid = a.id; _renderAll(); });
        body.appendChild(item);
      });

      const addAgent = _mk('div', 'pc-nav-add');
      addAgent.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Hire Agent`;
      addAgent.addEventListener('click', () => _modal('agent'));
      body.appendChild(addAgent);
    }

    _navEl.appendChild(body);
  }

  function _navRow(iconSvg, label, active, onClick) {
    const item = document.createElement('div');
    item.className = 'pc-nav-item' + (active ? ' active' : '');
    item.innerHTML = `${iconSvg}<span style="flex:1">${label}</span>`;
    if (onClick) item.addEventListener('click', onClick);
    return item;
  }

  // ── Main content ───────────────────────────────────────────────────────────
  function _renderMain() {
    _mainEl.innerHTML = '';
    if (_aid)                          { _renderAgent();    return; }
    if (_view === 'project' && _cid)   { _renderProject();  return; }
    _renderDashboard();
  }

  // ── Dashboard ──────────────────────────────────────────────────────────────
  function _renderDashboard() {
    const allAgents = Object.values(_agents).flat();
    const allTasks  = Object.values(_tasks).flat();
    const inProg = allTasks.filter(t => t.status === 'in-progress').length;
    const done   = allTasks.filter(t => t.status === 'done').length;

    const hdr = _mk('div', 'pc-main-hdr');
    hdr.innerHTML = `<div><div class="pc-main-title">Dashboard</div><div class="pc-main-sub">Paperclip</div></div>`;
    _mainEl.appendChild(hdr);

    const body = _mk('div', 'pc-main-body');

    const stats = _mk('div', 'pc-stats');
    stats.innerHTML = `
      <div class="pc-stat">
        <div class="pc-stat-n">${allAgents.length}</div>
        <div class="pc-stat-l">Agents Enabled</div>
        <div class="pc-stat-s">${_companies.length} project${_companies.length !== 1 ? 's' : ''}</div>
      </div>
      <div class="pc-stat">
        <div class="pc-stat-n" style="color:var(--yellow,#e5c07b)">${inProg}</div>
        <div class="pc-stat-l">Tasks In Progress</div>
        <div class="pc-stat-s">${allTasks.filter(t=>t.status==='todo').length} open</div>
      </div>
      <div class="pc-stat">
        <div class="pc-stat-n" style="color:var(--green,#98c379)">${done}</div>
        <div class="pc-stat-l">Completed</div>
        <div class="pc-stat-s">${allTasks.length > 0 ? Math.round(done/allTasks.length*100) : 0}% of ${allTasks.length} tasks</div>
      </div>
      <div class="pc-stat">
        <div class="pc-stat-n">${_companies.length}</div>
        <div class="pc-stat-l">Projects</div>
        <div class="pc-stat-s">Active workspaces</div>
      </div>
    `;
    body.appendChild(stats);

    if (_companies.length === 0) {
      const empty = _mk('div', 'pc-empty');
      empty.style.padding = '48px 24px';
      empty.innerHTML = `
        <div style="font-size:36px;margin-bottom:14px;opacity:.2">⬡</div>
        <div style="font-size:15px;font-weight:700;margin-bottom:8px;opacity:.7">No projects yet</div>
        <div style="font-size:12px;margin-bottom:20px">Create a project to start managing AI agents and tasks.</div>
      `;
      const btn = document.createElement('button');
      btn.className = 'pc-btn pc-btn-primary'; btn.textContent = '+ New Project';
      btn.addEventListener('click', () => _modal('company'));
      empty.appendChild(btn);
      body.appendChild(empty);
    } else {
      const two = _mk('div', 'pc-two');

      // Recent Tasks
      const tasksCol = document.createElement('div');
      const t1 = _mk('div', 'pc-section'); t1.textContent = 'Recent Tasks'; tasksCol.appendChild(t1);
      const recent = allTasks.slice(-15).reverse();
      if (!recent.length) {
        const e = _mk('div', 'pc-empty'); e.textContent = 'No tasks yet'; tasksCol.appendChild(e);
      } else {
        const list = _mk('div', 'pc-task-list');
        const am = _buildAgentMap();
        recent.forEach(t => list.appendChild(_taskRow(t, am)));
        tasksCol.appendChild(list);
      }
      two.appendChild(tasksCol);

      // Agents overview
      const agentsCol = document.createElement('div');
      const t2 = _mk('div', 'pc-section'); t2.textContent = 'Agents'; agentsCol.appendChild(t2);
      if (!allAgents.length) {
        const e = _mk('div', 'pc-empty'); e.textContent = 'No agents yet'; agentsCol.appendChild(e);
      } else {
        const list = _mk('div', 'pc-task-list');
        allAgents.slice(0, 10).forEach(a => {
          const co = _companies.find(c => c.id === a.company_id);
          const row = _mk('div', 'pc-task-row');
          row.style.cursor = 'pointer';
          row.innerHTML = `
            <div class="pc-avatar" style="width:28px;height:28px;font-size:12px">${a.name[0].toUpperCase()}</div>
            <div style="flex:1;min-width:0">
              <div style="font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(a.name)}</div>
              <div style="font-size:10px;opacity:.38">${_esc(a.role||'Agent')}${co?' · '+_esc(co.name):''}</div>
            </div>
          `;
          const runBtn = document.createElement('button');
          runBtn.className = 'pc-btn pc-btn-primary';
          runBtn.style.cssText = 'font-size:10px;padding:3px 8px';
          runBtn.textContent = '▶ Run';
          runBtn.addEventListener('click', async e => { e.stopPropagation(); await _runAgent(a.id, runBtn); });
          row.appendChild(runBtn);
          row.addEventListener('click', () => { _cid = a.company_id; _aid = a.id; _view = 'project'; _renderAll(); });
          list.appendChild(row);
        });
        agentsCol.appendChild(list);
      }
      two.appendChild(agentsCol);
      body.appendChild(two);
    }

    _mainEl.appendChild(body);
  }

  // ── Project view ───────────────────────────────────────────────────────────
  function _renderProject() {
    const company = _companies.find(c => c.id === _cid);
    if (!company) return;
    const agents = _agents[_cid] || [];
    const tasks  = _tasks[_cid]  || [];

    const hdr = _mk('div', 'pc-main-hdr');
    hdr.innerHTML = `<div style="flex:1"><div class="pc-main-title">${_esc(company.name)}</div>${company.goal?`<div class="pc-main-sub">${_esc(company.goal)}</div>`:''}</div>`;

    const acts = document.createElement('div');
    acts.style.cssText = 'display:flex;gap:8px;flex-shrink:0';

    const issueBtn = document.createElement('button');
    issueBtn.className = 'pc-btn pc-btn-primary'; issueBtn.textContent = '+ New Issue';
    issueBtn.addEventListener('click', () => _modal('task'));

    const agentBtn = document.createElement('button');
    agentBtn.className = 'pc-btn pc-btn-ghost'; agentBtn.textContent = '+ Hire Agent';
    agentBtn.addEventListener('click', () => _modal('agent'));

    const delBtn = document.createElement('button');
    delBtn.className = 'pc-btn pc-btn-danger'; delBtn.textContent = 'Delete';
    delBtn.addEventListener('click', async () => {
      if (!confirm(`Delete project "${company.name}" and everything in it?`)) return;
      const cid = _cid;
      _cid = null; _view = 'dashboard'; _aid = null;
      delete _agents[cid]; delete _tasks[cid];
      await _api('DELETE', `/companies/${cid}`);
      _reload();
    });

    acts.append(issueBtn, agentBtn, delBtn);
    hdr.appendChild(acts);
    _mainEl.appendChild(hdr);

    const body = _mk('div', 'pc-main-body');

    // Agents
    const aSec = _mk('div', 'pc-section');
    aSec.textContent = `Agents (${agents.length})`;
    body.appendChild(aSec);

    if (!agents.length) {
      const e = _mk('div', 'pc-empty');
      e.style.cssText = 'padding:20px;text-align:left';
      e.innerHTML = `No agents yet. <button class="pc-btn pc-btn-ghost" style="margin-left:8px;font-size:11px">+ Hire Agent</button>`;
      e.querySelector('button').addEventListener('click', () => _modal('agent'));
      body.appendChild(e);
    } else {
      const grid = _mk('div', 'pc-agent-grid');
      agents.forEach(a => {
        const card = document.createElement('div');
        card.className = 'pc-agent-card' + (_aid === a.id ? ' active' : '');

        const top = document.createElement('div');
        top.style.cssText = 'display:flex;gap:10px;align-items:flex-start';
        top.innerHTML = `
          <div class="pc-avatar">${a.name[0].toUpperCase()}</div>
          <div>
            <div class="pc-agent-name">${_esc(a.name)}</div>
            <div class="pc-agent-role">${_esc(a.role||'Agent')}</div>
            ${a.model?`<div class="pc-agent-model">${_esc(a.model)}</div>`:''}
          </div>
        `;
        card.appendChild(top);

        const cardActs = _mk('div', 'pc-agent-actions');
        const runBtn = document.createElement('button');
        runBtn.className = 'pc-btn pc-btn-primary'; runBtn.textContent = '▶ Run';
        runBtn.addEventListener('click', async e => { e.stopPropagation(); await _runAgent(a.id, runBtn); });

        const xBtn = document.createElement('button');
        xBtn.className = 'pc-btn pc-btn-ghost'; xBtn.textContent = '×';
        xBtn.style.marginLeft = 'auto';
        xBtn.addEventListener('click', async e => {
          e.stopPropagation();
          if (!confirm(`Delete agent "${a.name}"?`)) return;
          await _api('DELETE', `/agents/${a.id}`);
          _reload();
        });
        cardActs.append(runBtn, xBtn);
        card.appendChild(cardActs);
        card.addEventListener('click', () => { _aid = a.id; _renderAll(); });
        grid.appendChild(card);
      });
      body.appendChild(grid);
    }

    // Tasks
    const tSec = _mk('div', 'pc-section');
    tSec.style.marginTop = '20px';
    tSec.textContent = `Tasks (${tasks.length})`;
    body.appendChild(tSec);

    if (!tasks.length) {
      const e = _mk('div', 'pc-empty');
      e.style.cssText = 'padding:20px;text-align:left';
      e.innerHTML = `No tasks yet. <button class="pc-btn pc-btn-ghost" style="margin-left:8px;font-size:11px">+ New Issue</button>`;
      e.querySelector('button').addEventListener('click', () => _modal('task'));
      body.appendChild(e);
    } else {
      const agentMap = Object.fromEntries(agents.map(a => [a.id, a.name]));
      const list = _mk('div', 'pc-task-list');
      tasks.forEach(t => list.appendChild(_taskRow(t, agentMap)));
      body.appendChild(list);
    }

    _mainEl.appendChild(body);
  }

  // ── Agent detail ───────────────────────────────────────────────────────────
  function _renderAgent() {
    const agent = (_agents[_cid] || []).find(a => a.id === _aid);
    if (!agent) { _aid = null; _renderMain(); return; }
    const tasks = (_tasks[_cid] || []).filter(t => t.agent_id === _aid);

    const hdr = _mk('div', 'pc-main-hdr');
    const back = document.createElement('button');
    back.className = 'pc-btn pc-btn-ghost';
    back.style.padding = '4px 8px';
    back.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>`;
    back.addEventListener('click', () => { _aid = null; _view = 'project'; _renderAll(); });

    const titleDiv = document.createElement('div');
    titleDiv.style.flex = '1';
    titleDiv.innerHTML = `<div class="pc-main-title">${_esc(agent.name)}</div><div class="pc-main-sub">${_esc(agent.role||'Agent')}</div>`;

    const runBtn = document.createElement('button');
    runBtn.className = 'pc-btn pc-btn-primary'; runBtn.textContent = '▶ Run Agent';
    runBtn.addEventListener('click', async () => await _runAgent(agent.id, runBtn));

    hdr.append(back, titleDiv, runBtn);
    _mainEl.appendChild(hdr);

    const body = _mk('div', 'pc-main-body');

    const info = _mk('div', 'pc-info-card');
    info.innerHTML = `
      <div style="display:flex;gap:16px;align-items:flex-start">
        <div class="pc-avatar" style="width:48px;height:48px;font-size:20px;flex-shrink:0">${agent.name[0].toUpperCase()}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:16px;font-weight:700">${_esc(agent.name)}</div>
          <div style="font-size:12px;opacity:.45;margin-top:3px">${_esc(agent.role||'Agent')}</div>
          ${agent.model?`<div style="font-size:11px;opacity:.3;font-style:italic;margin-top:4px">${_esc(agent.model)}</div>`:''}
        </div>
      </div>
      ${agent.system_prompt ? `
        <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
          <div class="pc-section" style="margin-bottom:6px">System Prompt</div>
          <div style="font-size:12px;opacity:.55;white-space:pre-wrap;max-height:130px;overflow-y:auto;line-height:1.6">${_esc(agent.system_prompt)}</div>
        </div>
      ` : ''}
    `;
    body.appendChild(info);

    const tSec = _mk('div', 'pc-section');
    tSec.textContent = `Assigned Tasks (${tasks.length})`;
    body.appendChild(tSec);

    if (!tasks.length) {
      const e = _mk('div', 'pc-empty'); e.textContent = 'No tasks assigned';
      body.appendChild(e);
    } else {
      const list = _mk('div', 'pc-task-list');
      tasks.forEach(t => list.appendChild(_taskRow(t)));
      body.appendChild(list);
    }

    _mainEl.appendChild(body);
  }

  // ── Task row ───────────────────────────────────────────────────────────────
  function _taskRow(task, agentMap) {
    const cycle = ['todo', 'in-progress', 'done'];
    const icons  = { todo: '○', 'in-progress': '◑', done: '●' };
    const next   = cycle[(cycle.indexOf(task.status) + 1) % cycle.length];

    const row = _mk('div', 'pc-task-row');

    const sBtn = document.createElement('button');
    sBtn.className = 'pc-status-btn' + (task.status==='in-progress'?' pc-status-ip':task.status==='done'?' pc-status-done':'');
    sBtn.textContent = icons[task.status] || '○';
    sBtn.title = `Mark ${next}`;
    sBtn.addEventListener('click', async e => {
      e.stopPropagation();
      await _api('PUT', `/tasks/${task.id}`, { status: next });
      _reload();
    });
    row.appendChild(sBtn);

    const title = document.createElement('span');
    title.className = 'pc-task-title' + (task.status === 'done' ? ' done' : '');
    title.textContent = task.title;
    row.appendChild(title);

    if (agentMap && task.agent_id && agentMap[task.agent_id]) {
      const tag = _mk('span', 'pc-task-tag');
      tag.textContent = agentMap[task.agent_id].slice(0, 14);
      row.appendChild(tag);
    }

    const del = document.createElement('button');
    del.className = 'pc-icon-btn'; del.textContent = '×'; del.title = 'Delete';
    del.addEventListener('click', async e => {
      e.stopPropagation();
      await _api('DELETE', `/tasks/${task.id}`);
      _reload();
    });
    row.appendChild(del);
    return row;
  }

  // ── Run agent ──────────────────────────────────────────────────────────────
  async function _runAgent(aid, btn) {
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try {
      const d = await _api('POST', `/agents/${aid}/run`);
      if (window.sessionModule?.loadSessions) await window.sessionModule.loadSessions();
      document.getElementById('pkg-nav-paperclip')?._pkgClose?.();
      if (window.sessionModule?.selectSession) window.sessionModule.selectSession(d.session_id);
    } catch(e) {
      alert('Run failed: ' + e.message);
      btn.disabled = false; btn.textContent = orig;
    }
  }

  // ── Modals ─────────────────────────────────────────────────────────────────
  function _modal(type) {
    document.getElementById('pc-overlay')?.remove();

    const overlay = document.createElement('div');
    overlay.className = 'pc-overlay'; overlay.id = 'pc-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    const card = _mk('div', 'pc-modal');
    document.addEventListener('keydown', function onEsc(k) {
      if (k.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', onEsc); }
    });

    if (type === 'company') {
      const nameIn = _inp('Project name');
      const goalIn = _inp('Goal (optional)');
      card.append(
        _mhd('New Project'),
        _fg('Name *', nameIn),
        _fg('Goal', goalIn),
        _mact(overlay, async () => {
          if (!nameIn.value.trim()) return;
          const c = await _api('POST', '/companies', { name: nameIn.value.trim(), goal: goalIn.value });
          _cid = c.id; _view = 'project'; overlay.remove(); _reload();
        }, 'Create Project')
      );
    }

    else if (type === 'agent') {
      const nameIn  = _inp('Agent name (e.g. CEO, Engineer)');
      const roleIn  = _inp('Role description');
      const modelIn = _inp('Model (e.g. claude-sonnet-4-6)');
      const sysIn   = _ta('System prompt — how this agent thinks and behaves');
      card.append(
        _mhd('Hire Agent'),
        _fg('Name *', nameIn),
        _fg('Role', roleIn),
        _fg('Model', modelIn),
        _fg('System Prompt', sysIn),
        _mact(overlay, async () => {
          if (!nameIn.value.trim()) return;
          const cid = _cid || _companies[0]?.id;
          if (!cid) { alert('Create a project first'); return; }
          await _api('POST', `/companies/${cid}/agents`, {
            name: nameIn.value.trim(), role: roleIn.value,
            model: modelIn.value, system_prompt: sysIn.value,
          });
          _cid = cid; _view = 'project'; overlay.remove(); _reload();
        }, 'Hire Agent')
      );
    }

    else if (type === 'task') {
      const titleIn = _inp('Issue title *');
      const descIn  = _ta('Description (optional)');

      const projSel = document.createElement('select');
      projSel.className = 'pc-input';
      _companies.forEach(c => {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = c.name;
        if (c.id === _cid) o.selected = true;
        projSel.appendChild(o);
      });

      const agentSel = document.createElement('select');
      agentSel.className = 'pc-input';
      const refreshAgents = cid => {
        agentSel.innerHTML = '';
        const none = document.createElement('option');
        none.value = ''; none.textContent = 'Unassigned';
        agentSel.appendChild(none);
        (_agents[cid] || []).forEach(a => {
          const o = document.createElement('option'); o.value = a.id; o.textContent = a.name;
          agentSel.appendChild(o);
        });
      };
      refreshAgents(projSel.value);
      projSel.addEventListener('change', () => refreshAgents(projSel.value));

      const fields = [_mhd('New Issue')];
      if (_companies.length > 1) fields.push(_fg('Project', projSel));
      fields.push(
        _fg('Title', titleIn),
        _fg('Description', descIn),
        _fg('Assignee', agentSel),
        _mact(overlay, async () => {
          if (!titleIn.value.trim()) return;
          const cid = projSel.value || _cid;
          if (!cid) { alert('Select a project first'); return; }
          await _api('POST', `/companies/${cid}/tasks`, {
            title: titleIn.value.trim(), description: descIn.value,
            agent_id: agentSel.value || null,
          });
          _cid = cid; _view = 'project'; overlay.remove(); _reload();
        }, 'Create Issue')
      );
      card.append(...fields);
    }

    overlay.appendChild(card);
    document.body.appendChild(overlay);
    card.querySelector('input, textarea')?.focus();
  }

  function _mhd(t) { const d = _mk('div', 'pc-modal-title'); d.textContent = t; return d; }
  function _fg(label, el) {
    const g = _mk('div', 'pc-fg');
    const l = _mk('div', 'pc-fl'); l.textContent = label;
    g.append(l, el); return g;
  }
  function _mact(overlay, onSave, saveLabel) {
    const row = _mk('div', 'pc-modal-actions');
    const cancel = document.createElement('button');
    cancel.className = 'pc-btn pc-btn-ghost'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => overlay.remove());
    const save = document.createElement('button');
    save.className = 'pc-btn pc-btn-primary'; save.textContent = saveLabel;
    save.addEventListener('click', async () => {
      save.disabled = true; save.textContent = 'Saving…';
      try { await onSave(); }
      catch(e) { alert(e.message); save.disabled = false; save.textContent = saveLabel; }
    });
    row.append(cancel, save); return row;
  }

  // ── DOM helpers ────────────────────────────────────────────────────────────
  function _mk(tag, cls) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }
  function _inp(ph) {
    const i = document.createElement('input');
    i.className = 'pc-input'; i.placeholder = ph; return i;
  }
  function _ta(ph) {
    const t = document.createElement('textarea');
    t.className = 'pc-textarea'; t.placeholder = ph; return t;
  }
  function _esc(s) {
    const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML;
  }
  function _buildAgentMap() {
    const m = {};
    for (const arr of Object.values(_agents)) arr.forEach(a => { m[a.id] = a.name; });
    return m;
  }

  const PALETTE = ['#e06c75','#e5c07b','#98c379','#56b6c2','#61afef','#c678dd','#be5046'];
  function _color(id) {
    let h = 0;
    for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) & 0x7fffffff;
    return PALETTE[h % PALETTE.length];
  }

  // ── Register ───────────────────────────────────────────────────────────────
  const ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>`;

  const pkg = window.OdysseusPkg;
  if (pkg?.registerAppView) {
    pkg.registerAppView('paperclip', {
      icon: ICON, label: 'Paperclip',
      onMount: _mount, onUnmount: _unmount,
    });
  } else {
    console.warn('[paperclip] OdysseusPkg.registerAppView not available');
  }
})();
