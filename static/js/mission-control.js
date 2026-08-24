import uiModule from './ui.js';
import { setWorkspace } from './workspace.js';

const API_BASE = window.location.origin;
const TABS = [
  ['review', 'Review', 'Outcome, evidence, changes and recovery'],
  ['missions', 'Agent missions', 'Isolated implementation, verification and triage'],
  ['runtime', 'Runtime', 'Models, services and hardware'],
  ['context', 'Context', 'Token budget and conversation weight'],
  ['project', 'Project rules', 'Instructions, permissions and QA'],
  ['delivery', 'Delivery', 'Tests, visual QA and GitHub handoff'],
];

let shell = null;
let activeTab = 'review';
let loading = false;
let state = { review: null, missions: null, runtime: null, context: null, project: null, delivery: null };

const esc = (value) => uiModule.esc(String(value ?? ''));
const currentWorkspace = () => window.workspaceModule?.getWorkspace?.() || '';
const currentSession = () => window.sessionModule?.getCurrentSessionId?.() || '';
const endpoint = (path, extras = {}) => {
  const params = new URLSearchParams({ ...extras });
  const workspace = currentWorkspace();
  if (workspace) params.set('path', workspace);
  return `${API_BASE}${path}${params.size ? `?${params}` : ''}`;
};

function formatBytes(value) {
  let n = Number(value || 0);
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
  return `${n >= 10 || index === 0 ? Math.round(n) : n.toFixed(1)} ${units[index]}`;
}

function formatTokens(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`;
  return n.toLocaleString();
}

function relative(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  return data;
}

function addToChat(text) {
  const input = document.getElementById('message');
  if (!input) return;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  close();
  uiModule.showToast?.('Added follow-up to chat');
}

function icon(name) {
  const paths = {
    review: '<path d="M4 4h16v16H4z"/><path d="m8 12 2.5 2.5L16 9"/>',
    missions: '<path d="M5 4h10l4 4v12H5z"/><path d="M15 4v5h5M8 14h8M8 18h5"/>',
    runtime: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 9h3M7 13h6M17 9h.01"/>',
    context: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    project: '<path d="M3 7h7l2 2h9v10H3z"/><path d="M7 4h5l2 3"/>',
    delivery: '<path d="M4 17V7l8-4 8 4v10l-8 4z"/><path d="m8 12 2.5 2.5L16 9"/>',
    refresh: '<path d="M20 6v5h-5M4 18v-5h5"/><path d="M18 9a7 7 0 0 0-12-2L4 10M6 15a7 7 0 0 0 12 2l2-3"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    checkpoint: '<path d="M5 4h14v16H5z"/><path d="M8 4v6h8V4M8 20v-6h8v6"/>',
    branch: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="7" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 9c5 0 8 0 8-2"/>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.review}</svg>`;
}

function createShell() {
  if (shell) return shell;
  shell = document.createElement('div');
  shell.className = 'mission-control-shell';
  shell.setAttribute('aria-hidden', 'true');
  shell.innerHTML = `<div class="mission-control-backdrop" data-mc-close></div>
    <section class="mission-control" role="dialog" aria-modal="true" aria-label="Mission Control">
      <header class="mission-control-header">
        <div class="mission-control-mark">${icon('review')}</div>
        <div class="mission-control-heading"><strong>Mission Control</strong><span id="mission-control-workspace">Local agent operations</span></div>
        <button type="button" class="mission-icon-btn" data-mc-refresh title="Refresh">${icon('refresh')}</button>
        <button type="button" class="mission-icon-btn" data-mc-close title="Close">${icon('close')}</button>
      </header>
      <div class="mission-control-layout">
        <nav class="mission-control-nav" aria-label="Mission Control sections">
          ${TABS.map(([id, label, description]) => `<button type="button" data-mc-tab="${id}" aria-label="${label}" title="${label}">${icon(id)}<span><strong>${label}</strong><small>${description}</small></span></button>`).join('')}
        </nav>
        <main class="mission-control-main" id="mission-control-main"></main>
      </div>
    </section>`;
  document.body.appendChild(shell);
  shell.addEventListener('click', handleClick);
  return shell;
}

function skeleton() {
  return `<div class="mission-skeleton-grid">${Array.from({ length: 6 }, () => '<span></span>').join('')}</div>`;
}

function renderStatusPill(status) {
  const normalized = ['healthy', 'success', 'ready'].includes(status) ? 'healthy' : ['running', 'queued'].includes(status) ? 'running' : status === 'offline' || status === 'error' || status === 'failed' ? 'offline' : 'degraded';
  return `<span class="mission-status ${normalized}"><i></i>${esc(status || 'unknown')}</span>`;
}

function renderReview() {
  const data = state.review;
  if (!data) return skeleton();
  const git = data.git || {};
  const latest = (data.runs || [])[0];
  const outcome = latest
    ? `<section class="mission-outcome ${esc(latest.status)}"><div><span class="mission-eyebrow">Latest agent result · ${esc(relative(latest.finished_at || latest.started_at))}</span><h2>${esc(latest.task_name || 'Agent task')}</h2><p>${esc((latest.error || latest.result || 'No result summary was recorded.').slice(0, 900))}</p></div>${renderStatusPill(latest.status)}</section>`
    : `<section class="mission-outcome"><div><span class="mission-eyebrow">Workspace review</span><h2>${git.changed_files ? 'Changes ready to inspect' : 'Working tree is clean'}</h2><p>${git.head ? `${esc(git.head.short_sha)} · ${esc(git.head.subject)}` : 'No scheduled-agent result is associated with this chat yet.'}</p></div>${renderStatusPill(git.changed_files ? 'ready' : 'healthy')}</section>`;

  const statsByPath = new Map((git.file_stats || []).map(item => [item.path, item]));
  const files = (git.files || []).map(file => {
    const stats = statsByPath.get(file.path) || {};
    return `<div class="mission-file-row"><span class="mission-file-status">${esc(file.status)}</span><span class="mission-file-path" title="${esc(file.path)}">${esc(file.path)}</span><span class="mission-file-stats"><b>+${Number(stats.additions || 0)}</b><i>−${Number(stats.deletions || 0)}</i></span></div>`;
  }).join('');
  const checkpoints = (data.checkpoints || []).map(item => `<div class="mission-checkpoint-row"><span>${icon('checkpoint')}</span><div><strong>${esc(item.id.replace(/^\d{8}-\d{6}-/, '').replaceAll('-', ' '))}</strong><small>${esc(item.short_sha)} · ${esc(relative(item.created_at))}</small></div><button type="button" data-mc-restore="${esc(item.id)}">Restore</button></div>`).join('');
  const worktrees = (data.worktrees || []).map(item => `<div class="mission-worktree"><span>${icon('branch')}</span><div><strong>${esc(item.branch || 'Detached')}</strong><small title="${esc(item.path)}">${esc(item.path)}</small></div></div>`).join('');
  const risks = (data.risks || []).map(risk => `<li>${esc(risk)}</li>`).join('');
  const runs = (data.runs || []).map(run => `<div class="mission-run-row"><span class="mission-run-dot ${esc(run.status)}"></span><div><strong>${esc(run.task_name || 'Background task')}</strong><small>${esc(run.model || 'Local agent')} · ${esc(relative(run.finished_at || run.started_at))}</small></div>${renderStatusPill(run.status)}</div>`).join('');

  return `${outcome}
    <div class="mission-metric-grid">
      <div><small>Branch</small><strong>${esc(git.branch || '—')}</strong><span>${git.ahead || git.behind ? `${git.ahead || 0} ahead · ${git.behind || 0} behind` : 'In sync'}</span></div>
      <div><small>Changed files</small><strong>${Number(git.changed_files || 0)}</strong><span>${Number(git.additions || 0).toLocaleString()} additions</span></div>
      <div><small>Deleted lines</small><strong>${Number(git.deletions || 0).toLocaleString()}</strong><span>${git.upstream ? esc(git.upstream) : 'No upstream'}</span></div>
      <div><small>Recovery points</small><strong>${(data.checkpoints || []).length}</strong><span>${(data.worktrees || []).length} worktree(s)</span></div>
    </div>
    <div class="mission-review-actions">
      <button type="button" class="mission-primary" data-mc-followup="Review the current changes, run the project tests, report any remaining risks, then commit when everything is clean.">Accept &amp; commit</button>
      <button type="button" data-mc-followup="Review the current implementation critically. Fix any bugs, missing edge cases, or visual inconsistencies, then show me the updated evidence.">Send back</button>
      <button type="button" data-mc-checkpoint>${icon('checkpoint')} Create checkpoint</button>
      <button type="button" data-mc-worktree>${icon('branch')} New isolated task</button>
    </div>
    ${risks ? `<section class="mission-card mission-risks"><header><strong>Needs attention</strong><span>${data.risks.length}</span></header><ul>${risks}</ul></section>` : ''}
    <div class="mission-two-column">
      <section class="mission-card"><header><strong>Changed files</strong><span>${Number(git.changed_files || 0)}</span></header><div class="mission-file-list">${files || '<div class="mission-empty">No uncommitted files</div>'}</div></section>
      <section class="mission-card"><header><strong>Checkpoints</strong><span>Tracked files</span></header><div class="mission-checkpoint-list">${checkpoints || '<div class="mission-empty">Create a recovery point before risky changes.</div>'}</div></section>
    </div>
    <section class="mission-card"><header><strong>Background agent queue</strong><span>${(data.runs || []).length} recent</span></header><div class="mission-run-list">${runs || '<div class="mission-empty">No recent background agent runs for this task.</div>'}</div></section>
    <section class="mission-card"><header><strong>Agent worktrees</strong><span>Isolated branches</span></header><div class="mission-worktree-list">${worktrees || '<div class="mission-empty">No isolated worktrees yet.</div>'}</div></section>`;
}

function renderMissions() {
  const project = state.missions?.project || state.project || {};
  const review = state.missions?.review || state.review || {};
  const hooks = project.completion_hooks || [];
  const worktrees = (review.worktrees || []).filter(tree => String(tree.branch || '').startsWith('odysseus/'));
  const missions = [
    ['Isolated implementer', 'Create a dedicated Git worktree, switch to it, and begin a fresh scoped chat.', 'data-mc-worktree'],
    ['Independent verifier', 'Audit the current implementation without making speculative changes; run the deterministic checks and report evidence.', 'data-mc-mission="verifier"'],
    ['Visual QA', project.visual_qa_url ? `Inspect ${project.visual_qa_url} at desktop and mobile sizes; record concrete defects only.` : 'Set a Visual QA URL in Project rules, then inspect desktop and mobile rendering.', 'data-mc-mission="visual"'],
    ['Issue triage', 'Triage failures into reproducible issues, suspected ownership, impact, and a smallest safe next action.', 'data-mc-mission="triage"'],
  ];
  const cards = missions.map(([title, detail, action]) => `<article class="mission-agent-card"><span class="mission-agent-orb">${title.slice(0, 1)}</span><div><strong>${esc(title)}</strong><p>${esc(detail)}</p></div><button type="button" ${action}>Launch</button></article>`).join('');
  const hookList = hooks.length ? hooks.map(hook => `<code>${esc(hook)}</code>`).join('') : '<span>No deterministic hooks configured yet.</span>';
  const trees = worktrees.map(tree => `<div class="mission-worktree mission-worktree-action"><span>${icon('branch')}</span><div><strong>${esc(tree.branch || 'Detached')}</strong><small title="${esc(tree.path)}">${esc(tree.path)}</small></div><div class="mission-worktree-controls"><button type="button" data-mc-use-worktree="${esc(tree.path)}">Open task</button><button type="button" data-mc-merge-worktree="${esc(tree.branch)}">Merge</button><button type="button" data-mc-discard-worktree="${esc(tree.branch)}">Discard</button></div></div>`).join('');
  return `<div class="mission-section-heading"><div><span class="mission-eyebrow">Agent operating system</span><h2>Mission desk</h2><p>Give each significant task a clear role, isolated workspace, and evidence-based finish line.</p></div></div>
    <section class="mission-agent-grid">${cards}</section>
    <section class="mission-card mission-hooks"><header><strong>Deterministic completion hooks</strong><span>${hooks.length} configured</span></header><div>${hookList}</div><button type="button" data-mc-tab="project">Configure hooks</button></section>
    <section class="mission-card"><header><strong>Isolated worktrees</strong><span>${worktrees.length} active</span></header><div class="mission-worktree-list">${trees || '<div class="mission-empty">Create an isolated implementer mission when work should not touch your current branch.</div>'}</div></section>`;
}
function renderRuntime() {
  const data = state.runtime;
  if (!data) return skeleton();
  const services = (data.services || []).map(service => `<article class="mission-service"><div class="mission-service-top"><span class="mission-service-dot ${esc(service.status)}"></span><div><strong>${esc(service.name)}</strong><small>${esc(service.detail)}</small></div>${renderStatusPill(service.status)}</div><footer>${service.latency_ms != null ? `${Number(service.latency_ms)} ms` : ''}${service.url ? `<span title="${esc(service.url)}">${esc(service.url)}</span>` : ''}</footer></article>`).join('');
  const models = (data.ollama?.loaded_models || []).map(model => `<div class="mission-model-row"><div><strong>${esc(model.name)}</strong><small>${esc(model.quantization || 'Local model')}</small></div><span>${formatTokens(model.context_length)} ctx</span><span>${formatBytes(model.size_vram_bytes || model.size_bytes)}</span></div>`).join('');
  const gpus = (data.gpus || []).map(gpu => {
    const pct = gpu.memory_total_mb ? Math.round((gpu.memory_used_mb / gpu.memory_total_mb) * 100) : 0;
    return `<article class="mission-gpu"><header><div><strong>${esc(gpu.name)}</strong><small>GPU ${gpu.index} · ${gpu.temperature_c}°C</small></div><b>${gpu.utilization_percent}%</b></header><div class="mission-progress"><i style="width:${Math.min(100, pct)}%"></i></div><footer><span>${formatBytes(gpu.memory_used_mb * 1024 * 1024)} VRAM used</span><span>${formatBytes(gpu.memory_total_mb * 1024 * 1024)} total</span></footer></article>`;
  }).join('');
  const diskPct = data.disk?.total_bytes ? Math.round((data.disk.used_bytes / data.disk.total_bytes) * 100) : 0;
  return `<div class="mission-section-heading"><div><span class="mission-eyebrow">Live local stack</span><h2>Runtime health</h2><p>Checked ${esc(relative(data.checked_at))}. Degraded optional services do not block coding.</p></div><button type="button" data-mc-refresh>${icon('refresh')} Refresh</button></div>
    <div class="mission-service-grid">${services}</div>
    <div class="mission-two-column runtime-columns">
      <section class="mission-card"><header><strong>Loaded Ollama models</strong><span>v${esc(data.ollama?.version || '—')}</span></header><div class="mission-model-list">${models || '<div class="mission-empty">No model is currently loaded.</div>'}</div></section>
      <section class="mission-card"><header><strong>Hardware</strong><span>${diskPct}% disk used</span></header><div class="mission-gpu-list">${gpus || '<div class="mission-empty">No NVIDIA telemetry detected.</div>'}</div><div class="mission-disk"><div class="mission-progress"><i style="width:${Math.min(100, diskPct)}%"></i></div><span>${formatBytes(data.disk?.free_bytes)} free of ${formatBytes(data.disk?.total_bytes)}</span></div></section>
    </div>`;
}

function renderContext() {
  const data = state.context;
  if (!data) return skeleton();
  const pct = Math.min(100, Number(data.context_percent || 0));
  const breakdown = Object.entries(data.breakdown || {});
  const total = breakdown.reduce((sum, [, value]) => sum + Number(value || 0), 0) || 1;
  const rows = breakdown.map(([role, value]) => `<div class="mission-context-row"><span>${esc(role)}</span><div><i style="width:${Math.max(2, Math.round((Number(value) / total) * 100))}%"></i></div><strong>${formatTokens(value)}</strong></div>`).join('');
  return `<div class="mission-section-heading"><div><span class="mission-eyebrow">Active conversation</span><h2>Context budget</h2><p>${esc(data.model || 'Select a chat to inspect its model context.')}</p></div></div>
    <section class="mission-context-hero">
      <div class="mission-context-ring" style="--context-pct:${pct * 3.6}deg"><div><strong>${pct.toFixed(pct < 10 ? 1 : 0)}%</strong><span>used</span></div></div>
      <div class="mission-context-copy"><strong>${formatTokens(data.used_tokens)} of ${formatTokens(data.context_length)} tokens</strong><p>${data.context_length ? `${formatTokens(Math.max(0, data.context_length - data.used_tokens))} tokens remain before compaction or trimming.` : 'Context length is unavailable for this chat.'}</p><span>${Number(data.message_count || 0)} messages currently contribute to the request.</span></div>
    </section>
    <section class="mission-card"><header><strong>Estimated context composition</strong><span>By message role</span></header><div class="mission-context-breakdown">${rows || '<div class="mission-empty">No active conversation context.</div>'}</div></section>
    <section class="mission-context-advice"><strong>${pct >= 85 ? 'Compaction recommended' : pct >= 65 ? 'Context is getting dense' : 'Healthy working range'}</strong><p>${pct >= 85 ? 'Start a fresh task or compact this chat before the model begins losing important early details.' : pct >= 65 ? 'Pin only essential files and avoid pasting large tool outputs into the conversation.' : 'There is comfortable room for tools, code, and a long implementation pass.'}</p></section>`;
}

function renderProject() {
  const data = state.project;
  if (!data) return skeleton();
  return `<div class="mission-section-heading"><div><span class="mission-eyebrow">${esc(data.workspace || 'Workspace')}</span><h2>Project operating rules</h2><p>These settings are stored locally in <code>.odysseus/project.json</code>.</p></div><button type="button" class="mission-primary" data-mc-project-save>Save rules</button></div>
    <form class="mission-project-form" id="mission-project-form">
      <label class="mission-field mission-field-wide"><span>Agent instructions</span><small>Architecture, conventions, required checks, and things the agent should know.</small><textarea name="instructions" rows="8" placeholder="Example: Use PowerShell on Windows. Run focused tests before committing…">${esc(data.instructions)}</textarea></label>
      <label class="mission-field"><span>Test command</span><small>The canonical verification command.</small><input name="test_command" value="${esc(data.test_command)}" placeholder="npm test or pytest -q" /></label>
      <label class="mission-field"><span>GitHub base branch</span><small>Default target for comparisons and PRs.</small><input name="github_base_branch" value="${esc(data.github_base_branch || 'main')}" /></label>
      <label class="mission-field"><span>Protected paths</span><small>One path or glob per line.</small><textarea name="protected_paths" rows="5" placeholder=".env&#10;data/**">${esc((data.protected_paths || []).join('\n'))}</textarea></label>
      <label class="mission-field"><span>Reusable permission rules</span><small>Plain-language rules applied to this project.</small><textarea name="permission_rules" rows="5" placeholder="Always allow tests&#10;Ask before pushing">${esc((data.permission_rules || []).join('\n'))}</textarea></label>
      <label class="mission-field"><span>Visual QA URL</span><small>Page the agent should launch and inspect.</small><input name="visual_qa_url" value="${esc(data.visual_qa_url)}" placeholder="http://127.0.0.1:3000" /></label>
      <label class="mission-field"><span>Completion hooks</span><small>One deterministic check per line. The agent must run and report applicable checks before completion.</small><textarea name="completion_hooks" rows="4" placeholder="pytest -q&#10;npm run lint">${esc((data.completion_hooks || []).join('\n'))}</textarea></label>
      <label class="mission-field"><span>Compact context at</span><small>Recommended automatic threshold.</small><div class="mission-range"><input type="range" name="context_compaction_percent" min="50" max="95" value="${Number(data.context_compaction_percent || 80)}" /><output>${Number(data.context_compaction_percent || 80)}%</output></div></label>
      <label class="mission-check"><input type="checkbox" name="checkpoint_before_changes" ${data.checkpoint_before_changes !== false ? 'checked' : ''} /><span><strong>Create checkpoints automatically</strong><small>Preserve tracked files before large edits and dependency changes.</small></span></label>
    </form>`;
}

function renderDelivery() {
  const review = state.review;
  const project = state.project;
  if (!review || !project) return skeleton();
  const git = review.git || {};
  const origin = git.origin || 'No origin remote configured';
  const base = project.github_base_branch || 'main';
  const test = project.test_command || 'No canonical test command configured';
  const visual = project.visual_qa_url || 'No visual QA URL configured';
  return `<div class="mission-section-heading"><div><span class="mission-eyebrow">Release path</span><h2>Delivery cockpit</h2><p>Turn a completed local task into tested, visually checked, reviewable work.</p></div></div>
    <div class="mission-delivery-grid">
      <article class="mission-delivery-card"><span class="mission-delivery-number">01</span><div><strong>Verify the implementation</strong><p>${esc(test)}</p></div><button type="button" data-mc-followup="Run the project's canonical test command (${esc(test)}), fix any failures, and summarize the final evidence.">Run tests</button></article>
      <article class="mission-delivery-card"><span class="mission-delivery-number">02</span><div><strong>Inspect the rendered result</strong><p>${esc(visual)}</p></div><button type="button" ${project.visual_qa_url ? '' : 'disabled'} data-mc-followup="Open ${esc(project.visual_qa_url || 'the configured local app')} and perform visual QA at desktop and mobile sizes. Fix any functional, layout, or accessibility issues you find.">Visual QA</button></article>
      <article class="mission-delivery-card"><span class="mission-delivery-number">03</span><div><strong>Prepare the GitHub handoff</strong><p title="${esc(origin)}">${esc(origin)} · base ${esc(base)}</p></div><button type="button" data-mc-followup="Review the current diff, run the final checks, create a clear commit, push the current branch, and open a pull request against ${esc(base)}. Stop for any required external approval.">Commit &amp; PR</button></article>
    </div>
    <section class="mission-card mission-delivery-summary"><header><strong>Release readiness</strong><span>${git.changed_files ? `${Number(git.changed_files)} file(s) changed` : 'Clean tree'}</span></header>
      <dl><div><dt>Branch</dt><dd>${esc(git.branch || '—')}</dd></div><div><dt>Upstream</dt><dd>${esc(git.upstream || 'Not configured')}</dd></div><div><dt>Base branch</dt><dd>${esc(base)}</dd></div><div><dt>Recovery</dt><dd>${(review.checkpoints || []).length} checkpoint(s)</dd></div></dl>
    </section>`;
}

function render() {
  if (!shell) return;
  shell.querySelectorAll('[data-mc-tab]').forEach(button => button.classList.toggle('active', button.dataset.mcTab === activeTab));
  const main = shell.querySelector('#mission-control-main');
  if (!main) return;
  main.innerHTML = activeTab === 'review' ? renderReview() : activeTab === 'missions' ? renderMissions() : activeTab === 'runtime' ? renderRuntime() : activeTab === 'context' ? renderContext() : activeTab === 'project' ? renderProject() : renderDelivery();
  const range = main.querySelector('input[name="context_compaction_percent"]');
  range?.addEventListener('input', () => { const output = range.parentElement?.querySelector('output'); if (output) output.textContent = `${range.value}%`; });
}

async function load(tab = activeTab, force = false) {
  if (loading) return;
  loading = true;
  if (force) state[tab] = null;
  render();
  try {
    if (tab === 'review') state.review = await api(endpoint('/api/operations/review', { session_id: currentSession() }));
    else if (tab === 'missions') {
      const [review, project] = await Promise.all([api(endpoint('/api/operations/review', { session_id: currentSession() })), api(endpoint('/api/operations/project'))]);
      state.review = review;
      state.project = project;
      state.missions = { review, project };
    }
    else if (tab === 'runtime') state.runtime = await api(endpoint('/api/operations/runtime'));
    else if (tab === 'context') state.context = await api(`${API_BASE}/api/operations/context?session_id=${encodeURIComponent(currentSession())}`);
    else if (tab === 'project') state.project = await api(endpoint('/api/operations/project'));
    else {
      const [review, project] = await Promise.all([
        api(endpoint('/api/operations/review', { session_id: currentSession() })),
        api(endpoint('/api/operations/project')),
      ]);
      state.review = review;
      state.project = project;
      state.delivery = { loaded: true };
    }
  } catch (error) {
    const main = shell?.querySelector('#mission-control-main');
    if (main) main.innerHTML = `<div class="mission-error"><strong>Mission Control could not load</strong><span>${esc(error.message)}</span><button type="button" data-mc-refresh>Try again</button></div>`;
    return;
  } finally {
    loading = false;
  }
  render();
}

async function createCheckpoint() {
  const label = window.prompt('Checkpoint label', 'Before next change');
  if (label == null) return;
  try {
    const data = await api(endpoint('/api/operations/checkpoints'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label }) });
    uiModule.showToast?.(`Checkpoint ${data.checkpoint.short_sha} created`);
    await load('review', true);
  } catch (error) { uiModule.showError?.(error.message); }
}

async function restoreCheckpoint(id) {
  const confirmation = window.prompt('This replaces tracked files and the index. Type RESTORE to continue.');
  if (confirmation !== 'RESTORE') return;
  try {
    await api(endpoint('/api/operations/checkpoints/restore'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ checkpoint_id: id, confirmation }) });
    uiModule.showToast?.('Checkpoint restored; a safety checkpoint was created first');
    await load('review', true);
  } catch (error) { uiModule.showError?.(error.message); }
}

async function createWorktree() {
  const name = window.prompt('Name this isolated task', 'feature-task');
  if (!name) return;
  try {
    const data = await api(endpoint('/api/operations/worktrees'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, base: 'HEAD' }) });
    uiModule.showToast?.(`Created ${data.worktree.branch}`);
    startWorktreeMission(data.worktree.path, name);
    await load('missions', true);
  } catch (error) { uiModule.showError?.(error.message); }
}

function startWorktreeMission(path, title = 'isolated task') {
  setWorkspace(path);
  document.getElementById('sidebar-new-chat-btn')?.click();
  window.setTimeout(() => {
    addToChat(`You are the isolated implementation agent for “${title}”. Work only in the active worktree: ${path}. First inspect the repository, write a concise plan, implement the scoped task, run the project's completion hooks, and finish with evidence plus any merge risks.`);
  }, 0);
}

function launchMission(kind) {
  const project = state.missions?.project || state.project || {};
  const prompt = {
    verifier: `Act as an independent verifier. Do not assume the implementation is correct. Inspect the current diff and relevant code, run these deterministic checks where applicable: ${(project.completion_hooks || []).join(', ') || project.test_command || 'the project tests'}. Report concrete evidence, failures, and remaining risks. Do not make unrelated changes.`,
    visual: `Act as a visual QA agent. Open ${project.visual_qa_url || 'the configured local application'} and inspect desktop and mobile layouts, interaction states, and basic accessibility. Fix only confirmed defects, then report the pages, viewports, and evidence checked.`,
    triage: 'Act as an issue-triage agent. Reproduce the reported problem if possible, isolate the likely subsystem, estimate impact, list the smallest safe next action, and do not make speculative edits. Return a concise structured triage report.',
  }[kind];
  if (prompt) addToChat(prompt);
}
async function actOnWorktree(action, branch) {
  const verb = action === 'merge' ? 'MERGE' : 'DISCARD';
  const warning = action === 'merge'
    ? `Merge ${branch} into the current workspace branch? Type MERGE to confirm.`
    : `Discard ${branch} and its worktree permanently? Type DISCARD to confirm.`;
  const confirmation = window.prompt(warning);
  if (confirmation !== verb) return;
  try {
    await api(endpoint(`/api/operations/worktrees/${action}`), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ branch, confirmation }) });
    uiModule.showToast?.(action === 'merge' ? `Merged ${branch}` : `Discarded ${branch}`);
    await load('missions', true);
  } catch (error) { uiModule.showError?.(error.message); }
}
async function saveProject() {
  const form = shell?.querySelector('#mission-project-form');
  if (!form) return;
  const field = (name) => form.elements.namedItem(name);
  const lines = (name) => String(field(name)?.value || '').split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const payload = {
    instructions: String(field('instructions')?.value || ''),
    test_command: String(field('test_command')?.value || ''),
    github_base_branch: String(field('github_base_branch')?.value || 'main'),
    protected_paths: lines('protected_paths'),
    permission_rules: lines('permission_rules'),
    completion_hooks: lines('completion_hooks'),
    visual_qa_url: String(field('visual_qa_url')?.value || ''),
    context_compaction_percent: Number(field('context_compaction_percent')?.value || 80),
    checkpoint_before_changes: !!field('checkpoint_before_changes')?.checked,
  };
  try {
    state.project = await api(endpoint('/api/operations/project'), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    uiModule.showToast?.('Project rules saved');
    render();
  } catch (error) { uiModule.showError?.(error.message); }
}

function handleClick(event) {
  const target = event.target.closest('button, [data-mc-close]');
  if (!target) return;
  if (target.hasAttribute('data-mc-close')) close();
  else if (target.dataset.mcTab) { activeTab = target.dataset.mcTab; render(); load(activeTab); }
  else if (target.hasAttribute('data-mc-refresh')) load(activeTab, true);
  else if (target.dataset.mcFollowup) addToChat(target.dataset.mcFollowup);
  else if (target.hasAttribute('data-mc-checkpoint')) createCheckpoint();
  else if (target.dataset.mcRestore) restoreCheckpoint(target.dataset.mcRestore);
  else if (target.hasAttribute('data-mc-worktree')) createWorktree();
  else if (target.dataset.mcUseWorktree) startWorktreeMission(target.dataset.mcUseWorktree, 'existing isolated task');
  else if (target.dataset.mcMergeWorktree) actOnWorktree('merge', target.dataset.mcMergeWorktree);
  else if (target.dataset.mcDiscardWorktree) actOnWorktree('discard', target.dataset.mcDiscardWorktree);
  else if (target.dataset.mcMission) launchMission(target.dataset.mcMission);
  else if (target.hasAttribute('data-mc-project-save')) saveProject();
}

function open(tab = 'review') {
  createShell();
  activeTab = TABS.some(([id]) => id === tab) ? tab : 'review';
  const workspace = currentWorkspace();
  const heading = shell.querySelector('#mission-control-workspace');
  if (heading) heading.textContent = workspace || 'Local agent operations';
  shell.classList.add('visible');
  shell.setAttribute('aria-hidden', 'false');
  document.getElementById('mission-control-toggle')?.setAttribute('aria-expanded', 'true');
  document.body.classList.add('mission-control-open');
  render();
  load(activeTab, true);
  shell.querySelector('[data-mc-close]')?.focus();
}

function close() {
  if (!shell) return;
  shell.classList.remove('visible');
  shell.setAttribute('aria-hidden', 'true');
  document.getElementById('mission-control-toggle')?.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('mission-control-open');
}

function init() {
  document.getElementById('mission-control-toggle')?.addEventListener('click', () => open('review'));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && shell?.classList.contains('visible')) close();
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'm') {
      event.preventDefault();
      open();
    }
  });
  document.addEventListener('odysseus-workspace-change', () => { state = { review: null, missions: null, runtime: null, context: null, project: null, delivery: null }; });
}

const missionControlModule = { init, open, close, refresh: () => load(activeTab, true) };
window.odysseusMissionControl = missionControlModule;

export default missionControlModule;
