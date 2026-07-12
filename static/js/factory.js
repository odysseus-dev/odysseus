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
async function saveFactorySettings(agentModels, agentPrompts, agentMaxTokens, concurrentTasks, produceMaxTokens) {
  return _fetchJSON(`${_API}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agent_models: agentModels,
      agent_prompts: agentPrompts || {},
      agent_max_tokens: agentMaxTokens || {},
      concurrent_tasks: concurrentTasks || 3,
      produce_max_tokens: produceMaxTokens || 16384,
    }),
  });
}

// ── State ──────────────────────────────────────────────────────

let _projects = [];
let _activeProjectId = null;
let _activeStatus = null;
let _lastFingerprint = '';
let _autoMode = false;
let _produceMaxTokens = 16384; // fetched from settings, used for token estimation badges
let _activeView = 'tasks'; // 'tasks' (kanban) or 'files' (file browser)

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
  eye: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
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
  const _activeCount = _projects.filter(p => p.status === 'planning' || p.status === 'running').length;
  const _activeLabel = _activeCount > 0 ? ` \u00b7 ${_activeCount} active` : '';
  const _statsText = _projects.length ? `${_projects.length} project${_projects.length !== 1 ? 's' : ''}${_activeLabel}` : '';
  if (stats) stats.textContent = _statsText;

  container.innerHTML = `
    <div class="factory-subheader">
      <span class="factory-count">${_statsText}</span>
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

  // Refresh project list periodically while there are active projects
  if (_projects.some(p => p.status === 'planning' || p.status === 'running')) {
    if (!container._listPollTimer) {
      container._listPollTimer = setInterval(async () => {
        // Stop if the container is no longer in the DOM
        if (!document.body.contains(container)) {
          clearInterval(container._listPollTimer);
          container._listPollTimer = null;
          return;
        }
        // Stop if navigating to status view
        if (_activeProjectId) {
          clearInterval(container._listPollTimer);
          container._listPollTimer = null;
          return;
        }
        await _refreshProjects();
        // Re-render just the stats text + project list without full innerHTML rebuild
        const statsEl = _el('factory-stats');
        if (statsEl) {
          const ac = _projects.filter(p => p.status === 'planning' || p.status === 'running').length;
          const al = ac > 0 ? ` \u00b7 ${ac} active` : '';
          statsEl.textContent = _projects.length ? `${_projects.length} project${_projects.length !== 1 ? 's' : ''}${al}` : '';
        }
        // If no more active projects, stop polling
        if (!_projects.some(p => p.status === 'planning' || p.status === 'running')) {
          clearInterval(container._listPollTimer);
          container._listPollTimer = null;
          _renderProjectList(container); // Final full re-render
        }
      }, 5000);
    }
  }
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
  const { agents, agent_models, agent_prompts, agent_max_tokens, default_max_tokens, produce_max_tokens, concurrent_tasks, endpoints } = data;
  const currentModels = agent_models || {};
  const currentPrompts = agent_prompts || {};
  const currentMaxTokens = agent_max_tokens || {};
  const defaultMT = default_max_tokens || 16384;
  _produceMaxTokens = produce_max_tokens || default_max_tokens || 16384;

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
      <div class="factory-settings-bulk" style="display:flex;align-items:center;gap:8px;margin-bottom:10px;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border,#333);border-radius:6px;">
        <label for="factory-bulk-model" style="font-size:12px;font-weight:600;white-space:nowrap;">Set all agents to:</label>
        <select id="factory-bulk-model" style="padding:5px 8px;border:1px solid var(--border,#333);border-radius:4px;background:transparent;color:inherit;font-size:12px;max-width:220px;">
          <option value="">Default (task endpoint)</option>
          ${modelOptions}
        </select>
        <button class="factory-btn factory-btn-sm factory-btn-primary" id="factory-bulk-model-apply" type="button" style="flex-shrink:0;">Apply All</button>
        <span style="font-size:11px;opacity:0.6;margin-left:4px;">Sets every agent to the same model</span>
      </div>
      <div class="factory-settings-bulk" style="display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border,#333);border-radius:6px;">
        <label for="factory-bulk-tokens" style="font-size:12px;font-weight:600;white-space:nowrap;">Set all token limits:</label>
        <input type="number" id="factory-bulk-tokens" value="4096" min="1024" max="131072" step="1024"
               style="width:90px;padding:4px 8px;border-radius:4px;border:1px solid var(--border,#333);background:transparent;color:inherit;font-size:13px;" />
        <button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-bulk-tokens-apply" type="button">Set All</button>
        <span style="font-size:11px;opacity:0.6;margin-left:4px;">Applies to every agent below</span>
      </div>
      <div class="factory-settings-bulk" style="display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border,#333);border-radius:6px;">
        <label for="factory-concurrent-tasks" style="font-size:12px;font-weight:600;white-space:nowrap;">Max concurrent tasks:</label>
        <input type="number" id="factory-concurrent-tasks" value="${concurrent_tasks || 3}" min="1" max="10"
               style="width:60px;padding:4px 8px;border-radius:4px;border:1px solid var(--border,#333);background:transparent;color:inherit;font-size:13px;" />
        <span style="font-size:11px;opacity:0.6;">Independent tasks run in parallel (higher = faster, but may hit API rate limits)</span>
      </div>
      <div class="factory-settings-bulk" style="display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border,#333);border-radius:6px;">
        <label for="factory-produce-max-tokens" style="font-size:12px;font-weight:600;white-space:nowrap;">Produce token budget:</label>
        <input type="number" id="factory-produce-max-tokens" value="${produce_max_tokens || 16384}" min="2048" max="65536" step="1024"
               style="width:90px;padding:4px 8px;border-radius:4px;border:1px solid var(--border,#333);background:transparent;color:inherit;font-size:13px;" />
        <span style="font-size:11px;opacity:0.6;">Max output tokens per produce call (higher = less truncation, slower responses)</span>
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

  // Bulk "Apply All" model
  container.querySelector('#factory-bulk-model-apply')?.addEventListener('click', () => {
    const bulkSelect = container.querySelector('#factory-bulk-model');
    const targetVal = bulkSelect?.value;
    if (targetVal !== undefined && targetVal !== null) {
      container.querySelectorAll('.factory-settings-select').forEach(sel => {
        sel.value = targetVal;
      });
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

    // Collect performance settings
    const concurrentTasks = parseInt(container.querySelector('#factory-concurrent-tasks')?.value) || 3;
    const produceMaxTokens = parseInt(container.querySelector('#factory-produce-max-tokens')?.value) || 16384;

    btn.disabled = true;
    btn.innerHTML = `${ICONS.spinner} Saving...`;
    try {
      await saveFactorySettings(newModels, newPrompts, newMaxTokens, concurrentTasks, produceMaxTokens);
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
  _lastFingerprint = '';
  _autoMode = false;
  _activeView = 'tasks';
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

/**
 * Render the Files tab: file list + source code viewer.
 */
function _renderFilesView(container, project) {
  const files = _getProjectFiles(project);
  if (files.length === 0) {
    container.innerHTML = `<p style="opacity:0.6;text-align:center;padding:40px;">No completed files yet.</p>`;
    return;
  }

  let _selectedIdx = 0;

  function _renderViewer() {
    const viewer = container.querySelector('#factory-file-viewer');
    if (!viewer) return;
    const f = files[_selectedIdx];
    if (!f) return;
    let highlighted;
    try {
      if (window.hljs && f.language !== 'plaintext') {
        highlighted = window.hljs.highlight(f.content, { language: f.language }).value;
      } else {
        highlighted = _esc(f.content);
      }
    } catch (_) {
      highlighted = _esc(f.content);
    }
    viewer.innerHTML = `
      <div class="factory-file-toolbar">
        <span class="factory-file-toolbar-name">${_esc(f.filename)}</span>
        <span class="factory-file-toolbar-meta">T${f.taskId} · ${_esc(f.taskType)} · ${f.content.length.toLocaleString()} chars</span>
      </div>
      <pre class="factory-file-code"><code class="language-${f.language}">${highlighted}</code></pre>
    `;
  }

  container.innerHTML = `
    <div class="factory-files-view">
      <div class="factory-files-list" id="factory-files-list">
        ${files.map((f, i) => `
          <div class="factory-file-item${i === 0 ? ' active' : ''}" data-idx="${i}">
            <span class="factory-file-badge factory-file-badge-${f.typeLabel.toLowerCase()}">${f.typeLabel}</span>
            <span class="factory-file-name">${_esc(f.filename)}</span>
          </div>
        `).join('')}
      </div>
      <div class="factory-file-viewer" id="factory-file-viewer"></div>
    </div>
  `;

  _renderViewer();

  container.querySelectorAll('.factory-file-item').forEach(item => {
    item.addEventListener('click', () => {
      container.querySelectorAll('.factory-file-item').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      _selectedIdx = parseInt(item.dataset.idx) || 0;
      _renderViewer();
    });
  });
}

/**
 * Render the Terminal tab: command input + output panel.
 * Sends commands to POST /api/factory/projects/{id}/exec which
 * extracts project files to a workspace and runs the command there.
 */
function _renderTerminalView(container, projectId) {
  let _cmdHistory = [];
  let _historyIdx = -1;
  let _running = false;

  container.innerHTML = `
    <div class="factory-terminal">
      <div class="factory-terminal-output" id="factory-term-output">
        <div class="factory-term-line factory-term-info">Project workspace terminal. Type a command and press Enter.</div>
        <div class="factory-term-line factory-term-info">Files are extracted from completed tasks on each run. Try: ls -la, cat index.html, python -m http.server</div>
      </div>
      <div class="factory-terminal-input-row">
        <span class="factory-terminal-prompt">$</span>
        <input type="text" id="factory-term-input" class="factory-terminal-input"
               placeholder="Enter command..." autocomplete="off"
               spellcheck="false" autocapitalize="off" />
      </div>
    </div>
  `;

  const outputEl = container.querySelector('#factory-term-output');
  const inputEl = container.querySelector('#factory-term-input');

  function _appendLine(text, cls) {
    const line = document.createElement('div');
    line.className = `factory-term-line ${cls || ''}`;
    // Use textContent for safety, but preserve whitespace with white-space:pre-wrap (CSS)
    line.textContent = text;
    outputEl.appendChild(line);
    outputEl.scrollTop = outputEl.scrollHeight;
    return line;
  }

  async function _runCommand(cmd) {
    if (_running) return;
    _running = true;
    inputEl.disabled = true;

    // Echo the command
    _appendLine(`$ ${cmd}`, 'factory-term-cmd');

    // Add to history (skip duplicates)
    if (_cmdHistory[0] !== cmd) {
      _cmdHistory.unshift(cmd);
      if (_cmdHistory.length > 50) _cmdHistory.pop();
    }
    _historyIdx = -1;

    // Show loading
    const loadingLine = document.createElement('div');
    loadingLine.className = 'factory-term-line factory-term-loading';
    loadingLine.textContent = 'Running...';
    outputEl.appendChild(loadingLine);
    outputEl.scrollTop = outputEl.scrollHeight;

    try {
      const res = await fetch(`${_API}/projects/${projectId}/exec`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command: cmd,
          // Long-running commands get 5 min, everything else 60s
          timeout: /^(npm|yarn|pnpm|pip|pip3|cargo|go|composer|bundle|gem)\s+(install|update|build)/i.test(cmd.trim()) ? 300 : 60,
        }),
      });
      const result = await res.json();
      loadingLine.remove();

      if (result.stdout && result.stdout.trim()) {
        _appendLine(result.stdout, 'factory-term-stdout');
      }
      if (result.stderr && result.stderr.trim()) {
        _appendLine(result.stderr, 'factory-term-stderr');
      }
      if (result.exit_code !== 0) {
        _appendLine(`[exit code: ${result.exit_code}]`, 'factory-term-exit');
      }
      if (!result.stdout && !result.stderr) {
        _appendLine('(no output)', 'factory-term-info');
      }
    } catch (err) {
      loadingLine.remove();
      _appendLine(`Error: ${err.message}`, 'factory-term-stderr');
    }

    _running = false;
    inputEl.disabled = false;
    inputEl.focus();
  }

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const cmd = inputEl.value.trim();
      if (cmd) {
        _runCommand(cmd);
        inputEl.value = '';
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (_historyIdx < _cmdHistory.length - 1) {
        _historyIdx++;
        inputEl.value = _cmdHistory[_historyIdx] || '';
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (_historyIdx > 0) {
        _historyIdx--;
        inputEl.value = _cmdHistory[_historyIdx] || '';
      } else {
        _historyIdx = -1;
        inputEl.value = '';
      }
    } else if (e.key === 'l' && e.ctrlKey) {
      // Ctrl+L = clear screen (like real terminal)
      e.preventDefault();
      outputEl.innerHTML = '';
    }
  });

  // Focus the input when the terminal area is clicked
  container.querySelector('.factory-terminal')?.addEventListener('click', () => {
    inputEl.focus();
  });

  inputEl.focus();
}

function _renderKanban(container) {
  if (!container) return;
  const p = _activeStatus;
  if (!p) return;

  const info = _projectStatusInfo(p);
  const tasks = p.tasks || [];

  // ── Planning indicator ──
  // When the project is in 'planning' status with no tasks yet, the planner
  // LLM is decomposing the project. Show a clear indicator instead of an
  // empty board so the user knows work is in progress.
  const isPlanning = p.status === 'planning' && tasks.length === 0;

  // ── Smart rebuild: skip if nothing changed ──
  const fingerprint = tasks.map(t =>
    `${t.id}:${t.status}:${t.error || ''}:${t.result ? '1' : '0'}`
  ).join('|') + `:${p.status}:${info.pct}:${_activeView}`;

  if (fingerprint === _lastFingerprint) return;
  _lastFingerprint = fingerprint;

  const columns = [
    { key: 'pending', label: 'Pending' },
    { key: 'ready', label: 'Ready' },
    { key: 'running', label: 'Running' },
    { key: 'completed', label: 'Done' },
    { key: 'failed', label: 'Failed' },
    { key: 'human_intervention', label: 'Blocked' },
  ];

  const completedTasks = tasks.filter(t => t.status === 'completed');

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
      ${(p.status === 'completed' || p.status === 'running' || p.status === 'paused') ? `
        <div class="factory-iterate">
          <textarea id="factory-iterate-input" placeholder="Describe what to add or change..." rows="2"></textarea>
          <button id="factory-iterate-btn" class="factory-btn factory-btn-primary">Build more</button>
        </div>
      ` : ''}
    </div>
    ${isPlanning ? `
      <div class="factory-planning-indicator">
        <div class="factory-planning-spinner">${ICONS.spinner}</div>
        <div class="factory-planning-text">
          <strong>Planning tasks...</strong>
          <p>The planner agent is decomposing your project into tasks. This typically takes 10-30 seconds.</p>
        </div>
      </div>
    ` : `
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
    `}
  `;

  if (!isPlanning) {
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
          ${_tokenBadge(task)}
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
        ${task.status === 'completed' ? `
  <div class="factory-task-card-view">
    ${ICONS.eye} <span>View output</span>
  </div>
` : ''}
      `;
      card.addEventListener('click', (e) => {
        if (e.target.closest('.factory-task-retry-btn')) return;
        _openTaskDetail(task.id);
      });
      body.appendChild(card);
    });
  });
  }

  _renderProjectActions(container.querySelector('#factory-project-actions'), p);

  // ── Tab bar ──
  const fileCount = tasks.filter(t => t.status === 'completed' && t.filename).length;
  if (fileCount > 0) {
    const projInfo = container.querySelector('.factory-project-info');
    if (projInfo) {
      let tabBar = container.querySelector('.factory-tabs');
      if (!tabBar) {
        tabBar = document.createElement('div');
        tabBar.className = 'factory-tabs';
        projInfo.insertAdjacentElement('afterend', tabBar);
      }
      tabBar.innerHTML = `
        <button class="factory-tab${_activeView === 'tasks' ? ' active' : ''}" data-view="tasks">Tasks</button>
        <button class="factory-tab${_activeView === 'files' ? ' active' : ''}" data-view="files">Files (${fileCount})</button>
        <button class="factory-tab${_activeView === 'terminal' ? ' active' : ''}" data-view="terminal">Terminal</button>
      `;
      tabBar.querySelectorAll('.factory-tab').forEach(tab => {
        tab.addEventListener('click', () => {
          _activeView = tab.dataset.view;
          _lastFingerprint = ''; // force re-render
          _renderKanban(container);
        });
      });
    }
  }

  // ── Terminal view ──
  if (_activeView === 'terminal') {
    const kanban = container.querySelector('#factory-kanban');
    if (kanban) kanban.style.display = 'none';
    const iterateSection = container.querySelector('.factory-iterate');
    if (iterateSection) iterateSection.style.display = 'none';

    // Reuse the same files container for terminal
    let termContainer = container.querySelector('#factory-files-container');
    if (!termContainer) {
      termContainer = document.createElement('div');
      termContainer.id = 'factory-files-container';
      const refEl = container.querySelector('#factory-kanban') || container.querySelector('.factory-project-info');
      if (refEl) refEl.insertAdjacentElement('afterend', termContainer);
      else container.appendChild(termContainer);
    }
    _renderTerminalView(termContainer, p.id);
  }

  // ── Files view ──
  if (_activeView === 'files' && fileCount > 0) {
    const kanban = container.querySelector('#factory-kanban');
    if (kanban) kanban.style.display = 'none';
    const iterateSection = container.querySelector('.factory-iterate');
    if (iterateSection) iterateSection.style.display = 'none';

    let fileContainer = container.querySelector('#factory-files-container');
    if (!fileContainer) {
      fileContainer = document.createElement('div');
      fileContainer.id = 'factory-files-container';
      const refEl = container.querySelector('#factory-kanban') || container.querySelector('.factory-project-info');
      if (refEl) refEl.insertAdjacentElement('afterend', fileContainer);
      else container.appendChild(fileContainer);
    }
    _renderFilesView(fileContainer, p);
  } else if (_activeView === 'tasks') {
    const kanban = container.querySelector('#factory-kanban');
    if (kanban) kanban.style.display = '';
    const iterateSection = container.querySelector('.factory-iterate');
    if (iterateSection) iterateSection.style.display = '';
    const fileContainer = container.querySelector('#factory-files-container');
    if (fileContainer) fileContainer.remove();
  }

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
  const output = _getOutput(task.result);
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
          <span>Tokens: ${_tokenBadge(task)}</span>
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
      ${task.status === 'completed' ? `
        <div class="factory-task-detail-output-label">Output</div>
        ${output ? (showPreview ? `
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
              <!-- sandbox: allow-scripts ONLY (no allow-same-origin) — the framed LLM output is treated as untrusted; without allow-same-origin it runs in an opaque origin and cannot reach parent.document, cookies, localStorage, or authed /api/* calls -->
              <iframe class="factory-preview-iframe" sandbox="allow-scripts" src="${_API}/nodes/${task.id}/preview"></iframe>
            </div>
          </div>
        ` : `
          <pre class="factory-task-detail-output"><code>${_esc(output)}</code></pre>
        `) : `
          <div class="factory-task-detail-output" style="color:var(--fg-dim,#888);font-style:italic;display:flex;align-items:center;justify-content:center;min-height:60px;">No output available yet — the task may still be producing content.</div>
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
    body.querySelector('.factory-preview-refresh-btn')?.addEventListener('click', () => {
      const ifr = body.querySelector('.factory-preview-iframe');
      if (ifr) {
        // Cache-bust so clicking Refresh always reloads even if content is unchanged.
        ifr.src = `${_API}/nodes/${task.id}/preview?t=${Date.now()}`;
      }
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

/**
 * POST all project files to the server and return a base preview URL.
 * Files are served individually so the browser resolves relative URLs
 * (e.g. <script src="js/main.js">) naturally against the preview path.
 */
async function _postPreviewFiles(files, mainFile) {
  try {
    const res = await fetch(`${_API}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files, main: mainFile })
    });
    if (!res.ok) return null;
    const { token } = await res.json();
    return token ? `${_API}/preview/${token}` : null;
  } catch (_) { return null; }
}

/**
 * Start a dev server for a Node.js project and show it in a preview overlay.
 */
async function _serveNodeProject(project) {
  // Remove any existing preview overlay
  const existing = document.getElementById('factory-project-preview');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.id = 'factory-project-preview';
  overlay.className = 'factory-project-preview-overlay';
  overlay.innerHTML = `
    <div class="factory-project-preview-toolbar">
      <span class="factory-project-preview-title">Starting ${_esc(project.title || 'project')}...</span>
      <div class="factory-project-preview-actions">
        <button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-preview-close-btn" title="Close">✕ Close</button>
      </div>
    </div>
    <div class="factory-serve-loading" id="factory-serve-loading">
      <div class="factory-serve-spinner">${ICONS.spinner}</div>
      <div class="factory-serve-status" id="factory-serve-status">Installing dependencies...</div>
      <div class="factory-serve-log" id="factory-serve-log"></div>
    </div>
    <div class="factory-project-preview-frame-wrap" style="display:none;" id="factory-serve-frame-wrap">
      <iframe id="factory-project-preview-iframe" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" src="about:blank"></iframe>
    </div>
  `;
  document.body.appendChild(overlay);

  const statusEl = overlay.querySelector('#factory-serve-status');
  const logEl = overlay.querySelector('#factory-serve-log');
  const loadingEl = overlay.querySelector('#factory-serve-loading');
  const frameWrap = overlay.querySelector('#factory-serve-frame-wrap');
  const iframe = overlay.querySelector('#factory-project-preview-iframe');

  // Close handler — stop the server
  function _close() {
    overlay.remove();
    // Fire and forget — stop the dev server
    fetch(`${_API}/projects/${project.id}/serve/stop`, { method: 'POST' }).catch(() => {});
  }
  overlay.querySelector('#factory-preview-close-btn')?.addEventListener('click', _close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _close(); });
  const escHandler = (e) => { if (e.key === 'Escape') { _close(); document.removeEventListener('keydown', escHandler); } };
  document.addEventListener('keydown', escHandler);

  // Start the server
  try {
    statusEl.textContent = 'Installing dependencies (npm install)...';
    const res = await fetch(`${_API}/projects/${project.id}/serve`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `Server error (${res.status_code})`);
    }
    const result = await res.json();

    if (result.install_log) {
      logEl.textContent = result.install_log;
    }

    statusEl.textContent = `Server running on port ${result.port} (npm run ${result.script})`;

    // Switch from loading to iframe
    await new Promise(r => setTimeout(r, 500)); // brief transition
    loadingEl.style.display = 'none';
    frameWrap.style.display = '';

    // Load the proxy URL — this serves the dev server's output
    iframe.src = result.url;

    // Update title
    overlay.querySelector('.factory-project-preview-title').textContent =
      `${_esc(project.title || 'Project')} — running on :${result.port}`;
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
    logEl.textContent = '';
    // Add a close button since the server didn't start
    const retryBtn = document.createElement('button');
    retryBtn.className = 'factory-btn factory-btn-sm factory-btn-warn';
    retryBtn.textContent = 'Close';
    retryBtn.style.marginTop = '12px';
    retryBtn.addEventListener('click', _close);
    loadingEl.appendChild(retryBtn);
  }
}

/**
 * Assemble all completed task files into a full project preview.
 * Files are served individually so the browser resolves relative URLs
 * (<script src="js/main.js">, <link href="css/style.css">) naturally.
 */
function _previewProject(project) {
  const tasks = project.tasks || [];
  const completed = tasks.filter(t => t.status === 'completed');
  if (!completed.length) return;

  // Build file map: filename → content
  const files = {};
  completed.forEach(task => {
    const fname = (task.filename || '').trim();
    const output = _getOutput(task.result);
    if (!fname || !output) return;
    files[fname] = output;
  });

  // Find ALL HTML files
  const htmlFiles = Object.keys(files).filter(f => f.toLowerCase().endsWith('.html'));
  if (!htmlFiles.length) {
    alert('No HTML file found to preview. Add a frontend task first.');
    return;
  }

  // Default to index.html, else first HTML file
  const defaultFile = htmlFiles.find(f => f.toLowerCase() === 'index.html') || htmlFiles[0];

  // Show preview overlay with tabs — the server serves files individually
  // so relative URLs resolve naturally.
  _showProjectPreview(files, htmlFiles, defaultFile);
}

/**
 * Show full-screen preview overlay with tabbed HTML pages.
 * POSTs all files once; each tab sets iframe.src to a server-served URL
 * so relative URLs (e.g. <script src="js/main.js">) resolve naturally.
 * @param {Object} files - {filename: content} for ALL project files
 * @param {string[]} htmlFiles - ordered list of HTML filenames
 * @param {string} activeFile - the initially selected filename
 */
function _showProjectPreview(files, htmlFiles, activeFile) {
  // Remove any existing preview overlay
  const existing = document.getElementById('factory-project-preview');
  if (existing) existing.remove();

  const showTabs = htmlFiles.length > 1;
  let _previewBaseUrl = null;

  // POST all files once, then use the base URL for everything
  _postPreviewFiles(files, activeFile).then(baseUrl => {
    _previewBaseUrl = baseUrl;
    if (!baseUrl) {
      // If POST failed and overlay is still shown, close it
      const ov = document.getElementById('factory-project-preview');
      if (ov) ov.remove();
      return;
    }
    // If single file, open directly in new tab with filename so relative URLs
    // (e.g. <script src="js/main.js">) resolve against the token's directory.
    if (!showTabs && htmlFiles.length === 1) {
      window.open(`${baseUrl}/${activeFile}`, '_blank');
      document.getElementById('factory-project-preview')?.remove();
      return;
    }
    // Load the initial page into the iframe
    const ifr = document.querySelector('#factory-project-preview-iframe');
    if (ifr) ifr.src = `${baseUrl}/${activeFile}`;
  });

  const overlay = document.createElement('div');
  overlay.id = 'factory-project-preview';
  overlay.className = 'factory-project-preview-overlay';
  overlay.innerHTML = `
    <div class="factory-project-preview-toolbar">
      <span class="factory-project-preview-title">Project Preview</span>
      ${showTabs ? `
      <div class="factory-project-preview-tabs" id="factory-preview-tabs">
        ${htmlFiles.map(f => {
          const short = f.split('/').pop().replace(/\.html?$/i, '');
          const active = f === activeFile ? ' active' : '';
          return `<button class="factory-preview-tab${active}" data-file="${_esc(f)}">${_esc(short)}</button>`;
        }).join('')}
      </div>
      ` : ''}
      <div class="factory-project-preview-actions">
        <button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-preview-reload-btn" title="Reload current page">↻ Reload</button>
        <button class="factory-btn factory-btn-sm factory-btn-primary" id="factory-preview-open-tab-btn" title="Open in new browser tab">↗ Open in Tab</button>
        <button class="factory-btn factory-btn-sm factory-btn-ghost" id="factory-preview-close-btn" title="Close">✕ Close</button>
      </div>
    </div>
    <div class="factory-project-preview-frame-wrap">
      <iframe id="factory-project-preview-iframe" sandbox="allow-scripts" src="about:blank"></iframe>
    </div>
  `;
  document.body.appendChild(overlay);

  const iframe = overlay.querySelector('#factory-project-preview-iframe');
  
  // Load a specific page into the iframe
  function _loadPage(file) {
    if (iframe && _previewBaseUrl) {
      iframe.src = `${_previewBaseUrl}/${file}`;
    }
  }

  // Load initial page (handled async above, but also do it directly for tabs)
  // If baseUrl is already set, load now; otherwise the async handler above does it

  // Tab switching
  if (showTabs) {
    overlay.querySelectorAll('.factory-preview-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const file = tab.dataset.file;
        // Update active tab styling
        overlay.querySelectorAll('.factory-preview-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        _loadPage(file);
      });
    });
  }

  // Reload button
  overlay.querySelector('#factory-preview-reload-btn')?.addEventListener('click', () => {
    const activeTab = overlay.querySelector('.factory-preview-tab.active');
    const file = activeTab ? activeTab.dataset.file : activeFile;
    _loadPage(file);
  });

  // Open in new tab
  overlay.querySelector('#factory-preview-open-tab-btn')?.addEventListener('click', () => {
    const activeTab = overlay.querySelector('.factory-preview-tab.active');
    const file = activeTab ? activeTab.dataset.file : activeFile;
    if (_previewBaseUrl) {
      window.open(`${_previewBaseUrl}/${file}`, '_blank');
    }
  });

  // Close button
  overlay.querySelector('#factory-preview-close-btn')?.addEventListener('click', () => {
    overlay.remove();
  });

  // Close on Escape
  const escHandler = (e) => {
    if (e.key === 'Escape') {
      overlay.remove();
      document.removeEventListener('keydown', escHandler);
    }
  };
  document.addEventListener('keydown', escHandler);

  // Close on background click
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });
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
  const hasHTMLTasks = (project.tasks || []).some(t => t.status === 'completed' && ((t.filename || '').toLowerCase().endsWith('.html') || (t.task_type || '').toLowerCase() === 'frontend'));
  const hasNodeProject = _isNodeProject(project);
  if (hasHTMLTasks || hasNodeProject) {
    const previewLabel = hasNodeProject && !hasHTMLTasks ? '▶ Run Server' : '▶ Preview';
    buttons += `<button class="factory-btn factory-btn-sm factory-btn-primary" id="factory-preview-project-btn" title="${hasNodeProject ? 'Install deps and start dev server' : 'Preview the assembled project'}">${previewLabel}</button>`;
  }
  if (project.status === 'running') {
    buttons += `<button class="factory-btn factory-btn-sm ${_autoMode ? 'factory-btn-primary' : 'factory-btn-ghost'}" id="factory-auto-btn" title="Toggle autonomous mode">🤖 Auto</button>`;
  }

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
    // Full restart for completed/failed/blocked projects — partial only for
    // running/paused (where we just want to retry stuck tasks, not redo work).
    const mode = ['completed', 'failed', 'blocked'].includes(info.status) ? 'full' : 'partial';
    try { await restartProject(project.id, mode); await _openProjectStatus(project.id); }
    catch (err) { alert('Restart failed: ' + err.message); }
  });
  _el('factory-preview-project-btn')?.addEventListener('click', () => {
    if (_isNodeProject(project)) {
      _serveNodeProject(project);
    } else {
      _previewProject(project);
    }
  });
  _el('factory-auto-btn')?.addEventListener('click', async () => {
    const btn = _el('factory-auto-btn');
    if (!btn) return;
    const newState = !_autoMode;
    btn.disabled = true;
    btn.textContent = '...';
    try {
      await _fetchJSON(`${_API}/projects/${project.id}/start-autonomous`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ autonomous: newState }),
      });
      _autoMode = newState;
      btn.classList.toggle('factory-btn-primary', newState);
      btn.classList.toggle('factory-btn-ghost', !newState);
    } catch (err) {
      alert('Auto mode toggle failed: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '🤖 Auto';
    }
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

/**
 * Extract the output text from a task's result, which may be either an
 * object ({output: "...", ...}) or a JSON string that needs parsing.
 * Returns the actual content string (HTML, code, etc.) or empty string.
 */
function _getOutput(result) {
  // Null/undefined → empty
  if (!result) return '';

  // String — could be JSON or plain text
  if (typeof result === 'string') {
    try {
      const parsed = JSON.parse(result);
      if (parsed && typeof parsed === 'object') {
        const val = parsed.output;
        if (val !== undefined && val !== null) {
          if (typeof val === 'string') return val;
          if (typeof val === 'object') return JSON.stringify(val);
          return String(val);
        }
      }
    } catch (_) { /* not JSON — use the string as-is */ }
    return result;
  }

  // Object — extract .output field
  if (typeof result === 'object') {
    const val = result.output;
    if (val !== undefined && val !== null) {
      if (typeof val === 'string') return val;
      if (typeof val === 'object') return JSON.stringify(val);
      return String(val);
    }
    return '';
  }

  // Unexpected type — empty
  return '';
}

function _esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

/**
 * Check if a project has a package.json (Node.js project).
 */
function _isNodeProject(project) {
  return (project.tasks || []).some(t =>
    t.status === 'completed' &&
    (t.filename || '').toLowerCase() === 'package.json'
  );
}

function _isPreviewable(task) {
  if (!task || task.status !== 'completed') return false;
  const output = _getOutput(task.result);
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

/**
 * Estimate how many output tokens a task will likely require.
 * Uses description complexity + task type + filename to produce a
 * rough but directionally-correct estimate. Shown to the user as a
 * risk indicator (green/yellow/red) so they know upfront whether a
 * task is likely to hit the token budget and truncate.
 */
function _estimateTokens(task) {
  const desc = ((task.description || '') + ' ' + (task.title || '')).trim();
  if (!desc) return 500;

  const tt = (task.task_type || '').toLowerCase().trim();
  const fname = (task.filename || '').toLowerCase().trim();

  // Feature count: description clauses separated by commas, "and",
  // numbered items, semicolons, newlines.
  const features = desc.split(/[,;]|\band\b|\balso\b|\n|\d+[.)]/)
    .map(s => s.trim())
    .filter(s => s.length > 8);
  const featureCount = Math.max(1, features.length);

  const words = desc.split(/\s+/).filter(w => w.length > 0).length;

  // Token profiles by task type: {base, perFeature}
  const profiles = {
    frontend:   { base: 600, perFeat: 550 },
    design:     { base: 600, perFeat: 550 },
    ui:         { base: 500, perFeat: 450 },
    'space-ui': { base: 500, perFeat: 500 },
    backend:    { base: 400, perFeat: 350 },
    code:       { base: 400, perFeat: 350 },
    api:        { base: 400, perFeat: 350 },
    network:    { base: 400, perFeat: 350 },
    devops:     { base: 200, perFeat: 180 },
    infra:      { base: 200, perFeat: 180 },
    test:       { base: 300, perFeat: 250 },
    docs:       { base: 200, perFeat: 180 },
    execute:    { base: 100, perFeat: 80 },
  };
  const p = profiles[tt] || { base: 400, perFeat: 350 };
  let est = p.base + (featureCount * p.perFeat);

  // HTML files expand more (markup + inline styles + scripts)
  if (fname.endsWith('.html') || fname.endsWith('.htm')) est = Math.round(est * 1.3);
  else if (fname.endsWith('.css') || fname.endsWith('.scss')) est = Math.round(est * 1.1);

  // Cross-check with word-based estimate: code ≈ 4× desc words × 1.3 tokens/word
  const wordEst = Math.round(words * 4 * 1.3);

  return Math.max(est, wordEst);
}

/**
 * Build a colored token-budget badge for a task.
 */
function _tokenBadge(task) {
  const est = _estimateTokens(task);
  const budget = _produceMaxTokens || 16384;
  const pct = est / budget;
  const estK = est >= 1024 ? (est / 1024).toFixed(1).replace(/\.0$/, '') + 'K' : est.toString();
  const budK = Math.round(budget / 1024) + 'K';

  let cls;
  if (pct < 0.6) cls = 'factory-token-safe';
  else if (pct < 0.85) cls = 'factory-token-moderate';
  else cls = 'factory-token-risky';

  const titleText = pct >= 0.85
    ? `Estimated ~${est.toLocaleString()} tokens (budget ${budget.toLocaleString()}). HIGH RISK of truncation — consider splitting the task.`
    : `Estimated ~${est.toLocaleString()} tokens (budget ${budget.toLocaleString()}).`;

  return `<span class="factory-token-badge ${cls}" title="${_esc(titleText)}">~${estK}/${budK}</span>`;
}

/**
 * Detect the highlight.js language name from a filename.
 */
function _detectLanguage(fname) {
  const ext = (fname.split('.').pop() || '').toLowerCase();
  const map = {
    html: 'html', htm: 'html',
    css: 'css', scss: 'scss', less: 'less',
    js: 'javascript', mjs: 'javascript', cjs: 'javascript',
    ts: 'typescript', tsx: 'tsx', jsx: 'jsx',
    py: 'python', pyw: 'python',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'ini',
    md: 'markdown', markdown: 'markdown',
    sh: 'bash', bash: 'bash', zsh: 'bash',
    sql: 'sql',
    xml: 'xml', svg: 'xml',
    go: 'go', rs: 'rust', java: 'java', kt: 'kotlin',
    c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
    rb: 'ruby', php: 'php', dart: 'dart', swift: 'swift',
    vue: 'xml', svelte: 'xml',
    dockerfile: 'dockerfile',
  };
  return map[ext] || 'plaintext';
}

/**
 * Get a short label for a file type (shown as a colored badge).
 */
function _fileTypeLabel(fname) {
  const ext = (fname.split('.').pop() || '').toLowerCase();
  const colors = {
    html: 'HTML', htm: 'HTML',
    css: 'CSS', scss: 'SCSS',
    js: 'JS', mjs: 'JS', cjs: 'JS',
    ts: 'TS', tsx: 'TSX',
    py: 'PY', json: 'JSON', yaml: 'YML', yml: 'YML',
    md: 'MD', sh: 'SH', sql: 'SQL', go: 'GO', rs: 'RS',
    xml: 'XML', svg: 'SVG', vue: 'VUE',
  };
  return colors[ext] || ext.toUpperCase().slice(0, 4);
}

/**
 * Extract completed files from a project's task list.
 */
function _getProjectFiles(project) {
  const tasks = project.tasks || [];
  const all = tasks
    .filter(t => t.status === 'completed' && t.filename)
    .map(t => ({
      filename: t.filename,
      content: _getOutput(t.result),
      taskId: t.id,
      taskType: t.task_type || '',
      language: _detectLanguage(t.filename),
      typeLabel: _fileTypeLabel(t.filename),
    }))
    .filter(f => f.content);

  // Deduplicate by filename — keep latest version (highest taskId).
  // When iteration updates a file, the older version is superseded.
  const byName = {};
  all.forEach(f => {
    if (!byName[f.filename] || byName[f.filename].taskId < f.taskId) {
      byName[f.filename] = f;
    }
  });
  return Object.values(byName).sort((a, b) => a.filename.localeCompare(b.filename));
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
