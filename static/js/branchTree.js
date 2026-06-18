/**
 * branchTree.js — Branch Tree side panel
 *
 * Renders a draggable/pannable tree of parent/child branch sessions.
 * Opened via the ⎇ button in the chat top-bar.
 *
 * Public API (window.branchTreeModule):
 *   open(sessionId)   — open panel and load tree for sessionId
 *   close()           — close panel
 *   toggle(sessionId) — toggle panel
 *   refresh()         — reload tree for the current session
 *   init()            — wire DOM events (called by app.js)
 */

const NODE_W = 200;
const NODE_H = 68;
const H_GAP  = 36;   // horizontal gap between sibling columns
const V_GAP  = 60;   // vertical gap between parent and child rows
const API_BASE = window.location.origin;

let _sessionId = null;  // currently displayed session
let _panX = 0, _panY = 0;
let _dragging = false, _dsx = 0, _dsy = 0, _dox = 0, _doy = 0;

// ── Helpers ────────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Tree layout ────────────────────────────────────────────────────────────

function _computeWidths(node) {
  if (!node.children || !node.children.length) { node._w = 1; return; }
  node.children.forEach(_computeWidths);
  node._w = node.children.reduce((s, c) => s + c._w, 0);
}

function _assignPositions(node, depth, col) {
  node._depth = depth;
  if (!node.children || !node.children.length) {
    node._col = col;
    return col + 1;
  }
  let nextCol = col;
  for (const child of node.children) {
    nextCol = _assignPositions(child, depth + 1, nextCol);
  }
  // Center parent over its children
  const first = node.children[0];
  const last  = node.children[node.children.length - 1];
  node._col = (first._col + last._col) / 2;
  return nextCol;
}

function _flatten(node, out = []) {
  out.push(node);
  for (const c of node.children || []) _flatten(c, out);
  return out;
}

// ── Render ─────────────────────────────────────────────────────────────────

function _buildTree(tree, currentId) {
  _computeWidths(tree);
  const totalCols = tree._w;
  _assignPositions(tree, 0, 0);

  const nodes = _flatten(tree);
  const maxDepth = Math.max(...nodes.map(n => n._depth));

  const canvasW = totalCols * (NODE_W + H_GAP) + H_GAP;
  const canvasH = (maxDepth + 1) * (NODE_H + V_GAP) + V_GAP;

  // ── SVG edges ────────────────────────────────────────────────────────────
  let edgesSvg = '';
  for (const node of nodes) {
    const px = node._col  * (NODE_W + H_GAP) + H_GAP + NODE_W / 2;
    const py = node._depth * (NODE_H + V_GAP) + V_GAP + NODE_H;
    for (const child of node.children || []) {
      const cx = child._col  * (NODE_W + H_GAP) + H_GAP + NODE_W / 2;
      const cy = child._depth * (NODE_H + V_GAP) + V_GAP;
      const my = (py + cy) / 2;
      edgesSvg += `<path d="M${px},${py} C${px},${my} ${cx},${my} ${cx},${cy}" fill="none" stroke="var(--border)" stroke-width="1.5"/>`;
    }
  }

  // ── HTML nodes ───────────────────────────────────────────────────────────
  let nodesHtml = '';
  for (const node of nodes) {
    const nx = node._col  * (NODE_W + H_GAP) + H_GAP;
    const ny = node._depth * (NODE_H + V_GAP) + V_GAP;
    const isCurrent = node.id === currentId;

    // Badge: new messages in parent since fork
    let badge = '';
    if (node.parent_session_id && node.fork_message_index != null) {
      const parent = nodes.find(n => n.id === node.parent_session_id);
      if (parent) {
        const delta = Math.max(0, parent.message_count - node.fork_message_index);
        if (delta > 0) badge = `<span class="bt-badge" title="${delta} new message(s) in parent since fork">+${delta}</span>`;
      }
    }

    const shortName = node.name.length > 22 ? node.name.slice(0, 21) + '…' : node.name;
    const childCount = (node.children || []).length;
    const childLabel = childCount ? `· ${childCount} branch${childCount > 1 ? 'es' : ''}` : '';

    nodesHtml += `
      <div class="bt-node${isCurrent ? ' bt-node-active' : ''}"
           data-id="${_esc(node.id)}"
           style="left:${nx}px;top:${ny}px;width:${NODE_W}px;height:${NODE_H}px"
           title="${_esc(node.name)}">
        <div class="bt-node-body">
          <div class="bt-node-name">${_esc(shortName)}</div>
          <div class="bt-node-meta">${node.message_count || 0} msgs ${_esc(childLabel)} ${badge}</div>
        </div>
        <div class="bt-node-actions">
          <button class="bt-btn bt-btn-go"     data-id="${_esc(node.id)}" title="Open">→</button>
          <button class="bt-btn bt-btn-branch" data-id="${_esc(node.id)}" title="Create branch">⎇</button>
          <button class="bt-btn bt-btn-delete" data-id="${_esc(node.id)}" title="Delete">×</button>
        </div>
      </div>`;
  }

  return { canvasW, canvasH, edgesSvg, nodesHtml };
}

function _applyTransform() {
  const c = document.getElementById('bt-canvas-inner');
  if (c) c.style.transform = `translate(${_panX}px, ${_panY}px)`;
}

function _drawTree(tree, currentId) {
  const wrap = document.getElementById('bt-canvas-wrap');
  if (!wrap) return;

  const { canvasW, canvasH, edgesSvg, nodesHtml } = _buildTree(tree, currentId);

  wrap.innerHTML = `
    <div id="bt-canvas-inner" style="position:relative;width:${canvasW}px;height:${canvasH}px;transform:translate(${_panX}px,${_panY}px);will-change:transform">
      <svg style="position:absolute;top:0;left:0;pointer-events:none" width="${canvasW}" height="${canvasH}">
        ${edgesSvg}
      </svg>
      ${nodesHtml}
    </div>`;

  // ── Wire node actions ───────────────────────────────────────────────────
  wrap.querySelectorAll('.bt-btn-go').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      _navTo(btn.dataset.id);
    });
  });

  wrap.querySelectorAll('.bt-node-body').forEach(body => {
    body.addEventListener('click', () => {
      const id = body.closest('.bt-node')?.dataset.id;
      if (id) _navTo(id);
    });
  });

  wrap.querySelectorAll('.bt-btn-branch').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      await _createBranch(btn.dataset.id);
    });
  });

  wrap.querySelectorAll('.bt-btn-delete').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      await _deleteNode(btn.dataset.id);
    });
  });

  // ── Pan / drag ──────────────────────────────────────────────────────────
  const canvas = document.getElementById('bt-canvas');
  if (canvas) {
    canvas.onmousedown = e => {
      if (e.target.closest('.bt-btn') || e.target.closest('.bt-node-body')) return;
      _dragging = true;
      _dsx = e.clientX; _dsy = e.clientY;
      _dox = _panX;     _doy = _panY;
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
    };
    canvas.onmousemove = e => {
      if (!_dragging) return;
      _panX = _dox + (e.clientX - _dsx);
      _panY = _doy + (e.clientY - _dsy);
      _applyTransform();
    };
    const _stopDrag = () => { _dragging = false; canvas.style.cursor = 'grab'; };
    canvas.onmouseup = _stopDrag;
    canvas.onmouseleave = _stopDrag;
    canvas.style.cursor = 'grab';
  }
}

// ── Actions ────────────────────────────────────────────────────────────────

function _navTo(id) {
  if (window.sessionModule?.selectSession) {
    window.sessionModule.selectSession(id);
    // Refresh tree for the navigated-to session after a brief delay
    setTimeout(() => refresh(id), 400);
  }
}

async function _createBranch(fromId) {
  try {
    const res = await fetch(`${API_BASE}/api/session/${fromId}/branch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (window.sessionModule) {
      await window.sessionModule.loadSessions();
      window.sessionModule.selectSession(data.id);
    }
    setTimeout(() => refresh(data.id), 500);
  } catch (err) {
    alert('Failed to create branch: ' + err.message);
  }
}

async function _deleteNode(id) {
  if (!confirm('Delete this session and all its messages?')) return;
  try {
    await fetch(`${API_BASE}/api/session/${id}`, { method: 'DELETE' });
    if (window.sessionModule) await window.sessionModule.loadSessions();
    // If we deleted the currently-viewed session, pick any remaining
    const remaining = window.sessionModule?.getCurrentSessionId?.();
    const target = (remaining && remaining !== id) ? remaining : null;
    if (target) setTimeout(() => refresh(target), 400);
    else {
      const wrap = document.getElementById('bt-canvas-wrap');
      if (wrap) wrap.innerHTML = '<div class="bt-info">Session deleted.</div>';
    }
  } catch (err) {
    alert('Failed to delete: ' + err.message);
  }
}

// ── Load ───────────────────────────────────────────────────────────────────

async function _load(sessionId) {
  _sessionId = sessionId;
  const wrap = document.getElementById('bt-canvas-wrap');
  if (!wrap) return;

  wrap.innerHTML = '<div class="bt-info">Loading…</div>';

  if (!sessionId) {
    wrap.innerHTML = '<div class="bt-info">No session selected.</div>';
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/session/${sessionId}/branch-tree`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();

    if (!data.tree) {
      wrap.innerHTML = '<div class="bt-info">No branch data.</div>';
      return;
    }

    _drawTree(data.tree, data.current_session_id);
  } catch (err) {
    wrap.innerHTML = `<div class="bt-info bt-error">Error: ${_esc(err.message)}</div>`;
  }
}

// ── Public API ─────────────────────────────────────────────────────────────

export function open(sessionId) {
  const panel = document.getElementById('branch-tree-panel');
  if (!panel) return;
  _panX = 0; _panY = 0;
  panel.classList.add('bt-open');
  document.getElementById('bt-toggle-btn')?.classList.add('active');
  _load(sessionId);
}

export function close() {
  document.getElementById('branch-tree-panel')?.classList.remove('bt-open');
  document.getElementById('bt-toggle-btn')?.classList.remove('active');
}

export function toggle(sessionId) {
  const panel = document.getElementById('branch-tree-panel');
  if (!panel) return;
  if (panel.classList.contains('bt-open')) close();
  else open(sessionId || _sessionId || window.sessionModule?.getCurrentSessionId?.());
}

export function refresh(sessionId) {
  const sid = sessionId || _sessionId || window.sessionModule?.getCurrentSessionId?.();
  if (!sid) return;
  const panel = document.getElementById('branch-tree-panel');
  if (panel?.classList.contains('bt-open')) _load(sid);
}

export function init() {
  document.getElementById('bt-panel-close')?.addEventListener('click', close);
  document.getElementById('bt-panel-refresh')?.addEventListener('click', () => refresh());

  document.getElementById('bt-toggle-btn')?.addEventListener('click', () => {
    const sid = window.sessionModule?.getCurrentSessionId?.();
    toggle(sid);
  });
}

const branchTreeModule = { open, close, toggle, refresh, init };
window.branchTreeModule = branchTreeModule;
export default branchTreeModule;
