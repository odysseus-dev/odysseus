// static/js/chatTools.js — per-session MCP tool selection for agent chat
import Storage from './storage.js';
import uiModule from './ui.js';

const esc = uiModule.esc || ((s) => String(s || ''));

let _catalog = [];
let _sessionId = null;
let _pinned = new Set();
let _auto = new Set();
let _offManual = new Set();
let _panelOpen = false;

function _storageKey(sessionId) {
  return `odysseus-mcp-active-${sessionId || 'none'}`;
}

function _loadState(sessionId) {
  const data = Storage.getJSON(_storageKey(sessionId), { pinned: [], auto: [], off: [] });
  _pinned = new Set(data.pinned || []);
  _auto = new Set(data.auto || []);
  _offManual = new Set(data.off || []);
}

function _saveState() {
  if (!_sessionId) return;
  Storage.setJSON(_storageKey(_sessionId), {
    pinned: [..._pinned],
    auto: [..._auto],
    off: [..._offManual],
  });
}

export function getActiveMcpTools() {
  const active = new Set();
  for (const q of _pinned) active.add(q);
  for (const q of _auto) {
    if (!_offManual.has(q)) active.add(q);
  }
  return [...active];
}

const GENERIC_KW = new Set(['search', 'list', 'get', 'read', 'create', 'update', 'delete', 'fetch', 'tool', 'tools', 'run', 'call']);

function _ensureSession() {
  const sid = window.sessionModule?.getCurrentSessionId?.();
  if (sid && sid !== _sessionId) {
    _sessionId = sid;
    _loadState(sid);
  } else if (!_sessionId && sid) {
    _sessionId = sid;
    _loadState(sid);
  }
}

function _deriveKeywords(tool) {
  const kws = new Set();
  const add = (s) => {
    const v = String(s || '').trim().toLowerCase();
    if (v.length >= 2) kws.add(v);
  };
  add(tool.name);
  add(tool.server_name);
  const short = (tool.qualified || '').replace(/^mcp__[^_]+__/, '');
  add(short);
  short.split(/[_\-.]+/).forEach((t) => {
    if (t.length >= 4 && !GENERIC_KW.has(t)) add(t);
  });
  // Distinctive short tokens (arxiv, etc.)
  if (/\barxiv\b/i.test(short) || /\barxiv\b/i.test(tool.name || '')) add('arxiv');
  if (/\bgoogle\b/i.test(short) || /\bgoogle\b/i.test(tool.server_name || '')) add('google');
  return [...kws];
}

export async function loadCatalog() {
  try {
    const res = await fetch('/api/mcp/catalog', { credentials: 'same-origin' });
    if (!res.ok) return [];
    _catalog = await res.json();
    for (const t of _catalog) t._keywords = _deriveKeywords(t);
    _updateBtnCount();
    return _catalog;
  } catch {
    return [];
  }
}

function _updateBtnCount() {
  const btn = document.getElementById('mcp-tools-btn');
  if (!btn) return;
  const n = getActiveMcpTools().length;
  let badge = btn.querySelector('.mcp-tools-btn-count');
  if (n > 0) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'mcp-tools-btn-count';
      btn.appendChild(badge);
    }
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.style.display = '';
    btn.classList.add('has-mcp-count');
  } else if (badge) {
    badge.style.display = 'none';
    btn.classList.remove('has-mcp-count');
  }
}

export async function scanTextForKeywords(text) {
  if (!_catalog.length) await loadCatalog();
  const ql = String(text || '').toLowerCase();
  if (!ql.trim() || !_catalog.length) return false;
  _ensureSession();
  let changed = false;
  for (const tool of _catalog) {
    let matched = false;
    for (const kw of tool._keywords) {
      const re = new RegExp(`\\b${kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
      if (re.test(ql)) {
        matched = true;
        break;
      }
    }
    if (!matched) continue;
    // Typing a keyword again overrides a prior manual OFF for this conversation.
    if (_offManual.has(tool.qualified)) {
      _offManual.delete(tool.qualified);
      changed = true;
    }
    if (!_auto.has(tool.qualified)) {
      _auto.add(tool.qualified);
      changed = true;
    }
  }
  if (changed) {
    _saveState();
    renderBadges();
    _syncPanelChecks();
    _updateBtnCount();
  }
  return changed;
}

function _scheduleScan(el) {
  if (!el) return;
  clearTimeout(el._mcpScanTimer);
  el._mcpScanTimer = setTimeout(() => {
    scanTextForKeywords(el.value);
  }, 120);
}

export function wireTextSource(el) {
  if (!el || el._mcpKeywordWired) return;
  el._mcpKeywordWired = true;
  const run = () => _scheduleScan(el);
  el.addEventListener('input', run);
  el.addEventListener('paste', () => setTimeout(run, 0));
  el.addEventListener('change', run);
  // Scan current content when wired (e.g. after programmatic .value =)
  if (el.value) _scheduleScan(el);
}

function pinTool(qualified, on) {
  if (!qualified) return;
  if (on) {
    _pinned.add(qualified);
    _offManual.delete(qualified);
    _auto.add(qualified);
  } else {
    _pinned.delete(qualified);
    _auto.delete(qualified);
    _offManual.add(qualified);
  }
  _saveState();
  renderBadges();
  _syncPanelChecks();
  _updateBtnCount();
}

const MAX_VISIBLE_PILLS = 2;

function _toolLabel(q) {
  const meta = _catalog.find((t) => t.qualified === q);
  return meta ? meta.name : q.replace(/^mcp__[^_]+__/, '');
}

function _makePill(q, { summary = false, extraCount = 0 } = {}) {
  const label = summary && extraCount > 0
    ? `+${extraCount}`
    : _toolLabel(q);
  const pinned = _pinned.has(q);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'input-icon-btn tool-indicator mcp-tool-badge active'
    + (summary ? ' mcp-tool-badge-summary' : '');
  if (summary && extraCount > 0) {
    const names = getActiveMcpTools().map(_toolLabel).join(', ');
    btn.title = `${extraCount} more MCP tools — click to manage\n${names}`;
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      togglePanel(true);
    });
    btn.innerHTML = `<span class="mcp-tool-badge-label">${esc(label)}</span>`;
  } else {
    btn.title = pinned
      ? `${label} (pinned) — click to deactivate`
      : `${label} (auto) — click to deactivate`;
    btn.innerHTML =
      `<span class="mcp-tool-badge-label">${esc(label)}${pinned ? '' : ' · auto'}</span>`
      + '<svg class="tool-indicator-x" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      pinTool(q, false);
    });
  }
  return btn;
}

export function renderBadges() {
  const container = document.getElementById('mcp-tool-badges');
  if (!container) return;
  const active = getActiveMcpTools();
  container.innerHTML = '';
  if (!active.length) {
    container.classList.remove('has-badges');
    return;
  }
  container.classList.add('has-badges');
  if (active.length <= MAX_VISIBLE_PILLS) {
    for (const q of active) container.appendChild(_makePill(q));
  } else {
    container.appendChild(_makePill(active[0]));
    container.appendChild(_makePill(active[0], {
      summary: true,
      extraCount: active.length - 1,
    }));
  }
}

function _syncPanelChecks() {
  const panel = document.getElementById('mcp-tools-panel');
  if (!panel) return;
  const active = new Set(getActiveMcpTools());
  panel.querySelectorAll('input[data-mcp-qualified]').forEach((cb) => {
    cb.checked = active.has(cb.dataset.mcpQualified);
  });
  const countEl = panel.querySelector('.mcp-tools-chat-count');
  if (countEl && _catalog.length) {
    countEl.textContent = `${active.size}/${_catalog.length} active`;
  }
}

function _positionPanel() {
  const panel = document.getElementById('mcp-tools-panel');
  const btn = document.getElementById('mcp-tools-btn');
  if (!panel || !btn) return;
  const r = btn.getBoundingClientRect();
  const w = Math.min(320, Math.max(260, window.innerWidth - 16));
  let left = r.left;
  if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
  if (left < 8) left = 8;
  panel.style.width = `${w}px`;
  panel.style.left = `${left}px`;
  panel.style.bottom = `${window.innerHeight - r.top + 8}px`;
}

function _renderPanel() {
  const panel = document.getElementById('mcp-tools-panel');
  if (!panel) return;
  const active = getActiveMcpTools();
  if (!_catalog.length) {
    panel.innerHTML = `
      <div class="mcp-tools-chat-head">
        <div>
          <div class="mcp-tools-chat-title">MCP integrations</div>
          <div class="mcp-tools-chat-sub">Enable tools for the model to use in agent mode.</div>
        </div>
        <button type="button" class="mcp-tools-panel-close" id="mcp-tools-panel-close" aria-label="Close">×</button>
      </div>
      <div class="mcp-tools-panel-empty">No MCP tools available.<br><span style="opacity:0.7;font-size:11px;">Register them in Admin → Integrations.</span></div>`;
    _wirePanelActions(panel);
    return;
  }
  const byServer = {};
  for (const t of _catalog) {
    const key = t.server_name || t.server_id;
    (byServer[key] ||= []).push(t);
  }
  let html = `
    <div class="mcp-tools-chat-head">
      <div>
        <div class="mcp-tools-chat-title">MCP integrations</div>
        <div class="mcp-tools-chat-sub">Enable only the tools you want in this conversation.</div>
      </div>
      <button type="button" class="mcp-tools-panel-close" id="mcp-tools-panel-close" aria-label="Close">×</button>
    </div>
    <div class="mcp-tools-chat-toolbar">
      <span class="mcp-tools-chat-count">${active.length}/${_catalog.length} active</span>
      <a href="#" class="mcp-tools-chat-link" data-mcp-all="1">All</a>
      <a href="#" class="mcp-tools-chat-link" data-mcp-none="1">None</a>
    </div>`;
  if (_catalog.length > 6) {
    html += '<input type="search" class="mcp-tools-chat-search" placeholder="Search tools…" id="mcp-tools-chat-search">';
  }
  html += '<div class="mcp-tools-chat-list vis-toggles">';
  for (const [srv, tools] of Object.entries(byServer)) {
    html += `<div class="mcp-tools-chat-group"><div class="mcp-tools-chat-server">${esc(srv)}</div>`;
    for (const t of tools) {
      const checked = active.includes(t.qualified) ? 'checked' : '';
      const desc = esc((t.description || '').slice(0, 100));
      html += `<label class="vis-row mcp-tools-chat-row" title="${desc}" data-mcp-search="${esc(`${t.name} ${srv}`.toLowerCase())}">`
        + `<span class="vis-label"><strong>${esc(t.name)}</strong>`
        + (desc ? `<span class="mcp-tools-chat-desc">${desc}</span>` : '')
        + '</span>'
        + `<input type="checkbox" data-mcp-qualified="${esc(t.qualified)}" ${checked}>`
        + '<span class="vis-switch"></span></label>';
    }
    html += '</div>';
  }
  html += '</div>';
  panel.innerHTML = html;
  _wirePanelActions(panel);
}

function _wirePanelActions(panel) {
  panel.querySelector('#mcp-tools-panel-close')?.addEventListener('click', (e) => {
    e.preventDefault();
    togglePanel(false);
  });
  panel.querySelector('[data-mcp-all="1"]')?.addEventListener('click', (e) => {
    e.preventDefault();
    for (const t of _catalog) pinTool(t.qualified, true);
  });
  panel.querySelector('[data-mcp-none="1"]')?.addEventListener('click', (e) => {
    e.preventDefault();
    for (const t of _catalog) pinTool(t.qualified, false);
  });
  panel.querySelectorAll('input[data-mcp-qualified]').forEach((cb) => {
    cb.addEventListener('change', () => pinTool(cb.dataset.mcpQualified, cb.checked));
  });
  const search = panel.querySelector('#mcp-tools-chat-search');
  if (search) {
    search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      panel.querySelectorAll('.mcp-tools-chat-row').forEach((row) => {
        const hay = row.dataset.mcpSearch || '';
        row.style.display = !q || hay.includes(q) ? '' : 'none';
      });
    });
  }
}

export function togglePanel(force) {
  const panel = document.getElementById('mcp-tools-panel');
  const btn = document.getElementById('mcp-tools-btn');
  if (!panel) return;
  _panelOpen = typeof force === 'boolean' ? force : !_panelOpen;
  if (_panelOpen) {
    if (window.closeAllPopups) window.closeAllPopups('mcp-tools-panel');
    if (!panel.parentElement || panel.parentElement.id !== 'mcp-tools-panel-root') {
      let root = document.getElementById('mcp-tools-panel-root');
      if (!root) {
        root = document.createElement('div');
        root.id = 'mcp-tools-panel-root';
        document.body.appendChild(root);
      }
      root.appendChild(panel);
    }
    loadCatalog().then(() => {
      _renderPanel();
      panel.classList.remove('hidden');
      _positionPanel();
    });
  } else {
    panel.classList.add('hidden');
  }
  if (btn) {
    btn.classList.toggle('active', _panelOpen);
    btn.setAttribute('aria-expanded', _panelOpen ? 'true' : 'false');
  }
}

function _wirePanel() {
  const btn = document.getElementById('mcp-tools-btn');
  if (btn && !btn._mcpWired) {
    btn._mcpWired = true;
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      togglePanel();
    });
  }
  if (!document._mcpPanelDismissWired) {
    document._mcpPanelDismissWired = true;
    document.addEventListener('click', (e) => {
      if (!_panelOpen) return;
      if (e.target.closest('#mcp-tools-panel') || e.target.closest('#mcp-tools-btn') || e.target.closest('.mcp-tools-cluster')) return;
      togglePanel(false);
    });
    window.addEventListener('resize', () => { if (_panelOpen) _positionPanel(); });
    window.addEventListener('scroll', () => { if (_panelOpen) _positionPanel(); }, true);
  }
}

export function onSessionSwitch(sessionId) {
  _sessionId = sessionId;
  _loadState(sessionId);
  renderBadges();
  _updateBtnCount();
  _syncPanelChecks();
  if (_panelOpen) _renderPanel();
  // Re-scan composer in case keywords were typed before session was bound
  const msg = document.getElementById('message');
  if (msg?.value) scanTextForKeywords(msg.value);
}

export function initChatTools() {
  _wirePanel();
  _ensureSession();
  loadCatalog().then(() => {
    wireTextSource(document.getElementById('message'));
    renderBadges();
    _updateBtnCount();
  });
  if (!document._mcpEditTextWired) {
    document._mcpEditTextWired = true;
    document.addEventListener('input', (e) => {
      if (e.target?.classList?.contains('edit-textarea')) _scheduleScan(e.target);
    }, true);
    document.addEventListener('paste', (e) => {
      const t = e.target;
      if (t?.id === 'message' || t?.classList?.contains('edit-textarea')) {
        setTimeout(() => _scheduleScan(t), 0);
      }
    }, true);
  }
}

export default {
  initChatTools,
  onSessionSwitch,
  togglePanel,
  getActiveMcpTools,
  loadCatalog,
  wireTextSource,
  scanTextForKeywords,
  renderBadges,
};
