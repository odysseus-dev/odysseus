// swarm.js — Tech Duinn swarm management panel
// Tasks, agents, logs, events, and shared memory for the agent swarm.

import uiModule from './ui.js';

const API = '/api/swarm';
let _modalEl = null;
let _activeTab = 'tasks';

// ── Helpers ──────────────────────────────────────────────────────────────

async function _fetch(url, opts = {}) {
  const res = await fetch(url, { credentials: 'same-origin', ...opts });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function _esc(s) {
  return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _timeAgo(ts) {
  if (!ts) return 'never';
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function _statusColor(status) {
  const colors = {
    online: '#50fa7b', busy: '#f1fa8c', offline: '#6272a4', error: '#ff5555',
    pending: '#8be9fd', assigned: '#bd93f9', in_progress: '#f1fa8c',
    completed: '#50fa7b', failed: '#ff5555', cancelled: '#6272a4',
  };
  return colors[status] || '#6272a4';
}

// ── Modal ────────────────────────────────────────────────────────────────

function _ensureModal() {
  if (_modalEl) return _modalEl;
  const modal = document.createElement('div');
  modal.id = 'swarm-modal';
  modal.className = 'modal hidden';
  modal.innerHTML = `
    <div class="modal-content" style="max-width:900px;width:96%;max-height:90vh;display:flex;flex-direction:column;">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
          Swarm Manager
        </h4>
        <button class="close-btn" id="swarm-close">✖</button>
      </div>
      <div class="modal-body" style="flex:1;overflow:hidden;display:flex;flex-direction:column;">
        <div class="swarm-tabs" style="display:flex;gap:4px;margin-bottom:12px;flex-shrink:0;">
          <button class="swarm-tab active" data-tab="tasks">Tasks</button>
          <button class="swarm-tab" data-tab="agents">Agents</button>
          <button class="swarm-tab" data-tab="logs">Logs</button>
          <button class="swarm-tab" data-tab="events">Events</button>
          <button class="swarm-tab" data-tab="memory">Memory</button>
        </div>
        <div id="swarm-content" style="flex:1;overflow-y:auto;"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);

  modal.querySelector('#swarm-close').addEventListener('click', _close);
  modal.addEventListener('click', e => { if (e.target === modal) _close(); });
  modal.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); _close(); } });

  modal.querySelectorAll('.swarm-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      _activeTab = tab.dataset.tab;
      modal.querySelectorAll('.swarm-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      _render();
    });
  });

  _modalEl = modal;
  return modal;
}

function _close() {
  if (_modalEl) { _modalEl.classList.add('hidden'); _modalEl.style.display = ''; }
}

export async function open() {
  const modal = _ensureModal();
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  await _render();
}

// ── Render ───────────────────────────────────────────────────────────────

async function _render() {
  const content = _modalEl.querySelector('#swarm-content');
  content.innerHTML = '<div style="opacity:0.5;padding:20px;text-align:center;">Loading...</div>';
  try {
    switch (_activeTab) {
      case 'tasks': await _renderTasks(content); break;
      case 'agents': await _renderAgents(content); break;
      case 'logs': await _renderLogs(content); break;
      case 'events': await _renderEvents(content); break;
      case 'memory': await _renderMemory(content); break;
    }
  } catch (e) {
    content.innerHTML = `<div style="color:var(--red);padding:20px;">Error: ${_esc(e.message)}</div>`;
  }
}

// ── Tasks ────────────────────────────────────────────────────────────────

async function _renderTasks(container) {
  const tasks = await _fetch(`${API}/tasks?limit=100`);
  let html = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="opacity:0.6;font-size:12px;">${tasks.length} task(s)</span>
      <button class="cal-btn cal-btn-primary" id="swarm-add-task" style="font-size:11px;padding:4px 12px;">+ New Task</button>
    </div>`;

  if (!tasks.length) {
    html += '<div style="opacity:0.5;text-align:center;padding:40px;">No tasks yet. Create one to coordinate agent work.</div>';
  } else {
    html += '<div style="display:flex;flex-direction:column;gap:6px;">';
    for (const t of tasks) {
      html += `
        <div class="swarm-task-row" data-task-id="${_esc(t.id)}" style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;cursor:pointer;">
          <span style="width:8px;height:8px;border-radius:50%;background:${_statusColor(t.status)};flex-shrink:0;"></span>
          <span style="flex:1;font-size:12px;font-weight:500;">${_esc(t.title)}</span>
          <span style="font-size:10px;opacity:0.5;">P${t.priority}</span>
          <span style="font-size:10px;opacity:0.4;">${_esc(t.status)}</span>
          ${t.assigned_to ? `<span style="font-size:10px;opacity:0.4;">→ ${_esc(t.assigned_to)}</span>` : ''}
          <span style="font-size:10px;opacity:0.3;">${_timeAgo(t.created_at)}</span>
        </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;

  container.querySelector('#swarm-add-task')?.addEventListener('click', async () => {
    const title = await uiModule.styledPrompt('Task title', { placeholder: 'What needs to be done?' });
    if (!title) return;
    await _fetch(`${API}/tasks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) });
    uiModule.showToast('Task created');
    _render();
  });

  container.querySelectorAll('.swarm-task-row').forEach(row => {
    row.addEventListener('click', async () => {
      const task = await _fetch(`${API}/tasks/${row.dataset.taskId}`);
      const action = await uiModule.styledPrompt(`Task: ${task.title}\nStatus: ${task.status}\n\nSet new status (pending/assigned/in_progress/completed/failed/cancelled):`, { defaultValue: task.status });
      if (action && action !== task.status) {
        await _fetch(`${API}/tasks/${task.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: action }) });
        uiModule.showToast(`Task → ${action}`);
        _render();
      }
    });
  });
}

// ── Agents ───────────────────────────────────────────────────────────────

async function _renderAgents(container) {
  const agents = await _fetch(`${API}/agents`);
  let html = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="opacity:0.6;font-size:12px;">${agents.length} agent(s)</span>
    </div>`;

  if (!agents.length) {
    html += '<div style="opacity:0.5;text-align:center;padding:40px;">No agents registered yet. Agents register themselves via the MCP tools.</div>';
  } else {
    html += '<div style="display:flex;flex-direction:column;gap:6px;">';
    for (const a of agents) {
      html += `
        <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;">
          <span style="width:8px;height:8px;border-radius:50%;background:${_statusColor(a.status)};flex-shrink:0;"></span>
          <span style="flex:1;font-size:12px;font-weight:500;">${_esc(a.name)}</span>
          <span style="font-size:10px;opacity:0.5;">${_esc(a.role || 'no role')}</span>
          <span style="font-size:10px;opacity:0.4;">${_esc(a.status)}</span>
          <span style="font-size:10px;opacity:0.3;">last seen ${_timeAgo(a.last_heartbeat)}</span>
        </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;
}

// ── Logs ─────────────────────────────────────────────────────────────────

async function _renderLogs(container) {
  const logs = await _fetch(`${API}/logs?limit=100`);
  let html = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="opacity:0.6;font-size:12px;">${logs.length} log(s)</span>
      <input type="search" id="swarm-log-search" placeholder="Search logs..." style="padding:4px 8px;font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);width:200px;">
    </div>`;

  if (!logs.length) {
    html += '<div style="opacity:0.5;text-align:center;padding:40px;">No log entries yet.</div>';
  } else {
    html += '<div style="font-family:monospace;font-size:11px;display:flex;flex-direction:column;gap:2px;">';
    for (const l of logs) {
      const ts = l.timestamp ? new Date(l.timestamp * 1000).toLocaleTimeString() : '?';
      const levelColors = { debug: '#6272a4', info: '#8be9fd', warn: '#f1fa8c', error: '#ff5555', fatal: '#ff5555' };
      html += `
        <div style="display:flex;gap:8px;padding:3px 6px;background:var(--bg);border-radius:3px;">
          <span style="opacity:0.4;flex-shrink:0;">${ts}</span>
          <span style="color:${levelColors[l.level] || '#8be9fd'};flex-shrink:0;width:40px;">${(l.level || 'info').toUpperCase()}</span>
          <span style="opacity:0.5;flex-shrink:0;">${_esc(l.agent_id || '?')}</span>
          <span style="flex:1;word-break:break-all;">${_esc(l.message)}</span>
        </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;

  container.querySelector('#swarm-log-search')?.addEventListener('input', async (e) => {
    const q = e.target.value.trim();
    if (q.length < 2) { _render(); return; }
    const results = await _fetch(`${API}/logs/search/${encodeURIComponent(q)}?limit=50`);
    const list = container.querySelector('div[style*="monospace"]');
    if (list) {
      list.innerHTML = results.map(l => {
        const ts = l.timestamp ? new Date(l.timestamp * 1000).toLocaleTimeString() : '?';
        return `<div style="display:flex;gap:8px;padding:3px 6px;background:var(--bg);border-radius:3px;">
          <span style="opacity:0.4;flex-shrink:0;">${ts}</span>
          <span style="flex:1;word-break:break-all;">${_esc(l.message)}</span>
        </div>`;
      }).join('');
    }
  });
}

// ── Events ───────────────────────────────────────────────────────────────

async function _renderEvents(container) {
  const events = await _fetch(`${API}/events?limit=100`);
  let html = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="opacity:0.6;font-size:12px;">${events.length} event(s)</span>
    </div>`;

  if (!events.length) {
    html += '<div style="opacity:0.5;text-align:center;padding:40px;">No events yet. Events are published by agents via MCP tools.</div>';
  } else {
    html += '<div style="display:flex;flex-direction:column;gap:4px;">';
    for (const ev of events) {
      const ts = ev.timestamp ? new Date(ev.timestamp * 1000).toLocaleTimeString() : '?';
      html += `
        <div style="display:flex;gap:8px;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;font-size:11px;">
          <span style="opacity:0.4;flex-shrink:0;">${ts}</span>
          <span style="color:#bd93f9;flex-shrink:0;">${_esc(ev.topic)}</span>
          <span style="opacity:0.5;flex-shrink:0;">from ${_esc(ev.source || '?')}</span>
          <span style="flex:1;word-break:break-all;opacity:0.7;">${_esc((ev.payload || '').slice(0, 120))}</span>
        </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;
}

// ── Memory ───────────────────────────────────────────────────────────────

async function _renderMemory(container) {
  const memory = await _fetch(`${API}/memory?limit=100`);
  let html = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="opacity:0.6;font-size:12px;">${memory.length} entry(ies)</span>
      <input type="search" id="swarm-mem-search" placeholder="Search memory..." style="padding:4px 8px;font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);width:200px;">
    </div>`;

  if (!memory.length) {
    html += '<div style="opacity:0.5;text-align:center;padding:40px;">No shared memory entries yet. Agents store knowledge here via MCP tools.</div>';
  } else {
    html += '<div style="display:flex;flex-direction:column;gap:4px;">';
    for (const m of memory) {
      const val = m.value.length > 120 ? m.value.slice(0, 120) + '...' : m.value;
      html += `
        <div style="display:flex;gap:8px;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;font-size:11px;">
          <span style="color:#50fa7b;flex-shrink:0;font-weight:600;">${_esc(m.key)}</span>
          <span style="flex:1;word-break:break-all;opacity:0.7;">${_esc(val)}</span>
          ${m.tags ? `<span style="opacity:0.3;flex-shrink:0;">${_esc(m.tags)}</span>` : ''}
        </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;

  container.querySelector('#swarm-mem-search')?.addEventListener('input', async (e) => {
    const q = e.target.value.trim();
    if (q.length < 2) { _render(); return; }
    const results = await _fetch(`${API}/memory/search/${encodeURIComponent(q)}?limit=50`);
    const list = container.querySelector('div[style*="flex-direction:column"]');
    if (list) {
      list.innerHTML = results.map(m => {
        const val = m.value.length > 120 ? m.value.slice(0, 120) + '...' : m.value;
        return `<div style="display:flex;gap:8px;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;font-size:11px;">
          <span style="color:#50fa7b;flex-shrink:0;font-weight:600;">${_esc(m.key)}</span>
          <span style="flex:1;word-break:break-all;opacity:0.7;">${_esc(val)}</span>
        </div>`;
      }).join('');
    }
  });
}

// ── Export ────────────────────────────────────────────────────────────────

const swarmModule = { open };
export default swarmModule;
