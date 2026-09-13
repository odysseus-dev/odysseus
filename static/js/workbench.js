/* workbench.js — the agent observability window.
 *
 * One adjustable window (drag / resize / dock / minimize like every other
 * tool window) with four views:
 *   Activity  — the unified agent activity feed: every Odysseus turn,
 *               Claude Code run, sub-agent (send_to_session mode=agent),
 *               model pipeline and background-job follow-up, live over SSE.
 *   Changes   — what a run (or any approved repository) changed: per-file
 *               stats, unified or side-by-side "old vs new" diff.
 *   Commits   — commits since a run started (or the repository log).
 *   Pull Requests — the worktree repository's PRs with checks, files,
 *               comments and reviews; post comments / reviews from here.
 *
 * Each view owns its toolbar (the window header carries only window
 * controls), and every control uses the shared `wb-btn` / `wb-select` styles.
 *
 * It also owns the live "agent run" cards inside the chat: when an activity
 * event announces a sub-process for the current session (Claude Code, a
 * sub-agent, a pipeline, a background job) a compact card appears in the
 * conversation and fills in as the run progresses, with "Open in Workbench"
 * for the full transcript / diff.
 *
 * Everything the window shows comes from /api/workbench/* and
 * /api/claude-code/tasks/*; those routes are admin-only, so a non-admin user
 * never sees the rail button (the settings probe below hides it).
 */

import Modals from './modalManager.js';
import { esc, showToast } from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';
import { snapModalToZone } from './tileManager.js';
import { applyRightDock } from './modalSnap.js';
import { renderDiffText, renderFileTable, parseUnifiedDiff, diffStats } from './diffView.js';

const PREFS_KEY = 'odysseus-workbench-prefs';
const MAX_EVENTS = 1500;
const CHAT_CARD_SOURCES = new Set(['claude_code', 'session', 'pipeline', 'bg_job', 'worktree']);
const SOURCE_LABEL = {
  odysseus: 'Odysseus', claude_code: 'Claude Code', session: 'Sub-agent', pipeline: 'Pipeline',
  bg_job: 'Background job', worktree: 'Worktree', system: 'System',
};
const KIND_ICON = {
  run_started: '▸', run_finished: '■', message: '›', tool_start: '→', tool_result: '←',
  file_change: '±', commit: '●', status: '·', error: '!', note: '~',
};

const state = {
  inited: false,
  enabled: true,
  autoOpen: true,
  prefs: { tab: 'activity', mode: 'split', repo: '', scope: 'session', filter: '' },
  sessionId: null,
  es: null,
  esKey: null,
  lastSeq: 0,
  events: [],
  runs: new Map(),          // run_id → run summary (built from events)
  paused: false,
  focusRun: null,
  // Approved repositories for the Changes / Commits pickers.
  repos: [],
  reposError: '',
  // Changes / Commits view context: either a repository path or a Claude Code task.
  repoCtx: { path: '', base: '', taskId: null, label: '' },
  files: [],
  filesLoading: false,
  commits: [],
  selectedFile: null,
  selectedCommit: null,
  diffText: '',
  pr: { config: null, list: [], selected: null, detail: null, stateFilter: 'open', loading: false },
  chatCards: new Map(),     // run_id → element
};

// ── prefs ─────────────────────────────────────────────────────────────────
function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) Object.assign(state.prefs, JSON.parse(raw) || {});
  } catch (_) {}
}
function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(state.prefs)); } catch (_) {}
}

// ── small helpers ─────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
function currentSessionId() {
  try { return window.sessionModule?.getCurrentSessionId?.() || null; } catch (_) { return null; }
}
function plural(n, word) { return `${n} ${word}${n === 1 ? '' : 's'}`; }
function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  // 24h keeps the time column one fixed width in every locale.
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}
/** ISO string or epoch seconds → "14:05" today, "Sep 10, 14:05" otherwise. */
function fmtWhen(v) {
  if (v == null || v === '') return '';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  if (d.toDateString() === new Date().toDateString()) return time;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric', year: sameYear ? undefined : 'numeric' })}, ${time}`;
}
function whenHtml(v) {
  const label = fmtWhen(v);
  if (!label) return '';
  const full = typeof v === 'number' ? new Date(v * 1000).toISOString() : String(v);
  return `<time title="${esc(full)}">${esc(label)}</time>`;
}
function fmtDur(a, b) {
  if (!a || !b) return '';
  const s = Math.max(0, Math.round(b - a));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
/** A path as dimmed directory + emphasised file name; the directory is what
 *  truncates, so the file name stays readable in narrow lists. */
function pathHtml(path) {
  const p = String(path || '');
  const cut = p.lastIndexOf('/');
  const dir = cut >= 0 ? p.slice(0, cut + 1) : '';
  const name = cut >= 0 ? p.slice(cut + 1) : p;
  return `<span class="wb-path" title="${esc(p)}">${dir ? `<span class="wb-path-dir">${esc(dir)}</span>` : ''}<span class="wb-path-name">${esc(name)}</span></span>`;
}
function statHtml(add, del) {
  return `<span class="wb-stat"><span class="wb-stat-add">+${add || 0}</span><span class="wb-stat-del">−${del || 0}</span></span>`;
}
function emptyHtml(text, { tone = '' } = {}) {
  return `<div class="wb-empty${tone ? ' ' + tone : ''}">${text}</div>`;
}
function segHtml(act, attr, current, options) {
  return `<span class="wb-seg" role="group">${options.map(([value, label]) =>
    `<button type="button" class="${current === value ? 'on' : ''}" data-wb-act="${act}" data-${attr}="${esc(value)}" aria-pressed="${current === value}">${esc(label)}</button>`).join('')}</span>`;
}
function modeSegHtml() {
  return segHtml('mode', 'mode', state.prefs.mode, [['split', 'Split'], ['unified', 'Unified']]);
}
async function api(path, opts = {}) {
  const r = await fetch(path, Object.assign({ credentials: 'same-origin' }, opts));
  let body = null;
  try { body = await r.json(); } catch (_) {}
  if (!r.ok) {
    const msg = (body && (body.detail || body.error)) || `${r.status} ${r.statusText}`;
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    err.status = r.status;
    throw err;
  }
  return body;
}
function post(path, data) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data || {}) });
}
function fillComposer(text) {
  const ta = $('message');
  if (!ta) return;
  const cur = ta.value.trim();
  ta.value = (cur ? cur + '\n\n' : '') + text;
  ta.focus();
  try { ta.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
}
/** Re-render a container without losing where the reader was: scroll offsets
 *  of its scrollers and which disclosures were open. The live feed re-renders
 *  on every event, which used to snap the list back to the top. */
function preserveView(box, render) {
  const scrollers = Array.from(box.querySelectorAll('[data-wb-scroll]')).map((el) => [el.dataset.wbScroll, el.scrollTop]);
  const open = new Set(Array.from(box.querySelectorAll('details[open][data-wb-key]')).map((el) => el.dataset.wbKey));
  render();
  for (const [key, top] of scrollers) {
    const el = box.querySelector(`[data-wb-scroll="${key}"]`);
    if (el) el.scrollTop = top;
  }
  if (open.size) box.querySelectorAll('details[data-wb-key]').forEach((el) => { if (open.has(el.dataset.wbKey)) el.open = true; });
}

// ── activity model ────────────────────────────────────────────────────────
function ingest(ev, { live = true } = {}) {
  if (!ev || typeof ev.seq !== 'number' && ev.seq == null) return;
  state.events.push(ev);
  if (state.events.length > MAX_EVENTS) state.events.splice(0, state.events.length - MAX_EVENTS);
  if (ev.seq > state.lastSeq && (state.prefs.scope === 'all' || ev.session_id === state.sessionId)) state.lastSeq = ev.seq;
  if (ev.run_id) {
    let run = state.runs.get(ev.run_id);
    if (!run) {
      run = { run_id: ev.run_id, source: ev.source, session_id: ev.session_id, title: ev.title, status: 'running',
              started_at: ev.ts, finished_at: null, events: [], data: {}, tools: 0, errors: 0 };
      state.runs.set(ev.run_id, run);
    }
    run.events.push(ev);
    if (ev.kind === 'run_started') { run.title = ev.title; run.started_at = ev.ts; run.detail = ev.detail; Object.assign(run.data, ev.data || {}); }
    if (ev.kind === 'run_finished') { run.status = (ev.data && ev.data.status) || 'completed'; run.finished_at = ev.ts; run.result = ev.title; Object.assign(run.data, ev.data || {}); }
    if (ev.kind === 'tool_start') run.tools += 1;
    if (ev.kind === 'error' || ev.level === 'error') run.errors += 1;
    if (ev.kind === 'status' && ev.data && ev.data.status && ev.data.status !== 'running') run.status = ev.data.status;
  }
  if (live) {
    updateChatCard(ev);
    scheduleStrip();
    if (!state.paused && isOpen()) scheduleRender('activity');
    else updateBadges();
    if (ev.kind === 'run_started' && CHAT_CARD_SOURCES.has(ev.source) && ev.session_id === state.sessionId) {
      try { document.dispatchEvent(new CustomEvent('workbench:run-started', { detail: ev })); } catch (_) {}
    }
  }
}

function isOpen() {
  const m = $('workbench-modal');
  return !!m && !m.classList.contains('hidden');
}

let _renderTimer = null;
function scheduleRender(which) {
  if (_renderTimer) return;
  _renderTimer = setTimeout(() => {
    _renderTimer = null;
    if (which === 'activity' && state.prefs.tab === 'activity') renderActivity();
    else updateBadges();
  }, 120);
}

// ── agent strip (above the composer) ─────────────────────────────────────
// The chat's live delegated work, where the user is looking: each sub-agent,
// Claude Code job and background job with what it is doing right now, how
// long it has run, and Open / Stop. Finished rows linger briefly so the end
// of a run is visible rather than a row silently vanishing.
const STRIP_LINGER_S = 8;
const STRIP_PREVIEW = 3;
let _stripTimer = null;
let _stripTick = null;
function scheduleStrip() {
  if (_stripTimer) return;
  _stripTimer = setTimeout(() => { _stripTimer = null; renderAgentStrip(); }, 150);
}
function stripRuns() {
  const now = Date.now() / 1000;
  return Array.from(state.runs.values())
    .filter((r) => r.session_id === state.sessionId && r.source !== 'odysseus'
      && (r.status === 'running' || (r.finished_at && now - r.finished_at < STRIP_LINGER_S)))
    .sort((a, b) => (a.status === 'running' ? 0 : 1) - (b.status === 'running' ? 0 : 1) || (a.started_at || 0) - (b.started_at || 0));
}
function latestActivity(run) {
  for (let i = run.events.length - 1; i >= 0; i--) {
    const ev = run.events[i];
    if (['message', 'tool_start', 'tool_result', 'status', 'file_change', 'commit'].includes(ev.kind)) return ev.title || '';
  }
  return run.detail ? String(run.detail).split('\n')[0] : '';
}
function stripRowHtml(run) {
  const d = run.data || {};
  const running = run.status === 'running';
  const end = run.finished_at || Date.now() / 1000;
  const openTitle = run.source === 'session' && d.target_session ? `Open the ${d.target_session_name || 'sub-agent'} chat`
    : run.source === 'claude_code' && d.task_id ? 'Open its changes in the Workbench' : 'Open its events in the Workbench';
  return `<div class="agent-strip-row${running ? '' : ' done'}" data-run="${esc(run.run_id)}">
    ${statusPill(run.status)}
    ${sourceChip(run.source)}
    <span class="agent-strip-title" title="${esc(run.detail || run.title || '')}">${esc(String(run.title || '').replace(/^(Sub-agent|Claude Code|Background job)\s*[·:]\s*/, ''))}</span>
    <span class="agent-strip-activity" title="${esc(latestActivity(run))}">${esc(latestActivity(run))}</span>
    <span class="agent-strip-time" data-started="${run.started_at || ''}" data-running="${running ? 1 : 0}">${esc(fmtDur(run.started_at, end))}</span>
    <button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-strip-act="open" data-run="${esc(run.run_id)}" title="${esc(openTitle)}">Open</button>
    ${running ? `<button type="button" class="wb-btn wb-btn-sm" data-strip-act="stop" data-run="${esc(run.run_id)}" title="Stop this run; its partial result goes back to the chat">Stop</button>` : ''}
  </div>`;
}
function renderAgentStrip() {
  const box = $('agent-strip');
  if (!box) return;
  const runs = state.enabled ? stripRuns() : [];
  if (!runs.length) {
    box.hidden = true;
    box.innerHTML = '';
    if (_stripTick) { clearInterval(_stripTick); _stripTick = null; }
    return;
  }
  const running = runs.filter((r) => r.status === 'running').length;
  const collapsed = !!state.prefs.stripCollapsed;
  const expanded = !!state.stripExpanded;
  const shown = collapsed ? [] : (expanded ? runs : runs.slice(0, STRIP_PREVIEW));
  const more = runs.length - shown.length;
  box.hidden = false;
  box.innerHTML = `
    <div class="agent-strip-head">
      <button type="button" class="agent-strip-toggle" data-strip-act="collapse" aria-expanded="${!collapsed}" title="${collapsed ? 'Show' : 'Hide'} agents">
        <span class="agent-run-caret" aria-hidden="true"></span><span class="agent-strip-label">Agents</span>
        <span class="wb-count">${running ? `${running} running` : 'finished'}</span>
      </button>
      <span class="wb-spacer"></span>
      ${!collapsed && more > 0 ? `<button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-strip-act="more">+${more} more</button>` : ''}
      ${!collapsed && expanded && runs.length > STRIP_PREVIEW ? '<button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-strip-act="less">Show fewer</button>' : ''}
      <button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-strip-act="workbench" title="Open the Workbench activity view">Workbench</button>
    </div>
    ${shown.length ? `<div class="agent-strip-rows">${shown.map(stripRowHtml).join('')}</div>` : ''}`;
  if (!_stripTick) {
    _stripTick = setInterval(() => {
      const el = $('agent-strip');
      if (!el || el.hidden) return;
      let lingering = false;
      el.querySelectorAll('.agent-strip-time[data-running="1"]').forEach((t) => {
        const started = parseFloat(t.dataset.started);
        if (started) t.textContent = fmtDur(started, Date.now() / 1000);
      });
      // Drop rows whose linger window ended.
      for (const r of stripRuns()) if (r.status !== 'running') lingering = true;
      if (el.querySelector('.agent-strip-row.done') || lingering) renderAgentStrip();
    }, 1000);
  }
}
async function onStripAction(btn) {
  const act = btn.dataset.stripAct;
  if (act === 'collapse') { state.prefs.stripCollapsed = !state.prefs.stripCollapsed; savePrefs(); renderAgentStrip(); return; }
  if (act === 'more' || act === 'less') { state.stripExpanded = act === 'more'; renderAgentStrip(); return; }
  if (act === 'workbench') { open(); setTab('activity'); return; }
  const run = state.runs.get(btn.dataset.run);
  if (!run) return;
  const d = run.data || {};
  if (act === 'open') {
    if (run.source === 'session' && d.target_session && window.sessionModule?.selectSession) {
      window.sessionModule.selectSession(d.target_session);
    } else if (run.source === 'claude_code' && d.task_id) {
      open(); loadTask(d.task_id);
    } else {
      open(); state.focusRun = run.run_id; setTab('activity');
    }
    return;
  }
  if (act === 'stop') {
    btn.disabled = true;
    btn.textContent = 'Stopping…';
    try {
      const r = await post(`/api/workbench/runs/${encodeURIComponent(run.run_id)}/stop`, {});
      showToast(r.stopped ? 'Stopping — its partial result goes back to the chat' : `Not stopped: ${r.reason || r.status || 'already finished'}`, r.stopped ? 'success' : 'warning');
    } catch (e) {
      showToast(e.message, 'error');
      btn.disabled = false;
      btn.textContent = 'Stop';
    }
  }
}
/** History can show a run as started with no end when the server restarted
 *  mid-run; the run registry knows it was interrupted. */
async function reconcileRunningRuns(sessionId) {
  const stale = Array.from(state.runs.values()).filter((r) => r.status === 'running' && r.session_id === sessionId);
  if (!stale.length) return;
  try {
    const r = await api(`/api/workbench/runs?session_id=${encodeURIComponent(sessionId)}&active=true&limit=200`);
    const live = new Set((r.runs || []).map((x) => x.run_id));
    for (const run of stale) {
      if (!live.has(run.run_id)) { run.status = 'interrupted'; run.finished_at = run.finished_at || run.started_at; }
    }
  } catch (_) {}
}

// ── SSE ───────────────────────────────────────────────────────────────────
function streamKey() {
  return state.prefs.scope === 'all' ? '*' : (state.sessionId || '');
}
let _connectGen = 0;
async function connect(force = false) {
  const key = streamKey();
  // `esKey` is set before the history fetch, so a second caller during that
  // await (the session watcher and open() both connect at startup) no longer
  // starts a parallel connection that ingested the history twice.
  if (!force && state.esKey === key && key) return;
  const gen = ++_connectGen;
  disconnect();
  state.events = [];
  state.runs.clear();
  state.lastSeq = 0;
  if (!key) { renderActivity(); return; }
  state.esKey = key;
  // History first (bounded), then the live stream from the last seq we saw.
  if (key !== '*') {
    try {
      const h = await api(`/api/workbench/activity?session_id=${encodeURIComponent(key)}&limit=400`);
      if (gen !== _connectGen) return;  // superseded by a newer connect
      (h.events || []).forEach((ev) => ingest(ev, { live: false }));
      state.lastSeq = h.seq || state.lastSeq;
      await reconcileRunningRuns(key);
      if (gen !== _connectGen) return;
      renderAgentStrip();
    } catch (e) {
      if (gen !== _connectGen) return;
      if (e.status === 403) { state.enabled = false; hideRail(); return; }
    }
  }
  const es = new EventSource(`/api/workbench/activity/stream?session_id=${encodeURIComponent(key)}&since=${state.lastSeq}`);
  state.es = es;
  es.onopen = () => setLive('live');
  es.onmessage = (m) => {
    let ev = null;
    try { ev = JSON.parse(m.data); } catch (_) { return; }
    if (!ev || ev.type === 'ready') { setLive('live'); return; }
    ingest(ev, { live: true });
  };
  // EventSource reconnects itself; the `since` is stale but history replay
  // covers the gap on reopen.
  es.onerror = () => setLive('reconnecting');
  renderActivity();
}
function disconnect() {
  if (state.es) { try { state.es.close(); } catch (_) {} }
  state.es = null;
  state.esKey = null;
  setLive('off');
}
function setLive(mode) {
  const el = $('wb-live');
  if (!el) return;
  const m = state.paused ? 'paused' : mode;
  el.dataset.state = m;
  el.textContent = { live: 'Live', reconnecting: 'Reconnecting…', paused: 'Paused', off: 'Offline' }[m] || m;
}

// Follow the chat's current session. There is no session-change event, so a
// light poll (the same approach other windows use) keeps the feed aligned.
function watchSession() {
  const tick = () => {
    const sid = currentSessionId();
    if (sid !== state.sessionId) {
      state.sessionId = sid;
      state.chatCards.clear();
      state.focusRun = null;
      state.stripExpanded = false;
      renderAgentStrip();
      if (state.es || isOpen() || state.enabled) connect(true);
    }
  };
  tick();
  setInterval(tick, 1500);
}

// ── rendering: activity ───────────────────────────────────────────────────
function statusClass(status) {
  const s = status || 'running';
  if (s === 'running' || s === 'queued' || s === 'in_progress' || s === 'pending') return 'run';
  if (s === 'completed' || s === 'succeeded' || s === 'done' || s === 'success' || s === 'open' || s === 'merged') return 'ok';
  if (s === 'cancelled' || s === 'draft' || s === 'interrupted') return 'warn';
  return 'bad';
}
function statusPill(status, label) {
  const s = status || 'running';
  return `<span class="wb-pill ${statusClass(s)}"><i aria-hidden="true"></i>${esc(label || s)}</span>`;
}
function sourceChip(source) {
  return `<span class="wb-chip wb-src-${esc(source)}">${esc(SOURCE_LABEL[source] || source)}</span>`;
}
function runCardHtml(run) {
  const d = run.data || {};
  const meta = [sourceChip(run.source)];
  if (d.repository) meta.push(`<span class="wb-meta-item" title="${esc(d.repository)}">${esc(String(d.repository).split('/').slice(-2).join('/'))}</span>`);
  if (d.model) meta.push(`<span class="wb-meta-item">${esc(d.model)}</span>`);
  if (run.tools) meta.push(`<span class="wb-meta-item">${plural(run.tools, 'tool')}</span>`);
  if (d.changes_count) meta.push(`<span class="wb-meta-item">${plural(d.changes_count, 'file')}</span>`);
  if (d.commit_count) meta.push(`<span class="wb-meta-item">${plural(d.commit_count, 'commit')}</span>`);
  if (run.errors) meta.push(`<span class="wb-meta-item wb-text-bad">${plural(run.errors, 'error')}</span>`);
  const dur = run.finished_at ? fmtDur(run.started_at, run.finished_at) : fmtDur(run.started_at, Date.now() / 1000);
  const actions = [];
  if (run.source === 'claude_code' && d.task_id) {
    actions.push(`<button type="button" class="wb-btn wb-btn-sm" data-wb-act="run-changes" data-task="${esc(d.task_id)}">Changes</button>`);
    actions.push(`<button type="button" class="wb-btn wb-btn-sm" data-wb-act="run-transcript" data-task="${esc(d.task_id)}">Transcript</button>`);
  }
  const excerpt = d.error || d.result_excerpt;
  const focused = state.focusRun === run.run_id;
  return `<div class="wb-run${focused ? ' focused' : ''}" data-run="${esc(run.run_id)}" role="button" tabindex="0" aria-pressed="${focused}" title="${focused ? 'Show all events' : "Show only this run's events"}">
    <div class="wb-run-head">
      ${statusPill(run.status)}
      <span class="wb-run-title" title="${esc(run.detail || run.title || '')}">${esc(run.title || run.run_id)}</span>
      <span class="wb-run-dur">${esc(dur)}</span>
    </div>
    <div class="wb-run-meta">${meta.join('')}</div>
    ${excerpt && run.status !== 'running' ? `<div class="wb-run-excerpt${d.error ? ' wb-text-bad' : ''}">${esc(excerpt)}</div>` : ''}
    ${actions.length ? `<div class="wb-run-actions">${actions.join('')}</div>` : ''}
  </div>`;
}
function disclosureHtml(key, label, body) {
  return `<details class="wb-disclosure" data-wb-key="${esc(key)}"><summary>${esc(label)}</summary><pre>${esc(body)}</pre></details>`;
}
function eventRowHtml(ev, { showSource = true } = {}) {
  const lvl = ev.level === 'error' || ev.kind === 'error' ? ' err' : (ev.level === 'warning' ? ' warn' : '');
  const extra = [];
  if (ev.detail) extra.push(disclosureHtml(`${ev.seq}:detail`, 'Detail', ev.detail));
  if (ev.data && Object.keys(ev.data).length) extra.push(disclosureHtml(`${ev.seq}:data`, 'Data', JSON.stringify(ev.data, null, 1)));
  return `<div class="wb-ev${lvl}${showSource ? '' : ' no-src'}" data-seq="${ev.seq}" data-run="${esc(ev.run_id || '')}">
    <span class="wb-ev-time">${fmtTime(ev.ts)}</span>
    ${showSource ? `<span class="wb-ev-src">${sourceChip(ev.source)}</span>` : ''}
    <span class="wb-ev-kind" title="${esc(ev.kind)}">${KIND_ICON[ev.kind] || '·'}</span>
    <div class="wb-ev-body"><span class="wb-ev-title">${esc(ev.title || '')}</span>${extra.join('')}</div>
  </div>`;
}
function renderActivity() {
  const box = $('wb-activity');
  if (!box) return;
  const filter = state.prefs.filter || '';
  const runs = Array.from(state.runs.values()).filter((r) => !filter || r.source === filter)
    .sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
  const active = runs.filter((r) => r.status === 'running');
  const recent = runs.filter((r) => r.status !== 'running').slice(0, 20);
  const focusRun = state.focusRun ? state.runs.get(state.focusRun) : null;
  const shown = focusRun ? focusRun.events : state.events.filter((e) => !filter || e.source === filter).slice(-300);
  const noSession = !state.sessionId && state.prefs.scope !== 'all';
  const streamHead = focusRun
    ? `<span class="wb-group-title">Events · <em title="${esc(focusRun.title)}">${esc(focusRun.title)}</em></span><button type="button" class="wb-btn wb-btn-sm" data-wb-act="unfocus">Show all</button>`
    : '<span class="wb-group-title">Event stream</span>';
  preserveView(box, () => {
    box.innerHTML = `
      <section class="wb-card wb-runs">
        <div class="wb-card-scroll" data-wb-scroll="runs">
          <div class="wb-group-h"><span class="wb-group-title">Running</span><span class="wb-count">${active.length}</span></div>
          ${active.length ? active.map(runCardHtml).join('') : emptyHtml('Nothing running. Claude Code delegations, sub-agents, pipelines and background jobs show up here when they start.', { tone: 'inline' })}
          <div class="wb-group-h"><span class="wb-group-title">Recent</span><span class="wb-count">${recent.length}</span></div>
          ${recent.length ? recent.map(runCardHtml).join('') : emptyHtml(noSession ? 'Open a chat to follow its activity, or switch the scope to All sessions.' : 'No finished runs yet.', { tone: 'inline' })}
        </div>
      </section>
      <section class="wb-card wb-stream">
        <div class="wb-card-h">${streamHead}<span class="wb-count">${shown.length}</span></div>
        <div class="wb-ev-list" data-wb-scroll="events">${shown.length ? shown.slice().reverse().map((ev) => eventRowHtml(ev, { showSource: !focusRun && !filter })).join('') : emptyHtml(noSession ? 'No chat selected.' : 'No events yet.')}</div>
      </section>`;
  });
  updateBadges();
}
function updateBadges() {
  const running = Array.from(state.runs.values()).filter((r) => r.status === 'running').length;
  const setBadge = (name, text, tone) => {
    const el = document.querySelector(`#workbench-modal [data-wb-badge="${name}"]`);
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || '';
    el.className = `wb-tab-badge${tone ? ' ' + tone : ''}`;
  };
  setBadge('activity', running ? String(running) : '', 'run');
  setBadge('changes', state.files.length ? String(state.files.length) : '');
  setBadge('commits', state.commits.length ? String(state.commits.length) : '');
}

// ── rendering: changes / commits ──────────────────────────────────────────
function fileRowHtml(f, selected, attr = 'data-path') {
  const status = String(f.status || (f.new_file ? 'A' : 'M')).slice(0, 1).toUpperCase();
  return `<div class="wb-row wb-file${selected ? ' active' : ''}" ${attr}="${esc(f.path)}" role="button" tabindex="0">
    <span class="wb-file-status wb-st-${esc(status)}" title="${esc(f.status || status)}">${esc(status)}</span>
    ${pathHtml(f.path)}
    ${f.binary ? '<span class="wb-meta-item">binary</span>' : statHtml(f.additions, f.deletions)}
  </div>`;
}
/** Where Changes / Commits read from: the repository picker, or — while a
 *  Claude Code run is loaded — a chip naming the run with a way back. */
function sourceControlHtml() {
  const c = state.repoCtx;
  if (c.taskId) {
    return `<span class="wb-ctx-chip" title="${esc(c.path || '')}">${sourceChip('claude_code')}<span class="wb-ctx-chip-label">${esc(c.label || c.taskId.slice(0, 8))}</span><button type="button" class="wb-chip-close" data-wb-act="leave-run" title="Back to the repository's working tree" aria-label="Back to repository">×</button></span>`;
  }
  if (state.reposError) return `<span class="wb-meta-item wb-text-bad" title="${esc(state.reposError)}">Repositories unavailable</span>`;
  const options = state.repos.map((x) => `<option value="${esc(x.path)}"${x.path === c.path ? ' selected' : ''}>${esc(x.path)}${x.branch ? ` (${esc(x.branch)})` : ''}</option>`).join('');
  return `<select class="wb-select wb-repo-select" data-wb-change="repo" title="Repository" aria-label="Repository">${c.path ? '' : '<option value="">Choose a repository…</option>'}${options}</select>`;
}
function renderChanges() {
  const box = $('wb-changes');
  if (!box) return;
  const c = state.repoCtx;
  const total = state.files.reduce((a, f) => { a.add += f.additions || 0; a.del += f.deletions || 0; return a; }, { add: 0, del: 0 });
  const hasSource = !!(c.path || c.taskId);
  let list;
  if (state.filesLoading) list = emptyHtml('Loading changes…');
  else if (state.files.length) list = state.files.map((f) => fileRowHtml(f, f.path === state.selectedFile)).join('');
  else list = emptyHtml(hasSource ? 'Working tree is clean.' : 'Choose a repository, or open a Claude Code run from Activity.');
  preserveView(box, () => {
    box.innerHTML = `
      <div class="wb-toolbar">
        ${sourceControlHtml()}
        ${c.taskId ? '' : `<input class="wb-input wb-base" id="wb-base" placeholder="Compare against (default: HEAD)" value="${esc(c.base || '')}" title="Base ref: a branch, tag or commit. Enter to apply." aria-label="Base ref">`}
        <button type="button" class="wb-btn" data-wb-act="refresh-changes" title="Reload the change list">Refresh</button>
        <span class="wb-spacer"></span>
        ${state.files.length ? `<span class="wb-summary">${plural(state.files.length, 'file')} ${statHtml(total.add, total.del)}</span>` : ''}
        ${modeSegHtml()}
      </div>
      <div class="wb-split">
        <div class="wb-card wb-list" data-wb-scroll="files">${list}</div>
        <div class="wb-card wb-pane" id="wb-diffpane" data-wb-scroll="diff">${diffPaneHtml()}</div>
      </div>`;
  });
  updateBadges();
}
function diffHeadHtml(path, stats, actions) {
  return `<div class="wb-pane-h">${pathHtml(path)}${stats ? statHtml(stats.additions, stats.deletions) : ''}<span class="wb-spacer"></span>${actions}</div>`;
}
function diffPaneHtml() {
  if (!state.selectedFile) return emptyHtml(state.files.length ? 'Select a file to compare old and new.' : 'Nothing to compare.');
  if (state.diffText == null) return emptyHtml('Loading diff…');
  const head = diffHeadHtml(state.selectedFile, diffStats(state.diffText),
    '<button type="button" class="wb-btn wb-btn-sm" data-wb-act="send-file" title="Paste this diff into the chat composer">Send to agent</button><button type="button" class="wb-btn wb-btn-sm" data-wb-act="popout" title="Open in a separate window">Pop out</button>');
  if (!state.diffText.trim()) return head + emptyHtml('No textual diff (binary or unchanged).');
  return head + `<div class="wb-diff-body">${renderDiffText(state.diffText, { mode: state.prefs.mode, path: state.selectedFile })}</div>`
    + '<div class="wb-hint">Click a line number to quote that line in the composer.</div>';
}
function renderCommits() {
  const box = $('wb-commits');
  if (!box) return;
  const c = state.repoCtx;
  const sel = state.selectedCommit;
  const list = state.commits.length
    ? state.commits.map((k) => `<div class="wb-row wb-commit${sel && sel.sha === k.sha ? ' active' : ''}" data-sha="${esc(k.sha)}" role="button" tabindex="0">
        <div class="wb-commit-line"><code class="wb-sha">${esc((k.sha || '').slice(0, 7))}</code><span class="wb-commit-subj" title="${esc(k.subject || k.message || '')}">${esc(k.subject || k.message || '')}</span></div>
        <div class="wb-row-meta">${esc(k.author || '')}${k.author && (k.date || k.ts) ? ' · ' : ''}${whenHtml(k.date || k.ts)}</div>
      </div>`).join('')
    : emptyHtml(c.path || c.taskId ? 'No commits.' : 'Choose a repository, or open a Claude Code run from Activity.');
  preserveView(box, () => {
    box.innerHTML = `
      <div class="wb-toolbar">
        ${sourceControlHtml()}
        <button type="button" class="wb-btn" data-wb-act="refresh-commits" title="Reload commits">Refresh</button>
        <span class="wb-spacer"></span>
        <span class="wb-summary">${plural(state.commits.length, 'commit')}${c.taskId ? ' since the run started' : ''}</span>
        ${modeSegHtml()}
      </div>
      <div class="wb-split">
        <div class="wb-card wb-list" data-wb-scroll="commits">${list}</div>
        <div class="wb-card wb-pane" id="wb-commitpane" data-wb-scroll="commit">${commitPaneHtml()}</div>
      </div>`;
  });
  updateBadges();
}
function commitPaneHtml() {
  const k = state.selectedCommit;
  if (!k) return emptyHtml(state.commits.length ? 'Select a commit to see what it changed.' : 'Nothing selected.');
  if (k.loading) return emptyHtml('Loading commit…');
  const files = k.files || [];
  const body = k.body ? `<pre class="wb-prose">${esc(k.body)}</pre>` : '';
  const fileList = files.map((f) => fileRowHtml(f, f.path === k.selectedFile)).join('');
  let diff = '';
  if (k.selectedFile) {
    diff = diffHeadHtml(k.selectedFile, k.diffText ? diffStats(k.diffText) : null,
      '<button type="button" class="wb-btn wb-btn-sm" data-wb-act="popout-commit">Pop out</button>')
      + (k.diffText == null ? emptyHtml('Loading diff…') : `<div class="wb-diff-body">${renderDiffText(k.diffText, { mode: state.prefs.mode, path: k.selectedFile })}</div>`);
  }
  return `<div class="wb-detail">
      <div class="wb-detail-title">${esc(k.subject || '')}</div>
      <div class="wb-row-meta"><code class="wb-sha">${esc((k.sha || '').slice(0, 12))}</code> · ${esc(k.author || '')}${k.email ? ` &lt;${esc(k.email)}&gt;` : ''} · ${whenHtml(k.date || k.ts)}</div>
      ${body}
      <div class="wb-group-h"><span class="wb-group-title">Files</span><span class="wb-count">${files.length}</span></div>
      <div class="wb-sublist">${fileList || emptyHtml('No file list.', { tone: 'inline' })}</div>
    </div>${diff}`;
}

// ── rendering: pull requests ──────────────────────────────────────────────
function checkPill(c) {
  const st = (c.conclusion || c.status || '').toLowerCase();
  const cls = st === 'success' ? 'ok' : (st === 'failure' || st === 'error' || st === 'timed_out' || st === 'cancelled') ? 'bad' : (st === 'in_progress' || st === 'queued' || st === 'pending') ? 'run' : 'warn';
  const name = c.name || c.context || 'check';
  return `<span class="wb-pill ${cls}" title="${esc(name)}: ${esc(st || 'unknown')}"><i aria-hidden="true"></i>${esc(name)}</span>`;
}
function renderPRs() {
  const box = $('wb-prs');
  if (!box) return;
  const p = state.pr;
  if (p.config && !p.config.configured) {
    box.innerHTML = `<div class="wb-card wb-notice">
      <div class="wb-detail-title">Connect GitHub to review pull requests</div>
      <p>Pull request review uses the agent worktree's GitHub credential, which isn't ready yet:</p>
      <ul>${(p.config.blockers || []).map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
      <p class="wb-row-meta">Set it up under Settings → Agent worktree, then come back to this tab.</p>
      <div><button type="button" class="wb-btn" data-wb-act="recheck-prs">Check again</button></div>
    </div>`;
    return;
  }
  let list;
  if (!p.config || p.loading) list = emptyHtml('Loading pull requests…');
  else if (p.list.length) {
    list = p.list.map((pr) => `<div class="wb-row wb-pr${p.selected === pr.number ? ' active' : ''}" data-number="${pr.number}" role="button" tabindex="0">
      <div class="wb-commit-line"><span class="wb-pr-num">#${pr.number}</span><span class="wb-commit-subj" title="${esc(pr.title || '')}">${esc(pr.title || '')}</span></div>
      <div class="wb-row-meta">${statusPill(pr.draft ? 'draft' : (pr.merged ? 'merged' : pr.state), pr.draft ? 'draft' : (pr.merged ? 'merged' : pr.state))}<span>${esc(pr.user || pr.author || '')}</span><span class="wb-branch" title="${esc(pr.head || '')} → ${esc(pr.base || '')}">${esc(pr.head || '')} → ${esc(pr.base || '')}</span></div>
    </div>`).join('');
  } else list = emptyHtml(`No ${p.stateFilter === 'all' ? '' : p.stateFilter + ' '}pull requests.`);
  preserveView(box, () => {
    box.innerHTML = `
      <div class="wb-toolbar">
        ${p.config && p.config.repo ? `<span class="wb-ctx-chip"><span class="wb-ctx-chip-label">${esc(p.config.repo)}</span></span>` : ''}
        ${segHtml('pr-state', 'state', p.stateFilter, [['open', 'Open'], ['closed', 'Closed'], ['all', 'All']])}
        <span class="wb-spacer"></span>
        <button type="button" class="wb-btn" data-wb-act="refresh-prs">Refresh</button>
      </div>
      <div class="wb-split">
        <div class="wb-card wb-list" data-wb-scroll="prs">${list}</div>
        <div class="wb-card wb-pane" id="wb-prpane" data-wb-scroll="pr">${prPaneHtml()}</div>
      </div>`;
  });
}
function prPaneHtml() {
  const d = state.pr.detail;
  if (!state.pr.selected) return emptyHtml(state.pr.list.length ? 'Select a pull request to review it.' : 'Nothing selected.');
  if (!d) return emptyHtml('Loading pull request…');
  if (d.error) return emptyHtml(esc(d.error), { tone: 'bad' });
  const pr = d.pull || {};
  const prState = pr.merged ? 'merged' : (pr.draft ? 'draft' : pr.state);
  const checks = (d.checks || []).map(checkPill).join('');
  const files = (d.files || []).map((f) => fileRowHtml(f, d.selectedFile === f.path, 'data-prfile')).join('');
  const comments = [...(d.comments || []).map((c) => ({ ...c, _t: 'comment' })), ...(d.review_comments || []).map((c) => ({ ...c, _t: 'review' })), ...(d.reviews || []).filter((r) => r.body).map((r) => ({ ...r, _t: 'reviewsum' }))]
    .sort((a, b) => String(a.created_at || a.submitted_at || '').localeCompare(String(b.created_at || b.submitted_at || '')));
  const thread = comments.map((c) => {
    const kind = c._t === 'review' ? `<span class="wb-comment-where">on ${pathHtml(`${c.path || ''}${c.line ? ':' + c.line : ''}`)}</span>`
      : c._t === 'reviewsum' ? `reviewed · ${esc(String(c.state || '').toLowerCase().replace('_', ' '))}` : 'commented';
    return `<div class="wb-comment"><div class="wb-row-meta"><b>${esc(c.user || c.author || '')}</b><span>${kind}</span><span class="wb-spacer"></span>${whenHtml(c.created_at || c.submitted_at)}</div><div class="wb-comment-body">${esc(c.body || '')}</div></div>`;
  }).join('') || emptyHtml('No comments yet.', { tone: 'inline' });
  let diff = '';
  if (d.selectedFile) {
    const parsed = d.diffText ? parseUnifiedDiff(d.diffText) : [];
    const f = parsed.find((x) => x.newPath === d.selectedFile || x.oldPath === d.selectedFile);
    diff = `<div class="wb-card wb-inline-diff">${diffHeadHtml(d.selectedFile, null, `${modeSegHtml()}<button type="button" class="wb-btn wb-btn-sm" data-wb-act="popout-pr">Pop out</button>`)}
      <div class="wb-diff-body">${d.diffText == null ? emptyHtml('Loading diff…') : (f ? renderFileTable(f, { mode: state.prefs.mode, path: d.selectedFile }) : emptyHtml('No textual diff for this file.'))}</div>
      <div class="wb-hint">Click a line number to attach a review comment to that line.</div></div>`;
  }
  const pending = d.pendingComments || [];
  return `<div class="wb-detail">
    <div class="wb-detail-title"><a href="${esc(pr.html_url || pr.url || '#')}" target="_blank" rel="noopener">#${pr.number} ${esc(pr.title || '')}</a></div>
    <div class="wb-row-meta">${statusPill(prState, prState)}<span>${esc(pr.user || '')}</span><span class="wb-branch">${esc(pr.head || '')} → ${esc(pr.base || '')}</span>${pr.mergeable_state ? `<span>${esc(pr.mergeable_state)}</span>` : ''}</div>
    ${checks ? `<div class="wb-checks">${checks}</div>` : ''}
    ${pr.body ? `<details class="wb-disclosure" data-wb-key="pr-body"><summary>Description</summary><pre>${esc(pr.body)}</pre></details>` : ''}
    <div class="wb-pr-cols">
      <div class="wb-pr-col">
        <div class="wb-group-h"><span class="wb-group-title">Files</span><span class="wb-count">${(d.files || []).length}</span></div>
        <div class="wb-sublist">${files || emptyHtml('No files.', { tone: 'inline' })}</div>
        ${diff}
      </div>
      <div class="wb-pr-col">
        <div class="wb-group-h"><span class="wb-group-title">Conversation</span><span class="wb-count">${comments.length}</span></div>
        <div class="wb-thread">${thread}</div>
        ${pending.length ? `<div class="wb-group-h"><span class="wb-group-title">Pending line comments</span><span class="wb-count">${pending.length}</span></div>${pending.map((c, i) => `<div class="wb-comment pending"><div class="wb-row-meta">${pathHtml(`${c.path}:${c.line}`)}<span class="wb-spacer"></span><button type="button" class="wb-btn wb-btn-sm wb-btn-ghost" data-wb-act="drop-pending" data-i="${i}">Remove</button></div><div class="wb-comment-body">${esc(c.body)}</div></div>`).join('')}` : ''}
        <textarea id="wb-pr-body" class="wb-input wb-textarea" placeholder="Leave a comment or review summary…" aria-label="Comment"></textarea>
        <div class="wb-compose-actions">
          <button type="button" class="wb-btn wb-btn-primary" data-wb-act="pr-comment" title="Post as a plain comment">Comment</button>
          <span class="wb-seg wb-seg-actions" role="group" aria-label="Submit review">
            <button type="button" data-wb-act="pr-review" data-event="COMMENT" title="Submit a review with the pending line comments">Review</button>
            <button type="button" class="ok" data-wb-act="pr-review" data-event="APPROVE">Approve</button>
            <button type="button" class="bad" data-wb-act="pr-review" data-event="REQUEST_CHANGES">Request changes</button>
          </span>
        </div>
        <button type="button" class="wb-btn wb-btn-ghost wb-btn-block" data-wb-act="pr-to-agent" title="Draft a request in the chat composer">Ask Odysseus to address this PR</button>
      </div>
    </div></div>`;
}

// ── data loading ──────────────────────────────────────────────────────────
async function loadRoots() {
  try {
    const r = await api('/api/workbench/repo/roots');
    state.repos = r.repositories || [];
    state.reposError = '';
    const repos = state.repos;
    const want = state.repoCtx.path || state.prefs.repo || (r.workspace && repos.some((x) => x.path === r.workspace) ? r.workspace : '') || (repos[0] && repos[0].path) || '';
    if (want && repos.some((x) => x.path === want) && !state.repoCtx.taskId && state.repoCtx.path !== want) { setRepo(want); return; }
  } catch (e) {
    if (e.status === 403) { state.enabled = false; hideRail(); }
    state.repos = [];
    state.reposError = e.message;
  }
  if (state.prefs.tab === 'changes') renderChanges();
  if (state.prefs.tab === 'commits') renderCommits();
}
function setRepo(path) {
  state.repoCtx = { path, base: '', taskId: null, label: '' };
  state.prefs.repo = path; savePrefs();
  state.files = []; state.commits = []; state.selectedFile = null; state.selectedCommit = null; state.diffText = '';
  refreshChanges(); refreshCommits();
}
async function loadTask(taskId, { tab = 'changes' } = {}) {
  state.repoCtx = { path: '', base: '', taskId, label: '' };
  state.files = []; state.commits = []; state.selectedFile = null; state.selectedCommit = null; state.diffText = '';
  state.filesLoading = true;
  setTab(tab);
  try {
    const r = await api(`/api/claude-code/tasks/${encodeURIComponent(taskId)}/changes`);
    state.repoCtx.path = r.repository || '';
    state.repoCtx.base = r.start_commit || '';
    state.repoCtx.label = r.label || '';
    state.repoCtx.task = r;
    state.files = r.changes || [];
    state.commits = r.commits || [];
    if (r.changes_error) showToast(`Change list: ${r.changes_error}`, 'warning');
  } catch (e) { showToast(`Could not load run changes: ${e.message}`, 'error'); }
  state.filesLoading = false;
  renderChanges(); renderCommits();
  autoSelectFirstFile();
}
async function refreshChanges() {
  const c = state.repoCtx;
  if (c.taskId) return loadTask(c.taskId, { tab: state.prefs.tab });
  if (!c.path) { renderChanges(); return; }
  const baseInput = $('wb-base');
  if (baseInput) c.base = baseInput.value.trim();
  state.filesLoading = !state.files.length;
  if (state.filesLoading) renderChanges();
  try {
    const r = await api(`/api/workbench/repo/changes?path=${encodeURIComponent(c.path)}${c.base ? '&base=' + encodeURIComponent(c.base) : ''}`);
    state.files = r.files || [];
    if (r.truncated) showToast('Change list truncated', 'warning');
  } catch (e) { state.files = []; showToast(e.message, 'error'); }
  state.filesLoading = false;
  if (state.selectedFile && !state.files.some((f) => f.path === state.selectedFile)) { state.selectedFile = null; state.diffText = ''; }
  renderChanges();
  autoSelectFirstFile();
}
/** Land on something useful: the first changed file's diff. */
function autoSelectFirstFile() {
  if (!state.selectedFile && state.files.length && state.prefs.tab === 'changes') selectFile(state.files[0].path);
}
async function refreshCommits() {
  const c = state.repoCtx;
  if (!c.path && !c.taskId) { renderCommits(); return; }
  if (c.taskId) { renderCommits(); return; }
  try {
    const r = await api(`/api/workbench/repo/commits?path=${encodeURIComponent(c.path)}&limit=50${c.base ? '&base=' + encodeURIComponent(c.base) : ''}`);
    state.commits = r.commits || [];
  } catch (e) { state.commits = []; showToast(e.message, 'error'); }
  renderCommits();
}
async function selectFile(path) {
  state.selectedFile = path; state.diffText = null;
  renderChanges();
  const c = state.repoCtx;
  try {
    const r = c.taskId
      ? await api(`/api/claude-code/tasks/${encodeURIComponent(c.taskId)}/diff?path=${encodeURIComponent(path)}`)
      : await api(`/api/workbench/repo/diff?path=${encodeURIComponent(c.path)}&file=${encodeURIComponent(path)}${c.base ? '&base=' + encodeURIComponent(c.base) : ''}`);
    if (state.selectedFile !== path) return;
    state.diffText = r.diff || r.text || '';
    if (r.truncated) showToast('Diff truncated', 'warning');
  } catch (e) { if (state.selectedFile === path) state.diffText = ''; showToast(e.message, 'error'); }
  if (state.selectedFile === path) {
    const pane = $('wb-diffpane');
    if (pane) { pane.innerHTML = diffPaneHtml(); pane.scrollTop = 0; }
  }
}
async function selectCommit(sha) {
  const k = state.commits.find((x) => x.sha === sha) || { sha };
  state.selectedCommit = Object.assign({ loading: true }, k);
  renderCommits();
  try {
    const r = await api(`/api/workbench/repo/commit?path=${encodeURIComponent(state.repoCtx.path)}&sha=${encodeURIComponent(sha)}`);
    state.selectedCommit = Object.assign({}, k, r, { loading: false, selectedFile: null, diffText: null });
  } catch (e) { state.selectedCommit = Object.assign({}, k, { loading: false, body: e.message }); }
  renderCommits();
}
async function selectCommitFile(path) {
  const k = state.selectedCommit; if (!k) return;
  k.selectedFile = path; k.diffText = null; renderCommits();
  try {
    const r = await api(`/api/workbench/repo/diff?path=${encodeURIComponent(state.repoCtx.path)}&file=${encodeURIComponent(path)}&commit=${encodeURIComponent(k.sha)}`);
    k.diffText = r.diff || r.text || '';
  } catch (e) { k.diffText = ''; showToast(e.message, 'error'); }
  renderCommits();
}
async function loadPRConfig() {
  state.pr.config = null;
  renderPRs();
  try { state.pr.config = await api('/api/workbench/prs/config'); }
  catch (e) { state.pr.config = { configured: false, blockers: [e.message] }; }
  renderPRs();
  if (state.pr.config.configured) refreshPRs();
}
async function refreshPRs() {
  state.pr.loading = !state.pr.list.length;
  if (state.pr.loading) renderPRs();
  try {
    const r = await api(`/api/workbench/prs?state=${encodeURIComponent(state.pr.stateFilter)}&limit=30`);
    state.pr.list = r.pulls || [];
  } catch (e) { state.pr.list = []; showToast(e.message, 'error'); }
  state.pr.loading = false;
  renderPRs();
}
async function selectPR(number) {
  state.pr.selected = number; state.pr.detail = null; renderPRs();
  try {
    const d = await api(`/api/workbench/prs/${number}`);
    d.pendingComments = []; d.selectedFile = null; d.diffText = null;
    state.pr.detail = d;
  } catch (e) { state.pr.detail = { error: e.message }; }
  renderPRs();
}
async function selectPRFile(path) {
  const d = state.pr.detail; if (!d) return;
  d.selectedFile = path; renderPRs();
  if (d.diffText == null) {
    try { const r = await api(`/api/workbench/prs/${state.pr.selected}/diff`); d.diffText = r.diff || r.text || ''; }
    catch (e) { d.diffText = ''; showToast(e.message, 'error'); }
    renderPRs();
  }
}
async function postPRComment() {
  const ta = $('wb-pr-body'); const body = ta ? ta.value.trim() : '';
  if (!body) { showToast('Write a comment first', 'warning'); return; }
  try {
    await post(`/api/workbench/prs/${state.pr.selected}/comment`, { body, session_id: state.sessionId });
    showToast('Comment posted', 'success'); selectPR(state.pr.selected);
  } catch (e) { showToast(e.message, 'error'); }
}
async function postPRReview(event) {
  const d = state.pr.detail; if (!d) return;
  const ta = $('wb-pr-body'); const body = ta ? ta.value.trim() : '';
  const comments = (d.pendingComments || []).map((c) => ({ path: c.path, line: c.line, body: c.body }));
  if (!body && !comments.length && event !== 'APPROVE') { showToast('Write a review summary or add line comments', 'warning'); return; }
  try {
    await post(`/api/workbench/prs/${state.pr.selected}/review`, { event, body, comments, session_id: state.sessionId });
    showToast(`Review submitted (${event.toLowerCase().replace('_', ' ')})`, 'success'); selectPR(state.pr.selected);
  } catch (e) { showToast(e.message, 'error'); }
}
function prToAgent() {
  const d = state.pr.detail; if (!d || !d.pull) return;
  const pr = d.pull;
  const failing = (d.checks || []).filter((c) => ['failure', 'error', 'timed_out'].includes(String(c.conclusion || '').toLowerCase())).map((c) => c.name || c.context);
  const lines = [`Please look at pull request #${pr.number} "${pr.title}" (${pr.head} → ${pr.base}) in ${d.repo || ''}.`];
  if (failing.length) lines.push(`Failing checks: ${failing.join(', ')}.`);
  const open = (d.review_comments || []).slice(-5).map((c) => `- ${c.user || ''} on ${c.path || ''}${c.line ? ':' + c.line : ''}: ${String(c.body || '').split('\n')[0].slice(0, 200)}`);
  if (open.length) lines.push('Recent review comments:', ...open);
  lines.push('Address the feedback and failing checks, then summarise what you changed.');
  fillComposer(lines.join('\n'));
  showToast('Request drafted in the composer', 'success');
}

// ── pop-out diff window ───────────────────────────────────────────────────
function popout(title, html) {
  const modal = $('workbench-diff-modal');
  if (!modal) return;
  modal.querySelector('.wb-popout-title').textContent = title;
  modal.querySelector('.wb-popout-body').innerHTML = html;
  modal.classList.remove('hidden');
  Modals.register('workbench-diff-modal', { label: 'Diff', closeFn: () => modal.classList.add('hidden') });
  try { window.dispatchEvent(new CustomEvent('odysseus:modal-opened', { detail: { id: 'workbench-diff-modal' } })); } catch (_) {}
}

// ── chat run cards ────────────────────────────────────────────────────────
function chatHistory() { return $('chat-history'); }
function updateChatCard(ev) {
  if (!ev.run_id || !CHAT_CARD_SOURCES.has(ev.source)) return;
  if (ev.session_id !== state.sessionId) return;
  const run = state.runs.get(ev.run_id); if (!run) return;
  const hist = chatHistory(); if (!hist) return;
  let card = state.chatCards.get(ev.run_id);
  if (!card) {
    card = document.createElement('div');
    card.className = 'agent-run-card';
    card.dataset.run = ev.run_id;
    card.innerHTML = '<div class="agent-run-head" role="button" tabindex="0" aria-expanded="false"></div><div class="agent-run-steps"></div><div class="agent-run-foot"></div>';
    state.chatCards.set(ev.run_id, card);
    // Attach inside the assistant turn that is streaming (so the card sits with
    // the conversation), else at the end of the history.
    const live = hist.querySelector('.message.assistant:last-of-type, .msg.assistant:last-of-type') || hist.lastElementChild;
    if (live && live.classList && (live.classList.contains('assistant') || live.querySelector('.agent-thread'))) live.appendChild(card); else hist.appendChild(card);
    card.addEventListener('click', (e) => {
      const b = e.target.closest('[data-wb-act]');
      if (!b) {
        if (e.target.closest('.agent-run-head')) {
          card.classList.toggle('open');
          card.querySelector('.agent-run-head').setAttribute('aria-expanded', String(card.classList.contains('open')));
        }
        return;
      }
      e.stopPropagation();
      const act = b.dataset.wbAct;
      if (act === 'open-wb') { open(); state.focusRun = ev.run_id; setTab('activity'); }
      if (act === 'open-changes' && run.data.task_id) { open(); loadTask(run.data.task_id); }
    });
    const near = hist.scrollHeight - hist.scrollTop - hist.clientHeight < 160;
    if (near) hist.scrollTop = hist.scrollHeight;
  }
  const head = card.querySelector('.agent-run-head');
  const steps = card.querySelector('.agent-run-steps');
  const foot = card.querySelector('.agent-run-foot');
  const running = run.status === 'running';
  card.classList.toggle('running', running);
  card.classList.toggle('failed', !running && statusClass(run.status) === 'bad');
  head.innerHTML = `${statusPill(run.status)}${sourceChip(run.source)}<span class="agent-run-title" title="${esc(run.title || '')}">${esc(run.title || '')}</span><span class="agent-run-count">${run.tools ? plural(run.tools, 'step') : ''}</span><span class="agent-run-caret" aria-hidden="true"></span>`;
  if (['message', 'tool_start', 'tool_result', 'file_change', 'commit', 'status', 'error'].includes(ev.kind)) {
    const row = document.createElement('div');
    row.className = 'agent-run-step' + (ev.kind === 'error' || ev.level === 'error' ? ' err' : '');
    row.innerHTML = `<span class="wb-ev-kind">${KIND_ICON[ev.kind] || '·'}</span><span class="agent-run-step-text">${esc(ev.title || '')}</span>${ev.detail ? disclosureHtml(`${ev.seq}:more`, 'More', ev.detail) : ''}`;
    steps.appendChild(row);
    while (steps.children.length > 60) steps.removeChild(steps.firstChild);
    if (running) steps.scrollTop = steps.scrollHeight;
  }
  if (!running) {
    const d = run.data || {};
    const bits = [];
    if (d.changes_count) bits.push(`${plural(d.changes_count, 'file')} changed`);
    if (d.commit_count) bits.push(plural(d.commit_count, 'commit'));
    const excerpt = d.error || d.result_excerpt;
    foot.innerHTML = `${excerpt ? `<div class="agent-run-excerpt${d.error ? ' wb-text-bad' : ''}">${esc(String(excerpt).slice(0, 240))}</div>` : ''}<div class="agent-run-actions"><span class="wb-row-meta">${bits.join(' · ')}</span><span class="wb-spacer"></span>${d.task_id ? '<button type="button" class="wb-btn wb-btn-sm" data-wb-act="open-changes">View changes</button>' : ''}<button type="button" class="wb-btn wb-btn-sm" data-wb-act="open-wb">Open in Workbench</button></div>`;
  } else if (!foot.innerHTML) {
    foot.innerHTML = '<div class="agent-run-actions"><span class="wb-spacer"></span><button type="button" class="wb-btn wb-btn-sm" data-wb-act="open-wb">Open in Workbench</button></div>';
  }
}

// ── window plumbing ───────────────────────────────────────────────────────
function hideRail() {
  for (const id of ['rail-workbench', 'tool-workbench-btn']) { const b = $(id); if (b) b.style.display = 'none'; }
}
function setTab(tab) {
  state.prefs.tab = tab; savePrefs();
  document.querySelectorAll('#workbench-modal [data-wb-tab]').forEach((b) => {
    const on = b.dataset.wbTab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  document.querySelectorAll('#workbench-modal [data-wb-panel]').forEach((p) => p.classList.toggle('hidden', p.dataset.wbPanel !== tab));
  if (tab === 'activity') renderActivity();
  if (tab === 'changes') { renderChanges(); autoSelectFirstFile(); }
  if (tab === 'commits') renderCommits();
  if (tab === 'prs') { if (!state.pr.config) loadPRConfig(); else renderPRs(); }
}
// The Workbench is a tool window like Lotus or Settings: `_` minimizes it to a
// dock chip (freeing the chat, including a right-docked layout), and the
// sidebar item / rail button / chip restore it.
const MODAL_ID = 'workbench-modal';
function registerWithManager() {
  if (Modals.isRegistered(MODAL_ID)) return;
  Modals.register(MODAL_ID, {
    railBtnId: 'rail-workbench', sidebarBtnId: 'tool-workbench-btn',
    restoreFn: () => open(), closeFn: hideWindow,
  });
}
function hideWindow() { $(MODAL_ID)?.classList.add('hidden'); }
export function open() {
  const modal = $(MODAL_ID); if (!modal) return;
  if (Modals.isMinimized(MODAL_ID)) { Modals.restore(MODAL_ID); return; }  // restoreFn re-enters open()
  registerWithManager();
  if (modal.classList.contains('hidden')) {
    modal.classList.remove('hidden');
    try { window.dispatchEvent(new CustomEvent('odysseus:modal-opened', { detail: { id: 'workbench-modal' } })); } catch (_) {}
  }
  connect();
  loadRoots();
  setTab(state.prefs.tab || 'activity');
}
export function close() {
  // The manager's close also releases a right-dock push and drops the chip;
  // hiding the element alone left the chat squeezed.
  if (Modals.isRegistered(MODAL_ID)) Modals.close(MODAL_ID);
  else hideWindow();
}
export function toggle() {
  if (Modals.toggle('workbench-modal')) return;
  if (isOpen()) close(); else open();
}

function wireWindow() {
  const modal = $('workbench-modal'); if (!modal) return;
  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  makeWindowDraggable(modal, {
    content, header, skipSelector: 'button, input, select, label, textarea', enableDock: true, enableLeftDock: true,
    resizeStorageKey: 'odysseus-workbench-size',
    onEnterFullscreen: () => snapModalToZone(modal, { name: 'fullscreen', rect: { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight } }),
  });
  registerWithManager();
  // Inject through this module's manager instance (the one holding the
  // registration) so the `_` minimizes into the shared dock.
  Modals.injectMinimizeButton(modal, MODAL_ID);
  $('close-workbench-modal')?.addEventListener('click', close);
  $('wb-dock-right')?.addEventListener('click', () => { try { applyRightDock(modal); } catch (_) {} });
  for (const id of ['rail-workbench', 'tool-workbench-btn']) $(id)?.addEventListener('click', toggle);
  $('close-workbench-diff-modal')?.addEventListener('click', () => $('workbench-diff-modal')?.classList.add('hidden'));
  $('agent-strip')?.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-strip-act]');
    if (b) onStripAction(b);
  });
  const dm = $('workbench-diff-modal');
  if (dm) makeWindowDraggable(dm, { content: dm.querySelector('.modal-content'), header: dm.querySelector('.modal-header'), resizeStorageKey: 'odysseus-workbench-diff-size' });

  modal.querySelectorAll('[data-wb-tab]').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.wbTab)));
  $('wb-scope')?.addEventListener('change', (e) => { state.prefs.scope = e.target.value; savePrefs(); state.focusRun = null; connect(true); });
  $('wb-filter')?.addEventListener('change', (e) => { state.prefs.filter = e.target.value; savePrefs(); state.focusRun = null; renderActivity(); });
  $('wb-pause')?.addEventListener('click', (e) => {
    state.paused = !state.paused;
    e.currentTarget.textContent = state.paused ? 'Resume' : 'Pause';
    e.currentTarget.classList.toggle('wb-btn-primary', state.paused);
    setLive(state.es ? 'live' : 'off');
    renderActivity();
  });
  $('wb-clear')?.addEventListener('click', () => { state.events = []; state.runs.clear(); state.focusRun = null; renderActivity(); });
  const scope = $('wb-scope'); if (scope) scope.value = state.prefs.scope;
  const filt = $('wb-filter'); if (filt) filt.value = state.prefs.filter;

  // Controls rendered into the view toolbars are delegated.
  modal.addEventListener('change', (e) => {
    const el = e.target.closest('[data-wb-change]');
    if (el && el.dataset.wbChange === 'repo' && el.value) setRepo(el.value);
  });
  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.id === 'wb-base') { e.preventDefault(); state.selectedFile = null; refreshChanges(); refreshCommits(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.wb-row[role="button"], .wb-run[role="button"]')) { e.preventDefault(); e.target.click(); }
  });

  // One delegated click handler for every action inside the window.
  modal.addEventListener('click', (e) => {
    if (e.target.closest('details.wb-disclosure > summary')) return;
    const lineNo = e.target.closest('td.wb-no');
    const lineRow = lineNo && lineNo.closest('tr.wb-l[data-path]');
    if (lineRow && (lineRow.dataset.new || lineRow.dataset.old)) { onLineClick(lineRow); return; }
    const b = e.target.closest('button[data-wb-act]');
    if (b) { onAction(b); return; }
    if (e.target.closest('#wb-commitpane .wb-file[data-path]')) { selectCommitFile(e.target.closest('.wb-file').dataset.path); return; }
    const file = e.target.closest('.wb-file[data-path]');
    if (file) { selectFile(file.dataset.path); return; }
    const prfile = e.target.closest('.wb-file[data-prfile]');
    if (prfile) { selectPRFile(prfile.dataset.prfile); return; }
    const commit = e.target.closest('.wb-commit[data-sha]');
    if (commit) { selectCommit(commit.dataset.sha); return; }
    const pr = e.target.closest('.wb-pr[data-number]');
    if (pr) { selectPR(parseInt(pr.dataset.number, 10)); return; }
    const run = e.target.closest('.wb-run[data-run]');
    if (run) { state.focusRun = state.focusRun === run.dataset.run ? null : run.dataset.run; renderActivity(); }
  });
}

function onAction(b) {
  switch (b.dataset.wbAct) {
    case 'mode': state.prefs.mode = b.dataset.mode; savePrefs(); renderChanges(); renderCommits(); renderPRs(); break;
    case 'refresh-changes': refreshChanges(); break;
    case 'refresh-commits': refreshCommits(); break;
    case 'refresh-prs': refreshPRs(); break;
    case 'recheck-prs': loadPRConfig(); break;
    case 'pr-state': state.pr.stateFilter = b.dataset.state; state.pr.list = []; refreshPRs(); break;
    case 'leave-run': { const path = state.repoCtx.path || state.prefs.repo; state.repoCtx = { path: '', base: '', taskId: null, label: '' }; if (path) setRepo(path); else { renderChanges(); renderCommits(); } break; }
    case 'run-changes': loadTask(b.dataset.task, { tab: 'changes' }); break;
    case 'run-transcript': showTranscript(b.dataset.task); break;
    case 'unfocus': state.focusRun = null; renderActivity(); break;
    case 'popout': popout(state.selectedFile || 'Diff', renderDiffText(state.diffText || '', { mode: state.prefs.mode, path: state.selectedFile })); break;
    case 'popout-commit': { const k = state.selectedCommit; if (k && k.selectedFile) popout(`${(k.sha || '').slice(0, 7)} · ${k.selectedFile}`, renderDiffText(k.diffText || '', { mode: state.prefs.mode, path: k.selectedFile })); break; }
    case 'popout-pr': { const d = state.pr.detail; if (d && d.selectedFile) { const f = parseUnifiedDiff(d.diffText || '').find((x) => x.newPath === d.selectedFile || x.oldPath === d.selectedFile); popout(`PR #${state.pr.selected} · ${d.selectedFile}`, f ? renderFileTable(f, { mode: state.prefs.mode, path: d.selectedFile }) : ''); } break; }
    case 'send-file': sendFileToAgent(); break;
    case 'pr-comment': postPRComment(); break;
    case 'pr-review': postPRReview(b.dataset.event); break;
    case 'pr-to-agent': prToAgent(); break;
    case 'drop-pending': { const d = state.pr.detail; if (d) { d.pendingComments.splice(parseInt(b.dataset.i, 10), 1); renderPRs(); } break; }
    default: break;
  }
}

function onLineClick(row) {
  const path = row.dataset.path || state.selectedFile || '';
  const line = row.dataset.new || row.dataset.old;
  const code = Array.from(row.querySelectorAll('.wb-code')).map((c) => c.textContent.trim()).filter(Boolean).pop() || '';
  if (state.prefs.tab === 'prs' && state.pr.detail && state.pr.detail.selectedFile) {
    const text = window.prompt(`Review comment for ${path}:${line}`, '');
    if (!text) return;
    state.pr.detail.pendingComments.push({ path, line: parseInt(line, 10), body: text });
    renderPRs();
    return;
  }
  fillComposer(`In \`${path}\` line ${line}:\n\`\`\`\n${code}\n\`\`\`\n`);
  showToast('Line reference added to the composer', 'success');
}
function sendFileToAgent() {
  if (!state.selectedFile) return;
  const st = diffStats(state.diffText || '');
  const c = state.repoCtx;
  fillComposer(`Review the change to \`${state.selectedFile}\`${c.path ? ` in ${c.path}` : ''} (+${st.additions}/−${st.deletions}):\n\`\`\`diff\n${String(state.diffText || '').slice(0, 6000)}\n\`\`\``);
  showToast('Diff added to the composer', 'success');
}
async function showTranscript(taskId) {
  try {
    const r = await api(`/api/claude-code/tasks/${encodeURIComponent(taskId)}/changes`);
    const rows = (r.transcript || []).map((t, i) => {
      const err = t.is_error || t.type === 'error';
      const label = t.kind === 'tool_start' ? `${t.tool || 'tool'} ${t.summary || ''}` : t.kind === 'tool_result' ? `${t.tool || 'tool'} ${err ? 'failed' : 'done'}` : (t.title || t.text || t.summary || JSON.stringify(t).slice(0, 300));
      const body = t.excerpt || t.detail || '';
      return `<div class="wb-ev no-src${err ? ' err' : ''}"><span class="wb-ev-time">${esc(t.ts ? fmtWhen(t.ts) : '')}</span><span class="wb-ev-kind">${KIND_ICON[t.kind] || '·'}</span><div class="wb-ev-body"><span class="wb-ev-title">${esc(label)}</span>${body ? disclosureHtml(`t${i}`, 'Output', body) : ''}</div></div>`;
    }).join('');
    popout(`Transcript · ${r.label || taskId}`, `<div class="wb-transcript">${rows || emptyHtml('No transcript recorded (stream-json unsupported or disabled).')}${r.transcript_truncated ? '<div class="wb-hint">Transcript truncated.</div>' : ''}${r.result ? `<div class="wb-group-h"><span class="wb-group-title">Result</span></div><pre class="wb-prose">${esc(r.result)}</pre>` : ''}</div>`);
  } catch (e) { showToast(e.message, 'error'); }
}

async function probeSettings() {
  try {
    const r = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    if (r.status === 403 || r.status === 401) { state.enabled = false; hideRail(); return; }
    const s = await r.json();
    state.enabled = s.workbench_enabled !== false;
    state.autoOpen = s.workbench_auto_open !== false;
    if (!state.enabled) { hideRail(); disconnect(); renderAgentStrip(); }
  } catch (_) {}
}

export async function init() {
  if (state.inited) return;
  state.inited = true;
  loadPrefs();
  wireWindow();
  await probeSettings();
  if (!state.enabled) return;
  watchSession();
  // Auto-open on the first sub-process run of the session (Claude Code, a
  // sub-agent…), so the user sees the work as it happens.
  // A minimized Workbench stays minimized: the user put it away on purpose.
  document.addEventListener('workbench:run-started', () => { if (state.autoOpen && !isOpen() && !Modals.isMinimized(MODAL_ID)) open(); });
}

export function refreshSettings() { return probeSettings(); }
/** Open one run from the Agents dashboard. Align the Workbench stream with
 * the selected chat before focusing so its history is available immediately,
 * rather than waiting for the session watcher to notice the navigation. */
export async function openRun(runId, sessionId) {
  if (!state.enabled) throw new Error('Workbench is disabled');
  open();
  if (sessionId && sessionId !== state.sessionId) {
    state.sessionId = sessionId;
    state.prefs.scope = 'session';
    savePrefs();
    await connect(true);
  }
  state.focusRun = runId || null;
  setTab('activity');
}
export const _state = state;
export default { init, open, close, toggle, refreshSettings, openRun };
