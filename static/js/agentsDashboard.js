/* agentsDashboard.js — the Agents page: every chat and worker the user runs.
 *
 * A full-page view (rail button, sidebar item, Ctrl+Shift+A, /agents) with:
 *   Fleet     — every chat with agent activity, grouped by what needs you:
 *               waiting for approval → running → failed → finished → idle.
 *   Detail    — the selected chat: its live event stream, child runs (sub-
 *               agents, Claude Code, background jobs) with Stop, pending
 *               approvals with Approve/Deny, a Steer box that lands mid-turn,
 *               and a Reply box for an idle chat.
 *   Launch    — start a worker profile in a fresh chat from here.
 *
 * Data comes from /api/agents/* (owner-scoped, routes/agents_routes.py); the
 * live feed is one SSE stream of the user's activity events.
 */

import uiModule from './ui.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const STATUS = {
  waiting_approval: ['Needs approval', 'warn'], running: ['Running', 'run'], failed: ['Failed', 'bad'],
  finished: ['Finished', 'ok'], stopped: ['Stopped', 'warn'], idle: ['Idle', ''],
};
const SOURCE_LABEL = { odysseus: 'Odysseus', claude_code: 'Claude Code', session: 'Sub-agent', pipeline: 'Pipeline', bg_job: 'Background job', worktree: 'Worktree', system: 'System' };
const KIND_ICON = { run_started: '▸', run_finished: '■', message: '›', tool_start: '→', tool_result: '←', file_change: '±', commit: '●', status: '·', error: '!', note: '~' };

const state = {
  open: false, rows: [], totals: {}, profiles: [], chats: [], approvals: [], selected: null,
  events: new Map(), es: null, pollTimer: null, tick: null, launchOpen: false, filter: '',
  error: '', refreshing: false, refreshQueued: false,
};

async function api(path, opts = {}) {
  const r = await fetch(path, Object.assign({ credentials: 'same-origin' }, opts));
  let body = null;
  try { body = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error((body && (body.detail || body.error)) || `${r.status}`); e.status = r.status; throw e; }
  return body;
}
const post = (path, data) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data || {}) });
function fmtDur(a, b) {
  if (!a) return '';
  const s = Math.max(0, Math.round((b || Date.now() / 1000) - a));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function fmtTime(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }) : ''; }
function pill(status) { const [label, cls] = STATUS[status] || [status, '']; return `<span class="wb-pill ${cls}"><i aria-hidden="true"></i>${esc(label)}</span>`; }
function chip(source) { return `<span class="wb-chip wb-src-${esc(source)}">${esc(SOURCE_LABEL[source] || source)}</span>`; }

// ── data ──────────────────────────────────────────────────────────────────
async function refresh() {
  if (state.refreshing) { state.refreshQueued = true; return; }
  state.refreshing = true;
  try {
    const [ov, ap] = await Promise.all([api('/api/agents/overview'), api('/api/agents/approvals')]);
    state.rows = ov.rows || []; state.totals = ov.totals || {}; state.profiles = ov.profiles || []; state.chats = ov.chats || [];
    state.approvals = ap.approvals || [];
    state.error = '';
    if (state.selected && !state.rows.some((r) => r.session_id === state.selected)) state.selected = null;
    if (!state.selected && state.rows.length) state.selected = state.rows[0].session_id;
  } catch (e) {
    if (e.status === 401 || e.status === 403) { close(); return; }
    state.error = e.message || 'Agents are temporarily unavailable';
  } finally {
    state.refreshing = false;
  }
  updateBadges();
  // Do not replace the dashboard shell here. A run finishing used to rebuild
  // the entire page, which stole focus, erased half-written steer/reply text,
  // closed the launch form, and visibly flashed the UI. Refresh only the data
  // regions and preserve the controls the user is working in.
  if (state.open) updateOpenView();
  if (state.refreshQueued) {
    state.refreshQueued = false;
    queueMicrotask(refresh);
  }
}
function connect() {
  if (state.es) return;
  const es = new EventSource('/api/agents/stream');
  state.es = es;
  es.onmessage = (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch (_) { return; }
    if (!ev || ev.type === 'ready' || !ev.session_id) return;
    const list = state.events.get(ev.session_id) || [];
    list.push(ev); if (list.length > 400) list.splice(0, list.length - 400);
    state.events.set(ev.session_id, list);
    notifyFor(ev);
    if (['run_started', 'run_finished', 'status', 'note'].includes(ev.kind)) scheduleRefresh();
    else if (state.open && ev.session_id === state.selected) renderDetail();
  };
}
/** Desktop notifications for the two things worth interrupting for: an agent
 *  blocked on an approval, and a worker finishing — only when the user isn't
 *  already looking at that chat or the dashboard. */
const _notified = new Set();
function chatName(sid) { return (state.rows.find((r) => r.session_id === sid) || state.chats.find((c) => c.id === sid) || {}).name || 'a chat'; }
function notifyFor(ev) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const d = ev.data || {};
  let title = '', body = '', key = '';
  if (ev.kind === 'status' && d.approval_id) {
    title = 'Approval needed'; body = `${d.tool || 'A tool'} in ${chatName(ev.session_id)}: ${String(ev.title || '').replace(/^Waiting for approval:\s*/, '')}`; key = `ap:${d.approval_id}`;
  } else if (ev.kind === 'run_finished' && ev.source === 'session' && /^Worker/.test(ev.title || '')) {
    title = d.status === 'failed' ? 'Worker failed' : 'Worker finished'; body = ev.title; key = `run:${ev.run_id}`;
  } else if (ev.kind === 'run_finished' && ev.source === 'claude_code') {
    title = d.status === 'failed' ? 'Claude Code failed' : 'Claude Code finished'; body = ev.title; key = `run:${ev.run_id}`;
  }
  if (!title || _notified.has(key)) return;
  _notified.add(key);
  const viewing = !document.hidden && (state.open || window.sessionModule?.getCurrentSessionId?.() === ev.session_id);
  if (viewing && !d.approval_id) return;
  const n = new Notification(title, { body: String(body).slice(0, 180), tag: key });
  n.onclick = () => { window.focus(); if (d.approval_id) { state.selected = ev.session_id; open(); } else window.sessionModule?.selectSession?.(ev.session_id); n.close(); };
  setTimeout(() => n.close(), 15000);
}
function disconnect() { if (state.es) { try { state.es.close(); } catch (_) {} state.es = null; } }
let _refreshTimer = null;
function scheduleRefresh() { if (_refreshTimer) return; _refreshTimer = setTimeout(() => { _refreshTimer = null; refresh(); }, 400); }
async function loadHistory(sid) {
  if (state.events.has(sid)) return;
  try {
    const h = await api(`/api/workbench/activity?session_id=${encodeURIComponent(sid)}&limit=300`);
    state.events.set(sid, h.events || []);
  } catch (_) {
    // Non-admin: history comes only from run events.
    const row = state.rows.find((r) => r.session_id === sid);
    const list = [];
    for (const c of (row?.children || [])) {
      try { const r = await api(`/api/agents/runs/${encodeURIComponent(c.run_id)}/events`); list.push(...(r.events || [])); } catch (_) {}
    }
    list.sort((a, b) => (a.seq || 0) - (b.seq || 0));
    state.events.set(sid, list);
  }
}
function updateBadges() {
  const attention = (state.totals.waiting_approval || 0);
  const running = (state.totals.running || 0);
  const badge = $('rail-agents-badge');
  if (badge) {
    badge.hidden = !(attention || running);
    badge.textContent = attention ? String(attention) : String(running);
    badge.classList.toggle('attn', attention > 0);
  }
  const dot = $('tool-agents-dot');
  if (dot) dot.style.display = attention ? 'inline-block' : 'none';
  $('rail-agents')?.classList.toggle('rail-notify', attention > 0);
}

// ── render ────────────────────────────────────────────────────────────────
function render() {
  const root = $('agents-dashboard');
  if (!root || !state.open) return;
  const t = state.totals;
  root.innerHTML = `
    <div class="ag-head">
      <div class="ag-title"><span class="ag-title-text">Agents</span><span class="ag-sub">every chat and worker you're running</span></div>
      <div class="ag-stats">
        <button type="button" class="ag-stat${t.waiting_approval ? ' attn' : ''}" data-ag="filter-status" data-status="waiting_approval"><b>${t.waiting_approval || 0}</b><span>need approval</span></button>
        <button type="button" class="ag-stat" data-ag="filter-status" data-status="running"><b>${t.running || 0}</b><span>running</span></button>
        <div class="ag-stat"><b>${t.workers_running || 0}</b><span>workers live</span></div>
        <div class="ag-stat"><b>${t.finished_24h || 0}</b><span>finished · 24h</span></div>
        <div class="ag-stat${t.failed_24h ? ' bad' : ''}"><b>${t.failed_24h || 0}</b><span>failed · 24h</span></div>
      </div>
      <div class="ag-head-actions">
        <button type="button" class="wb-btn wb-btn-primary" data-ag="launch">Launch worker</button>
        <button type="button" class="wb-btn" data-ag="refresh" title="Refresh agent status">Refresh</button>
        <button type="button" class="wb-btn" data-ag="workbench" title="Repository changes, commits and PRs (admin)">Workbench</button>
        <button type="button" class="wb-icon-btn" data-ag="close" aria-label="Close" title="Close (Esc)">✕</button>
      </div>
    </div>
    <div class="ag-refresh-error" id="ag-refresh-error" role="status"${state.error ? '' : ' hidden'}>${esc(state.error)}</div>
    <div class="ag-body">
      <aside class="ag-fleet wb-card">
        <div class="ag-fleet-tools"><input type="search" class="wb-input" id="ag-filter" placeholder="Filter chats…" value="${esc(state.filter)}" aria-label="Filter chats"></div>
        <div class="ag-fleet-list" data-wb-scroll="fleet">${fleetHtml()}</div>
      </aside>
      <section class="ag-detail wb-card" id="ag-detail"></section>
      ${state.launchOpen ? `<aside class="ag-launch wb-card" id="ag-launch">${launchHtml()}</aside>` : ''}
    </div>`;
  renderDetail();
  $('ag-filter')?.addEventListener('input', (e) => { state.filter = e.target.value; renderFleetOnly(); });
}
function filteredRows() {
  const q = state.filter.trim().toLowerCase();
  return state.rows.filter((r) => !q || (r.name || '').toLowerCase().includes(q) || (r.latest || '').toLowerCase().includes(q));
}
function fleetHtml() {
  const rows = filteredRows();
  const groups = [['waiting_approval', 'Needs you'], ['running', 'Running'], ['failed', 'Failed'], ['finished', 'Finished'], ['stopped', 'Stopped'], ['idle', 'Workers & recent']];
  return groups.map(([key, label]) => {
    const items = rows.filter((r) => r.status === key);
    if (!items.length) return '';
    return `<div class="ag-group"><div class="wb-group-h"><span class="wb-group-title">${label}</span><span class="wb-count">${items.length}</span></div>${items.map(rowHtml).join('')}</div>`;
  }).join('') || `<div class="wb-empty">${state.rows.length ? 'No chats match.' : 'Nothing is running. Send a chat a task, or launch a worker.'}</div>`;
}
function renderFleetOnly() {
  const list = $('agents-dashboard')?.querySelector('.ag-fleet-list');
  if (!list) return;
  const top = list.scrollTop;
  list.innerHTML = fleetHtml();
  list.scrollTop = top;
}
function updateStats() {
  const box = $('agents-dashboard')?.querySelector('.ag-stats');
  if (!box) return;
  const t = state.totals;
  box.innerHTML = `
    <button type="button" class="ag-stat${t.waiting_approval ? ' attn' : ''}" data-ag="filter-status" data-status="waiting_approval"><b>${t.waiting_approval || 0}</b><span>need approval</span></button>
    <button type="button" class="ag-stat" data-ag="filter-status" data-status="running"><b>${t.running || 0}</b><span>running</span></button>
    <div class="ag-stat"><b>${t.workers_running || 0}</b><span>workers live</span></div>
    <div class="ag-stat"><b>${t.finished_24h || 0}</b><span>finished · 24h</span></div>
    <div class="ag-stat${t.failed_24h ? ' bad' : ''}"><b>${t.failed_24h || 0}</b><span>failed · 24h</span></div>`;
}
function updateOpenView() {
  if (!state.open || !$('agents-dashboard')?.querySelector('.ag-body')) return;
  updateStats();
  renderFleetOnly();
  renderDetail();
  const error = $('ag-refresh-error');
  if (error) { error.textContent = state.error; error.hidden = !state.error; }
}
function rowHtml(r) {
  const sel = r.session_id === state.selected;
  const dur = r.status === 'running' && r.started_at ? fmtDur(r.started_at) : '';
  return `<div class="ag-row${sel ? ' active' : ''}" data-sid="${esc(r.session_id)}" role="button" tabindex="0" aria-selected="${sel ? 'true' : 'false'}">
    <div class="ag-row-top">${pill(r.status)}<span class="ag-row-name" title="${esc(r.name)}">${esc(r.name)}</span>${dur ? `<span class="ag-row-dur" data-started="${r.started_at}">${esc(dur)}</span>` : ''}</div>
    <div class="ag-row-meta">${r.profile ? `<span class="wb-chip wb-src-session">${esc(r.profile)}</span>` : ''}${r.model ? `<span class="wb-meta-item">${esc(String(r.model).split('/').pop())}</span>` : ''}${r.children_running ? `<span class="wb-meta-item">${r.children_running} worker${r.children_running === 1 ? '' : 's'}</span>` : ''}${r.pending_approvals ? `<span class="wb-meta-item wb-text-bad">${r.pending_approvals} approval${r.pending_approvals === 1 ? '' : 's'}</span>` : ''}</div>
    ${r.latest ? `<div class="ag-row-latest" title="${esc(r.latest)}">${esc(r.latest)}</div>` : ''}
  </div>`;
}
function renderDetail() {
  const box = $('ag-detail');
  if (!box) return;
  const active = box.contains(document.activeElement) ? document.activeElement : null;
  const activeId = active?.id || '';
  const selection = active && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
  const draft = {};
  const sameSelection = box.dataset.sessionId === state.selected;
  if (sameSelection) box.querySelectorAll('textarea[id]').forEach((el) => { draft[el.id] = el.value; });
  const r = state.rows.find((x) => x.session_id === state.selected);
  if (!r) { delete box.dataset.sessionId; box.innerHTML = '<div class="wb-empty">Select a chat to see what its agent is doing.</div>'; return; }
  box.dataset.sessionId = r.session_id;
  const scroll = box.querySelector('[data-wb-scroll="events"]');
  const keep = scroll ? scroll.scrollTop : null;
  const approvals = state.approvals.filter((a) => a.session_id === r.session_id);
  const running = r.status === 'running' || r.status === 'waiting_approval';
  const events = (state.events.get(r.session_id) || []).slice(-200).reverse();
  const children = r.children || [];
  box.innerHTML = `
    <div class="ag-detail-head">
      ${pill(r.status)}<span class="ag-detail-name" title="${esc(r.name)}">${esc(r.name)}</span>
      <span class="wb-spacer"></span>
      <button type="button" class="wb-btn wb-btn-sm" data-ag="open-chat" data-sid="${esc(r.session_id)}">Open chat</button>
      ${r.parent_session ? `<button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-ag="open-chat" data-sid="${esc(r.parent_session)}" title="This worker's parent chat">↳ parent</button>` : ''}
      ${r.status === 'running' ? `<button type="button" class="wb-btn wb-btn-sm" data-ag="stop-chat" data-sid="${esc(r.session_id)}">Stop</button>` : ''}
    </div>
    <div class="ag-detail-meta">${r.model ? `<span class="wb-meta-item">${esc(r.model)}</span>` : ''}${r.started_at ? `<span class="wb-meta-item">started ${esc(fmtTime(r.started_at))}</span>` : ''}${r.approval_mode ? `<span class="wb-meta-item">approvals: ${esc(r.approval_mode.replace('_', ' '))}</span>` : ''}</div>
    ${approvals.length ? `<div class="ag-section"><div class="wb-group-h"><span class="wb-group-title">Waiting for your approval</span><span class="wb-count">${approvals.length}</span></div>${approvals.map(approvalHtml).join('')}</div>` : ''}
    ${children.length ? `<div class="ag-section"><div class="wb-group-h"><span class="wb-group-title">Workers & jobs</span><span class="wb-count">${children.length}</span></div>${children.map(childHtml).join('')}</div>` : ''}
    <div class="ag-section ag-compose">
      ${running
        ? `<label class="ag-compose-label" for="ag-steer">Steer <small>lands before the agent's next step${r.steer_queued ? ` · ${r.steer_queued} queued` : ''}</small></label>
           <div class="ag-compose-row"><textarea id="ag-steer" class="wb-input ag-textarea" rows="2" placeholder="e.g. Skip the tests for now and focus on the migration"></textarea><button type="button" class="wb-btn wb-btn-primary" data-ag="steer" data-sid="${esc(r.session_id)}">Steer</button></div>`
        : `<label class="ag-compose-label" for="ag-reply">Send a message <small>opens the chat and sends it</small></label>
           <div class="ag-compose-row"><textarea id="ag-reply" class="wb-input ag-textarea" rows="2" placeholder="Next task for this chat…"></textarea><button type="button" class="wb-btn wb-btn-primary" data-ag="reply" data-sid="${esc(r.session_id)}">Send</button></div>`}
    </div>
    <div class="ag-section ag-events">
      <div class="wb-group-h"><span class="wb-group-title">Live events</span><span class="wb-count">${events.length}</span></div>
      <div class="wb-ev-list ag-ev-list" data-wb-scroll="events">${events.map(eventHtml).join('') || '<div class="wb-empty">No events yet.</div>'}</div>
    </div>`;
  if (keep != null) { const s2 = box.querySelector('[data-wb-scroll="events"]'); if (s2) s2.scrollTop = keep; }
  Object.entries(draft).forEach(([id, value]) => { const el = $(id); if (el) el.value = value; });
  if (sameSelection && activeId) {
    const next = $(activeId);
    if (next) {
      next.focus({ preventScroll: true });
      if (selection && next.setSelectionRange) next.setSelectionRange(selection[0], selection[1]);
    }
  }
  if (!state.events.has(r.session_id)) loadHistory(r.session_id).then(() => { if (state.selected === r.session_id) renderDetail(); });
}
function approvalHtml(a) {
  return `<div class="ag-approval"><div class="ag-approval-head"><span class="approval-badge">Approval needed</span><code class="approval-tool">${esc(a.tool)}</code><span class="wb-meta-item">${esc(a.reason)}</span></div>
    <pre class="approval-command">${esc(a.command)}</pre>
    <div class="approval-actions"><button type="button" class="approval-btn approval-approve" data-ag="approve" data-sid="${esc(a.session_id)}" data-id="${esc(a.id)}" data-decision="once">Approve once</button><button type="button" class="approval-btn" data-ag="approve" data-sid="${esc(a.session_id)}" data-id="${esc(a.id)}" data-decision="always">Always allow ${esc(a.tool)}</button><button type="button" class="approval-btn approval-deny" data-ag="approve" data-sid="${esc(a.session_id)}" data-id="${esc(a.id)}" data-decision="deny">Deny</button></div></div>`;
}
function childHtml(c) {
  const live = c.status === 'running';
  const s = c.summary || {};
  const title = String(c.title || '').replace(/^(Sub-agent|Claude Code|Background job|Worker)\s*[·:]\s*/, '');
  return `<div class="ag-child${live ? '' : ' done'}">${pill(c.status === 'completed' ? 'finished' : c.status)}${chip(c.source)}<span class="ag-child-title" title="${esc(c.title)}">${esc(title)}</span><span class="ag-row-dur" data-started="${c.started_at || ''}" data-finished="${c.finished_at || ''}">${esc(fmtDur(c.started_at, c.finished_at))}</span>
    ${s.target_session ? `<button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-ag="open-chat" data-sid="${esc(s.target_session)}">Open</button>` : ''}
    <button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-ag="inspect-run" data-run="${esc(c.run_id)}" data-sid="${esc(state.selected || '')}">Inspect</button>
    ${live ? `<button type="button" class="wb-btn wb-btn-sm" data-ag="stop-run" data-run="${esc(c.run_id)}">Stop</button>` : ''}</div>`;
}
function eventHtml(ev) {
  const lvl = ev.level === 'error' || ev.kind === 'error' ? ' err' : ev.level === 'warning' ? ' warn' : '';
  return `<div class="wb-ev no-src${lvl}"><span class="wb-ev-time">${fmtTime(ev.ts)}</span><span class="wb-ev-kind">${KIND_ICON[ev.kind] || '·'}</span><div class="wb-ev-body"><span class="wb-ev-title">${chip(ev.source)} ${esc(ev.title || '')}</span>${ev.detail ? `<details class="wb-disclosure"><summary>Detail</summary><pre>${esc(ev.detail)}</pre></details>` : ''}</div></div>`;
}
function launchHtml() {
  const opts = state.profiles.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}${p.description ? ` — ${esc(p.description)}` : ''}</option>`).join('');
  const chats = state.chats.map((c) => `<option value="${esc(c.id)}"${c.id === state.selected ? ' selected' : ''}>${esc(c.name)}</option>`).join('');
  return `<div class="ag-launch-head"><span class="wb-group-title">Launch a worker</span><button type="button" class="wb-icon-btn" data-ag="launch-close" aria-label="Close">✕</button></div>
    <label class="ag-field"><span>Profile</span><select id="ag-profile" class="wb-select"><option value="">No profile (parent chat's model, all tools)</option>${opts}</select></label>
    <label class="ag-field"><span>Task</span><textarea id="ag-task" class="wb-input ag-textarea" rows="5" placeholder="The whole task — the worker starts with no other context."></textarea></label>
    <label class="ag-field"><span>Report to chat</span><select id="ag-parent" class="wb-select"><option value="">None (standalone)</option>${chats}</select></label>
    <label class="ag-field"><span>Model override</span><input id="ag-model" class="wb-input" placeholder="optional, e.g. qwen3 or model@endpoint"></label>
    <div class="ag-launch-actions"><button type="button" class="wb-btn wb-btn-primary" data-ag="launch-go">Launch</button><span class="ag-launch-msg" id="ag-launch-msg"></span></div>
    ${state.profiles.length ? '' : '<p class="wb-hint">No profiles yet — define workers under Settings › Workbench › Agent profiles.</p>'}`;
}

// ── actions ───────────────────────────────────────────────────────────────
async function onClick(e) {
  const row = e.target.closest('.ag-row[data-sid]');
  const b = e.target.closest('[data-ag]');
  if (!b && row) {
    state.selected = row.dataset.sid;
    renderFleetOnly();
    renderDetail();
    return;
  }
  if (!b) return;
  const act = b.dataset.ag;
  try {
    if (act === 'close') close();
    else if (act === 'workbench') {
      if (!window.workbenchModule?.open) throw new Error('Workbench is unavailable');
      close(); window.workbenchModule.open();
    }
    else if (act === 'refresh') { b.disabled = true; await refresh(); if (b.isConnected) b.disabled = false; }
    else if (act === 'launch') { state.launchOpen = true; render(); $('ag-task')?.focus(); }
    else if (act === 'launch-close') { state.launchOpen = false; render(); }
    else if (act === 'filter-status') {
      const s = b.dataset.status;
      state.filter = '';
      const input = $('ag-filter'); if (input) input.value = '';
      state.selected = (state.rows.find((r) => r.status === s) || {}).session_id || state.selected;
      renderFleetOnly(); renderDetail();
    }
    else if (act === 'open-chat') { await openChat(b.dataset.sid); }
    else if (act === 'inspect-run') {
      if (!window.workbenchModule?.openRun) throw new Error('Workbench inspection is unavailable');
      close();
      await selectChat(b.dataset.sid);
      await window.workbenchModule.openRun(b.dataset.run, b.dataset.sid);
    }
    else if (act === 'stop-chat') {
      b.disabled = true;
      const r = await post(`/api/chat/stop/${encodeURIComponent(b.dataset.sid)}`);
      uiModule.showToast(r.stopped ? 'Stopping' : 'Nothing to stop'); scheduleRefresh();
    } else if (act === 'stop-run') {
      b.disabled = true; b.textContent = 'Stopping…';
      const r = await post(`/api/agents/runs/${encodeURIComponent(b.dataset.run)}/stop`);
      uiModule.showToast(r.stopped ? 'Stopping — its partial result goes back to the chat' : `Not stopped: ${r.reason || ''}`, r.stopped ? 'success' : 'warning'); scheduleRefresh();
    } else if (act === 'approve') {
      b.closest('.approval-actions')?.querySelectorAll('button').forEach((x) => { x.disabled = true; });
      await post(`/api/session/${encodeURIComponent(b.dataset.sid)}/approvals/${encodeURIComponent(b.dataset.id)}`, { decision: b.dataset.decision });
      // The decision is a grant; the chat must be told so the agent re-issues the call.
      const tool = b.closest('.ag-approval')?.querySelector('.approval-tool')?.textContent || 'tool';
      const text = b.dataset.decision === 'deny' ? `Denied: don't run that \`${tool}\` call. Tell me what you'll do instead.`
        : b.dataset.decision === 'always' ? `Approved, and always allow \`${tool}\` in this chat. Run that call now.` : `Approved: run that \`${tool}\` call now.`;
      await sendToChat(b.dataset.sid, text);
      uiModule.showToast(b.dataset.decision === 'deny' ? 'Denied' : 'Approved — the agent is resuming'); scheduleRefresh();
    } else if (act === 'steer') {
      const ta = $('ag-steer'); const text = (ta?.value || '').trim(); if (!text) return;
      b.disabled = true;
      await post(`/api/agents/sessions/${encodeURIComponent(b.dataset.sid)}/steer`, { text });
      ta.value = ''; b.disabled = false; uiModule.showToast('Steer queued — applied before the next step'); scheduleRefresh();
    } else if (act === 'reply') {
      const ta = $('ag-reply'); const text = (ta?.value || '').trim(); if (!text) return;
      await sendToChat(b.dataset.sid, text, { open: true });
    } else if (act === 'launch-go') {
      const task = ($('ag-task')?.value || '').trim();
      const msg = $('ag-launch-msg');
      if (!task) { if (msg) msg.textContent = 'Describe the task first.'; return; }
      b.disabled = true; if (msg) msg.textContent = 'Launching…';
      try {
        const r = await post('/api/agents/launch', { task, profile: $('ag-profile')?.value || '', parent_session: $('ag-parent')?.value || '', model: $('ag-model')?.value || '' });
        state.launchOpen = false; state.selected = r.session_id; state.events.delete(r.session_id);
        uiModule.showToast(`Worker started: ${r.session_name}`, 'success');
        await refresh();
      } catch (err) { if (msg) msg.textContent = err.message; b.disabled = false; }
    }
  } catch (err) {
    uiModule.showToast(err.message || String(err), 'error');
    scheduleRefresh();
  }
}
/** Deliver a message to a chat: open it and send through the composer, so it
 *  becomes a normal turn with the chat's own settings. */
async function sendToChat(sid, text, { open = true } = {}) {
  if (open) close();
  await selectChat(sid);
  const ta = $('message');
  if (!ta) throw new Error('Chat composer is unavailable');
  ta.value = text;
  try { ta.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
  const send = document.querySelector('.send-btn');
  if (!send) throw new Error('Chat send control is unavailable');
  send.click();
}
async function selectChat(sid) {
  if (!window.sessionModule?.selectSession) throw new Error('Chat navigation is unavailable');
  await window.sessionModule.selectSession(sid);
  const current = window.sessionModule.getCurrentSessionId?.();
  if (current && current !== sid) throw new Error('Could not open the selected chat');
}
async function openChat(sid) {
  close();
  await selectChat(sid);
}

// ── open / close ──────────────────────────────────────────────────────────
export function open() {
  const root = $('agents-dashboard'); if (!root) return;
  state.open = true; root.hidden = false; document.body.classList.add('agents-dashboard-open');
  if ('Notification' in window && Notification.permission === 'default') { try { Notification.requestPermission(); } catch (_) {} }
  const cur = window.sessionModule?.getCurrentSessionId?.();
  if (cur && state.rows.some((r) => r.session_id === cur)) state.selected = cur;
  render(); refresh(); connect();
  if (!state.tick) state.tick = setInterval(() => {
    if (!state.open) return;
    root.querySelectorAll('.ag-row-dur[data-started]').forEach((el) => {
      const started = Number(el.dataset.started);
      const finished = Number(el.dataset.finished) || undefined;
      if (started) el.textContent = fmtDur(started, finished);
    });
  }, 1000);
}
export function close() {
  const root = $('agents-dashboard'); if (!root) return;
  state.open = false; root.hidden = true; document.body.classList.remove('agents-dashboard-open');
  if (state.tick) { clearInterval(state.tick); state.tick = null; }
}
export function toggle() { state.open ? close() : open(); }

function init() {
  const root = $('agents-dashboard'); if (!root) return;
  root.addEventListener('click', onClick);
  root.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('ag-row')) { e.preventDefault(); e.target.click(); } });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.open) { close(); return; }
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'a') { e.preventDefault(); toggle(); }
  });
  // Badges stay current while the page is closed: a light poll plus the feed.
  connect();
  refresh();
  state.pollTimer = setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, state.open ? 5000 : 20000);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

const agentsDashboard = { open, close, toggle, refresh };
window.agentsDashboard = agentsDashboard;
export default agentsDashboard;
