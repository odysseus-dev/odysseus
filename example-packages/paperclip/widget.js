/**
 * Paperclip — widget.js
 * Full-page AI team management. CEO agents get company context + management tools.
 * Embedded chat panel: stay in Paperclip while talking to agents.
 * AI responses with ```action JSON blocks are parsed and executed automatically.
 */
(() => {
  const API = '/api/paperclip';

  // ── State ──────────────────────────────────────────────────────────────────
  let _companies = [];
  let _agents  = {};  // { [cid]: agent[] }
  let _tasks   = {};  // { [cid]: task[] }
  let _models  = [];
  let _mcps    = [];
  let _skills  = [];
  let _cid   = null;
  let _aid   = null;
  let _view  = 'dashboard'; // 'dashboard' | 'project' | 'agent' | 'chat'
  let _root  = null;
  let _navEl = null;
  let _mainEl = null;
  let _resourcesLoaded = false;
  // Chat state
  let _chatSession  = null;
  let _chatMessages = []; // [{role,content}]
  let _chatStreaming = false;
  let _chatAbort    = null;

  // ── CSS ────────────────────────────────────────────────────────────────────
  const CSS = `
    .pc-shell { display:flex;flex:1;height:100%;overflow:hidden;background:var(--bg);font-family:var(--font-family,'Fira Code',monospace); }
    .pc-nav { width:220px;min-width:220px;height:100%;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;background:color-mix(in srgb,var(--panel) 70%,var(--bg)); }
    .pc-nav-hdr { padding:13px 12px 11px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border);flex-shrink:0; }
    .pc-nav-logo { width:26px;height:26px;border-radius:6px;background:var(--accent,#63b3ed);display:flex;align-items:center;justify-content:center;color:#000;flex-shrink:0; }
    .pc-nav-brand { font-size:13px;font-weight:700;flex:1; }
    .pc-nav-close { cursor:pointer;opacity:.4;padding:3px;display:flex;align-items:center;border-radius:4px;background:none;border:none;color:var(--fg); }
    .pc-nav-close:hover { opacity:.9;background:color-mix(in srgb,var(--fg) 10%,transparent); }
    .pc-nav-body { flex:1;overflow-y:auto;padding:6px 0 12px; }
    .pc-nav-new { margin:6px 10px 10px;display:flex;align-items:center;gap:6px;padding:6px 10px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;font-family:inherit;background:var(--accent,#63b3ed);color:#000;border:none;width:calc(100% - 20px); }
    .pc-nav-new:hover { opacity:.85; }
    .pc-nav-sep { height:1px;background:var(--border);margin:6px 0; }
    .pc-nav-section { padding:8px 12px 3px;font-size:10px;font-weight:700;opacity:.4;letter-spacing:.07em;text-transform:uppercase; }
    .pc-nav-item { display:flex;align-items:center;gap:8px;padding:5px 12px;cursor:pointer;font-size:12px;user-select:none;transition:background .1s; }
    .pc-nav-item:hover { background:color-mix(in srgb,var(--fg) 6%,transparent); }
    .pc-nav-item.active { background:color-mix(in srgb,var(--accent,#63b3ed) 14%,transparent);font-weight:600; }
    .pc-nav-dot { width:8px;height:8px;border-radius:50%;flex-shrink:0; }
    .pc-nav-badge { margin-left:auto;font-size:10px;opacity:.4;background:color-mix(in srgb,var(--fg) 10%,transparent);padding:1px 5px;border-radius:10px; }
    .pc-nav-add { display:flex;align-items:center;gap:6px;padding:4px 12px;cursor:pointer;font-size:11px;opacity:.4; }
    .pc-nav-add:hover { opacity:.8; }
    .pc-main { flex:1;min-width:0;height:100%;display:flex;flex-direction:column;overflow:hidden; }
    .pc-main-hdr { padding:13px 24px 12px;border-bottom:1px solid var(--border);flex-shrink:0;display:flex;align-items:center;gap:12px; }
    .pc-main-title { font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;flex:1; }
    .pc-main-sub { font-size:11px;opacity:.4;margin-top:2px; }
    .pc-main-body { flex:1;min-height:0;overflow-y:auto;padding:24px; }
    .pc-stats { display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:28px; }
    .pc-stat { background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px 18px; }
    .pc-stat-n { font-size:30px;font-weight:700;line-height:1; }
    .pc-stat-l { font-size:11px;opacity:.5;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:6px; }
    .pc-stat-s { font-size:10px;opacity:.35;margin-top:3px; }
    .pc-section { font-size:11px;font-weight:700;opacity:.45;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px; }
    .pc-two { display:grid;grid-template-columns:1fr 1fr;gap:24px; }
    @media (max-width:860px) { .pc-two { grid-template-columns:1fr; } }
    .pc-task-list { display:flex;flex-direction:column;gap:4px; }
    .pc-task-row { display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;background:var(--panel);border:1px solid var(--border);font-size:12px; }
    .pc-task-row:hover { border-color:color-mix(in srgb,var(--accent,#63b3ed) 45%,var(--border)); }
    .pc-status-btn { background:none;border:none;cursor:pointer;font-size:13px;flex-shrink:0;color:var(--fg);opacity:.55;padding:0;line-height:1;font-family:inherit; }
    .pc-status-btn:hover { opacity:1; }
    .pc-status-ip { color:var(--yellow,#e5c07b);opacity:1!important; }
    .pc-status-done { color:var(--green,#98c379);opacity:1!important; }
    .pc-task-title { flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
    .pc-task-title.done { opacity:.35;text-decoration:line-through; }
    .pc-task-tag { font-size:10px;opacity:.4;background:color-mix(in srgb,var(--fg) 8%,transparent);padding:1px 6px;border-radius:10px;flex-shrink:0; }
    .pc-icon-btn { background:none;border:none;cursor:pointer;opacity:.3;padding:2px 4px;font-size:13px;color:var(--fg);flex-shrink:0;font-family:inherit;line-height:1; }
    .pc-icon-btn:hover { opacity:.9; }
    .pc-agent-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;margin-bottom:24px; }
    .pc-agent-card { background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;flex-direction:column;gap:8px;cursor:pointer;transition:border-color .15s; }
    .pc-agent-card:hover,.pc-agent-card.active { border-color:color-mix(in srgb,var(--accent,#63b3ed) 70%,var(--border)); }
    .pc-agent-card.ceo { border-color:color-mix(in srgb,var(--yellow,#e5c07b) 50%,var(--border)); }
    .pc-avatar { width:36px;height:36px;border-radius:50%;background:color-mix(in srgb,var(--accent,#63b3ed) 18%,var(--panel));display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;flex-shrink:0; }
    .pc-avatar.ceo { background:color-mix(in srgb,var(--yellow,#e5c07b) 25%,var(--panel)); }
    .pc-agent-name { font-size:13px;font-weight:600; }
    .pc-agent-role { font-size:11px;opacity:.45; }
    .pc-agent-model { font-size:10px;opacity:.3;font-style:italic; }
    .pc-agent-chips { display:flex;flex-wrap:wrap;gap:3px;margin-top:2px; }
    .pc-chip { font-size:9px;padding:1px 5px;border-radius:8px;background:color-mix(in srgb,var(--accent,#63b3ed) 12%,transparent);border:1px solid color-mix(in srgb,var(--accent,#63b3ed) 30%,transparent);opacity:.7; }
    .pc-chip.mcp { background:color-mix(in srgb,var(--green,#98c379) 12%,transparent);border-color:color-mix(in srgb,var(--green,#98c379) 30%,transparent); }
    .pc-agent-actions { display:flex;gap:6px;margin-top:auto; }
    .pc-session-dot { width:7px;height:7px;border-radius:50%;background:var(--green,#98c379);flex-shrink:0;animation:pc-pulse 2s infinite; }
    @keyframes pc-pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
    .pc-btn { border:none;border-radius:5px;cursor:pointer;font-size:11px;padding:5px 11px;font-weight:600;font-family:inherit; }
    .pc-btn-primary { background:var(--accent,#63b3ed);color:#000; }
    .pc-btn-primary:hover { opacity:.85; }
    .pc-btn-ghost { background:none;border:1px solid var(--border);color:var(--fg); }
    .pc-btn-ghost:hover { background:color-mix(in srgb,var(--fg) 8%,transparent); }
    .pc-btn-warn { background:none;border:1px solid color-mix(in srgb,var(--yellow,#e5c07b) 40%,transparent);color:var(--yellow,#e5c07b); }
    .pc-btn-warn:hover { background:color-mix(in srgb,var(--yellow,#e5c07b) 10%,transparent); }
    .pc-btn-danger { background:none;border:1px solid color-mix(in srgb,var(--danger,#e55) 40%,transparent);color:var(--danger,#e55); }
    .pc-btn-danger:hover { background:color-mix(in srgb,var(--danger,#e55) 10%,transparent); }
    .pc-btn:disabled { opacity:.45;cursor:not-allowed; }
    .pc-empty { padding:40px 24px;text-align:center;opacity:.35;font-size:13px; }
    .pc-bootstrap { background:color-mix(in srgb,var(--yellow,#e5c07b) 8%,var(--panel));border:1px dashed color-mix(in srgb,var(--yellow,#e5c07b) 40%,var(--border));border-radius:8px;padding:20px 24px;display:flex;align-items:center;gap:16px;margin-bottom:20px; }
    .pc-overlay { position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9900;display:flex;align-items:center;justify-content:center; }
    .pc-modal { background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:24px;width:min(520px,93vw);display:flex;flex-direction:column;gap:14px;max-height:90vh;overflow-y:auto; }
    .pc-modal-title { font-size:14px;font-weight:700; }
    .pc-fg { display:flex;flex-direction:column;gap:4px; }
    .pc-fl { font-size:10px;opacity:.55;font-weight:700;text-transform:uppercase;letter-spacing:.05em; }
    .pc-input { background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:7px 10px;font-size:13px;color:var(--fg);width:100%;box-sizing:border-box;font-family:inherit; }
    .pc-input:focus { outline:none;border-color:var(--accent,#63b3ed); }
    .pc-textarea { background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:7px 10px;font-size:12px;color:var(--fg);width:100%;box-sizing:border-box;font-family:inherit;resize:vertical;min-height:80px; }
    .pc-textarea:focus { outline:none;border-color:var(--accent,#63b3ed); }
    .pc-check-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:4px; }
    .pc-check-item { display:flex;align-items:center;gap:5px;padding:3px 6px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid transparent; }
    .pc-check-item:hover { background:color-mix(in srgb,var(--fg) 5%,transparent); }
    .pc-check-item input { cursor:pointer;flex-shrink:0; }
    .pc-modal-actions { display:flex;gap:8px;justify-content:flex-end;margin-top:4px; }
    .pc-info-card { background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:18px;margin-bottom:24px; }
    .pc-mem-badge { font-size:10px;padding:1px 6px;border-radius:8px;background:color-mix(in srgb,var(--accent,#63b3ed) 15%,transparent);border:1px solid color-mix(in srgb,var(--accent,#63b3ed) 30%,transparent);cursor:pointer; }
    .pc-mem-badge:hover { opacity:.7; }
    /* ── Chat panel ─────────────────────────────────────────────────────────── */
    .pc-chat { display:flex;flex-direction:column;flex:1;height:100%;overflow:hidden; }
    .pc-chat-hdr { padding:10px 16px;border-bottom:1px solid var(--border);flex-shrink:0;display:flex;align-items:center;gap:10px; }
    .pc-chat-msgs { flex:1;min-height:0;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px; }
    .pc-msg { display:flex;gap:8px;max-width:88%; }
    .pc-msg.user { flex-direction:row-reverse;align-self:flex-end; }
    .pc-msg-bubble { padding:8px 12px;border-radius:10px;font-size:12px;line-height:1.6;word-break:break-word;white-space:pre-wrap; }
    .pc-msg.user .pc-msg-bubble { background:var(--accent,#63b3ed);color:#000;border-bottom-right-radius:3px; }
    .pc-msg.assistant .pc-msg-bubble { background:var(--panel);border:1px solid var(--border);border-bottom-left-radius:3px; }
    .pc-msg-av { width:26px;height:26px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;background:color-mix(in srgb,var(--accent,#63b3ed) 18%,var(--panel));align-self:flex-end; }
    .pc-msg-av.ceo { background:color-mix(in srgb,var(--yellow,#e5c07b) 25%,var(--panel)); }
    .pc-action-toast { background:color-mix(in srgb,var(--green,#98c379) 12%,var(--panel));border:1px solid color-mix(in srgb,var(--green,#98c379) 40%,var(--border));border-radius:6px;padding:6px 10px;font-size:11px;color:var(--green,#98c379);display:flex;gap:6px;align-items:center;margin-top:4px; }
    .pc-action-err { background:color-mix(in srgb,var(--danger,#e55) 10%,var(--panel));border:1px solid color-mix(in srgb,var(--danger,#e55) 30%,var(--border));border-radius:6px;padding:6px 10px;font-size:11px;color:var(--danger,#e55);margin-top:4px; }
    .pc-chat-input-row { padding:10px 16px;border-top:1px solid var(--border);flex-shrink:0;display:flex;gap:8px;align-items:flex-end; }
    .pc-chat-ta { flex:1;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:8px 11px;font-size:12px;color:var(--fg);font-family:inherit;resize:none;min-height:38px;max-height:140px;line-height:1.5; }
    .pc-chat-ta:focus { outline:none;border-color:var(--accent,#63b3ed); }
    .pc-action-block { background:color-mix(in srgb,var(--yellow,#e5c07b) 6%,var(--panel));border:1px solid color-mix(in srgb,var(--yellow,#e5c07b) 25%,var(--border));border-radius:6px;padding:8px 10px;font-size:11px;font-family:monospace;color:var(--fg);opacity:.8;white-space:pre-wrap;margin-top:6px; }
    .pc-typing { display:flex;gap:4px;align-items:center;padding:4px 0; }
    .pc-typing span { width:6px;height:6px;border-radius:50%;background:var(--fg);opacity:.4;animation:pc-bounce 1.2s infinite; }
    .pc-typing span:nth-child(2) { animation-delay:.2s; }
    .pc-typing span:nth-child(3) { animation-delay:.4s; }
    @keyframes pc-bounce { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-5px)} }
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

  // ── Load data ──────────────────────────────────────────────────────────────
  async function _loadResources() {
    if (_resourcesLoaded) return;
    try {
      const [md, mp, sk] = await Promise.all([
        _api('GET', '/available-models'),
        _api('GET', '/available-mcps'),
        _api('GET', '/available-skills'),
      ]);
      _models = md.models || [];
      _mcps   = mp.mcps   || [];
      _skills = sk.skills || [];
      _resourcesLoaded = true;
    } catch (e) {
      console.warn('[paperclip] resources load failed:', e);
    }
  }

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
    _navEl  = document.createElement('div'); _navEl.className  = 'pc-nav';
    _mainEl = document.createElement('div'); _mainEl.className = 'pc-main';
    shell.append(_navEl, _mainEl);
    _root.appendChild(shell);
    _loadResources().then(() => _reload());
  }

  function _unmount() {
    if (_chatAbort) { _chatAbort.abort(); _chatAbort = null; }
    _root = null; _navEl = null; _mainEl = null;
  }

  function _renderAll() {
    if (!_navEl || !_mainEl) return;
    _renderNav(); _renderMain();
  }

  // ── Left nav ───────────────────────────────────────────────────────────────
  function _renderNav() {
    _navEl.innerHTML = '';

    const hdr = _mk('div', 'pc-nav-hdr');
    hdr.innerHTML = `<div class="pc-nav-logo"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></div><span class="pc-nav-brand">Paperclip</span>`;
    const closeBtn = document.createElement('button');
    closeBtn.className = 'pc-nav-close'; closeBtn.title = 'Back to chats';
    closeBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
    closeBtn.addEventListener('click', () => document.getElementById('pkg-nav-paperclip')?._pkgClose?.());
    hdr.appendChild(closeBtn);
    _navEl.appendChild(hdr);

    const body = _mk('div', 'pc-nav-body');

    const newBtn = document.createElement('button');
    newBtn.className = 'pc-nav-new';
    newBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> New Issue`;
    newBtn.addEventListener('click', () => _modal('task'));
    body.appendChild(newBtn);

    body.appendChild(_navRow(`<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>`,
      'Dashboard', _view === 'dashboard', () => { _view = 'dashboard'; _aid = null; _renderAll(); }));
    body.appendChild(_navRow(`<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
      'Inbox', false, null));

    body.appendChild(_mk('div', 'pc-nav-sep'));

    if (_companies.length > 0) {
      const sec = _mk('div', 'pc-nav-section'); sec.textContent = 'Projects'; body.appendChild(sec);
      _companies.forEach(c => {
        const item = document.createElement('div');
        item.className = 'pc-nav-item' + (_cid === c.id && _view !== 'dashboard' ? ' active' : '');
        item.dataset.cid = c.id;
        item.innerHTML = `<div class="pc-nav-dot" style="background:${_color(c.id)}"></div><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(c.name)}</span><span class="pc-nav-badge">${(_agents[c.id] || []).length}</span>`;
        item.addEventListener('click', () => {
          if (_chatAbort) { _chatAbort.abort(); _chatAbort = null; }
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
      const sec2 = _mk('div', 'pc-nav-section'); sec2.textContent = 'Team'; body.appendChild(sec2);
      (_agents[_cid] || []).forEach(a => {
        const isChatting = _view === 'chat' && _aid === a.id;
        const item = document.createElement('div');
        item.className = 'pc-nav-item' + ((_aid === a.id && _view !== 'chat') || isChatting ? ' active' : '');
        const icon = a.is_ceo
          ? `<span style="font-size:12px">👑</span>`
          : `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" opacity=".5"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>`;
        const dot = a.active_session_id
          ? `<div class="pc-session-dot" title="Active chat"></div>`
          : (isChatting ? `<span style="font-size:10px">💬</span>` : '');
        item.innerHTML = `${icon}<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(a.name)}</span>${dot}${a.role && !isChatting ? `<span class="pc-nav-badge">${_esc(a.role.slice(0,8))}</span>` : ''}`;
        item.addEventListener('click', () => { _aid = a.id; _view = 'agent'; _renderAll(); });
        body.appendChild(item);
      });
      const addAgent = _mk('div', 'pc-nav-add');
      addAgent.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Hire Agent`;
      addAgent.addEventListener('click', () => _modal('agent'));
      body.appendChild(addAgent);
    }

    _navEl.appendChild(body);
  }

  function _navRow(icon, label, active, onClick) {
    const item = document.createElement('div');
    item.className = 'pc-nav-item' + (active ? ' active' : '');
    item.innerHTML = `${icon}<span style="flex:1">${label}</span>`;
    if (onClick) item.addEventListener('click', onClick);
    return item;
  }

  // ── Main ───────────────────────────────────────────────────────────────────
  function _renderMain() {
    _mainEl.innerHTML = '';
    if (_view === 'chat' && _aid && _chatSession) { _renderChat(); return; }
    if (_aid)                        { _renderAgent();   return; }
    if (_view === 'project' && _cid) { _renderProject(); return; }
    _renderDashboard();
  }

  // ── Dashboard ──────────────────────────────────────────────────────────────
  function _renderDashboard() {
    const all = Object.values(_agents).flat();
    const allT = Object.values(_tasks).flat();
    const ip   = allT.filter(t => t.status === 'in-progress').length;
    const done = allT.filter(t => t.status === 'done').length;

    const hdr = _mk('div', 'pc-main-hdr');
    hdr.innerHTML = `<div><div class="pc-main-title">Dashboard</div><div class="pc-main-sub">Paperclip</div></div>`;
    _mainEl.appendChild(hdr);

    const body = _mk('div', 'pc-main-body');

    const stats = _mk('div', 'pc-stats');
    stats.innerHTML = `
      <div class="pc-stat"><div class="pc-stat-n">${all.length}</div><div class="pc-stat-l">Agents</div><div class="pc-stat-s">${_companies.length} project${_companies.length !== 1 ? 's' : ''}</div></div>
      <div class="pc-stat"><div class="pc-stat-n" style="color:var(--yellow,#e5c07b)">${ip}</div><div class="pc-stat-l">In Progress</div><div class="pc-stat-s">${allT.filter(t=>t.status==='todo').length} open</div></div>
      <div class="pc-stat"><div class="pc-stat-n" style="color:var(--green,#98c379)">${done}</div><div class="pc-stat-l">Completed</div><div class="pc-stat-s">${allT.length > 0 ? Math.round(done/allT.length*100) : 0}% of ${allT.length}</div></div>
      <div class="pc-stat"><div class="pc-stat-n">${_companies.length}</div><div class="pc-stat-l">Projects</div><div class="pc-stat-s">${all.filter(a=>a.active_session_id).length} active chats</div></div>
    `;
    body.appendChild(stats);

    if (!_companies.length) {
      const e = _mk('div', 'pc-empty');
      e.style.padding = '48px 24px';
      e.innerHTML = `<div style="font-size:36px;margin-bottom:14px;opacity:.2">⬡</div><div style="font-size:15px;font-weight:700;margin-bottom:8px;opacity:.7">No projects yet</div><div style="font-size:12px;margin-bottom:20px">Create a project and bootstrap it with a CEO & CTO to get started.</div>`;
      const btn = document.createElement('button');
      btn.className = 'pc-btn pc-btn-primary'; btn.textContent = '+ New Project';
      btn.addEventListener('click', () => _modal('company'));
      e.appendChild(btn);
      body.appendChild(e);
    } else {
      const two = _mk('div', 'pc-two');

      const tasksCol = document.createElement('div');
      const t1 = _mk('div', 'pc-section'); t1.textContent = 'Recent Tasks'; tasksCol.appendChild(t1);
      const recent = allT.slice(-15).reverse();
      if (!recent.length) {
        const e = _mk('div', 'pc-empty'); e.textContent = 'No tasks yet'; tasksCol.appendChild(e);
      } else {
        const list = _mk('div', 'pc-task-list');
        const am = _buildAgentMap();
        recent.forEach(t => list.appendChild(_taskRow(t, am)));
        tasksCol.appendChild(list);
      }
      two.appendChild(tasksCol);

      const agentsCol = document.createElement('div');
      const t2 = _mk('div', 'pc-section'); t2.textContent = 'Team'; agentsCol.appendChild(t2);
      if (!all.length) {
        const e = _mk('div', 'pc-empty'); e.textContent = 'No agents yet'; agentsCol.appendChild(e);
      } else {
        const list = _mk('div', 'pc-task-list');
        all.slice(0, 10).forEach(a => {
          const co = _companies.find(c => c.id === a.company_id);
          const row = _mk('div', 'pc-task-row');
          row.style.cursor = 'pointer';
          row.innerHTML = `
            <div class="pc-avatar${a.is_ceo ? ' ceo' : ''}" style="width:28px;height:28px;font-size:12px">${a.is_ceo ? '👑' : a.name[0].toUpperCase()}</div>
            <div style="flex:1;min-width:0">
              <div style="font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(a.name)}</div>
              <div style="font-size:10px;opacity:.38">${_esc(a.role||'Agent')}${co?' · '+_esc(co.name):''}</div>
            </div>
            ${a.active_session_id ? '<div class="pc-session-dot"></div>' : ''}
          `;
          const chatBtn = document.createElement('button');
          chatBtn.className = 'pc-btn pc-btn-primary';
          chatBtn.style.cssText = 'font-size:10px;padding:3px 8px';
          chatBtn.textContent = a.active_session_id ? '▶ Continue' : '▶ Chat';
          chatBtn.addEventListener('click', async e => { e.stopPropagation(); await _openChat(a); });
          row.appendChild(chatBtn);
          row.addEventListener('click', () => { _cid = a.company_id; _aid = a.id; _view = 'agent'; _renderAll(); });
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

    if (!agents.length) {
      const banner = _mk('div', 'pc-bootstrap');
      banner.innerHTML = `
        <div style="font-size:28px">🏢</div>
        <div style="flex:1">
          <div style="font-size:13px;font-weight:700;margin-bottom:4px">Bootstrap your team</div>
          <div style="font-size:12px;opacity:.6">Automatically create a CEO and CTO with pre-configured system prompts and company context.</div>
        </div>
      `;
      const bBtn = document.createElement('button');
      bBtn.className = 'pc-btn pc-btn-warn'; bBtn.textContent = '⚡ Bootstrap CEO + CTO';
      bBtn.addEventListener('click', async () => {
        bBtn.disabled = true; bBtn.textContent = 'Creating…';
        try {
          await _api('POST', `/companies/${_cid}/bootstrap`);
          await _reload();
        } catch (e) { alert(e.message); bBtn.disabled = false; bBtn.textContent = '⚡ Bootstrap CEO + CTO'; }
      });
      banner.appendChild(bBtn);
      body.appendChild(banner);
    }

    const aSec = _mk('div', 'pc-section');
    aSec.textContent = `Team (${agents.length})`;
    body.appendChild(aSec);

    if (agents.length) {
      const grid = _mk('div', 'pc-agent-grid');
      [...agents].sort((a, b) => (b.is_ceo ? 1 : 0) - (a.is_ceo ? 1 : 0)).forEach(a => {
        const card = document.createElement('div');
        card.className = 'pc-agent-card' + (a.is_ceo ? ' ceo' : '') + (_aid === a.id ? ' active' : '');

        const top = document.createElement('div');
        top.style.cssText = 'display:flex;gap:10px;align-items:flex-start';

        const av = _mk('div', `pc-avatar${a.is_ceo ? ' ceo' : ''}`);
        av.textContent = a.is_ceo ? '👑' : a.name[0].toUpperCase();
        top.appendChild(av);

        const info = document.createElement('div');
        info.style.flex = '1';
        info.innerHTML = `<div class="pc-agent-name">${_esc(a.name)}${a.is_ceo ? ' <span style="font-size:9px;opacity:.5;font-weight:400">CEO</span>' : ''}</div><div class="pc-agent-role">${_esc(a.role||'Agent')}</div>${a.model?`<div class="pc-agent-model">${_esc(a.model)}</div>`:''}`;

        if ((a.skills||[]).length || (a.mcps||[]).length) {
          const chips = _mk('div', 'pc-agent-chips');
          (a.skills||[]).slice(0,3).forEach(s => {
            const c = _mk('span', 'pc-chip'); c.textContent = s.split('/').pop().replace(/-/g,' '); chips.appendChild(c);
          });
          (a.mcps||[]).slice(0,2).forEach(m => {
            const c = _mk('span', 'pc-chip mcp'); c.textContent = m.split('/').pop(); chips.appendChild(c);
          });
          info.appendChild(chips);
        }

        top.appendChild(info);
        card.appendChild(top);

        const cardActs = _mk('div', 'pc-agent-actions');

        const chatBtn = document.createElement('button');
        chatBtn.className = 'pc-btn pc-btn-primary';
        chatBtn.innerHTML = a.active_session_id
          ? `<div class="pc-session-dot" style="width:6px;height:6px;animation:none;opacity:.8;margin-right:4px;display:inline-block"></div> Continue`
          : '💬 Chat';
        chatBtn.addEventListener('click', async e => { e.stopPropagation(); await _openChat(a); });

        const editBtn = document.createElement('button');
        editBtn.className = 'pc-btn pc-btn-ghost'; editBtn.textContent = '✎';
        editBtn.title = 'Edit agent';
        editBtn.addEventListener('click', async e => { e.stopPropagation(); await _editAgent(a); });

        const xBtn = document.createElement('button');
        xBtn.className = 'pc-btn pc-btn-ghost'; xBtn.textContent = '×';
        xBtn.style.marginLeft = 'auto';
        xBtn.addEventListener('click', async e => {
          e.stopPropagation();
          if (!confirm(`Delete agent "${a.name}"?`)) return;
          await _api('DELETE', `/agents/${a.id}`); _reload();
        });

        cardActs.append(chatBtn, editBtn, xBtn);
        card.appendChild(cardActs);
        card.addEventListener('click', () => { _aid = a.id; _view = 'agent'; _renderAll(); });
        grid.appendChild(card);
      });
      body.appendChild(grid);
    }

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
    back.className = 'pc-btn pc-btn-ghost'; back.style.padding = '4px 8px';
    back.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>`;
    back.addEventListener('click', () => { _aid = null; _view = 'project'; _renderAll(); });

    const titleDiv = document.createElement('div');
    titleDiv.style.flex = '1';
    titleDiv.innerHTML = `<div class="pc-main-title">${agent.is_ceo ? '👑 ' : ''}${_esc(agent.name)}</div><div class="pc-main-sub">${_esc(agent.role||'Agent')}</div>`;

    const chatBtn = document.createElement('button');
    chatBtn.className = 'pc-btn pc-btn-primary';
    chatBtn.textContent = agent.active_session_id ? '💬 Continue Chat' : '💬 Start Chat';
    chatBtn.addEventListener('click', async () => await _openChat(agent));

    const editBtn = document.createElement('button');
    editBtn.className = 'pc-btn pc-btn-ghost'; editBtn.textContent = '✎ Edit';
    editBtn.addEventListener('click', async () => await _editAgent(agent));

    hdr.append(back, titleDiv, editBtn, chatBtn);
    _mainEl.appendChild(hdr);

    const body = _mk('div', 'pc-main-body');

    const info = _mk('div', 'pc-info-card');
    let infoHtml = `
      <div style="display:flex;gap:16px;align-items:flex-start">
        <div class="pc-avatar${agent.is_ceo?' ceo':''}" style="width:48px;height:48px;font-size:20px;flex-shrink:0">${agent.is_ceo?'👑':agent.name[0].toUpperCase()}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:16px;font-weight:700">${_esc(agent.name)}</div>
          <div style="font-size:12px;opacity:.45;margin-top:3px">${_esc(agent.role||'Agent')}</div>
          ${agent.model?`<div style="font-size:11px;opacity:.3;font-style:italic;margin-top:4px">${_esc(agent.model)}</div>`:''}
        </div>
      </div>
    `;
    if (agent.system_prompt) {
      infoHtml += `<div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)"><div class="pc-section" style="margin-bottom:6px">System Prompt</div><div style="font-size:12px;opacity:.55;white-space:pre-wrap;max-height:130px;overflow-y:auto;line-height:1.6">${_esc(agent.system_prompt)}</div></div>`;
    }
    if ((agent.skills||[]).length) {
      infoHtml += `<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border)"><div class="pc-section" style="margin-bottom:6px">Skills</div><div class="pc-agent-chips">${(agent.skills||[]).map(s=>`<span class="pc-chip">${_esc(s.split('/').pop().replace(/-/g,' '))}</span>`).join('')}</div></div>`;
    }
    if ((agent.mcps||[]).length) {
      infoHtml += `<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border)"><div class="pc-section" style="margin-bottom:6px">MCP Tools</div><div class="pc-agent-chips">${(agent.mcps||[]).map(m=>`<span class="pc-chip mcp">${_esc(m)}</span>`).join('')}</div></div>`;
    }
    info.innerHTML = infoHtml;
    body.appendChild(info);

    const memSec = _mk('div', 'pc-section');
    memSec.innerHTML = `Memory <span class="pc-mem-badge" title="Edit memory">✎</span>`;
    memSec.querySelector('.pc-mem-badge').addEventListener('click', () => _editMemory(agent));
    body.appendChild(memSec);

    if (agent.memory) {
      const memBox = _mk('div', 'pc-info-card');
      memBox.style.cssText = 'margin-bottom:20px;font-size:12px;opacity:.7;white-space:pre-wrap;line-height:1.6';
      memBox.textContent = agent.memory;
      body.appendChild(memBox);
    } else {
      const noMem = _mk('div', '');
      noMem.style.cssText = 'font-size:11px;opacity:.3;margin-bottom:20px;padding:8px 0';
      noMem.textContent = 'No memory stored. Click ✎ to add persistent context for this agent.';
      body.appendChild(noMem);
    }

    const tSec = _mk('div', 'pc-section');
    tSec.textContent = `Assigned Tasks (${tasks.length})`;
    body.appendChild(tSec);

    if (!tasks.length) {
      const e = _mk('div', 'pc-empty'); e.textContent = 'No tasks assigned'; body.appendChild(e);
    } else {
      const list = _mk('div', 'pc-task-list');
      tasks.forEach(t => list.appendChild(_taskRow(t)));
      body.appendChild(list);
    }

    _mainEl.appendChild(body);
  }

  // ── EMBEDDED CHAT ──────────────────────────────────────────────────────────
  async function _openChat(agent) {
    if (_chatAbort) { _chatAbort.abort(); _chatAbort = null; }
    _chatStreaming = false;
    _chatSession = null;
    _chatMessages = [];

    // Get/create session
    try {
      const d = await _api('POST', `/agents/${agent.id}/run`);
      _chatSession = d.session_id;
      _aid = agent.id;
      _cid = agent.company_id;
    } catch (e) {
      alert('Could not start agent: ' + e.message); return;
    }

    // Load history
    try {
      const hist = await fetch(`/api/history/${_chatSession}`).then(r => r.json());
      const msgs = hist.messages || hist.history || [];
      _chatMessages = msgs.filter(m => m.role !== 'system' && !m.metadata?.hidden);
    } catch (e) { _chatMessages = []; }

    _view = 'chat';
    _renderAll();
  }

  function _renderChat() {
    const agent = (_agents[_cid] || []).find(a => a.id === _aid);
    if (!agent) { _view = 'project'; _renderMain(); return; }

    const chat = _mk('div', 'pc-chat');

    // Header
    const hdr = _mk('div', 'pc-chat-hdr');
    const back = document.createElement('button');
    back.className = 'pc-btn pc-btn-ghost'; back.style.padding = '4px 8px';
    back.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>`;
    back.addEventListener('click', () => {
      if (_chatAbort) { _chatAbort.abort(); _chatAbort = null; }
      _chatStreaming = false;
      _view = 'project'; _renderAll();
    });
    const agentInfo = _mk('div', '');
    agentInfo.style.flex = '1';
    agentInfo.innerHTML = `<span style="font-size:13px;font-weight:700">${agent.is_ceo?'👑 ':''}${_esc(agent.name)}</span><span style="font-size:11px;opacity:.4;margin-left:8px">${_esc(agent.role||'')}</span>`;
    const helpBtn = _mk('div', '');
    helpBtn.style.cssText = 'font-size:10px;opacity:.35;cursor:pointer;max-width:280px;text-align:right;line-height:1.4';
    helpBtn.innerHTML = `Tip: CEO/CTO can hire agents, assign tasks, set models.<br>Actions are executed automatically.`;
    hdr.append(back, agentInfo, helpBtn);
    chat.appendChild(hdr);

    // Messages area
    const msgsEl = _mk('div', 'pc-chat-msgs');
    msgsEl.id = 'pc-chat-msgs';

    _chatMessages.forEach(m => {
      msgsEl.appendChild(_msgBubble(m.role, m.content, agent));
    });

    // Typing indicator placeholder
    const typingEl = _mk('div', '');
    typingEl.id = 'pc-chat-typing';
    msgsEl.appendChild(typingEl);

    chat.appendChild(msgsEl);

    // Input row
    const inputRow = _mk('div', 'pc-chat-input-row');
    const ta = document.createElement('textarea');
    ta.className = 'pc-chat-ta';
    ta.placeholder = `Message ${agent.name}…`;
    ta.rows = 1;
    ta.addEventListener('input', () => {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
    });
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendBtn.click();
      }
    });

    const sendBtn = document.createElement('button');
    sendBtn.className = 'pc-btn pc-btn-primary';
    sendBtn.style.cssText = 'padding:8px 14px;align-self:flex-end';
    sendBtn.textContent = 'Send';
    sendBtn.addEventListener('click', async () => {
      const text = ta.value.trim();
      if (!text || _chatStreaming) return;
      ta.value = ''; ta.style.height = 'auto';
      await _sendMsg(text, agent, msgsEl, typingEl);
    });

    inputRow.append(ta, sendBtn);
    chat.appendChild(inputRow);

    _mainEl.appendChild(chat);

    // Scroll to bottom
    requestAnimationFrame(() => { msgsEl.scrollTop = msgsEl.scrollHeight; });
  }

  function _msgBubble(role, content, agent) {
    const isUser = role === 'user';
    const wrap = _mk('div', `pc-msg ${isUser ? 'user' : 'assistant'}`);

    const av = _mk('div', `pc-msg-av${agent?.is_ceo && !isUser ? ' ceo' : ''}`);
    av.textContent = isUser ? 'U' : (agent?.is_ceo ? '👑' : (agent?.name?.[0]?.toUpperCase() || 'A'));

    // Render content: strip action blocks from display, show as separate element
    const { display, actions } = _splitContent(content);

    const bubble = _mk('div', 'pc-msg-bubble');
    bubble.textContent = display;

    wrap.append(isUser ? bubble : av, isUser ? av : bubble);

    // Show action blocks as annotations
    if (!isUser && actions.length) {
      const actWrap = _mk('div', '');
      actWrap.style.cssText = 'padding-left:34px;display:flex;flex-direction:column;gap:4px';
      actions.forEach(({ raw }) => {
        const blk = _mk('div', 'pc-action-block');
        blk.textContent = raw;
        actWrap.appendChild(blk);
      });
      const outer = _mk('div', '');
      outer.style.cssText = 'display:flex;flex-direction:column';
      outer.appendChild(wrap);
      outer.appendChild(actWrap);
      return outer;
    }
    return wrap;
  }

  async function _sendMsg(text, agent, msgsEl, typingEl) {
    if (!_chatSession) return;
    _chatStreaming = true;

    // Add user bubble
    const userBubble = _msgBubble('user', text, agent);
    msgsEl.insertBefore(userBubble, typingEl);
    _scrollBottom(msgsEl);

    // Show typing indicator
    typingEl.innerHTML = `
      <div class="pc-msg assistant" style="align-self:flex-start">
        <div class="pc-msg-av${agent.is_ceo?' ceo':''}">${agent.is_ceo?'👑':agent.name[0].toUpperCase()}</div>
        <div class="pc-msg-bubble"><div class="pc-typing"><span></span><span></span><span></span></div></div>
      </div>`;
    _scrollBottom(msgsEl);

    // Create streaming bubble
    const streamBubble = _mk('div', 'pc-msg-bubble');
    const streamWrap = _mk('div', 'pc-msg assistant');
    streamWrap.style.alignSelf = 'flex-start';
    const streamAv = _mk('div', `pc-msg-av${agent.is_ceo?' ceo':''}`);
    streamAv.textContent = agent.is_ceo ? '👑' : agent.name[0].toUpperCase();
    streamWrap.append(streamAv, streamBubble);

    let fullText = '';

    try {
      const fd = new FormData();
      fd.append('message', text);
      fd.append('session', _chatSession);

      _chatAbort = new AbortController();
      const res = await fetch('/api/chat_stream', {
        method: 'POST', body: fd,
        signal: _chatAbort.signal,
      });

      if (!res.ok) {
        throw new Error(`Stream error ${res.status}`);
      }

      typingEl.innerHTML = '';
      msgsEl.insertBefore(streamWrap, typingEl);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() || '';
        for (const part of parts) {
          const lines = part.split('\n');
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const json = JSON.parse(line.slice(6));
              if (json.delta) {
                fullText += json.delta;
                streamBubble.textContent = fullText;
                _scrollBottom(msgsEl);
              }
            } catch (e) {}
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') { _chatStreaming = false; return; }
      typingEl.innerHTML = '';
      const errEl = _mk('div', 'pc-action-err');
      errEl.textContent = 'Error: ' + e.message;
      msgsEl.insertBefore(errEl, typingEl);
    } finally {
      _chatAbort = null;
      _chatStreaming = false;
    }

    // Save to local messages
    _chatMessages.push({ role: 'user', content: text });
    _chatMessages.push({ role: 'assistant', content: fullText });

    // Parse and execute actions
    if (fullText) {
      const actions = _parseActions(fullText);
      if (actions.length) {
        // Replace the plain text bubble with annotated one
        const { display } = _splitContent(fullText);
        streamBubble.textContent = display;

        const actWrap = _mk('div', '');
        actWrap.style.cssText = 'padding-left:34px;display:flex;flex-direction:column;gap:6px';

        for (const act of actions) {
          const toast = _mk('div', 'pc-action-toast');
          toast.innerHTML = `<span style="font-size:13px">⚙</span> Executing: <strong>${act.action}</strong>…`;
          actWrap.appendChild(toast);
          msgsEl.insertBefore(actWrap, typingEl);
          _scrollBottom(msgsEl);

          try {
            const result = await _execAction(act);
            toast.innerHTML = `<span style="font-size:13px">✅</span> ${result}`;
            await _reload();
          } catch (err) {
            const errEl = _mk('div', 'pc-action-err');
            errEl.textContent = `❌ ${act.action} failed: ${err.message}`;
            actWrap.appendChild(errEl);
          }
        }
        // Re-render nav to show new agents
        _renderNav();
      }
    }

    _scrollBottom(msgsEl);
  }

  // ── Action parsing ─────────────────────────────────────────────────────────
  function _parseActions(text) {
    const results = [];
    const re = /```action\s*\n([\s\S]*?)\n```/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      try {
        const obj = JSON.parse(m[1].trim());
        results.push({ ...obj, _raw: m[0] });
      } catch (e) {}
    }
    return results;
  }

  function _splitContent(text) {
    const re = /```action\s*\n([\s\S]*?)\n```/g;
    const actions = [];
    let m;
    while ((m = re.exec(text)) !== null) {
      try { actions.push({ raw: m[1].trim(), parsed: JSON.parse(m[1].trim()) }); } catch(e) {}
    }
    const display = text.replace(/```action\s*\n[\s\S]*?\n```/g, '').trim();
    return { display, actions };
  }

  // ── Action execution ───────────────────────────────────────────────────────
  async function _execAction(act) {
    const cid = _cid || _companies[0]?.id;
    if (!cid) throw new Error('No active project');
    const agents = _agents[cid] || [];

    switch ((act.action || '').toLowerCase()) {

      case 'hire':
      case 'hire_agent': {
        if (!act.name) throw new Error('name required');
        await _api('POST', `/companies/${cid}/agents`, {
          name: act.name,
          role: act.role || '',
          model: act.model || '',
          system_prompt: act.system_prompt || '',
          is_ceo: !!act.is_ceo,
          skills: act.skills || [],
          mcps: act.mcps || [],
        });
        return `Hired ${act.name} as ${act.role || 'Agent'}`;
      }

      case 'assign_task':
      case 'create_task': {
        if (!act.title) throw new Error('title required');
        const assignee = act.assignee
          ? agents.find(a => a.name.toLowerCase() === String(act.assignee).toLowerCase() || a.id === act.assignee)
          : null;
        await _api('POST', `/companies/${cid}/tasks`, {
          title: act.title,
          description: act.description || '',
          agent_id: assignee?.id || null,
        });
        return `Task created: "${act.title}"${assignee ? ` → ${assignee.name}` : ''}`;
      }

      case 'set_model': {
        const target = act.agent
          ? agents.find(a => a.name.toLowerCase() === String(act.agent).toLowerCase() || a.id === act.agent)
          : (_agents[cid] || []).find(a => a.id === _aid);
        if (!target) throw new Error(`Agent "${act.agent}" not found`);
        await _api('PUT', `/agents/${target.id}`, { model: act.model });
        return `Set ${target.name} model to ${act.model}`;
      }

      case 'update_memory': {
        const target = act.agent
          ? agents.find(a => a.name.toLowerCase() === String(act.agent).toLowerCase() || a.id === act.agent)
          : (_agents[cid] || []).find(a => a.id === _aid);
        if (!target) throw new Error(`Agent "${act.agent}" not found`);
        const existing = target.memory || '';
        const newMem = existing ? existing + '\n\n' + act.memory : act.memory;
        await _api('PUT', `/agents/${target.id}`, { memory: newMem });
        return `Memory updated for ${target.name}`;
      }

      default:
        throw new Error(`Unknown action: ${act.action}`);
    }
  }

  function _scrollBottom(el) {
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  }

  // ── Edit memory inline ─────────────────────────────────────────────────────
  async function _editMemory(agent) {
    document.getElementById('pc-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pc-overlay'; overlay.id = 'pc-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    const card = _mk('div', 'pc-modal');
    const ta = _ta(`Persistent context/memory for ${agent.name}…`);
    ta.value = agent.memory || '';
    ta.style.minHeight = '160px';

    const note = _mk('div', '');
    note.style.cssText = 'font-size:11px;opacity:.45;line-height:1.5';
    note.textContent = 'This memory is injected into every chat session for this agent. Use it for persistent facts, preferences, or background context.';

    card.append(
      _mhd(`Memory — ${agent.name}`),
      note,
      _fg('Content', ta),
      _mact(overlay, async () => {
        await _api('PUT', `/agents/${agent.id}`, { memory: ta.value });
        overlay.remove();
        _reload();
      }, 'Save Memory')
    );
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    ta.focus();
  }

  // ── Edit agent ─────────────────────────────────────────────────────────────
  async function _editAgent(agent) {
    await _loadResources();
    document.getElementById('pc-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pc-overlay'; overlay.id = 'pc-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    const card = _mk('div', 'pc-modal');
    const nameIn  = _inp('Agent name');     nameIn.value  = agent.name;
    const roleIn  = _inp('Role');           roleIn.value  = agent.role || '';
    const modelSel = _modelSelect(agent.model || '');
    const sysIn   = _ta('System prompt');   sysIn.value   = agent.system_prompt || '';
    sysIn.style.minHeight = '100px';

    const ceoToggle = document.createElement('input');
    ceoToggle.type = 'checkbox'; ceoToggle.checked = !!agent.is_ceo;

    const ceoWrap = document.createElement('label');
    ceoWrap.style.cssText = 'display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;padding:2px 0';
    ceoWrap.append(ceoToggle, 'CEO / Manager role');

    card.append(
      _mhd(`Edit — ${agent.name}`),
      _fg('Name', nameIn),
      _fg('Role', roleIn),
      _fg('Model', modelSel),
      _fg('System Prompt', sysIn),
      _fg('', ceoWrap),
    );

    if (_skills.length) {
      card.appendChild(_checkSection('Skills', _skills, agent.skills || [], 'pc-check-skills'));
    }
    if (_mcps.length) {
      card.appendChild(_checkSection('MCP Tools', _mcps.map(m => ({ id: m.id, name: m.name })), agent.mcps || [], 'pc-check-mcps'));
    }

    card.appendChild(_mact(overlay, async () => {
      const selectedSkills = [...card.querySelectorAll('.pc-check-skills input:checked')].map(el => el.value);
      const selectedMcps   = [...card.querySelectorAll('.pc-check-mcps  input:checked')].map(el => el.value);
      await _api('PUT', `/agents/${agent.id}`, {
        name: nameIn.value.trim() || agent.name,
        role: roleIn.value,
        model: modelSel.value,
        system_prompt: sysIn.value,
        is_ceo: ceoToggle.checked,
        skills: selectedSkills,
        mcps: selectedMcps,
      });
      overlay.remove();
      _reload();
    }, 'Save Changes'));

    overlay.appendChild(card);
    document.body.appendChild(overlay);
    nameIn.focus();
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
      await _api('DELETE', `/tasks/${task.id}`); _reload();
    });
    row.appendChild(del);
    return row;
  }

  // ── Modals ─────────────────────────────────────────────────────────────────
  async function _modal(type) {
    await _loadResources();
    document.getElementById('pc-overlay')?.remove();

    const overlay = document.createElement('div');
    overlay.className = 'pc-overlay'; overlay.id = 'pc-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.addEventListener('keydown', function onEsc(k) {
      if (k.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', onEsc); }
    });

    const card = _mk('div', 'pc-modal');

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
      const nameIn   = _inp('Agent name (e.g. CEO, Engineer)');
      const roleIn   = _inp('Role description');
      const modelSel = _modelSelect('');
      const sysIn    = _ta('System prompt — how this agent thinks and behaves');
      sysIn.style.minHeight = '90px';

      const ceoWrap = document.createElement('label');
      ceoWrap.style.cssText = 'display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;padding:2px 0';
      const ceoChk = document.createElement('input'); ceoChk.type = 'checkbox';
      ceoWrap.append(ceoChk, 'CEO / Manager role');

      card.append(
        _mhd('Hire Agent'),
        _fg('Name *', nameIn),
        _fg('Role', roleIn),
        _fg('Model', modelSel),
        _fg('System Prompt', sysIn),
        _fg('', ceoWrap),
      );

      if (_skills.length) card.appendChild(_checkSection('Skills', _skills, [], 'pc-check-skills'));
      if (_mcps.length)   card.appendChild(_checkSection('MCP Tools', _mcps.map(m=>({id:m.id,name:m.name})), [], 'pc-check-mcps'));

      card.appendChild(_mact(overlay, async () => {
        if (!nameIn.value.trim()) return;
        const cid = _cid || _companies[0]?.id;
        if (!cid) { alert('Create a project first'); return; }
        const selectedSkills = [...card.querySelectorAll('.pc-check-skills input:checked')].map(el => el.value);
        const selectedMcps   = [...card.querySelectorAll('.pc-check-mcps  input:checked')].map(el => el.value);
        await _api('POST', `/companies/${cid}/agents`, {
          name: nameIn.value.trim(), role: roleIn.value,
          model: modelSel.value, system_prompt: sysIn.value,
          is_ceo: ceoChk.checked,
          skills: selectedSkills, mcps: selectedMcps,
        });
        _cid = cid; _view = 'project'; overlay.remove(); _reload();
      }, 'Hire Agent'));
    }

    else if (type === 'task') {
      const titleIn = _inp('Issue title *');
      const descIn  = _ta('Description (optional)');

      const projSel = document.createElement('select'); projSel.className = 'pc-input';
      _companies.forEach(c => {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = c.name;
        if (c.id === _cid) o.selected = true;
        projSel.appendChild(o);
      });

      const agentSel = document.createElement('select'); agentSel.className = 'pc-input';
      const refreshAgents = cid => {
        agentSel.innerHTML = '';
        const none = document.createElement('option'); none.value = ''; none.textContent = 'Unassigned';
        agentSel.appendChild(none);
        (_agents[cid] || []).forEach(a => {
          const o = document.createElement('option'); o.value = a.id;
          o.textContent = (a.is_ceo ? '👑 ' : '') + a.name;
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
          if (!cid) { alert('Select a project'); return; }
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
    card.querySelector('input:not([type=checkbox]),textarea')?.focus();
  }

  // ── Modal helpers ──────────────────────────────────────────────────────────
  function _mhd(t)    { const d = _mk('div','pc-modal-title'); d.textContent = t; return d; }
  function _fg(l, el) {
    const g = _mk('div','pc-fg');
    if (l) { const lbl = _mk('div','pc-fl'); lbl.textContent = l; g.appendChild(lbl); }
    g.appendChild(el); return g;
  }
  function _mact(overlay, onSave, label) {
    const row = _mk('div','pc-modal-actions');
    const cancel = document.createElement('button');
    cancel.className = 'pc-btn pc-btn-ghost'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => overlay.remove());
    const save = document.createElement('button');
    save.className = 'pc-btn pc-btn-primary'; save.textContent = label;
    save.addEventListener('click', async () => {
      save.disabled = true; save.textContent = 'Saving…';
      try { await onSave(); }
      catch(e) { alert(e.message); save.disabled = false; save.textContent = label; }
    });
    row.append(cancel, save); return row;
  }

  function _modelSelect(current) {
    const sel = document.createElement('select');
    sel.className = 'pc-input';
    const auto = document.createElement('option');
    auto.value = ''; auto.textContent = '— Use default model —';
    sel.appendChild(auto);
    if (_models.length) {
      let lastEp = null;
      _models.forEach(m => {
        if (m.endpoint !== lastEp) {
          const og = document.createElement('optgroup');
          og.label = m.endpoint; sel.appendChild(og); lastEp = m.endpoint;
        }
        const o = document.createElement('option');
        o.value = m.id; o.textContent = m.id;
        if (m.id === current) o.selected = true;
        sel.lastElementChild.appendChild(o);
      });
    } else if (current) {
      const o = document.createElement('option'); o.value = current; o.textContent = current; o.selected = true;
      sel.appendChild(o);
    }
    if (current && ![...sel.options].some(o => o.value === current)) {
      const o = document.createElement('option'); o.value = current; o.textContent = current; o.selected = true;
      sel.appendChild(o);
    }
    return sel;
  }

  function _checkSection(title, items, selected, cls) {
    const wrap = _mk('div', 'pc-fg');
    const lbl = _mk('div','pc-fl'); lbl.textContent = title; wrap.appendChild(lbl);
    if (!items.length) {
      const note = _mk('div',''); note.style.cssText = 'font-size:11px;opacity:.35'; note.textContent = 'None configured'; wrap.appendChild(note);
      return wrap;
    }
    const grid = _mk('div','pc-check-grid');
    items.forEach(item => {
      const row = document.createElement('label');
      row.className = `pc-check-item ${cls}`;
      const chk = document.createElement('input');
      chk.type = 'checkbox'; chk.value = item.id || item.name;
      if (selected.includes(chk.value)) chk.checked = true;
      const lbl2 = document.createElement('span');
      lbl2.textContent = item.name || item.id;
      row.append(chk, lbl2);
      grid.appendChild(row);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  // ── DOM helpers ────────────────────────────────────────────────────────────
  function _mk(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function _inp(ph)  { const i = document.createElement('input');    i.className = 'pc-input';    i.placeholder = ph; return i; }
  function _ta(ph)   { const t = document.createElement('textarea'); t.className = 'pc-textarea'; t.placeholder = ph; return t; }
  function _esc(s)   { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; }

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
