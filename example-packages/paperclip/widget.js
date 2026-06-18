/**
 * Paperclip — widget.js
 *
 * AI agent team management inside the Odysseus sidebar.
 * Companies → Agents + Tasks.  Agents can be "Run" to open a live chat.
 */
(() => {
  const API = '/api/paperclip';

  // ── State ──────────────────────────────────────────────────────────────────
  let _companies = [], _agents = [], _tasks = [];
  let _cid = null;       // selected company id
  let _tab = 'agents';   // 'agents' | 'tasks'
  let _root = null;      // sidebar view content el
  let _form = null;      // active inline form type

  // ── HTTP ───────────────────────────────────────────────────────────────────
  async function _api(method, path, body) {
    const r = await fetch(API + path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
    return r.json();
  }

  function _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Load ───────────────────────────────────────────────────────────────────
  async function _reload() {
    try {
      const d = await _api('GET', '/companies');
      _companies = d.companies || [];
      if (!_cid && _companies.length) _cid = _companies[0].id;
      if (_cid) {
        const [ad, td] = await Promise.all([
          _api('GET', `/companies/${_cid}/agents`),
          _api('GET', `/companies/${_cid}/tasks`),
        ]);
        _agents = ad.agents || [];
        _tasks  = td.tasks  || [];
      } else {
        _agents = []; _tasks = [];
      }
    } catch (e) {
      console.error('[paperclip]', e);
    }
    _render();
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function _render() {
    if (!_root) return;
    _root.innerHTML = '';

    // ── Company bar ──────────────────────────────────────────────────────────
    const compBar = _el('div', 'padding:6px 8px 4px;display:flex;gap:4px;align-items:center;flex-shrink:0');
    if (_companies.length === 0) {
      compBar.innerHTML = '<span style="font-size:11px;opacity:.5;flex:1">No companies yet</span>';
    } else {
      const sel = _el('select', 'flex:1;font-size:11px;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:5px;padding:2px 4px;cursor:pointer');
      _companies.forEach(c => {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = c.name;
        if (c.id === _cid) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', () => { _cid = sel.value; _form = null; _reload(); });
      compBar.appendChild(sel);

      const delBtn = _btn('×', 'danger', () => _deleteCompany(_cid));
      delBtn.title = 'Delete company';
      compBar.appendChild(delBtn);
    }
    const addCompBtn = _btn('+', 'accent', () => { _form = _form === 'company' ? null : 'company'; _render(); });
    addCompBtn.title = 'New company';
    compBar.appendChild(addCompBtn);
    _root.appendChild(compBar);

    // ── New company form ─────────────────────────────────────────────────────
    if (_form === 'company') {
      _root.appendChild(_companyForm());
    }

    if (!_cid) { _root.appendChild(_empty('Create a company to get started.')); return; }

    // ── Tabs ─────────────────────────────────────────────────────────────────
    const tabs = _el('div', 'display:flex;gap:0;flex-shrink:0;border-bottom:1px solid var(--border);margin:0 0 2px');
    ['agents','tasks'].forEach(t => {
      const tb = _el('div', `flex:1;text-align:center;font-size:11px;padding:5px 4px;cursor:pointer;border-bottom:2px solid ${_tab===t?'var(--accent,#63b3ed)':'transparent'};opacity:${_tab===t?'1':'.55'};font-weight:${_tab===t?'600':'400'}`);
      tb.textContent = t === 'agents' ? `Agents (${_agents.length})` : `Tasks (${_tasks.length})`;
      tb.addEventListener('click', () => { _tab = t; _form = null; _render(); });
      tabs.appendChild(tb);
    });
    _root.appendChild(tabs);

    // ── Tab content ──────────────────────────────────────────────────────────
    if (_tab === 'agents') {
      _renderAgents();
    } else {
      _renderTasks();
    }
  }

  // ── Agents tab ─────────────────────────────────────────────────────────────
  function _renderAgents() {
    const list = _el('div', 'flex:1;overflow-y:auto;min-height:0');

    _agents.forEach(a => {
      const row = _el('div', 'padding:6px 8px;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent);display:flex;flex-direction:column;gap:3px');

      const top = _el('div', 'display:flex;align-items:center;gap:4px');
      const name = _el('span', 'font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap');
      name.textContent = a.name;
      top.appendChild(name);

      const runBtn = _btn('▶ Run', 'accent-sm', async () => {
        runBtn.disabled = true; runBtn.textContent = '…';
        try {
          const d = await _api('POST', `/agents/${a.id}/run`);
          if (window.sessionModule?.loadSessions) await window.sessionModule.loadSessions();
          if (window.sessionModule?.selectSession) window.sessionModule.selectSession(d.session_id);
        } catch (e) { alert('Run failed: ' + e.message); }
        finally { runBtn.disabled = false; runBtn.textContent = '▶ Run'; }
      });
      top.appendChild(runBtn);

      const delBtn = _btn('×', 'ghost-sm', async () => {
        if (!confirm(`Delete agent "${a.name}"?`)) return;
        await _api('DELETE', `/agents/${a.id}`);
        _reload();
      });
      top.appendChild(delBtn);
      row.appendChild(top);

      const meta = _el('div', 'font-size:10px;opacity:.5;display:flex;gap:6px');
      if (a.role) { const r = _el('span',''); r.textContent = a.role; meta.appendChild(r); }
      if (a.model) { const m = _el('span','font-style:italic'); m.textContent = a.model; meta.appendChild(m); }
      if (meta.children.length) row.appendChild(meta);

      list.appendChild(row);
    });

    if (!_agents.length) list.appendChild(_empty('No agents yet.'));

    // Add agent button
    const addRow = _el('div','padding:6px 8px;flex-shrink:0');
    const addBtn = _el('div','cursor:pointer;font-size:11px;opacity:.6;display:flex;align-items:center;gap:4px');
    addBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> New Agent';
    addBtn.addEventListener('click', () => { _form = _form === 'agent' ? null : 'agent'; _render(); });
    addRow.appendChild(addBtn);
    if (_form === 'agent') addRow.appendChild(_agentForm());
    _root.appendChild(list);
    _root.appendChild(addRow);
  }

  // ── Tasks tab ──────────────────────────────────────────────────────────────
  function _renderTasks() {
    const list = _el('div', 'flex:1;overflow-y:auto;min-height:0');

    const agentMap = Object.fromEntries(_agents.map(a => [a.id, a.name]));
    const statusIcon = { todo: '○', 'in-progress': '◑', done: '●' };

    _tasks.forEach(t => {
      const row = _el('div', 'padding:5px 8px;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent);display:flex;align-items:flex-start;gap:5px');

      // Status toggle button
      const statuses = ['todo', 'in-progress', 'done'];
      const nextStatus = statuses[(statuses.indexOf(t.status) + 1) % statuses.length];
      const statusBtn = _el('button', 'background:none;border:none;cursor:pointer;font-size:13px;padding:0;line-height:1;flex-shrink:0;margin-top:1px;color:var(--fg);opacity:.7');
      statusBtn.title = `Mark as ${nextStatus}`;
      statusBtn.textContent = statusIcon[t.status] || '○';
      statusBtn.style.textDecoration = t.status === 'done' ? 'line-through' : '';
      statusBtn.addEventListener('click', async () => {
        await _api('PUT', `/tasks/${t.id}`, { status: nextStatus });
        _reload();
      });
      row.appendChild(statusBtn);

      const info = _el('div', 'flex:1;min-width:0');
      const title = _el('div', `font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${t.status==='done'?'opacity:.4;text-decoration:line-through':''}`);
      title.textContent = t.title;
      info.appendChild(title);
      if (t.agent_id && agentMap[t.agent_id]) {
        const assignee = _el('div', 'font-size:10px;opacity:.45');
        assignee.textContent = '→ ' + agentMap[t.agent_id];
        info.appendChild(assignee);
      }
      row.appendChild(info);

      const delBtn = _btn('×', 'ghost-sm', async () => {
        await _api('DELETE', `/tasks/${t.id}`);
        _reload();
      });
      row.appendChild(delBtn);
      list.appendChild(row);
    });

    if (!_tasks.length) list.appendChild(_empty('No tasks yet.'));

    const addRow = _el('div', 'padding:6px 8px;flex-shrink:0');
    const addBtn = _el('div','cursor:pointer;font-size:11px;opacity:.6;display:flex;align-items:center;gap:4px');
    addBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> New Task';
    addBtn.addEventListener('click', () => { _form = _form === 'task' ? null : 'task'; _render(); });
    addRow.appendChild(addBtn);
    if (_form === 'task') addRow.appendChild(_taskForm());
    _root.appendChild(list);
    _root.appendChild(addRow);
  }

  // ── Inline forms ───────────────────────────────────────────────────────────
  function _companyForm() {
    const wrap = _el('div', 'padding:6px 8px;display:flex;flex-direction:column;gap:4px;border-bottom:1px solid var(--border);flex-shrink:0;background:color-mix(in srgb,var(--accent,#63b3ed) 6%,transparent)');
    const nameIn = _input('Company name…');
    const goalIn = _input('Goal (optional)…');
    const row = _el('div','display:flex;gap:4px');
    const saveBtn = _btn('Create','accent', async () => {
      if (!nameIn.value.trim()) return;
      const c = await _api('POST', '/companies', { name: nameIn.value.trim(), goal: goalIn.value });
      _cid = c.id; _form = null; _reload();
    });
    const cancelBtn = _btn('Cancel', 'ghost', () => { _form = null; _render(); });
    row.append(saveBtn, cancelBtn);
    wrap.append(nameIn, goalIn, row);
    return wrap;
  }

  function _agentForm() {
    const wrap = _el('div', 'margin-top:6px;display:flex;flex-direction:column;gap:4px;padding:6px 0;border-top:1px solid var(--border)');
    const nameIn = _input('Agent name…');
    const roleIn = _input('Role (e.g. Engineer)…');
    const modelIn = _input('Model (e.g. claude-sonnet-4-6)…');
    const sysIn = _textarea('System prompt (optional)…');
    const row = _el('div','display:flex;gap:4px');
    const saveBtn = _btn('Add', 'accent', async () => {
      if (!nameIn.value.trim()) return;
      await _api('POST', `/companies/${_cid}/agents`, {
        name: nameIn.value.trim(), role: roleIn.value,
        model: modelIn.value, system_prompt: sysIn.value,
      });
      _form = null; _reload();
    });
    const cancelBtn = _btn('Cancel', 'ghost', () => { _form = null; _render(); });
    row.append(saveBtn, cancelBtn);
    wrap.append(nameIn, roleIn, modelIn, sysIn, row);
    return wrap;
  }

  function _taskForm() {
    const wrap = _el('div', 'margin-top:6px;display:flex;flex-direction:column;gap:4px;padding:6px 0;border-top:1px solid var(--border)');
    const titleIn = _input('Task title…');
    const agentSel = _el('select', 'font-size:11px;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:5px;padding:3px 6px;width:100%');
    const noneOpt = document.createElement('option'); noneOpt.value = ''; noneOpt.textContent = 'Unassigned';
    agentSel.appendChild(noneOpt);
    _agents.forEach(a => {
      const o = document.createElement('option'); o.value = a.id; o.textContent = a.name; agentSel.appendChild(o);
    });
    const row = _el('div','display:flex;gap:4px');
    const saveBtn = _btn('Add', 'accent', async () => {
      if (!titleIn.value.trim()) return;
      await _api('POST', `/companies/${_cid}/tasks`, {
        title: titleIn.value.trim(),
        agent_id: agentSel.value || null,
      });
      _form = null; _reload();
    });
    const cancelBtn = _btn('Cancel', 'ghost', () => { _form = null; _render(); });
    row.append(saveBtn, cancelBtn);
    wrap.append(titleIn, agentSel, row);
    return wrap;
  }

  // ── Company delete ──────────────────────────────────────────────────────────
  async function _deleteCompany(cid) {
    const c = _companies.find(x => x.id === cid);
    if (!c || !confirm(`Delete company "${c.name}" and all its agents/tasks?`)) return;
    await _api('DELETE', `/companies/${cid}`);
    _cid = null; _reload();
  }

  // ── DOM helpers ────────────────────────────────────────────────────────────
  function _el(tag, css) {
    const e = document.createElement(tag);
    if (css) e.style.cssText = css;
    return e;
  }
  function _empty(msg) {
    const e = _el('div', 'padding:16px 8px;font-size:11px;opacity:.4;text-align:center');
    e.textContent = msg; return e;
  }
  function _input(ph) {
    const i = _el('input', 'width:100%;box-sizing:border-box;font-size:11px;padding:4px 6px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--fg)');
    i.placeholder = ph; return i;
  }
  function _textarea(ph) {
    const t = _el('textarea', 'width:100%;box-sizing:border-box;font-size:11px;padding:4px 6px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--fg);resize:vertical;min-height:52px');
    t.placeholder = ph; return t;
  }

  const _btnStyles = {
    accent:    'background:var(--accent,#63b3ed);color:#000;border:none;border-radius:4px;font-size:11px;padding:3px 8px;cursor:pointer;white-space:nowrap',
    'accent-sm':'background:none;border:1px solid var(--accent,#63b3ed);color:var(--accent,#63b3ed);border-radius:4px;font-size:10px;padding:1px 5px;cursor:pointer;white-space:nowrap',
    ghost:     'background:none;border:1px solid var(--border);border-radius:4px;font-size:11px;padding:3px 8px;cursor:pointer;color:var(--fg)',
    'ghost-sm':'background:none;border:none;font-size:13px;cursor:pointer;color:var(--fg);opacity:.4;padding:0 2px;line-height:1',
    danger:    'background:none;border:none;font-size:13px;cursor:pointer;color:var(--danger,#e55);opacity:.6;padding:0 2px;line-height:1',
  };
  function _btn(label, style, onClick) {
    const b = _el('button', _btnStyles[style] || _btnStyles.ghost);
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  // ── Register sidebar view ──────────────────────────────────────────────────
  const ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>`;

  function _mount(el) {
    _root = el;
    _form = null;
    _reload();
  }
  function _unmount() {
    _root = null;
  }

  const pkg = window.OdysseusPkg;
  if (pkg?.registerSidebarView) {
    pkg.registerSidebarView('paperclip', {
      icon: ICON,
      label: 'Paperclip',
      onMount: _mount,
      onUnmount: _unmount,
    });
  } else {
    console.warn('[paperclip] registerSidebarView not available — update pkg-api.js');
  }
})();
