import uiModule from './ui.js';

let API_BASE = window.location.origin;
let _panelOpen = false;
let _mode = 'inbox';
let _countTimer = null;
let _bound = false;
let _anchorButton = null;

const ICONS = {
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  open: '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
};

function _svg(path, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

function _esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _relativeTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const diff = Date.now() - d.getTime();
  const abs = Math.abs(diff);
  if (abs < 60000) return 'just now';
  if (abs < 3600000) return `${Math.round(abs / 60000)}m ago`;
  if (abs < 86400000) return `${Math.round(abs / 3600000)}h ago`;
  return `${Math.round(abs / 86400000)}d ago`;
}

function _ensureShell() {
  const rail = document.getElementById('icon-rail');
  if (!document.getElementById('notification-inbox-btn')) {
    const railBtn = document.createElement('button');
    railBtn.id = 'notification-inbox-btn';
    railBtn.className = 'icon-rail-btn notification-inbox-btn';
    railBtn.type = 'button';
    railBtn.title = 'Notifications';
    railBtn.setAttribute('aria-label', 'Notifications');
    railBtn.innerHTML = `${_svg(ICONS.bell, 16)}<span class="notification-inbox-badge hidden">0</span>`;
    const separator = rail?.querySelector('.rail-separator');
    if (separator) separator.insertAdjacentElement('afterend', railBtn);
    else if (rail) rail.appendChild(railBtn);
    else document.body.appendChild(railBtn);
  }

  if (!document.getElementById('notification-inbox-sidebar-btn')) {
    const sidebarBtn = document.createElement('div');
    sidebarBtn.id = 'notification-inbox-sidebar-btn';
    sidebarBtn.className = 'list-item notification-sidebar-item';
    sidebarBtn.title = 'Notifications';
    sidebarBtn.setAttribute('role', 'button');
    sidebarBtn.setAttribute('tabindex', '0');
    sidebarBtn.innerHTML = `
      ${_svg(ICONS.bell, 14)}
      <span class="grow">Notifications</span>
      <span class="notification-inbox-badge notification-sidebar-badge hidden">0</span>
    `;
    const searchBtn = document.getElementById('sidebar-search-btn');
    const sessionsSection = document.getElementById('sessions-section');
    if (searchBtn) searchBtn.insertAdjacentElement('afterend', sidebarBtn);
    else if (sessionsSection) sessionsSection.insertAdjacentElement('beforebegin', sidebarBtn);
    else document.getElementById('sidebar')?.appendChild(sidebarBtn);
  }

  if (!document.getElementById('notification-inbox-panel')) {
    const panel = document.createElement('div');
    panel.id = 'notification-inbox-panel';
    panel.className = 'notification-inbox-panel hidden';
    panel.innerHTML = `
      <div class="notification-inbox-head">
        <div class="notification-inbox-title">${_svg(ICONS.inbox, 15)}<span id="notification-inbox-title">Notifications</span></div>
        <div class="notification-inbox-tools">
          <button id="notification-inbox-mode" class="notification-icon-btn" type="button" title="System log">${_svg(ICONS.list, 15)}</button>
          <button id="notification-inbox-close" class="notification-icon-btn" type="button" title="Close">${_svg(ICONS.x, 15)}</button>
        </div>
      </div>
      <div id="notification-inbox-list" class="notification-inbox-list"></div>
    `;
    document.body.appendChild(panel);
  }
}

function _setBadge(n) {
  const count = Number(n || 0);
  document.querySelectorAll('.notification-inbox-badge').forEach((badge) => {
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.classList.toggle('hidden', count <= 0);
  });
  document.querySelectorAll('.notification-inbox-btn, .notification-sidebar-item').forEach((btn) => {
    btn.classList.toggle('has-unread', count > 0);
  });
}

async function refreshCount() {
  try {
    const res = await fetch(`${API_BASE}/api/notifications/count`, { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    _setBadge(data.unread || 0);
  } catch (_) {}
}

function _setLoading() {
  const list = document.getElementById('notification-inbox-list');
  if (list) list.innerHTML = '<div class="notification-empty">Loading...</div>';
}

function _setEmpty(text) {
  const list = document.getElementById('notification-inbox-list');
  if (list) list.innerHTML = `<div class="notification-empty">${_esc(text)}</div>`;
}

async function _loadPanel() {
  const title = document.getElementById('notification-inbox-title');
  const modeBtn = document.getElementById('notification-inbox-mode');
  if (title) title.textContent = _mode === 'events' ? 'System log' : 'Notifications';
  if (modeBtn) modeBtn.title = _mode === 'events' ? 'Notifications' : 'System log';
  _setLoading();
  try {
    const path = _mode === 'events'
      ? `${API_BASE}/api/notifications/events?limit=80`
      : `${API_BASE}/api/notifications?limit=80`;
    const res = await fetch(path, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (_mode === 'events') _renderEvents(data.events || []);
    else _renderInbox(data.notifications || []);
    _positionPanel();
  } catch (e) {
    _setEmpty('Could not load notifications');
    _positionPanel();
  }
}

function _renderInbox(items) {
  const list = document.getElementById('notification-inbox-list');
  if (!list) return;
  if (!items.length) {
    _setEmpty('Nothing new');
    return;
  }
  list.innerHTML = items.map((item) => {
    const summary = item.body ? `<div class="notification-body">${_esc(item.body)}</div>` : '';
    const cls = [
      'notification-item',
      item.is_read ? 'is-read' : 'is-unread',
      `severity-${_esc(item.severity || 'info')}`,
    ].join(' ');
    return `
      <div class="${cls}" data-id="${_esc(item.id)}">
        <button class="notification-main" type="button" data-action="open">
          <span class="notification-item-title">${_esc(item.title || 'Notification')}</span>
          <span class="notification-item-meta">${_esc(_relativeTime(item.created_at))}</span>
          ${summary}
        </button>
        <div class="notification-actions">
          <button class="notification-icon-btn" type="button" title="Open" data-action="open">${_svg(ICONS.open, 14)}</button>
          <button class="notification-icon-btn" type="button" title="Mark read" data-action="read">${_svg(ICONS.check, 14)}</button>
          <button class="notification-icon-btn" type="button" title="Archive" data-action="archive">${_svg(ICONS.archive, 14)}</button>
          <button class="notification-icon-btn" type="button" title="Dismiss" data-action="dismiss">${_svg(ICONS.x, 14)}</button>
        </div>
      </div>
    `;
  }).join('');
  list.querySelectorAll('.notification-item').forEach((row) => {
    row.addEventListener('click', (e) => {
      const action = e.target.closest('[data-action]')?.dataset?.action;
      const item = items.find(n => n.id === row.dataset.id);
      if (!item || !action) return;
      e.stopPropagation();
      _handleItemAction(item, action);
    });
  });
}

function _renderEvents(events) {
  const list = document.getElementById('notification-inbox-list');
  if (!list) return;
  if (!events.length) {
    _setEmpty('No events');
    return;
  }
  list.innerHTML = events.map((event) => `
    <div class="notification-item is-read severity-${_esc(event.severity || 'info')}">
      <div class="notification-main static">
        <span class="notification-item-title">${_esc(event.title || 'Event')}</span>
        <span class="notification-item-meta">${_esc(_relativeTime(event.created_at))}</span>
        ${event.body ? `<div class="notification-body">${_esc(event.body)}</div>` : ''}
      </div>
    </div>
  `).join('');
}

async function _postItem(item, action) {
  const body = action === 'read' ? { read: true } : undefined;
  const res = await fetch(`${API_BASE}/api/notifications/${encodeURIComponent(item.id)}/${action}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function _handleItemAction(item, action) {
  try {
    if (action === 'open') {
      if (!item.is_read) await _postItem(item, 'read');
      _openTarget(item);
      _closePanel();
    } else {
      await _postItem(item, action);
    }
    await refreshCount();
    if (_panelOpen) await _loadPanel();
  } catch (e) {
    if (uiModule) uiModule.showError('Notification action failed');
  }
}

function _openTarget(item) {
  const meta = item.metadata || {};
  const taskId = meta.task_id || '';
  const actionUrl = item.action_url || item.source_url || '';
  if ((item.source_type === 'task_run' || actionUrl.startsWith('odysseus://tasks')) && window.tasksModule) {
    window.tasksModule.openTasks(taskId || null);
    return;
  }
  if (
    (item.source_type || '').startsWith('email')
    || actionUrl.startsWith('odysseus://email')
    || actionUrl.startsWith('#email')
  ) {
    _openEmailTarget(actionUrl);
    return;
  }
  if (
    item.source_type === 'chat_session'
    || item.source_type === 'session'
    || actionUrl.startsWith('odysseus://chat')
    || actionUrl.startsWith('odysseus://session')
    || actionUrl.startsWith('#chat=')
    || actionUrl.startsWith('#session=')
  ) {
    _openChatTarget(item, actionUrl);
    return;
  }
  if (actionUrl && actionUrl.startsWith('#')) {
    window.location.hash = actionUrl;
    return;
  }
  if (actionUrl && /^https?:\/\//.test(actionUrl)) {
    window.location.assign(actionUrl);
  }
}

function _openEmailTarget(actionUrl) {
  import('./emailLibrary.js')
    .then((mod) => {
      const open = mod.openEmailLibrary || (mod.default && mod.default.openEmailLibrary);
      if (typeof open !== 'function') throw new Error('Email library unavailable');
      open(_emailTargetOptions(actionUrl));
    })
    .catch(() => {
      document.querySelector('#email-section .section-header-flex')?.click();
    });
}

function _emailTargetOptions(actionUrl) {
  const opts = {};
  const match = String(actionUrl || '').match(/#email=([^:]+):(.+)$/);
  if (match) {
    opts.folder = decodeURIComponent(match[1]);
    opts.uid = decodeURIComponent(match[2]);
  }
  return opts;
}

function _openChatTarget(item, actionUrl) {
  const meta = item.metadata || {};
  const sessionId = meta.session_id || item.source_id || _chatTargetId(actionUrl);
  if (!sessionId || !window.sessionModule) return;
  const open = () => window.sessionModule.selectSession(sessionId);
  const sessions = window.sessionModule.getSessions?.() || [];
  if (sessions.some((s) => s.id === sessionId)) {
    open();
    return;
  }
  const load = window.sessionModule.loadSessions?.();
  if (load && typeof load.then === 'function') {
    load.then(open).catch(open);
  } else {
    open();
  }
}

function _chatTargetId(actionUrl) {
  const raw = String(actionUrl || '');
  const match = raw.match(/^odysseus:\/\/(?:chat|session)s?\/(.+)$/)
    || raw.match(/^#(?:chat|session)=(.+)$/);
  return match ? decodeURIComponent(match[1]) : '';
}

function _isNotificationTrigger(node) {
  return !!node?.closest?.('#notification-inbox-btn, #notification-inbox-sidebar-btn');
}

function _closePanel() {
  _panelOpen = false;
  const panel = document.getElementById('notification-inbox-panel');
  if (!panel) return;
  panel.classList.add('hidden');
  panel.style.left = '';
  panel.style.right = '';
  panel.style.top = '';
}

function _positionPanel(anchor = _anchorButton) {
  const panel = document.getElementById('notification-inbox-panel');
  if (!panel || panel.classList.contains('hidden')) return;
  const rect = anchor?.getBoundingClientRect?.();
  const margin = 10;
  const width = Math.min(360, window.innerWidth - margin * 2);
  panel.style.width = `${width}px`;
  panel.style.right = 'auto';
  panel.style.left = `${margin}px`;
  panel.style.top = `${margin}px`;
  const panelRect = panel.getBoundingClientRect();
  if (!rect) return;

  const railOnRight = rect.left > window.innerWidth / 2;
  const left = railOnRight
    ? Math.max(margin, rect.left - panelRect.width - 8)
    : Math.min(window.innerWidth - panelRect.width - margin, rect.right + 8);
  const top = Math.min(
    window.innerHeight - panelRect.height - margin,
    Math.max(margin, rect.top - 8)
  );
  panel.style.left = `${Math.max(margin, left)}px`;
  panel.style.top = `${Math.max(margin, top)}px`;
}

function _openPanel(anchor) {
  _anchorButton = anchor || document.getElementById('notification-inbox-btn');
  _panelOpen = true;
  const panel = document.getElementById('notification-inbox-panel');
  if (!panel) return;
  panel.classList.remove('hidden');
  _positionPanel();
  _loadPanel();
}

function _togglePanel(anchor) {
  if (_panelOpen && _anchorButton === anchor) {
    _closePanel();
    return;
  }
  _openPanel(anchor);
}

function _bindEvents() {
  if (_bound) return;
  _bound = true;
  document.querySelectorAll('#notification-inbox-btn, #notification-inbox-sidebar-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      _togglePanel(btn);
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      _togglePanel(btn);
    });
  });
  document.getElementById('notification-inbox-close')?.addEventListener('click', () => {
    _closePanel();
  });
  document.getElementById('notification-inbox-mode')?.addEventListener('click', () => {
    _mode = _mode === 'events' ? 'inbox' : 'events';
    _loadPanel();
  });
  document.addEventListener('pointerdown', (e) => {
    if (!_panelOpen) return;
    const panel = document.getElementById('notification-inbox-panel');
    const path = e.composedPath ? e.composedPath() : [];
    if (path.includes(panel) || _isNotificationTrigger(e.target)) return;
    _closePanel();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _panelOpen) _closePanel();
  }, true);
  window.addEventListener('resize', () => {
    if (_panelOpen) _positionPanel();
  });
  document.addEventListener('odysseus:notifications-changed', () => {
    refreshCount();
    if (_panelOpen) _loadPanel();
  });
}

export function init(apiBase = window.location.origin) {
  API_BASE = apiBase || window.location.origin;
  _ensureShell();
  _bindEvents();
  refreshCount();
  if (!_countTimer) _countTimer = setInterval(refreshCount, 45000);
}

const notificationsModule = { init, refreshCount };
export default notificationsModule;
window.notificationsModule = notificationsModule;
