/**
 * Factory Project Manager — floating modal for creating, viewing, and
 * managing factory projects and their tasks.  Follows the same modal
 * pattern as gallery/calendar/research (modal-content + modalManager).
 */

import * as Modals from './modalManager.js';
import themeModule from './theme.js';

let _open = false;
let _onDocKeydown = null;

// ── Internal helpers ──────────────────────────────────────────

function _el(id) { return document.getElementById(id); }

// ── API layer ──────────────────────────────────────────────────

const _API = '/api/factory';

async function _fetchJSON(url, opts = {}) {
  const res = await fetch(url, { headers: { 'Accept': 'application/json' }, ...opts });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function getProjects() { return _fetchJSON(`${_API}/projects`); }
async function getStatus(pid) { return _fetchJSON(`${_API}/projects/${pid}`); }
async function createProject(description) {
  return _fetchJSON(`${_API}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description }),
  });
}
async function pauseProject(pid) { return _fetchJSON(`${_API}/projects/${pid}/pause`, { method: 'POST' }); }
async function resumeProject(pid) { return _fetchJSON(`${_API}/projects/${pid}/resume`, { method: 'POST' }); }
async function restartProject(pid, mode = 'partial') {
  return _fetchJSON(`${_API}/projects/${pid}/restart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
}
async function retryTask(taskId) { return _fetchJSON(`${_API}/tasks/${taskId}/retry`, { method: 'POST' }); }
async function iterateProject(pid, prompt) {
  return _fetchJSON(`${_API}/projects/${pid}/iterate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  });
}
async function getFactorySettings() { return _fetchJSON(`${_API}/settings`); }
async function saveFactorySettings(agentModels, agentPrompts, agentMaxTokens) {
  return _fetchJSON(`${_API}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_models: agentModels, agent_prompts: agentPrompts || {}, agent_max_tokens: agentMaxTokens || {} }),
  });
}

// ── State ──────────────────────────────────────────────────────

let _projects = [];
let _activeProjectId = null;
let _activeStatus = null;

// ── SVG icons (compact) ───────────────────────────────────────

const ICONS = {
  factory: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M17 18h1"/><path d="M12 18h1"/><path d="M7 18h1"/></svg>',
  plus: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>',
  pause: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="4" height="16" x="6" y="4"/><rect width="4" height="16" x="14" y="4"/></svg>',
  play: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>',
  refresh: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>',
  retry: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 16h-6v6"/><path d="M21 12v5h-5"/></svg>',
  back: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><polyline points="12 19 5 12 12 5"/></svg>',
  close: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
  spinner: '<svg class="factory-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a10 10 0 0 1 10 10" stroke-linecap="round"/></svg>',
  eye: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
};

// ── Status helpers ─────────────────────────────────────────────

const STATUS_COLORS = {
  pending: 'var(--muted, #666)',
  ready: 'var(--muted, #666)',
  running: 'var(--accent, #e63946)',
  completed: 'var(--green, #2d6a4f)',
  failed: 'var(--red, #d00000)',
  human_intervention: 'var(--orange, #e85d04)',
  skipped: 'var(--muted, #666)',
  cancelled: 'var(--muted, #666)',
};

function _projectStatusInfo(p) {
  const tasks = p.tasks || [];
  const done = tasks.filter(t => t.status === 'completed').length;
  const total = tasks.length;
  const pct = total ? Math.round((done / total) * 100) : 0;

  let status = 'pending';
  if (p.status === 'paused') status = 'paused';
  else if (p.status === 'running' || tasks.some(t => t.status === 'running' || t.status === 'ready')) status = 'running';
  else if (done === total && total > 0) status = 'completed';
  else if (tasks.some(t => t.status === 'failed')) status = 'failed';
  else if (tasks.some(t => t.status === 'human_intervention')) status = 'blocked';

  return { done, total, pct, status };
}

// ── Render: Project List View ─────────────────────────────────

function _renderProjectList(container) {
  const stats = _el('factory-stats');
  if (stats) stats.textContent = _projects.length ? `${_projects.length} project${_projects.length !== 1 ? 's' : ''}` : '';

  container.innerHTML = `
    <div class="factory-subheader">
      <span class="factory-count">${_projects.length} project${_projects.length !== 1 ? 's' : ''}</span>
      <div style="display:flex;gap:6px;">
        <button id="factory-settings-btn" class="factory-btn factory-btn-ghost" title="Agent settings">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        </button>
        <button id="factory-create-btn" class="factory-btn factory-btn-primary" title="New project">
          ${ICONS.plus} New
        </button>
      </div>
    </div>
    <div id="factory-project-list" class="factory-list"></div>
  `;

  const list = container.querySelector('#factory-project-list');

  _el('factory-create-btn')?.addEventListener('click', _showCreateForm);
  _el('factory-settings-btn')?.addEventListener('click', _openSettings);

  if (_projects.length === 0) {
    list.innerHTML = `
      <div class="factory-empty">
        <div class="factory-empty-icon">${ICONS.factory}</div>
        <p>No projects yet</p>
        <p class="factory-empty-hint">Create a project to start building with the Factory</p>
      </div>
    `;
    return;
  }

  const sorted = [..._projects].sort((a, b) => {
    const sa = _projectStatusInfo(a);
    const sb = _projectStatusInfo(b);
    const order = { running: 0, blocked: 1, failed: 2, paused: 3, pending: 4, completed: 5 };
    const diff = (order[sa.status] ?? 9) - (order[sb.status] ?? 9);
    return diff !== 0 ? diff : (b.id || 0) - (a.id || 0);
  });

  sorted.forEach(p => {
    const info = _projectStatusInfo(p);
    const card = document.createElement('div');
    card.className = 'factory-card';
    card.dataset.projectId = p.id;
    card.innerHTML = `
      <div class="factory-card-top">
        <span class="factory-card-id">#${p.id}</span>
        <span class="factory-card-status factory-status-${info.status}">${info.status}</span>
      </div>
      <div class="factory-card-desc">${_esc(p.description || 'Untitled project')}</div>
      <div class="factory-card-bottom">
        <div class="factory-progress-bar">
          <div class="factory-progress-fill" style="width:${info.pct}%"></div>
        </div>
        <span class="factory-progress-text">${info.done}/${info.total} tasks</span>
      </div>
    `;
    card.addEventListener('click', () => _openProjectStatus(p.id));
    list.appendChild(card);
  });
}

// ── Render: Agent Settings View ──────────────────────────────

let _factorySettings = null;

async function _openSettings() {
  const body = document.getElementById('factory-body');
  if (!body) return;
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }

  body.innerHTML = `
    <div class="factory-subheader">
      <button id="factory-back-btn" class="factory-btn factory-btn-ghost">${ICONS.back} Back</button>
      <span style="font-size:13px;font-weight:600;">Agent Model Settings</span>
    </div>
    <div id="factory-settings-loading" style="text-align:center;padding:40px;">
      ${ICONS.spinner} Loading...
    </div>
  `;

  _el('factory-back-btn')?.addEventListener('click', () => {
    _renderProjectList(body);
  });

  try {
    _factorySettings = await getFactorySettings();
  } catch (err) {
    body.querySelector('#factory-settings-loading').innerHTML =
      `<p style="color:var(--red)">Failed to load: ${_esc(err.message)}</p>`;
    return;
  }

  body.querySelector('#factory-settings-loading').remove();
  _renderSettings(body, _factorySettings);
}

function _renderSettings(container, data) {
  const { agents, agent_models, agent_prompts, agent_max_tokens, default_max_tokens, endpoints } = data;
  const currentModels = agent_models || {};
  const currentPrompts = agent_prompts || {};
  const currentMaxTokens = agent_max_tokens || {};
  const defaultMT = default_max_tokens || 16384;

  const modelOptions = endpoints.map(ep =>
    ep.models.map(m => `<option value="${ep.id}::${m}">${ep.name} / ${m}</option>`).join('')
  ).join('');

  // NOTE: append via a fragment, NOT `container.innerHTML +=`. The latter
  // re-serializes and recreates every node in `container` (incl. the back
  // button wired in _openSettings), dropping its click listener — which is
  // why the back button stopped working.
  const _settingsFrag = document.createElement('div');
  _settingsFrag.innerHTML = `
    <div class="factory-settings">
      <p class="factory-settings-hint">
        Assign models and customize agent instructions. "Default" model uses the task endpoint.
        Custom prompts override how each agent behaves.
      </p>
      <div class="factory-settings-bulk" style="display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border,#333);border-radius:6px;">
        <label for="factory-bulk-tokens" style="font-size:12px;font-weight:600;white-space:nowrap;">Set all token limits:</label>
        <input type="number" id="factory-bulk-tokens" value="4096" min="1024" max="131072" step="1024"
               style="width:90px;padding:4px 8px;border-radius:4px;border:1px solid var(--border,#333);background:transparent;color:inherit;font-size:13px;" />
        <button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-bulk-tokens-apply" type="button">Set All</button>
        <span style="font-size:11px;opacity:0.6;margin-left:4px;">Applies to every agent below</span>
      </div>
      ${agents.map(a => {
        const cfg = currentModels[a.key] || {};
        const isCustom = a.is_custom;
        const promptText = a.current_prompt || a.default_prompt || '';
        return `
          <div class="factory-settings-card" data-agent-key="${a.key}">
            <div class="factory-settings-row">
              <div class="factory-settings-agent">
                <span class="factory-settings-name">${a.name}</span>
                <span class="factory-settings-role">${a.role}${isCustom ? ' <span class="factory-settings-badge">custom</span>' : ''}</span>
              </div>
              <select class="factory-settings-select" data-agent-key="${a.key}">
                <option value="">Default (task endpoint)</option>
                ${modelOptions}
              </select>
              <div class="factory-settings-tokens">
                <label>Tokens</label>
                <input type="number" class="factory-settings-max-tokens" data-agent-key="${a.key}"
                  value="${currentMaxTokens[a.key] || defaultMT}" min="256" max="131072" step="1024" />
              </div>
            </div>
            <button class="factory-btn factory-btn-sm factory-btn-ghost factory-settings-toggle" data-agent-key="${a.key}">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
              Edit prompt
            </button>
            <div class="factory-settings-prompt-wrap" style="display:none;">
              <textarea class="factory-settings-prompt" data-agent-key="${a.key}" rows="8">${_esc(promptText)}</textarea>
              <div class="factory-settings-prompt-actions">
                <button class="factory-btn factory-btn-sm factory-btn-ghost factory-settings-reset" data-agent-key="${a.key}">Reset to default</button>
              </div>
            </div>
          </div>
        `;
      }).join('')}
      <button id="factory-settings-save" class="factory-btn factory-btn-primary" style="margin-top:12px;">
        Save settings
      </button>
    </div>
  `;
  container.appendChild(_settingsFrag.firstElementChild);

  // Bulk "Set All" token limit
  container.querySelector('#factory-bulk-tokens-apply')?.addEventListener('click', () => {
    const val = parseInt(container.querySelector('#factory-bulk-tokens')?.value);
    if (val && val >= 1024) {
      container.querySelectorAll('.factory-settings-max-tokens').forEach(inp => { inp.value = val; });
    }
  });

  // Set model dropdown values
  container.querySelectorAll('.factory-settings-select').forEach(sel => {
    const key = sel.dataset.agentKey;
    const cfg = currentModels[key] || {};
    if (cfg.endpoint_id) {
      const target = `${cfg.endpoint_id}::${cfg.model || ''}`;
      for (const opt of sel.options) {
        if (opt.value === target) { opt.selected = true; break; }
      }
    }
  });

  // Toggle prompt editor
  container.querySelectorAll('.factory-settings-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.factory-settings-card');
      const wrap = card.querySelector('.factory-settings-prompt-wrap');
      const isHidden = wrap.style.display === 'none';
      wrap.style.display = isHidden ? '' : 'none';
      btn.innerHTML = isHidden
        ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 15 12 9 18 15"/></svg> Hide prompt'
        : '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg> Edit prompt';
    });
  });

  // Reset to default
  container.querySelectorAll('.factory-settings-reset').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.agentKey;
      const ta = container.querySelector(`textarea.factory-settings-prompt[data-agent-key="${key}"]`);
      const agent = agents.find(a => a.key === key);
      if (ta && agent) {
        ta.value = agent.default_prompt || '';
      }
    });
  });

  // Save
  container.querySelector('#factory-settings-save')?.addEventListener('click', async () => {
    const btn = container.querySelector('#factory-settings-save');

    // Collect models
    const newModels = {};
    container.querySelectorAll('.factory-settings-select').forEach(sel => {
      if (sel.value) {
        const [endpoint_id, model] = sel.value.split('::');
        newModels[sel.dataset.agentKey] = { endpoint_id, model };
      }
    });

    // Collect prompts — compare to defaults, only save if different
    const newPrompts = {};
    container.querySelectorAll('.factory-settings-prompt').forEach(ta => {
      const key = ta.dataset.agentKey;
      const agent = agents.find(a => a.key === key);
      const val = ta.value.trim();
      if (val && val !== (agent?.default_prompt || '').trim()) {
        newPrompts[key] = val;
      }
    });

    // Collect max_tokens — only save if different from default
    const newMaxTokens = {};
    container.querySelectorAll('.factory-settings-max-tokens').forEach(inp => {
      const val = parseInt(inp.value);
      if (val && val !== defaultMT) {
        newMaxTokens[inp.dataset.agentKey] = val;
      }
    });

    btn.disabled = true;
    btn.innerHTML = `${ICONS.spinner} Saving...`;
    try {
      await saveFactorySettings(newModels, newPrompts, newMaxTokens);
      btn.innerHTML = 'Saved!';
      setTimeout(() => { btn.disabled = false; btn.innerHTML = 'Save settings'; }, 1500);
    } catch (err) {
      btn.disabled = false;
      btn.innerHTML = 'Save settings';
      alert('Save failed: ' + err.message);
    }
  });
}

// ── Render: Create Form ───────────────────────────────────────

function _showCreateForm() {
  const body = document.getElementById('factory-body');
  if (!body) return;

  body.innerHTML = `
    <div class="factory-subheader">
      <button id="factory-back-btn" class="factory-btn factory-btn-ghost">${ICONS.back} Back</button>
    </div>
    <div class="factory-create-form">
      <h3 style="margin:0 0 12px 0;">Create New Project</h3>
      <textarea id="factory-desc-input" placeholder="Describe what you want to build..." rows="6"
        style="width:100%;resize:vertical;box-sizing:border-box;padding:10px;
        border:1px solid var(--border, #333);border-radius:8px;
        background:var(--surface, #1a1a1a);color:var(--fg, #eee);font-family:inherit;"></textarea>
      <div class="factory-create-actions">
        <button id="factory-submit-btn" class="factory-btn factory-btn-primary" style="width:100%;">
          ${ICONS.factory} Create Project
        </button>
      </div>
    </div>
  `;

  _el('factory-back-btn')?.addEventListener('click', () => _renderProjectList(body));
  _el('factory-submit-btn')?.addEventListener('click', _handleCreate);
}

async function _handleCreate() {
  const input = _el('factory-desc-input');
  const desc = input?.value?.trim();
  if (!desc) { input?.focus(); return; }

  const btn = _el('factory-submit-btn');
  btn.disabled = true;
  btn.innerHTML = `${ICONS.spinner} Creating...`;

  try {
    const project = await createProject(desc);
    _activeProjectId = project.id;
    await _refreshProjects();
    _openProjectStatus(project.id);
  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = `${ICONS.factory} Create Project`;
    alert('Failed to create project: ' + err.message);
  }
}

// ── Render: Project Status / Kanban View ───────────────────────

let _pollTimer = null;

async function _openProjectStatus(projectId) {
  _activeProjectId = projectId;
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }

  const body = document.getElementById('factory-body');
  if (!body) return;

  body.innerHTML = `
    <div class="factory-subheader">
      <button id="factory-back-btn" class="factory-btn factory-btn-ghost">${ICONS.back} Back</button>
      <button id="factory-refresh-btn" class="factory-btn factory-btn-ghost" title="Refresh">
        ${ICONS.refresh}
      </button>
    </div>
    <div id="factory-status-loading" style="text-align:center;padding:40px;">
      ${ICONS.spinner} Loading...
    </div>
    <div id="factory-status-content"></div>
  `;

  _el('factory-back-btn')?.addEventListener('click', () => {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    _renderProjectList(body);
  });
  _el('factory-refresh-btn')?.addEventListener('click', () => _refreshStatus(projectId));

  await _refreshStatus(projectId);
  _startPolling(projectId);
}

async function _refreshStatus(projectId) {
  const body = document.getElementById('factory-body');
  if (!body) return;
  try {
    _activeStatus = await getStatus(projectId);
  } catch (err) {
    const loading = body.querySelector('#factory-status-loading');
    if (loading) loading.innerHTML =
      `<p style="color:var(--red)">Failed to load: ${_esc(err.message)}</p>`;
    return;
  }
  const loading = body.querySelector('#factory-status-loading');
  if (loading) loading.remove();
  const content = body.querySelector('#factory-status-content');
  if (content) _renderKanban(content);
}

function _renderKanban(container) {
  if (!container) return;
  const p = _activeStatus;
  if (!p) return;

  const info = _projectStatusInfo(p);
  const tasks = p.tasks || [];

  const columns = [
    { key: 'pending', label: 'Pending' },
    { key: 'ready', label: 'Ready' },
    { key: 'running', label: 'Running' },
    { key: 'completed', label: 'Done' },
    { key: 'failed', label: 'Failed' },
    { key: 'human_intervention', label: 'Blocked' },
  ];

  container.innerHTML = `
    <div class="factory-project-info">
      <div class="factory-project-info-top">
        <span class="factory-card-id">#${p.id}</span>
        <span class="factory-card-status factory-status-${info.status}">${info.status}</span>
      </div>
      <div class="factory-project-desc">${_esc(p.description || '')}</div>
      <div class="factory-progress-bar factory-progress-bar-lg">
        <div class="factory-progress-fill" style="width:${info.pct}%;background:${STATUS_COLORS.completed}"></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;">
        <span class="factory-progress-text">${info.done}/${info.total} tasks (${info.pct}%)</span>
        <div class="factory-project-actions" id="factory-project-actions"></div>
      </div>
    </div>
    <div class="factory-kanban" id="factory-kanban">
      ${columns.map(col => `
        <div class="factory-kanban-col" data-status="${col.key}">
          <div class="factory-kanban-col-header">
            <span class="factory-kanban-col-label" style="color:${STATUS_COLORS[col.key]}">${col.label}</span>
            <span class="factory-kanban-col-count">${tasks.filter(t => t.status === col.key).length}</span>
          </div>
          <div class="factory-kanban-col-body" data-status="${col.key}"></div>
        </div>
      `).join('')}
    </div>
    ${(p.status === 'completed' || p.status === 'running' || p.status === 'paused') ? `
      <div class="factory-iterate">
        <textarea id="factory-iterate-input" placeholder="Describe what to add or change..." rows="2"></textarea>
        <button id="factory-iterate-btn" class="factory-btn factory-btn-primary">Build more</button>
      </div>
    ` : ''}
  `;

  columns.forEach(col => {
    const body = container.querySelector(`.factory-kanban-col-body[data-status="${col.key}"]`);
    if (!body) return;
    const colTasks = tasks.filter(t => t.status === col.key);
    if (colTasks.length === 0) return;

    colTasks.forEach(task => {
      const card = document.createElement('div');
      card.className = 'factory-task-card';
      card.dataset.taskId = task.id;
      card.style.cursor = 'pointer';
      const resultText = (typeof task.result === 'object' && task.result)
        ? (task.result.output || JSON.stringify(task.result))
        : (task.result || '');
      const previewOutput = (typeof task.result === 'object' && task.result)
        ? (task.result.output || '')
        : (typeof task.result === 'string' ? task.result : '');
      const isPreviewable = _isPreviewable(task);
      const fname = task.filename ? `<div class="factory-task-card-file">${_esc(task.filename)}</div>` : '';
      const deps = (task.dependencies || []).map(depId => {
        const dep = tasks.find(t => t.id === depId);
        if (!dep) return `<span class="factory-dep factory-dep-done">T${depId} ✓</span>`;
        const done = dep.status === 'completed';
        return `<span class="factory-dep ${done ? 'factory-dep-done' : 'factory-dep-waiting'}">T${depId} ${done ? '✓' : '⏳'}</span>`;
      }).join(' ');
      const depsLine = deps ? `<div class="factory-task-card-deps">Requires: ${deps}</div>` : '';
      card.innerHTML = `
        <div class="factory-task-card-top">
          <span class="factory-task-card-id">T${task.id}</span>
          <span class="factory-task-card-agent">${_esc(task.agent || task.assigned_agent || '')}${task.task_type ? ` · ${_esc(task.task_type)}` : ''}</span>
          ${isPreviewable ? `<button class="factory-preview-eye-btn" data-task-id="${task.id}" title="Preview output">${ICONS.eye}</button>` : ''}
        </div>
        <div class="factory-task-card-title">${_esc(task.title || task.description || '')}</div>
        ${fname}
        ${depsLine}
        ${resultText ? `<div class="factory-task-card-result">${_esc(String(resultText).substring(0, 150))}${String(resultText).length > 150 ? '...' : ''}</div>` : ''}
        ${task.status === 'human_intervention' ? `
          <button class="factory-btn factory-btn-sm factory-btn-warn factory-task-retry-btn" data-task-id="${task.id}">
            ${ICONS.retry} Retry
          </button>
        ` : ''}
        ${task.status === 'running' ? `
          <div class="factory-task-card-progress">
            ${ICONS.spinner} <span>Working...</span>
          </div>
        ` : ''}
        ${task.status === 'completed' ? `<div class="factory-task-card-view">Click to view output →</div>` : ''}
      `;
      card.addEventListener('click', (e) => {
        if (e.target.closest('.factory-task-retry-btn')) return;
        if (e.target.closest('.factory-preview-eye-btn')) return;
        _openTaskDetail(task.id);
      });
      body.appendChild(card);

      // ── Eye button preview toggle ─────────────────────────
      if (isPreviewable) {
        const eyeBtn = card.querySelector('.factory-preview-eye-btn');
        if (eyeBtn) {
          eyeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            let expander = card.nextElementSibling;
            if (expander && expander.classList.contains('factory-task-card-preview')) {
              const hidden = expander.style.display === 'none';
              expander.style.display = hidden ? '' : 'none';
              eyeBtn.classList.toggle('active', hidden);
            } else {
              expander = document.createElement('div');
              expander.className = 'factory-task-card-preview';
              const iframe = document.createElement('iframe');
              iframe.className = 'factory-task-card-preview-iframe';
              iframe.sandbox = 'allow-scripts';
              iframe.srcdoc = previewOutput;
              expander.appendChild(iframe);
              card.parentNode.insertBefore(expander, card.nextSibling);
              eyeBtn.classList.add('active');
            }
          });
        }
      }
    });
  });

  _renderProjectActions(container.querySelector('#factory-project-actions'), p);

  container.querySelectorAll('.factory-task-retry-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const taskId = parseInt(btn.dataset.taskId);
      btn.disabled = true;
      btn.innerHTML = `${ICONS.spinner} Retrying...`;
      try {
        await retryTask(taskId);
        await _openProjectStatus(_activeProjectId);
      } catch (err) {
        btn.disabled = false;
        btn.innerHTML = `${ICONS.retry} Retry`;
        alert('Retry failed: ' + err.message);
      }
    });
  });

  const iterateBtn = container.querySelector('#factory-iterate-btn');
  const iterateInput = container.querySelector('#factory-iterate-input');
  if (iterateBtn && iterateInput) {
    iterateBtn.addEventListener('click', async () => {
      const prompt = iterateInput.value.trim();
      if (!prompt) { iterateInput.focus(); return; }
      iterateBtn.disabled = true;
      iterateBtn.innerHTML = `${ICONS.spinner} Planning...`;
      try {
        await iterateProject(p.id, prompt);
        await _openProjectStatus(p.id);
      } catch (err) {
        iterateBtn.disabled = false;
        iterateBtn.innerHTML = 'Build more';
        alert('Iterate failed: ' + err.message);
      }
    });
  }
}

function _openTaskDetail(taskId) {
  const task = (_activeStatus?.tasks || []).find(t => t.id === taskId);
  if (!task) return;

  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }

  const body = document.getElementById('factory-body');
  if (!body) return;

  _renderTaskDetail(taskId);

  // If the task is still running, poll for updates so the output
  // appears as soon as the agent finishes.
  if (task.status === 'running' || task.status === 'ready') {
    _pollTimer = setInterval(async () => {
      const overlay = _el('factory-overlay');
      if (!overlay) { clearInterval(_pollTimer); _pollTimer = null; return; }
      try {
        _activeStatus = await getStatus(_activeProjectId);
      } catch { return; }
      const updated = (_activeStatus?.tasks || []).find(t => t.id === taskId);
      if (!updated) return;
      _renderTaskDetail(taskId);
      if (updated.status !== 'running' && updated.status !== 'ready') {
        clearInterval(_pollTimer);
        _pollTimer = null;
      }
    }, 3000);
  }
}

function _renderTaskDetail(taskId) {
  const task = (_activeStatus?.tasks || []).find(t => t.id === taskId);
  if (!task) return;
  const body = document.getElementById('factory-body');
  if (!body) return;

  const resultObj = (typeof task.result === 'object' && task.result) ? task.result : null;
  const output = resultObj?.output || (typeof task.result === 'string' ? task.result : '');
  const producer = resultObj?.producer || task.assigned_agent || '';
  const reviewer = resultObj?.reviewer || '';
  const attempts = resultObj?.attempts || 0;
  const isRunning = task.status === 'running' || task.status === 'ready';
  const showPreview = task.status === 'completed' && _isPreviewable(task);

  body.innerHTML = `
    <div class="factory-subheader">
      <button id="factory-back-btn" class="factory-btn factory-btn-ghost">${ICONS.back} Back to board</button>
    </div>
    <div class="factory-task-detail">
      <div class="factory-task-detail-header">
        <div>
          <span class="factory-task-card-id">T${task.id}</span>
          <span class="factory-card-status factory-status-${task.status}">${task.status}</span>
          ${task.filename ? `<span class="factory-task-detail-filename">${_esc(task.filename)}</span>` : ''}
        </div>
        <div class="factory-task-detail-meta">
          ${task.task_type ? `<span>Type: <strong>${_esc(task.task_type)}</strong></span>` : ''}
          ${producer ? `<span>Producer: <strong>${_esc(producer)}</strong></span>` : ''}
          ${reviewer ? `<span>Reviewer: <strong>${_esc(reviewer)}</strong></span>` : ''}
          ${attempts ? `<span>Attempts: ${attempts}</span>` : ''}
        </div>
      </div>
      <h3 class="factory-task-detail-title">${_esc(task.title || '')}</h3>
      ${task.description ? `<p class="factory-task-detail-desc">${_esc(task.description)}</p>` : ''}
      ${isRunning ? `
        <div class="factory-task-detail-running">
          ${ICONS.spinner}
          <div class="factory-task-detail-progress">
            <span class="factory-task-detail-progress-phase">${_esc(task.error || `${producer || task.task_type || 'Agent'} working...`)}</span>
          </div>
        </div>
      ` : ''}
      ${task.error ? `<div class="factory-task-detail-error">${_esc(task.error)}</div>` : ''}
      ${(task.status === 'human_intervention' || task.status === 'failed') ? `
        <button class="factory-btn factory-btn-warn" id="factory-detail-retry-btn">${ICONS.retry} Retry task</button>
      ` : ''}
      ${output ? `
        <div class="factory-task-detail-output-label">Output</div>
        ${showPreview ? `
          <div class="factory-preview-split">
            <div class="factory-preview-code-pane">
              <pre class="factory-task-detail-output"><code>${_esc(output)}</code></pre>
            </div>
            <div class="factory-preview-render-pane">
              <div class="factory-preview-toolbar">
                <span class="factory-preview-toolbar-label">Preview</span>
                <button class="factory-preview-refresh-btn" title="Refresh preview">↻ Refresh</button>
                <button class="factory-preview-resize-btn" title="Toggle split direction">⬌</button>
              </div>
              <iframe class="factory-preview-iframe" sandbox="allow-scripts" src="about:blank"></iframe>
            </div>
          </div>
        ` : `
          <pre class="factory-task-detail-output"><code>${_esc(output)}</code></pre>
        `}
      ` : ''}
    </div>
  `;

  _el('factory-back-btn')?.addEventListener('click', () => {
    _openProjectStatus(_activeProjectId);
  });

  _el('factory-detail-retry-btn')?.addEventListener('click', async () => {
    const btn = _el('factory-detail-retry-btn');
    btn.disabled = true;
    btn.innerHTML = `${ICONS.spinner} Retrying...`;
    try {
      await retryTask(task.id);
      _openProjectStatus(_activeProjectId);
    } catch (err) {
      btn.disabled = false;
      btn.innerHTML = `${ICONS.retry} Retry task`;
      alert('Retry failed: ' + err.message);
    }
  });

  // ── Preview wiring ──────────────────────────────────────────
  if (showPreview) {
    const iframe = body.querySelector('.factory-preview-iframe');
    if (iframe) iframe.srcdoc = output;

    body.querySelector('.factory-preview-refresh-btn')?.addEventListener('click', () => {
      const ifr = body.querySelector('.factory-preview-iframe');
      if (ifr) ifr.srcdoc = output;
    });

    body.querySelector('.factory-preview-resize-btn')?.addEventListener('click', () => {
      const split = body.querySelector('.factory-preview-split');
      if (split) split.classList.toggle('factory-preview-split-stacked');
    });
  }
}

function _startPolling(projectId) {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  _pollTimer = setInterval(async () => {
    const overlay = _el('factory-overlay');
    if (!overlay || !document.body.contains(overlay)) {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      return;
    }
    if (_activeProjectId !== projectId) {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      return;
    }
    await _refreshStatus(projectId);
    const p = _activeStatus;
    if (p && ['completed', 'failed', 'cancelled', 'paused'].includes(p.status)) {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    }
  }, 3000);
}

function _renderProjectActions(container, project) {
  if (!container) return;
  const info = _projectStatusInfo(project);

  let buttons = '';
  if (info.done > 0) {
    buttons += `<a class="factory-btn factory-btn-sm factory-btn-primary" href="${_API}/projects/${project.id}/download" download title="Download ZIP"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> ZIP</a>`;
  }
  if (project.status === 'running' || info.status === 'running') {
    buttons += `<button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-pause-btn">${ICONS.pause} Pause</button>`;
  }
  if (project.status === 'paused') {
    buttons += `<button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-resume-btn">${ICONS.play} Resume</button>`;
  }
  buttons += `<button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-restart-btn">${ICONS.refresh} Restart</button>`;

  container.innerHTML = buttons;

  _el('factory-pause-btn')?.addEventListener('click', async () => {
    try { await pauseProject(project.id); await _openProjectStatus(project.id); }
    catch (err) { alert('Pause failed: ' + err.message); }
  });
  _el('factory-resume-btn')?.addEventListener('click', async () => {
    try { await resumeProject(project.id); await _openProjectStatus(project.id); }
    catch (err) { alert('Resume failed: ' + err.message); }
  });
  _el('factory-restart-btn')?.addEventListener('click', async () => {
    const mode = info.status === 'completed' ? 'full' : 'partial';
    try { await restartProject(project.id, mode); await _openProjectStatus(project.id); }
    catch (err) { alert('Restart failed: ' + err.message); }
  });
}

// ── Refresh projects list ─────────────────────────────────────

async function _refreshProjects() {
  try {
    _projects = await getProjects();
  } catch (err) {
    console.warn('Factory: failed to refresh projects', err);
  }
}

// ── Utility ───────────────────────────────────────────────────

function _esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function _isPreviewable(task) {
  if (!task || task.status !== 'completed') return false;
  const resultObj = (typeof task.result === 'object' && task.result) ? task.result : null;
  const output = resultObj?.output || (typeof task.result === 'string' ? task.result : '');
  if (!output) return false;
  const tt = (task.task_type || '').toLowerCase();
  const fname = (task.filename || '').toLowerCase();
  const trimmed = output.trim();
  if (['frontend', 'design', 'ui', 'webpage'].includes(tt)) return true;
  if (fname.endsWith('.html') || fname.endsWith('.htm')) return true;
  if (trimmed.startsWith('<') || trimmed.toLowerCase().startsWith('<!doctype') || trimmed.toLowerCase().startsWith('<html')) return true;
  if (output.includes('<html') && output.includes('</html>')) return true;
  return false;
}

// ── Public API ─────────────────────────────────────────────────

export function isOpen() { return _open; }

export async function toggle() {
  if (_open) {
    closePanel();
  } else {
    await openPanel();
  }
}

export async function openPanel() {
  if (_open) return;
  _open = true;

  const overlay = document.createElement('div');
  overlay.id = 'factory-overlay';
  overlay.className = 'modal';

  const pane = document.createElement('div');
  pane.className = 'modal-content factory-content';
  pane.style.cssText = (window.innerWidth <= 768)
    ? 'width:100vw;max-width:100vw;height:90dvh;max-height:90dvh;border-radius:14px 14px 0 0;padding:0;'
    : 'width:min(640px, 92vw);max-height:85vh;padding:0;';

  pane.innerHTML = `
    <div class="modal-header factory-pane-header">
      <h4><span style="display:inline-flex;vertical-align:middle;align-items:center;">${ICONS.factory}</span><span style="margin-left:6px;">Factory</span> <span id="factory-stats" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal;margin-left:8px;"></span></h4>
      <button class="modal-minimize-btn" id="factory-minimize" type="button" title="Minimize"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="5" y1="18" x2="19" y2="18"/></svg></button>
      <button class="modal-close" id="factory-close" title="Close">&#x2715;</button>
    </div>
    <div class="factory-body" id="factory-body"></div>
  `;

  overlay.appendChild(pane);
  document.body.appendChild(overlay);

  document.body.classList.add('factory-panel-view');

  _onDocKeydown = (e) => {
    if (e.key === 'Escape') closePanel();
  };
  document.addEventListener('keydown', _onDocKeydown);

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closePanel();
  });

  pane.querySelector('#factory-close').addEventListener('click', closePanel);
  pane.querySelector('#factory-minimize').addEventListener('click', (e) => {
    e.stopPropagation();
    Modals.minimize('factory-overlay');
  });

  const header = pane.querySelector('.factory-pane-header');
  if (themeModule && themeModule.makeDraggable && header) {
    themeModule.makeDraggable(pane, header);
  }

  Modals.register('factory-overlay', {
    railBtnId: 'rail-factory',
    sidebarBtnId: 'tool-factory-btn',
    closeFn: () => closePanel(),
    restoreFn: () => {},
  });

  const btn = _el('tool-factory-btn');
  if (btn) btn.classList.add('active');

  await _refreshProjects();
  _renderProjectList(pane.querySelector('#factory-body'));
}

export function closePanel() {
  if (!_open) return;
  _open = false;

  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }

  if (_onDocKeydown) {
    document.removeEventListener('keydown', _onDocKeydown);
    _onDocKeydown = null;
  }

  document.body.classList.remove('factory-panel-view');
  const btn = _el('tool-factory-btn');
  if (btn) btn.classList.remove('active');

  const overlay = _el('factory-overlay');
  if (overlay) overlay.remove();

  Modals.unregister('factory-overlay');
}
