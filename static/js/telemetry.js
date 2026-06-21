// static/js/telemetry.js
// Live hardware telemetry sidebar widget for the chat view.
//
// Polls GET /api/telemetry once per second while the user is streaming a
// response; stops polling on idle to avoid unnecessary overhead. Renders a
// compact bar showing CPU%, RAM GB, VRAM GB, and GPU °C. Displays a thermal
// throttle warning when the GPU temperature crosses the server-configured
// threshold (default 87 °C; configurable via ODYSSEUS_THROTTLE_TEMP).

const _POLL_INTERVAL_MS = 1000;
const _THROTTLE_WARN_ID = 'telemetry-throttle-warn';
const _WIDGET_ID = 'telemetry-widget';

let _pollTimer = null;
let _isEnabled = false;      // mirrors the telemetry_enabled setting
let _isCollapsed = false;    // user can collapse the widget
let _lastThrottle = false;   // tracks throttle state to avoid re-rendering

/** Returns true when the send button indicates an active stream. */
function _isStreaming() {
  const btn = document.querySelector('.send-btn');
  return btn ? btn.dataset.mode === 'streaming' : false;
}

function _widget() { return document.getElementById(_WIDGET_ID); }
function _warn()   { return document.getElementById(_THROTTLE_WARN_ID); }

/** Build the widget DOM once and insert it before the chat input bar. */
function _createWidget() {
  if (_widget()) return;
  const el = document.createElement('div');
  el.id = _WIDGET_ID;
  el.className = 'telemetry-widget';
  el.style.display = 'none';
  el.innerHTML = `
    <div class="telemetry-row" id="telemetry-bar">
      <span class="telemetry-label">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        Live
      </span>
      <span class="telemetry-chip" id="tel-cpu" title="CPU usage"></span>
      <span class="telemetry-chip" id="tel-ram" title="RAM used"></span>
      <span class="telemetry-chip" id="tel-vram" title="VRAM used (GPU)"></span>
      <span class="telemetry-chip" id="tel-temp" title="GPU temperature"></span>
      <button type="button" class="telemetry-collapse-btn" id="telemetry-collapse-btn" title="Collapse telemetry">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
      </button>
    </div>`;

  // Insert directly above the chat input bar.
  const inputBar = document.querySelector('.chat-input-bar');
  if (inputBar && inputBar.parentNode) {
    inputBar.parentNode.insertBefore(el, inputBar);
  }

  document.getElementById('telemetry-collapse-btn').addEventListener('click', () => {
    _isCollapsed = !_isCollapsed;
    const bar = document.getElementById('telemetry-bar');
    if (bar) bar.style.display = _isCollapsed ? 'none' : '';
    const btn = document.getElementById('telemetry-collapse-btn');
    if (btn) {
      btn.title = _isCollapsed ? 'Expand telemetry' : 'Collapse telemetry';
      btn.querySelector('svg polyline').setAttribute(
        'points', _isCollapsed ? '6 9 12 15 18 9' : '18 15 12 9 6 15'
      );
    }
    if (_isCollapsed) el.style.minHeight = '';
  });
}

/** Update the chip text content for one metric. */
function _setChip(id, text, warn) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.classList.toggle('telemetry-chip-warn', !!warn);
}

/** Render a snapshot from the API into the widget chips. */
function _render(snap) {
  if (!snap || !snap.timestamp) return;
  const w = _widget();
  if (!w) return;

  _setChip('tel-cpu',  `CPU ${Math.round(snap.cpu_pct ?? 0)}%`, snap.cpu_pct >= 95);
  _setChip('tel-ram',  `RAM ${(snap.ram_gb ?? 0).toFixed(1)} GB`, snap.ram_pct >= 90);
  _setChip('tel-vram', snap.vram_gb > 0 ? `VRAM ${snap.vram_gb.toFixed(1)} GB` : '', false);
  _setChip('tel-temp', snap.gpu_temp_c > 0 ? `${snap.gpu_temp_c} °C` : '', snap.throttle);

  // Show/hide throttle warning inline below the last AI message.
  if (snap.throttle && !_lastThrottle) {
    _showThrottleWarning();
  } else if (!snap.throttle && _lastThrottle) {
    _hideThrottleWarning();
  }
  _lastThrottle = !!snap.throttle;
}

function _showThrottleWarning() {
  if (document.getElementById(_THROTTLE_WARN_ID)) return;
  const el = document.createElement('div');
  el.id = _THROTTLE_WARN_ID;
  el.className = 'telemetry-throttle-warn';
  el.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
    GPU thermal throttle detected — inference will be slower. Consider a cooling break.
    <button type="button" class="telemetry-warn-dismiss" title="Dismiss">&#x2715;</button>`;
  el.querySelector('.telemetry-warn-dismiss').addEventListener('click', () => el.remove());

  // Insert warning before the chat input bar.
  const inputBar = document.querySelector('.chat-input-bar');
  const widget = _widget();
  const anchor = widget || inputBar;
  if (anchor && anchor.parentNode) {
    anchor.parentNode.insertBefore(el, anchor);
  }
}

function _hideThrottleWarning() {
  const el = document.getElementById(_THROTTLE_WARN_ID);
  if (el) el.remove();
}

/** Fetch a fresh snapshot and update the widget. */
async function _tick() {
  if (!_isStreaming()) {
    _stopPolling();
    return;
  }
  try {
    const r = await fetch('/api/telemetry', { credentials: 'same-origin' });
    if (!r.ok) {
      // 403 means disabled or not admin — stop silently.
      _stopPolling();
      return;
    }
    const snap = await r.json();
    _render(snap);
  } catch (_e) {
    // Network error — keep widget visible but don't crash.
  }
}

function _stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
  const w = _widget();
  if (w) w.style.display = 'none';
  _hideThrottleWarning();
  _lastThrottle = false;
}

function _startPolling() {
  if (_pollTimer) return;
  const w = _widget();
  if (w) w.style.display = '';
  _tick();
  _pollTimer = setInterval(_tick, _POLL_INTERVAL_MS);
}

/** Watch the send button for streaming state changes. */
function _observe() {
  const root = document.getElementById('chat-container') || document.body;
  // Polling the DOM attribute once per second is cheap and avoids having to
  // couple this module to chat.js internals via an exported flag.
  setInterval(() => {
    if (!_isEnabled) return;
    if (_isStreaming()) {
      _createWidget();
      _startPolling();
    } else if (_pollTimer) {
      _stopPolling();
    }
  }, 500);
}

/**
 * Initialise the telemetry widget.
 * Can be called explicitly with a known value, or the module auto-fetches the
 * setting on load (mirrors the TTS module's self-bootstrap pattern).
 *
 * @param {boolean} enabled - mirrors the telemetry_enabled backend setting.
 */
export function initTelemetry(enabled) {
  _isEnabled = !!enabled;
  if (!_isEnabled) return;
  _observe();
}

// Auto-bootstrap: fetch the setting on module load so callers don't need to
// wire the setting value in explicitly. Falls back gracefully on any error
// (anonymous/pre-login, 401, network failure).
(async () => {
  try {
    const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    if (!res.ok) return;
    const settings = await res.json();
    initTelemetry(!!settings.telemetry_enabled);
  } catch (_) { /* not logged in or no network — skip silently */ }
})();
