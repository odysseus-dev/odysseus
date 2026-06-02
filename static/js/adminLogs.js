// Admin App Logs viewer — list/tail logs under logs/ (issue #981)
import * as Modals from './modalManager.js';
import { el, esc } from './ui.js';

const POLL_MS = 3000;
const TAIL_LINES = 200;

let _pollTimer = null;
let _registered = false;
let _tailInFlight = false;
let _tailSnapshot = '';

function _levelClass(line) {
  if (/\s-\sERROR\s+-/.test(line)) return 'app-logs-line-error';
  if (/\s-\sWARNING\s+-/.test(line)) return 'app-logs-line-warn';
  if (/\s-\sINFO\s+-/.test(line)) return 'app-logs-line-info';
  return '';
}

function _scrollToEnd() {
  if (!el('app-logs-autoscroll')?.checked) return;
  const body = el('app-logs-output')?.closest('.app-logs-body');
  if (body) body.scrollTop = body.scrollHeight;
}

function _renderLines(lines) {
  const out = el('app-logs-output');
  if (!out) return;
  if (!lines?.length) {
    out.textContent = '(no log lines yet)';
    return;
  }
  out.innerHTML = lines.map((ln) => {
    const cls = _levelClass(ln);
    const safe = esc(ln);
    return cls ? `<span class="${cls}">${safe}\n</span>` : `${safe}\n`;
  }).join('');
  _scrollToEnd();
}

function _setMeta(data) {
  const meta = el('app-logs-meta');
  if (!meta) return;
  const n = (data?.lines || []).length;
  const trunc = data?.truncated ? ' (truncated)' : '';
  const mod = data?.modified ? ` · updated ${data.modified}` : '';
  const bytes = data?.bytes != null ? ` · ${data.bytes} bytes` : '';
  meta.textContent = `${n} line${n === 1 ? '' : 's'} shown${trunc}${bytes}${mod}`;
}

async function _fetchList() {
  const res = await fetch('/api/admin/logs', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`list failed: ${res.status}`);
  return res.json();
}

async function _fetchTail(name) {
  const res = await fetch(
    `/api/admin/logs/${encodeURIComponent(name)}?tail=${TAIL_LINES}`,
    { credentials: 'same-origin' },
  );
  if (!res.ok) throw new Error(`tail failed: ${res.status}`);
  return res.json();
}

async function _populateSelect(preferred) {
  const sel = el('app-logs-select');
  if (!sel) return null;
  const data = await _fetchList();
  const logs = data.logs || [];
  sel.innerHTML = '';
  if (!logs.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '(no log files)';
    sel.appendChild(opt);
    return null;
  }
  for (const row of logs) {
    const opt = document.createElement('option');
    opt.value = row.name;
    opt.textContent = `${row.name} (${row.bytes} B)`;
    sel.appendChild(opt);
  }
  const pick = preferred && logs.some((r) => r.name === preferred)
    ? preferred
    : (logs.find((r) => r.name === 'odysseus.log')?.name || logs[0].name);
  sel.value = pick;
  return pick;
}

export async function loadTail(force = false) {
  const sel = el('app-logs-select');
  const name = sel?.value;
  if (!name) {
    _tailSnapshot = '';
    _renderLines([]);
    _setMeta({ lines: [] });
    return;
  }
  if (_tailInFlight) return;
  _tailInFlight = true;
  try {
    const data = await _fetchTail(name);
    const snapshot = `${name}:${data.bytes}:${data.modified}`;
    if (!force && snapshot === _tailSnapshot) {
      _scrollToEnd();
      return;
    }
    _tailSnapshot = snapshot;
    _renderLines(data.lines || []);
    _setMeta(data);
  } catch (e) {
    _tailSnapshot = '';
    _renderLines([`Error loading log: ${e.message}`]);
    _setMeta({ lines: [] });
  } finally {
    _tailInFlight = false;
  }
}

function _stopPoll() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

function _startPoll() {
  _stopPoll();
  _pollTimer = setInterval(() => {
    const modal = el('app-logs-modal');
    if (!modal || modal.classList.contains('hidden')) {
      _stopPoll();
      return;
    }
    loadTail();
  }, POLL_MS);
}

function _wireControls() {
  el('app-logs-refresh')?.addEventListener('click', () => loadTail(true));
  el('app-logs-select')?.addEventListener('change', () => {
    _tailSnapshot = '';
    loadTail(true);
  });
  el('app-logs-autoscroll')?.addEventListener('change', () => _scrollToEnd());
  el('app-logs-copy')?.addEventListener('click', async () => {
    const text = el('app-logs-output')?.innerText || '';
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) { /* ignore */ }
  });
  el('close-app-logs-modal')?.addEventListener('click', () => close());
}

function _ensureRegistered() {
  if (_registered) return;
  _registered = true;
  Modals.register('app-logs-modal', {
    railBtnId: null,
    sidebarBtnId: 'tool-app-logs-btn',
    closeFn: () => close(),
    restoreFn: () => {
      _startPoll();
      loadTail(true);
    },
  });
  _wireControls();
}

function isOpen() {
  const modal = el('app-logs-modal');
  return modal && !modal.classList.contains('hidden');
}

export async function open() {
  const modal = el('app-logs-modal');
  if (!modal) return;
  _ensureRegistered();
  if (Modals.isMinimized('app-logs-modal')) {
    Modals.restore('app-logs-modal');
    return;
  }
  if (!modal.classList.contains('hidden')) return;
  modal.classList.remove('hidden');
  modal.style.display = '';
  _tailSnapshot = '';
  await _populateSelect('odysseus.log');
  await loadTail(true);
  _startPoll();
}

export function close() {
  _stopPoll();
  _tailSnapshot = '';
  Modals.close('app-logs-modal');
}

export async function toggle() {
  _ensureRegistered();
  if (Modals.isRegistered('app-logs-modal') && Modals.toggle('app-logs-modal')) {
    if (isOpen()) _startPoll();
    else _stopPoll();
    return;
  }
  if (isOpen()) {
    close();
    return;
  }
  await open();
}
