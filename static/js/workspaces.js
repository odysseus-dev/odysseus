/**
 * Docker Workspace Manager UI
 * Manages AI workspaces: create, start, stop, destroy, exec, logs.
 * Workspace state is completely independent of chat history.
 */

const WorkspaceManager = (() => {
  const API = '/api/docker-workspaces';
  let _workspaces = [];
  let _dockerAvailable = null;
  let _container = null;

  // ── Status badge ────────────────────────────────────────────────────────────
  function _statusBadge(status) {
    const map = {
      running:   { cls: 'ws-status-running',   icon: '●', label: 'Running' },
      stopped:   { cls: 'ws-status-stopped',   icon: '○', label: 'Stopped' },
      creating:  { cls: 'ws-status-creating',  icon: '⟳', label: 'Creating' },
      error:     { cls: 'ws-status-error',     icon: '✕', label: 'Error' },
      destroyed: { cls: 'ws-status-destroyed', icon: '✕', label: 'Destroyed' },
    };
    const s = map[status] || { cls: 'ws-status-stopped', icon: '?', label: status };
    return `<span class="ws-status-badge ${s.cls}">${s.icon} ${s.label}</span>`;
  }

  // ── API helpers ────────────────────────────────────────────────────────────
  async function _api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  }

  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Load workspaces ────────────────────────────────────────────────────────
  async function load(includeDestroyed = false) {
    try {
      const data = await _api('GET', `?include_destroyed=${includeDestroyed}`);
      _workspaces = data.workspaces || [];
      return _workspaces;
    } catch (e) {
      console.error('[WorkspaceManager] load failed:', e);
      return [];
    }
  }

  async function checkAvailable() {
    try {
      const data = await _api('GET', '/available');
      _dockerAvailable = data.available;
    } catch (e) {
      _dockerAvailable = false;
    }
    return _dockerAvailable;
  }

  // ── Render workspace card ──────────────────────────────────────────────────
  function _renderCard(ws) {
    const limits = ws.resource_limits || {};
    const cpu = limits.cpu ? `${limits.cpu} CPU` : '';
    const mem = limits.memory ? limits.memory : '';
    const resourceStr = [cpu, mem].filter(Boolean).join(' · ');

    return `
      <div class="ws-card" data-id="${ws.id}">
        <div class="ws-card-header">
          <div class="ws-card-title">
            <strong>${_esc(ws.name)}</strong>
            <code class="ws-image">${_esc(ws.template_image)}</code>
          </div>
          ${_statusBadge(ws.status)}
        </div>
        ${ws.description ? `<p class="ws-description">${_esc(ws.description)}</p>` : ''}
        <div class="ws-meta">
          ${resourceStr ? `<span class="ws-meta-item">⚡ ${resourceStr}</span>` : ''}
          ${ws.volume_name ? `<span class="ws-meta-item">💾 ${_esc(ws.volume_name)}</span>` : ''}
          ${ws.bound_agent_id ? `<span class="ws-meta-item">🤖 Agent bound</span>` : ''}
        </div>
        <div class="ws-card-actions">
          ${ws.status === 'stopped' ? `<button class="ws-btn ws-btn-start" data-id="${ws.id}">Start</button>` : ''}
          ${ws.status === 'running' ? `<button class="ws-btn ws-btn-stop" data-id="${ws.id}">Stop</button>` : ''}
          ${ws.status === 'running' ? `<button class="ws-btn ws-btn-exec" data-id="${ws.id}">Execute</button>` : ''}
          ${ws.status === 'running' ? `<button class="ws-btn ws-btn-logs" data-id="${ws.id}">Logs</button>` : ''}
          <button class="ws-btn ws-btn-destroy" data-id="${ws.id}">Destroy</button>
        </div>
      </div>
    `;
  }

  // ── Render create form ─────────────────────────────────────────────────────
  function _renderCreateForm() {
    return `
      <div class="ws-create-form" id="ws-create-form">
        <h3>Create Workspace</h3>
        <div class="ws-form-grid">
          <label class="ws-form-label">
            Name
            <input type="text" id="ws-name" class="ws-input" placeholder="my-project" maxlength="64">
          </label>
          <label class="ws-form-label">
            Docker Image
            <input type="text" id="ws-image" class="ws-input" value="python:3.11-alpine">
          </label>
          <label class="ws-form-label">
            Description (optional)
            <input type="text" id="ws-desc" class="ws-input" placeholder="What is this workspace for?">
          </label>
          <label class="ws-form-label">
            Memory Limit
            <input type="text" id="ws-memory" class="ws-input" value="512m" placeholder="512m, 1g...">
          </label>
          <label class="ws-form-label">
            CPU Limit
            <input type="number" id="ws-cpu" class="ws-input" value="0.5" min="0.1" max="4" step="0.1">
          </label>
        </div>
        <div class="ws-form-actions">
          <button class="ws-btn ws-btn-primary" id="ws-create-btn">Create Workspace</button>
          <button class="ws-btn ws-btn-cancel" id="ws-form-cancel">Cancel</button>
        </div>
        <div class="ws-create-error" id="ws-create-error" style="display:none"></div>
      </div>
    `;
  }

  // ── Render exec modal ──────────────────────────────────────────────────────
  function _showExecModal(wsId) {
    const existing = document.getElementById('ws-exec-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'ws-exec-modal';
    modal.className = 'ws-modal-overlay';
    modal.innerHTML = `
      <div class="ws-modal">
        <div class="ws-modal-header">
          <h3>Execute in Workspace</h3>
          <button class="ws-modal-close" id="ws-modal-close">✕</button>
        </div>
        <div class="ws-modal-body">
          <label class="ws-form-label">
            Command
            <input type="text" id="ws-exec-cmd" class="ws-input ws-exec-input" placeholder="ls -la /workspace" autofocus>
          </label>
          <button class="ws-btn ws-btn-primary" id="ws-exec-run">Run</button>
        </div>
        <pre class="ws-exec-output" id="ws-exec-output"></pre>
      </div>
    `;
    document.body.appendChild(modal);

    modal.querySelector('#ws-modal-close').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });

    const cmdInput = modal.querySelector('#ws-exec-cmd');
    const output = modal.querySelector('#ws-exec-output');

    async function runCmd(guardToken) {
      const cmd = cmdInput.value.trim();
      if (!cmd) return;
      output.textContent = '$ ' + cmd + '\nRunning...';
      try {
        const body = guardToken ? { command: cmd, guard_token: guardToken } : { command: cmd };
        const result = await _api('POST', `/${wsId}/exec`, body);

        if (result.status === 'pending_approval') {
          output.textContent = `$ ${cmd}\n⚠️ This command requires your approval (MEDIUM risk).\n`;
          const approveBar = document.createElement('div');
          approveBar.className = 'ws-approval-bar';
          approveBar.innerHTML = `
            <span class="ws-approval-msg">Command: <code>${_esc(result.command)}</code></span>
            <button class="ws-btn ws-btn-primary ws-btn-approve" data-token="${_esc(result.token)}">Approve &amp; Run</button>
            <button class="ws-btn ws-btn-deny">Deny</button>
          `;
          modal.querySelector('.ws-modal-body').appendChild(approveBar);

          approveBar.querySelector('.ws-btn-approve').addEventListener('click', async () => {
            const token = result.token;
            approveBar.remove();
            try {
              await _apiGuardDecide(token, 'approve');
              await runCmd(token);
            } catch (e) {
              output.textContent += `\nApproval error: ${e.message}`;
            }
          });
          approveBar.querySelector('.ws-btn-deny').addEventListener('click', () => {
            approveBar.remove();
            output.textContent += '\nCommand denied by user.';
          });
          return;
        }

        output.textContent = `$ ${cmd}\n${result.output || '(no output)'}`;
        if (result.exit_code !== 0) {
          output.textContent += `\n[Exit code: ${result.exit_code}]`;
        }
      } catch (e) {
        output.textContent = `Error: ${e.message}`;
      }
    }

    async function _apiGuardDecide(token, decision) {
      const res = await fetch('/api/guard/decide/' + token, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Guard decision failed');
      }
      return res.json();
    }

    modal.querySelector('#ws-exec-run').addEventListener('click', runCmd);
    cmdInput.addEventListener('keydown', e => { if (e.key === 'Enter') runCmd(); });
  }

  // ── Render logs modal ──────────────────────────────────────────────────────
  async function _showLogsModal(wsId) {
    const existing = document.getElementById('ws-logs-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'ws-logs-modal';
    modal.className = 'ws-modal-overlay';
    modal.innerHTML = `
      <div class="ws-modal ws-modal-wide">
        <div class="ws-modal-header">
          <h3>Workspace Logs</h3>
          <button class="ws-modal-close">✕</button>
        </div>
        <pre class="ws-logs-output" id="ws-logs-content">Loading...</pre>
      </div>
    `;
    document.body.appendChild(modal);
    modal.querySelector('.ws-modal-close').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });

    try {
      const data = await _api('GET', `/${wsId}/logs?tail=200`);
      modal.querySelector('#ws-logs-content').textContent = data.logs || '(no logs)';
    } catch (e) {
      modal.querySelector('#ws-logs-content').textContent = `Error: ${e.message}`;
    }
  }

  // ── Render panel ───────────────────────────────────────────────────────────
  function _renderPanel(container) {
    if (!_dockerAvailable) {
      container.innerHTML = `
        <div class="ws-panel">
          <div class="ws-unavailable">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/>
              <line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
            <h3>Docker Not Available</h3>
            <p>Docker is not running on this host. Start the Docker daemon to use AI workspaces.</p>
          </div>
        </div>
      `;
      return;
    }

    container.innerHTML = `
      <div class="ws-panel">
        <div class="ws-panel-header">
          <div>
            <h2 class="ws-panel-title">AI Workspaces</h2>
            <p class="ws-panel-subtitle">Persistent Docker containers — independent of chat history.</p>
          </div>
          <button class="ws-btn ws-btn-primary" id="ws-new-btn">+ New Workspace</button>
        </div>

        <div id="ws-form-container"></div>

        <div class="ws-list-header">
          <h3>Active Workspaces</h3>
          <span class="ws-count" id="ws-count">0 workspaces</span>
        </div>
        <div class="ws-list" id="ws-list">
          <div class="ws-empty">No workspaces yet.</div>
        </div>
      </div>
    `;

    container.querySelector('#ws-new-btn').addEventListener('click', () => _showCreateForm(container));
    _refreshList(container);
  }

  function _showCreateForm(container) {
    const formContainer = container.querySelector('#ws-form-container');
    formContainer.innerHTML = _renderCreateForm();

    formContainer.querySelector('#ws-form-cancel').addEventListener('click', () => {
      formContainer.innerHTML = '';
    });

    formContainer.querySelector('#ws-create-btn').addEventListener('click', async () => {
      const name = formContainer.querySelector('#ws-name').value.trim();
      const image = formContainer.querySelector('#ws-image').value.trim() || 'python:3.11-alpine';
      const desc = formContainer.querySelector('#ws-desc').value.trim();
      const memory = formContainer.querySelector('#ws-memory').value.trim() || '512m';
      const cpu = parseFloat(formContainer.querySelector('#ws-cpu').value) || 0.5;
      const errEl = formContainer.querySelector('#ws-create-error');

      if (!name) { errEl.textContent = 'Name is required.'; errEl.style.display = 'block'; return; }

      errEl.style.display = 'none';
      const btn = formContainer.querySelector('#ws-create-btn');
      btn.disabled = true;
      btn.textContent = 'Creating...';

      try {
        await _api('POST', '', {
          name, template_image: image, description: desc,
          resource_limits: { memory, cpu },
        });
        formContainer.innerHTML = '';
        await load();
        _refreshList(container);
        _notifyChanged();
      } catch (e) {
        errEl.textContent = e.message;
        errEl.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Create Workspace';
      }
    });
  }

  function _refreshList(container) {
    const listEl = container.querySelector('#ws-list');
    const countEl = container.querySelector('#ws-count');
    if (!listEl) return;

    if (_workspaces.length === 0) {
      listEl.innerHTML = '<div class="ws-empty">No workspaces. Create one above.</div>';
      if (countEl) countEl.textContent = '0 workspaces';
      return;
    }

    if (countEl) countEl.textContent = `${_workspaces.length} workspace${_workspaces.length !== 1 ? 's' : ''}`;
    listEl.innerHTML = _workspaces.map(_renderCard).join('');

    listEl.querySelectorAll('.ws-btn-start').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        btn.disabled = true; btn.textContent = 'Starting...';
        try { await _api('POST', `/${id}/start`); await load(); _refreshList(container); _notifyChanged(); }
        catch (e) { alert(`Start failed: ${e.message}`); btn.disabled = false; btn.textContent = 'Start'; }
      });
    });

    listEl.querySelectorAll('.ws-btn-stop').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        btn.disabled = true; btn.textContent = 'Stopping...';
        try { await _api('POST', `/${id}/stop`); await load(); _refreshList(container); _notifyChanged(); }
        catch (e) { alert(`Stop failed: ${e.message}`); btn.disabled = false; btn.textContent = 'Stop'; }
      });
    });

    listEl.querySelectorAll('.ws-btn-exec').forEach(btn => {
      btn.addEventListener('click', () => _showExecModal(btn.dataset.id));
    });

    listEl.querySelectorAll('.ws-btn-logs').forEach(btn => {
      btn.addEventListener('click', () => _showLogsModal(btn.dataset.id));
    });

    listEl.querySelectorAll('.ws-btn-destroy').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const ws = _workspaces.find(w => w.id === id);
        if (!confirm(`DESTROY workspace "${ws?.name || id}"?\n\nThis will permanently delete the container AND all data. This cannot be undone.`)) return;
        btn.disabled = true; btn.textContent = 'Destroying...';
        try { await _api('DELETE', `/${id}`); await load(); _renderPanel(container); _notifyChanged(); }
        catch (e) { alert(`Destroy failed: ${e.message}`); btn.disabled = false; btn.textContent = 'Destroy'; }
      });
    });
  }

  // ── Notify shell selector (and other listeners) that workspaces changed ──
  function _notifyChanged() {
    window.dispatchEvent(new CustomEvent('workspace-changed'));
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  async function init(container) {
    _container = container;
    await checkAvailable();
    await load();
    _renderPanel(container);
  }

  return { init, load, checkAvailable };
})();

export default WorkspaceManager;
