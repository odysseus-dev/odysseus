import uiModule from './ui.js';

const API_BASE = window.location.origin;
const approvals = new Map();
let recentRuns = [];
let popover = null;
let pollTimer = null;
let initialized = false;

const esc = (value) => uiModule.esc(String(value || ''));
const pathKey = (value) => String(value || '').replace(/[\\/]+$/, '').toLocaleLowerCase();
const isRunning = (status) => status === 'running' || status === 'queued';
const isFailed = (status) => status === 'error' || status === 'failed' || status === 'aborted';
const isComplete = (status) => status === 'success';
const sessionApi = () => window.sessionModule || {};

function relativeTime(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return 'now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function sessionStates() {
  return (sessionApi().getSessions?.() || []).map((session) => ({
    session,
    state: sessionApi().getSessionActivityState?.(session.id) || '',
  })).filter((entry) => entry.state);
}

function snapshot() {
  const states = sessionStates();
  const runningSessionIds = new Set(states.filter((entry) => entry.state === 'running').map((entry) => entry.session.id));
  const readySessionIds = new Set(states.filter((entry) => entry.state === 'ready').map((entry) => entry.session.id));
  return {
    running: runningSessionIds.size + recentRuns.filter((run) => isRunning(run.status) && (!run.session_id || !runningSessionIds.has(run.session_id))).length,
    ready: readySessionIds.size + recentRuns.filter((run) => isComplete(run.status) && (!run.session_id || !readySessionIds.has(run.session_id))).length,
    failed: recentRuns.filter((run) => isFailed(run.status)).length,
    approvals: approvals.size,
    states,
    runs: recentRuns.slice(),
  };
}

function emitChange() {
  syncBadge();
  if (popover) renderPopover();
  try { document.dispatchEvent(new CustomEvent('odysseus-activity-change', { detail: snapshot() })); } catch (_) {}
}

async function refresh() {
  try {
    const response = await fetch(`${API_BASE}/api/tasks/runs/recent?limit=18&max_result_chars=500`, { credentials: 'same-origin' });
    if (!response.ok) return;
    const data = await response.json();
    recentRuns = Array.isArray(data.runs) ? data.runs : [];
    emitChange();
  } catch (_) {}
}

function getProjectActivity(workspace, sessions = []) {
  const key = pathKey(workspace);
  const owned = sessions.filter((session) => pathKey(session.workspace) === key);
  const ids = new Set(owned.map((session) => session.id));
  const states = owned.map((session) => sessionApi().getSessionActivityState?.(session.id) || '');
  const runningIds = new Set(owned.filter((session) => sessionApi().getSessionActivityState?.(session.id) === 'running').map((session) => session.id));
  const projectRuns = recentRuns.filter((run) => run.session_id && ids.has(run.session_id));
  let pending = 0;
  approvals.forEach((approval) => { if (ids.has(approval.sessionId)) pending += 1; });
  return {
    running: runningIds.size + projectRuns.filter((run) => isRunning(run.status) && !runningIds.has(run.session_id)).length,
    ready: states.filter((state) => state === 'ready').length,
    failed: projectRuns.filter((run) => isFailed(run.status)).length,
    approvals: pending,
  };
}

function summaryCard(label, value, tone) {
  return `<div class="activity-summary-card ${tone}"><span>${esc(value)}</span><small>${esc(label)}</small></div>`;
}

function sessionRow(entry) {
  const running = entry.state === 'running';
  const stateLabel = running ? 'Running' : 'Result ready';
  return `<button type="button" class="activity-center-row" data-session-id="${esc(entry.session.id)}">
    <span class="activity-row-state ${running ? 'running' : 'ready'}" aria-hidden="true"></span>
    <span class="activity-row-copy"><strong>${esc(entry.session.name || 'Untitled chat')}</strong><small>${stateLabel}</small></span>
    <span class="activity-row-time">${esc(relativeTime(entry.session.last_message_at || entry.session.updated_at))}</span>
  </button>`;
}

function runRow(run) {
  const status = isRunning(run.status) ? 'running' : isFailed(run.status) ? 'failed' : 'ready';
  return `<button type="button" class="activity-center-row" data-run-session-id="${esc(run.session_id || '')}">
    <span class="activity-row-state ${status}" aria-hidden="true"></span>
    <span class="activity-row-copy"><strong>${esc(run.task_name || run.action || 'Task')}</strong><small>${esc(isRunning(run.status) ? 'Running' : isFailed(run.status) ? 'Failed' : 'Completed')}</small></span>
    <span class="activity-row-time">${esc(relativeTime(run.finished_at || run.started_at))}</span>
  </button>`;
}

function approvalRow(approval) {
  return `<button type="button" class="activity-center-row approval" data-session-id="${esc(approval.sessionId || '')}">
    <span class="activity-row-state approval" aria-hidden="true"></span>
    <span class="activity-row-copy"><strong>${esc(approval.tool || 'Tool')} needs approval</strong><small>${esc(approval.command || 'Review requested action')}</small></span>
  </button>`;
}

function renderPopover() {
  if (!popover) return;
  const data = snapshot();
  const rows = [
    ...Array.from(approvals.values()).map(approvalRow),
    ...data.states.map(sessionRow),
    ...data.runs.slice(0, 10).map(runRow),
  ];
  popover.innerHTML = `<div class="activity-center-head">
      <div><strong>Agent activity</strong><span>Live work across Odysseus</span></div>
      <button type="button" class="activity-center-refresh" aria-label="Refresh activity" title="Refresh"><svg viewBox="0 0 24 24"><path d="M20 6v5h-5M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5"/></svg></button>
    </div>
    <div class="activity-summary-grid">
      ${summaryCard('Running', data.running, 'running')}
      ${summaryCard('Results', data.ready, 'ready')}
      ${summaryCard('Failed', data.failed, 'failed')}
      ${summaryCard('Approvals', data.approvals, 'approval')}
    </div>
    <div class="activity-center-list">${rows.length ? rows.join('') : '<div class="activity-center-empty">No agent activity yet.</div>'}</div>
    <div class="activity-center-footer">
      <button type="button" class="activity-center-full" data-open-mission>Open Mission Control <span>↗</span></button>
      <button type="button" class="activity-center-log" data-open-activity-log>Activity log</button>
    </div>`;

  popover.querySelector('.activity-center-refresh')?.addEventListener('click', (event) => { event.stopPropagation(); refresh(); });
  popover.querySelectorAll('[data-session-id], [data-run-session-id]').forEach((row) => row.addEventListener('click', async () => {
    const id = row.dataset.sessionId || row.dataset.runSessionId;
    if (id) await sessionApi().selectSession?.(id);
    close();
  }));
  popover.querySelector('[data-open-mission]')?.addEventListener('click', () => {
    close();
    window.odysseusMissionControl?.open?.('review');
  });
  popover.querySelector('[data-open-activity-log]')?.addEventListener('click', () => {
    close();
    window.tasksModule?.openTasks?.(null, { tab: 'activity' });
  });
}

function syncBadge() {
  const badge = document.getElementById('sidebar-activity-badge');
  const button = document.getElementById('sidebar-activity-btn');
  if (!badge || !button) return;
  const data = snapshot();
  const count = data.running + data.approvals + data.failed + data.states.filter((entry) => entry.state === 'ready').length;
  badge.textContent = count > 99 ? '99+' : String(count);
  badge.hidden = count === 0;
  button.classList.toggle('has-running', data.running > 0);
  button.classList.toggle('needs-attention', data.approvals > 0 || data.failed > 0);
}

function open() {
  if (popover) return;
  const button = document.getElementById('sidebar-activity-btn');
  if (!button) return;
  popover = document.createElement('section');
  popover.className = 'activity-center-popover';
  popover.setAttribute('aria-label', 'Agent activity');
  document.body.appendChild(popover);
  const rect = button.getBoundingClientRect();
  const width = Math.min(350, window.innerWidth - 20);
  popover.style.width = `${width}px`;
  popover.style.left = `${Math.max(10, Math.min(window.innerWidth - width - 10, rect.right - width))}px`;
  popover.style.top = `${rect.bottom + 8}px`;
  button.setAttribute('aria-expanded', 'true');
  renderPopover();
  requestAnimationFrame(() => popover?.classList.add('visible'));
}

function close() {
  const current = popover;
  popover = null;
  document.getElementById('sidebar-activity-btn')?.setAttribute('aria-expanded', 'false');
  if (!current) return;
  current.classList.remove('visible');
  setTimeout(() => current.remove(), 170);
}

function init() {
  if (initialized) return;
  initialized = true;
  const button = document.getElementById('sidebar-activity-btn');
  button?.addEventListener('click', (event) => { event.stopPropagation(); popover ? close() : open(); });
  document.addEventListener('pointerdown', (event) => {
    if (popover && !popover.contains(event.target) && !button?.contains(event.target)) close();
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && popover) close(); });
  document.addEventListener('odysseus-session-activity-change', emitChange);
  document.addEventListener('odysseus-approval-activity', (event) => {
    const detail = event.detail || {};
    if (!detail.id) return;
    if (detail.status === 'pending') approvals.set(detail.id, detail);
    else approvals.delete(detail.id);
    emitChange();
  });
  refresh();
  pollTimer = setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  syncBadge();
}

const activityCenterModule = { init, open, close, refresh, getSnapshot: snapshot, getProjectActivity };
window.odysseusActivity = activityCenterModule;

export default activityCenterModule;
