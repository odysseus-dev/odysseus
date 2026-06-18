// agent-studio/widget.js
// Registers a full-page app view that shows an Open WebUI instance
// running in Docker, with container start/stop controls.

(function () {
  const pkg = window.OdysseusPkg;
  if (!pkg?.registerAppView) {
    console.warn('[agent-studio] OdysseusPkg.registerAppView not available');
    return;
  }
  const { registerAppView } = pkg;

  async function request(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    return r.json();
  }

  const PROXY = '/pkgs/agent-studio/proxy';
  let _pollTimer = null;
  let _mounted = false;

  // ── CSS ────────────────────────────────────────────────────────────────────

  const STYLES = `
    #as-root { display:flex; flex-direction:column; height:100%; width:100%; background:#0f0f1a; }

    /* toolbar */
    #as-bar {
      display:flex; align-items:center; gap:12px;
      padding:8px 16px; background:#12121f;
      border-bottom:1px solid #1e1e30; flex-shrink:0;
    }
    #as-bar-title { font-weight:600; color:#e2e8f0; font-size:14px; }
    #as-status-dot {
      width:8px; height:8px; border-radius:50%; background:#555;
      transition:background 0.3s;
    }
    #as-status-txt { font-size:12px; color:#6b7280; }
    .as-btn {
      padding:4px 14px; border:none; border-radius:6px;
      cursor:pointer; font-size:12px; transition:opacity 0.15s;
    }
    .as-btn:hover { opacity:0.85; }
    .as-btn-start  { background:#4f46e5; color:#fff; }
    .as-btn-stop   { background:#374151; color:#9ca3af; }
    .as-btn-remove { background:#7f1d1d; color:#fca5a5; }
    .as-btn-logs   { background:#1f2937; color:#6b7280; border:1px solid #374151; }
    .as-ml-auto    { margin-left:auto; display:flex; gap:6px; align-items:center; }

    /* frame area */
    #as-body { flex:1; min-height:0; position:relative; overflow:hidden; }
    #as-frame { width:100%; height:100%; border:none; display:block; }

    /* placeholder */
    #as-placeholder {
      display:flex; flex-direction:column; align-items:center; justify-content:center;
      height:100%; gap:20px; color:#6b7280;
    }
    #as-ph-icon  { font-size:64px; opacity:0.6; }
    #as-ph-title { font-size:20px; font-weight:600; color:#94a3b8; }
    #as-ph-msg   { font-size:13px; text-align:center; max-width:320px; line-height:1.6; }
    #as-ph-cta   {
      padding:10px 28px; background:#4f46e5; color:#fff;
      border:none; border-radius:8px; cursor:pointer; font-size:14px;
    }
    #as-ph-cta:hover { background:#4338ca; }

    /* logs panel */
    #as-logs-panel {
      display:none; position:absolute; bottom:0; left:0; right:0;
      height:200px; background:#0a0a12; border-top:1px solid #1e1e30;
      overflow-y:auto; font-family:monospace; font-size:11px;
      color:#6b7280; padding:8px 12px; white-space:pre-wrap;
    }
    #as-logs-close {
      position:absolute; top:6px; right:10px; background:none; border:none;
      color:#6b7280; cursor:pointer; font-size:16px;
    }

    /* spinner */
    .as-spin {
      width:14px; height:14px; border:2px solid #333;
      border-top-color:#4f46e5; border-radius:50%;
      animation:as-rotate 0.8s linear infinite;
    }
    @keyframes as-rotate { to { transform:rotate(360deg); } }

    /* error toast */
    #as-error {
      display:none; position:absolute; bottom:16px; left:50%;
      transform:translateX(-50%); background:#7f1d1d; color:#fca5a5;
      padding:8px 20px; border-radius:8px; font-size:13px;
      max-width:80%; text-align:center; z-index:100;
    }
  `;

  function _injectStyles() {
    if (document.getElementById('as-styles')) return;
    const s = document.createElement('style');
    s.id = 'as-styles';
    s.textContent = STYLES;
    document.head.appendChild(s);
  }

  // ── DOM helpers ────────────────────────────────────────────────────────────

  function _el(id) { return document.getElementById(id); }

  function _render(container) {
    container.innerHTML = `
      <div id="as-root">
        <div id="as-bar">
          <span id="as-bar-title">🤖 Agent Studio</span>
          <span id="as-status-dot"></span>
          <span id="as-status-txt">Checking…</span>
          <div id="as-spinner" class="as-spin" style="display:none"></div>
          <div class="as-ml-auto">
            <button class="as-btn as-btn-start"  id="as-btn-start"  onclick="__as.start()">Start</button>
            <button class="as-btn as-btn-stop"   id="as-btn-stop"   onclick="__as.stop()" style="display:none">Stop</button>
            <button class="as-btn as-btn-logs"   id="as-btn-logs"   onclick="__as.toggleLogs()">Logs</button>
            <button class="as-btn as-btn-remove" id="as-btn-remove" onclick="__as.remove()" style="display:none" title="Remove container">✕ Remove</button>
            <button class="as-btn as-btn-logs"   onclick="__as.refresh()" title="Refresh status">↺</button>
          </div>
        </div>

        <div id="as-body">
          <div id="as-placeholder">
            <div id="as-ph-icon">🤖</div>
            <div id="as-ph-title">Agent Studio</div>
            <div id="as-ph-msg">
              Open WebUI provides a full multi-agent environment.<br>
              Start the container to launch your agent studio.
            </div>
            <button id="as-ph-cta" class="as-btn as-btn-start" onclick="__as.start()">
              Start Agent Studio
            </button>
          </div>

          <iframe id="as-frame" src="about:blank" style="display:none"
            allow="clipboard-read; clipboard-write; microphone; camera"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-top-navigation-by-user-activation">
          </iframe>

          <div id="as-logs-panel">
            <button id="as-logs-close" onclick="__as.toggleLogs()">✕</button>
            <div id="as-logs-content">Loading logs…</div>
          </div>
          <div id="as-error"></div>
        </div>
      </div>
    `;
  }

  // ── state machine ──────────────────────────────────────────────────────────

  function _setStatus(status, extraMsg) {
    const dot   = _el('as-status-dot');
    const txt   = _el('as-status-txt');
    const spin  = _el('as-spinner');
    const frame = _el('as-frame');
    const ph    = _el('as-placeholder');
    const phMsg = _el('as-ph-msg');
    const start = _el('as-btn-start');
    const stop  = _el('as-btn-stop');
    const rem   = _el('as-btn-remove');
    if (!dot) return;

    const colors = {
      running:   '#22c55e',
      starting:  '#f59e0b',
      exited:    '#ef4444',
      not_found: '#555',
      error:     '#ef4444',
    };
    dot.style.background = colors[status] || '#555';

    if (status === 'running') {
      txt.textContent = 'Running';
      spin.style.display = 'none';
      start.style.display = 'none';
      stop.style.display  = '';
      rem.style.display   = '';
      if (frame.src === 'about:blank' || frame.style.display === 'none') {
        frame.src           = PROXY + '/';
        frame.style.display = 'block';
        ph.style.display    = 'none';
      }
    } else if (status === 'starting') {
      txt.textContent = extraMsg || 'Starting…';
      spin.style.display = '';
      start.style.display = 'none';
      stop.style.display = 'none';
      rem.style.display  = '';
      if (phMsg) phMsg.textContent = 'Container is starting. This may take a moment…';
    } else if (status === 'exited' || status === 'stopped') {
      txt.textContent = 'Stopped';
      spin.style.display = 'none';
      start.style.display = '';
      stop.style.display  = 'none';
      rem.style.display   = '';
      frame.src           = 'about:blank';
      frame.style.display = 'none';
      ph.style.display    = 'flex';
      if (phMsg) phMsg.textContent = 'Container is stopped. Click Start to resume.';
    } else if (status === 'not_found') {
      txt.textContent = 'Not started';
      spin.style.display = 'none';
      start.style.display = '';
      stop.style.display  = 'none';
      rem.style.display   = 'none';
      frame.src           = 'about:blank';
      frame.style.display = 'none';
      ph.style.display    = 'flex';
      if (phMsg) phMsg.textContent = 'Agent Studio hasn\'t been started yet.\nClick the button below to launch it in Docker.';
    } else {
      txt.textContent = extraMsg || status;
      spin.style.display = 'none';
      start.style.display = '';
      stop.style.display  = 'none';
      rem.style.display   = 'none';
      frame.src           = 'about:blank';
      frame.style.display = 'none';
      ph.style.display    = 'flex';
    }
  }

  function _showError(msg) {
    const el = _el('as-error');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { if (el) el.style.display = 'none'; }, 6000);
  }

  function _stopPoll() {
    if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
  }

  function _startPoll(interval = 4000) {
    _stopPoll();
    _pollTimer = setTimeout(async () => {
      if (!_mounted) return;
      await _refresh();
    }, interval);
  }

  async function _refresh() {
    try {
      const r = await request('GET', '/pkgs/agent-studio/status');
      if (!_mounted) return r;
      _setStatus(r.status, r.error);
      if (r.status === 'starting' || r.status === 'restarting' || r.status === 'created') {
        _startPoll(3000); // poll faster while starting
      } else if (r.status === 'running') {
        _startPoll(15000); // check every 15s once running
      }
      return r;
    } catch (e) {
      console.error('[agent-studio] status error:', e);
      return { status: 'error' };
    }
  }

  // ── actions ────────────────────────────────────────────────────────────────

  async function _start() {
    _stopPoll();
    _setStatus('starting', 'Launching container…');
    try {
      await request('POST', '/pkgs/agent-studio/start');
      // Poll until container is up
      let attempts = 0;
      const poll = async () => {
        if (!_mounted) return;
        const r = await _refresh();
        if (r.status !== 'running' && attempts++ < 30) {
          _startPoll(4000);
        }
      };
      setTimeout(poll, 4000);
    } catch (e) {
      _showError('Failed to start: ' + (e.message || String(e)));
      _refresh();
    }
  }

  async function _stop() {
    _stopPoll();
    try {
      await request('POST', '/pkgs/agent-studio/stop');
      _setStatus('exited');
    } catch (e) {
      _showError('Failed to stop: ' + (e.message || String(e)));
    }
    _refresh();
  }

  async function _remove() {
    if (!confirm('Remove the Agent Studio container? Data volume will be kept.')) return;
    _stopPoll();
    try {
      await request('DELETE', '/pkgs/agent-studio/remove');
      _setStatus('not_found');
    } catch (e) {
      _showError('Remove failed: ' + (e.message || String(e)));
    }
  }

  async function _toggleLogs() {
    const panel = _el('as-logs-panel');
    if (!panel) return;
    if (panel.style.display === 'none' || !panel.style.display) {
      panel.style.display = 'block';
      try {
        const r = await request('GET', '/pkgs/agent-studio/logs?lines=200');
        const el = _el('as-logs-content');
        if (el) el.textContent = r.logs || '(no logs)';
      } catch (e) {
        const el = _el('as-logs-content');
        if (el) el.textContent = 'Error loading logs: ' + e.message;
      }
    } else {
      panel.style.display = 'none';
    }
  }

  // ── mount / unmount ────────────────────────────────────────────────────────

  function _mount(container) {
    _mounted = true;
    _injectStyles();
    _render(container);

    // Expose actions on a stable global so inline onclick handlers work
    window.__as = {
      start:      _start,
      stop:       _stop,
      remove:     _remove,
      refresh:    _refresh,
      toggleLogs: _toggleLogs,
    };

    _refresh();
  }

  function _unmount() {
    _mounted = false;
    _stopPoll();
    delete window.__as;
  }

  // ── register ───────────────────────────────────────────────────────────────

  registerAppView('agent-studio', {
    icon:      '🤖',
    label:     'Agent Studio',
    onMount:   _mount,
    onUnmount: _unmount,
  });
})();
