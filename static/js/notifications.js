// WebSocket notification push — replaces 30s HTTP polling
// Connects to /ws/notifications and shows browser notifications + toasts.

let _ws = null;
let _seenIds = new Set();
const _SEEN_MAX = 200; // cap to avoid unbounded growth
let _reconnectTimer = null;
let _reconnectDelay = 1000;
const _MAX_RECONNECT_DELAY = 10000;
let _enabled = false;
let _apiBase = '';

function _wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Derive WS host from the API base so it works with reverse-proxy setups
  let host = location.host;
  if (_apiBase && _apiBase.startsWith('http')) {
    try {
      const u = new URL(_apiBase);
      host = u.host;
    } catch (_) {}
  }
  return `${proto}//${host}/ws/notifications`;
}

export function initNotifications(apiBase) {
  _apiBase = apiBase || '';
  _enabled = true;
  _connect();
}

function _markSeen(n) {
  const key = (n.task_id || '') + '|' + (n.timestamp || '');
  _seenIds.add(key);
  if (_seenIds.size > _SEEN_MAX) {
    const iter = _seenIds.values();
    for (let i = 0; i < 50; i++) _seenIds.delete(iter.next().value);
  }
  return key;
}

export function wasNotificationSeen(n) {
  const key = (n.task_id || '') + '|' + (n.timestamp || '');
  return key ? _seenIds.has(key) : false;
}

export function stopNotifications() {
  _enabled = false;
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  if (_ws) {
    _ws.onclose = null;
    _ws.onmessage = null;
    _ws.onerror = null;
    _ws.close();
    _ws = null;
  }
}

function _connect() {
  if (!_enabled) return;
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;

  try {
    _ws = new WebSocket(_wsUrl());
  } catch (e) {
    _scheduleReconnect();
    return;
  }

  _ws.onopen = () => {
    _reconnectDelay = 1000; // reset on successful connect
  };

  _ws.onmessage = (e) => {
    try {
      const n = JSON.parse(e.data);
      _markSeen(n);
      _showNotification(n);
    } catch (_) {}
  };

  _ws.onclose = () => {
    _ws = null;
    _scheduleReconnect();
  };

  _ws.onerror = () => {
    // onclose will fire after this, so reconnect is handled there
  };
}

function _scheduleReconnect() {
  if (!_enabled) return;
  if (_reconnectTimer) return;
  _reconnectTimer = setTimeout(() => {
    _reconnectTimer = null;
    _reconnectDelay = Math.min(_reconnectDelay * 2, _MAX_RECONNECT_DELAY);
    _connect();
  }, _reconnectDelay);
}

function _showNotification(n) {
  const ok = n.status === 'success';
  const title = n.task_name || 'Task';
  const body = n.body || '';

  // Tasks with output_target='notification' carry result text in `body`
  // — show as a real browser Notification. Falls back to toast.
  if (ok && body) {
    let fired = false;
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        new Notification(title, {
          body: body,
          tag: 'task-' + (n.task_id || title),
          icon: '/static/favicon.ico',
        });
        fired = true;
      }
    } catch (_) {}
    if (!fired) {
      try {
        const ui = window.uiModule || window._uiModule;
        if (ui && ui.showToast) ui.showToast(title + ': ' + body.slice(0, 140), { duration: 7000 });
      } catch (_) {}
    }
    return;
  }

  const msg = `Task ${ok ? 'finished' : 'failed'}: ${title}`;
  try {
    const ui = window.uiModule || window._uiModule;
    if (!ui) return;
    if (ok) ui.showToast(msg, { duration: 5000 });
    else {
      ui.showError(msg);
      // Also notify the tasks module so it can update its failure-pending state
      // and activity view — the fallback HTTP poll won't fire because this
      // notification was already marked seen.
      try {
        const tm = window.tasksModule;
        if (tm && tm.handleWebSocketNotification) tm.handleWebSocketNotification(n);
      } catch (_) {}
    }
  } catch (_) {}
}
