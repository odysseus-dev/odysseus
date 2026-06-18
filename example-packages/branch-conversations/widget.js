/**
 * Branch Conversations — widget.js
 *
 * Injects a ⎇ button into .chat-top-bar that opens a pannable branch-tree
 * panel sliding in from the right. Also registers a sidebar widget showing
 * the parent-branch status for the current session.
 *
 * Uses:
 *   window.OdysseusPkg.events  — 'session:selected' (emitted by sessions.js)
 *   window.OdysseusShell.getSelectedSession() — initial session on load
 */
(() => {
  const API = '/api/branch-conversations';

  // ── Styles ──────────────────────────────────────────────────────────────────
  if (!document.getElementById('bc-styles')) {
    const s = document.createElement('style');
    s.id = 'bc-styles';
    s.textContent = `
      .bc-panel {
        position: fixed; top: 0; right: 0; bottom: 0; width: 420px;
        max-width: 100vw; background: var(--panel, #111);
        border-left: 1px solid var(--border); display: flex;
        flex-direction: column; z-index: 210;
        transform: translateX(105%);
        transition: transform .25s cubic-bezier(.4,0,.2,1);
        box-shadow: -4px 0 24px rgba(0,0,0,.35);
      }
      .bc-panel.bc-open { transform: translateX(0); }
      .bc-panel-hdr {
        display: flex; align-items: center; justify-content: space-between;
        padding: 10px 12px; border-bottom: 1px solid var(--border);
        flex-shrink: 0; min-height: 40px;
      }
      .bc-panel-title {
        display: flex; align-items: center; font-size: 12px; font-weight: 600;
        text-transform: uppercase; letter-spacing: .5px; opacity: .7; color: var(--fg);
      }
      .bc-panel-controls { display: flex; gap: 4px; }
      .bc-ctrl {
        background: none; border: none; cursor: pointer; color: var(--fg);
        opacity: .5; padding: 4px 6px; border-radius: 5px;
        display: flex; align-items: center;
        transition: opacity .1s, background .1s;
      }
      .bc-ctrl:hover { opacity: 1; background: color-mix(in srgb,var(--fg) 10%,transparent); }

      .bc-canvas {
        flex: 1; overflow: hidden; position: relative;
        cursor: grab; user-select: none;
      }
      .bc-canvas:active { cursor: grabbing; }
      #bc-canvas-wrap { position: absolute; }

      .bc-info {
        padding: 32px 24px; font-size: 12px; color: var(--fg);
        opacity: .45; text-align: center;
      }
      .bc-err { color: var(--danger,#e55); opacity: .8; }

      .bc-node {
        position: absolute; border: 1px solid var(--border); border-radius: 9px;
        background: var(--bg); box-sizing: border-box;
        display: flex; flex-direction: column; justify-content: space-between;
        padding: 9px 10px 7px; cursor: default;
        transition: border-color .12s, box-shadow .12s, background .12s;
        width: 170px;
      }
      .bc-node:hover {
        border-color: var(--accent,#63b3ed);
        box-shadow: 0 0 0 2px color-mix(in srgb,var(--accent,#63b3ed) 18%,transparent);
      }
      .bc-node-active {
        border-color: var(--accent,#63b3ed);
        background: color-mix(in srgb,var(--accent,#63b3ed) 10%,var(--bg));
      }
      .bc-node-body { cursor: pointer; }
      .bc-node-name {
        font-size: 12px; font-weight: 500; color: var(--fg);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        margin-bottom: 3px;
      }
      .bc-node-active .bc-node-name { color: var(--accent,#63b3ed); font-weight: 600; }
      .bc-node-meta {
        font-size: 10px; color: var(--fg); opacity: .45;
        display: flex; align-items: center; gap: 4px; white-space: nowrap;
      }
      .bc-badge {
        background: color-mix(in srgb,var(--accent,#63b3ed) 22%,transparent);
        color: var(--accent,#63b3ed); border-radius: 3px; padding: 0 4px;
        font-size: 9px; font-weight: 700; line-height: 14px;
      }
      .bc-node-actions {
        display: flex; gap: 3px; margin-top: 7px; opacity: 0;
        transition: opacity .12s;
      }
      .bc-node:hover .bc-node-actions { opacity: 1; }
      .bc-btn {
        background: none; border: 1px solid var(--border); border-radius: 4px;
        padding: 1px 6px; font-size: 11px; line-height: 1.5; cursor: pointer;
        color: var(--fg); opacity: .65;
        transition: opacity .1s,background .1s,border-color .1s,color .1s;
      }
      .bc-btn:hover {
        opacity: 1;
        background: color-mix(in srgb,var(--accent,#63b3ed) 14%,transparent);
        color: var(--accent,#63b3ed); border-color: var(--accent,#63b3ed);
      }
      .bc-btn-del:hover {
        background: color-mix(in srgb,var(--danger,#e55) 14%,transparent);
        color: var(--danger,#e55); border-color: var(--danger,#e55);
      }

      .bc-toggle-btn {
        position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
        background: none; border: none; cursor: pointer; color: var(--fg);
        opacity: .55; padding: 4px 5px; border-radius: 6px;
        display: flex; align-items: center; justify-content: center; z-index: 1;
        transition: opacity .1s,background .1s,color .1s;
      }
      .bc-toggle-btn:hover, .bc-toggle-btn.active {
        opacity: 1; color: var(--accent,#63b3ed);
        background: color-mix(in srgb,var(--accent,#63b3ed) 14%,transparent);
      }
      @media (max-width: 768px) { .bc-panel { width: 100vw; } }
    `;
    document.head.appendChild(s);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  async function _api(path, opts = {}) {
    const r = await fetch(API + path, opts);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  // ── Panel DOM ────────────────────────────────────────────────────────────────
  const panel = document.createElement('div');
  panel.className = 'bc-panel';
  panel.id = 'bc-panel';
  panel.innerHTML = `
    <div class="bc-panel-hdr">
      <span class="bc-panel-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;flex-shrink:0"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
        Branch Tree
      </span>
      <div class="bc-panel-controls">
        <button class="bc-ctrl" id="bc-refresh" title="Refresh">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.5 15a9 9 0 1 1-2.7-8.3L23 10"/></svg>
        </button>
        <button class="bc-ctrl" id="bc-close" title="Close">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>
    <div class="bc-canvas" id="bc-canvas">
      <div id="bc-canvas-wrap"></div>
    </div>
  `;
  document.body.appendChild(panel);

  // ── Toggle button in chat-top-bar ────────────────────────────────────────────
  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'bc-toggle-btn';
  toggleBtn.id = 'bc-toggle-btn';
  toggleBtn.title = 'Branch Tree';
  toggleBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>`;

  function _injectToggleBtn() {
    if (document.getElementById('bc-toggle-btn')) return;
    const bar = document.querySelector('.chat-top-bar');
    if (!bar) { setTimeout(_injectToggleBtn, 500); return; }
    bar.appendChild(toggleBtn);
  }
  _injectToggleBtn();

  // ── Canvas pan state ─────────────────────────────────────────────────────────
  const canvas = panel.querySelector('#bc-canvas');
  const wrap = panel.querySelector('#bc-canvas-wrap');
  let _panX = 0, _panY = 0, _dragging = false, _startX = 0, _startY = 0;

  canvas.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    _dragging = true;
    _startX = e.clientX - _panX;
    _startY = e.clientY - _panY;
  });
  window.addEventListener('mousemove', e => {
    if (!_dragging) return;
    _panX = e.clientX - _startX;
    _panY = e.clientY - _startY;
    wrap.style.transform = `translate(${_panX}px,${_panY}px)`;
  });
  window.addEventListener('mouseup', () => { _dragging = false; });

  function _resetPan() {
    _panX = 20; _panY = 20;
    wrap.style.transform = `translate(${_panX}px,${_panY}px)`;
  }

  // ── Tree rendering ───────────────────────────────────────────────────────────
  let _currentSession = null;

  const NODE_W = 170, NODE_H = 72, H_GAP = 40, V_GAP = 20;

  function _subtreeWidth(node) {
    if (!node.children || node.children.length === 0) return NODE_W;
    const childrenW = node.children.reduce((s, c) => s + _subtreeWidth(c) + H_GAP, -H_GAP);
    return Math.max(NODE_W, childrenW);
  }

  function _placeNodes(node, x, y, nodes, edges) {
    const sw = _subtreeWidth(node);
    const nx = x + (sw - NODE_W) / 2;
    nodes.push({ node, x: nx, y });

    if (node.children && node.children.length > 0) {
      let cx = x;
      for (const child of node.children) {
        const cw = _subtreeWidth(child);
        _placeNodes(child, cx, y + NODE_H + V_GAP, nodes, edges);
        const cnx = cx + (cw - NODE_W) / 2;
        edges.push({ x1: nx + NODE_W / 2, y1: y + NODE_H, x2: cnx + NODE_W / 2, y2: y + NODE_H + V_GAP });
        cx += cw + H_GAP;
      }
    }
  }

  function _renderTree(tree) {
    wrap.innerHTML = '';
    if (!tree) {
      wrap.innerHTML = '<div class="bc-info">No branch data.</div>';
      return;
    }

    const nodes = [], edges = [];
    _placeNodes(tree, 0, 0, nodes, edges);

    // Compute canvas size
    let maxX = 0, maxY = 0;
    for (const { x, y } of nodes) {
      maxX = Math.max(maxX, x + NODE_W + 20);
      maxY = Math.max(maxY, y + NODE_H + 20);
    }

    // SVG edges
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', maxX);
    svg.setAttribute('height', maxY);
    svg.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;overflow:visible';
    for (const { x1, y1, x2, y2 } of edges) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const my = (y1 + y2) / 2;
      path.setAttribute('d', `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`);
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', 'var(--border)');
      path.setAttribute('stroke-width', '1.5');
      svg.appendChild(path);
    }
    wrap.appendChild(svg);

    // Node divs
    for (const { node, x, y } of nodes) {
      const isActive = node.id === _currentSession;
      const div = document.createElement('div');
      div.className = 'bc-node' + (isActive ? ' bc-node-active' : '');
      div.style.cssText = `left:${x}px;top:${y}px;width:${NODE_W}px`;

      div.innerHTML = `
        <div class="bc-node-body">
          <div class="bc-node-name" title="${_esc(node.name)}">${_esc(node.name)}</div>
          <div class="bc-node-meta">
            <span>${node.message_count} msg${node.message_count !== 1 ? 's' : ''}</span>
            ${node.parent_session_id ? '<span class="bc-badge">branch</span>' : ''}
          </div>
        </div>
        <div class="bc-node-actions">
          <button class="bc-btn bc-btn-go" data-id="${_esc(node.id)}" title="Open session">Go</button>
          <button class="bc-btn bc-btn-branch" data-id="${_esc(node.id)}" title="Create branch from here">+ Branch</button>
          ${!isActive ? `<button class="bc-btn bc-btn-del" data-id="${_esc(node.id)}" title="Delete session">Del</button>` : ''}
        </div>
      `;
      wrap.appendChild(div);
    }

    // Button handlers
    wrap.addEventListener('click', async e => {
      const goBtn = e.target.closest('.bc-btn-go');
      const branchBtn = e.target.closest('.bc-btn-branch');
      const delBtn = e.target.closest('.bc-btn-del');

      if (goBtn) {
        const sid = goBtn.dataset.id;
        if (window.sessionModule?.selectSession) {
          await window.sessionModule.selectSession(sid);
        }
      } else if (branchBtn) {
        const sid = branchBtn.dataset.id;
        try {
          const data = await _api(`/session/${encodeURIComponent(sid)}/branch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          });
          if (window.sessionModule?.loadSessions) await window.sessionModule.loadSessions();
          if (window.sessionModule?.selectSession) await window.sessionModule.selectSession(data.id);
          _currentSession = data.id;
          _load();
        } catch (err) {
          console.error('[bc] branch failed:', err);
        }
      } else if (delBtn) {
        const sid = delBtn.dataset.id;
        if (!confirm('Delete this session?')) return;
        try {
          await fetch(`/api/session/${encodeURIComponent(sid)}`, { method: 'DELETE' });
          if (window.sessionModule?.loadSessions) await window.sessionModule.loadSessions();
          _load();
        } catch (err) {
          console.error('[bc] delete failed:', err);
        }
      }
    });

    wrap.style.width = maxX + 'px';
    wrap.style.height = maxY + 'px';
  }

  // ── Load tree for current session ────────────────────────────────────────────
  async function _load() {
    if (!_currentSession) {
      wrap.innerHTML = '<div class="bc-info">No session selected.</div>';
      return;
    }
    wrap.innerHTML = '<div class="bc-info">Loading…</div>';
    try {
      const data = await _api(`/session/${encodeURIComponent(_currentSession)}/branch-tree`);
      _resetPan();
      _renderTree(data.tree);
    } catch (err) {
      wrap.innerHTML = `<div class="bc-info bc-err">Error: ${_esc(err.message)}</div>`;
    }
  }

  // ── Panel open/close ─────────────────────────────────────────────────────────
  let _open = false;

  function _openPanel() {
    panel.classList.add('bc-open');
    toggleBtn.classList.add('active');
    _open = true;
    _load();
  }
  function _closePanel() {
    panel.classList.remove('bc-open');
    toggleBtn.classList.remove('active');
    _open = false;
  }

  toggleBtn.addEventListener('click', () => _open ? _closePanel() : _openPanel());
  panel.querySelector('#bc-close').addEventListener('click', _closePanel);
  panel.querySelector('#bc-refresh').addEventListener('click', _load);

  // ── Session selection listener ───────────────────────────────────────────────
  const pkg = window.OdysseusPkg;
  if (pkg?.events) {
    pkg.events.on('session:selected', ({ sessionId }) => {
      _currentSession = sessionId;
      if (_open) _load();
    });
  }

  // Pick up the session that's already selected when this widget loads
  const initial = window.OdysseusShell?.getSelectedSession?.();
  if (initial) _currentSession = initial;
})();
