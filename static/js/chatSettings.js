/* chatSettings.js — per-chat settings and the status line under the composer.
 *
 * Each chat keeps its own setup (routes/session_routes.py /session/{id}/settings):
 *   · approval mode — whether risky tool calls stop for approval first
 *     (auto / ask_risky / ask_all; enforced server-side, src/tool_approvals.py)
 *   · tools switched off for this chat only (enforced server-side)
 *   · the toggles and workspace it last ran with (recorded by the chat route on
 *     every turn and restored here when the chat is reopened, so a chat no
 *     longer inherits whatever the previous chat left in the browser)
 *
 * The status line shows the chat's model, context use, approval mode,
 * workspace, tool restrictions, always-allowed tools and fork source, and
 * opens the settings panel.
 */

import uiModule from './ui.js';
import workspaceModule from './workspace.js';

const API = '';
const MODE_INFO = {
  auto: { label: 'Auto', desc: 'Tools run without asking.' },
  ask_risky: { label: 'Ask for risky', desc: 'Destructive or outward-facing actions ask first: deleting files, git push, publishing, sending or deleting email, sudo.' },
  ask_all: { label: 'Ask for everything', desc: 'Every tool that can change something asks first, including any shell command.' },
};
// Group tool names for the per-chat tools picker.
const TOOL_GROUPS = [
  ['Shell & code', (t) => ['bash', 'python', 'manage_bg_jobs', 'delegate_to_claude_code', 'manage_agent_worktree'].includes(t)],
  ['Files', (t) => ['read_file', 'write_file', 'edit_file', 'apply_patch', 'grep', 'glob', 'ls', 'get_workspace'].includes(t)],
  ['Web', (t) => /^web_|browser/.test(t)],
  ['Email', (t) => /email|unsubscribe|attachment/.test(t)],
  ['Documents', (t) => /document/.test(t)],
  ['Sub-agents & sessions', (t) => /session|pipeline|chat_with_model|ask_teacher/.test(t)],
  ['Memory & knowledge', (t) => /memory|skill|search_documents|vault|rag/.test(t)],
  ['Notes, calendar & tasks', (t) => /notes|calendar|task|todo|contact|plan/.test(t)],
  ['Models & serving', (t) => /model|serve|download|cookbook|hf_/.test(t)],
];

const state = {
  sessionId: null,
  data: null,        // GET /settings payload
  context: null,     // last odysseus:context-usage detail for this chat
  tools: null,       // cached /api/tools list
  loadSeq: 0,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function basename(p) { const parts = String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/); return parts[parts.length - 1] || p; }

async function api(path, opts = {}) {
  const res = await fetch(API + path, Object.assign({ credentials: 'same-origin' }, opts));
  let body = null;
  try { body = await res.json(); } catch (_) {}
  if (!res.ok) {
    const err = new Error((body && (body.detail || body.error)) || `${res.status}`);
    err.status = res.status;
    throw err;
  }
  return body;
}
function patchSettings(patch) {
  return api(`/api/session/${encodeURIComponent(state.sessionId)}/settings`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
  });
}

// ── restore on chat switch ────────────────────────────────────────────────
async function onSessionSwitch(sessionId) {
  state.sessionId = sessionId || null;
  state.data = null;
  state.context = null;
  closePanel();
  render();
  if (!sessionId) return;
  const seq = ++state.loadSeq;
  try {
    const data = await api(`/api/session/${encodeURIComponent(sessionId)}/settings`);
    if (seq !== state.loadSeq) return;
    state.data = data;
    restore(data.settings || {});
  } catch (_) {
    if (seq !== state.loadSeq) return;
    state.data = null;
  }
  render();
}

function restore(settings) {
  const toggles = settings.toggles;
  if (toggles && window.__odysseusApplyChatToggles) {
    try { window.__odysseusApplyChatToggles(toggles); } catch (_) {}
  }
  // Workspace is agent-only and part of how the chat last ran: restore it
  // (or clear the previous chat's) once the chat has a recorded turn.
  if (toggles && workspaceModule?.setWorkspace) {
    const current = workspaceModule.getWorkspace?.() || '';
    const wanted = settings.workspace || '';
    if (current !== wanted) workspaceModule.setWorkspace(wanted);
  }
}

// ── status line ───────────────────────────────────────────────────────────
function ensureLine() {
  let line = $('chat-status-line');
  if (line) return line;
  const bar = document.querySelector('.chat-input-bar');
  if (!bar || !bar.parentNode) return null;
  line = document.createElement('div');
  line.id = 'chat-status-line';
  line.className = 'chat-status-line';
  line.setAttribute('role', 'toolbar');
  line.setAttribute('aria-label', 'Chat status and settings');
  bar.parentNode.insertBefore(line, bar.nextSibling);
  line.addEventListener('click', onLineClick);
  return line;
}

function currentModelLabel() {
  try {
    const m = window.sessionModule?.getCurrentModel?.() || '';
    return m ? String(m).split('/').pop() : '';
  } catch (_) { return ''; }
}

function render() {
  const line = ensureLine();
  if (!line) return;
  if (!state.sessionId) { line.hidden = true; line.innerHTML = ''; return; }
  line.hidden = false;
  const d = state.data || {};
  const s = d.settings || {};
  const mode = d.approval_mode || 'auto';
  const items = [];
  const model = currentModelLabel();
  if (model) items.push(`<span class="csl-item csl-model" title="Model for this chat">${esc(model)}</span>`);
  const ctx = state.context;
  if (ctx && ctx.context_length) {
    const pct = Math.round(Number(ctx.context_percent || 0));
    const tone = pct >= 85 ? ' danger' : pct >= 70 ? ' warn' : '';
    items.push(`<button type="button" class="csl-item csl-ctx${tone}" data-csl="context" title="${esc(`${ctx.used_tokens || 0} / ${ctx.context_length} tokens in context`)}"><span class="csl-meter"><i style="width:${Math.min(100, pct)}%"></i></span>${pct}%</button>`);
  }
  items.push(`<button type="button" class="csl-item csl-approval csl-mode-${esc(mode)}" data-csl="mode-menu" title="${esc((MODE_INFO[mode]?.desc || '') + ' Click to change.')}">Approvals: ${esc(MODE_INFO[mode]?.label || mode)}</button>`);
  if (s.workspace) items.push(`<span class="csl-item csl-workspace" title="${esc(`Workspace: ${s.workspace}`)}">${esc(basename(s.workspace))}</span>`);
  const off = (s.disabled_tools || []).length;
  items.push(`<button type="button" class="csl-item${off ? ' csl-attn' : ''}" data-csl="panel-tools" title="Choose which tools this chat may use">${off ? `${off} tool${off === 1 ? '' : 's'} off` : 'All tools'}</button>`);
  const always = d.always_allowed_tools || [];
  if (always.length) items.push(`<button type="button" class="csl-item csl-attn" data-csl="revoke" title="${esc(`Always allowed here: ${always.join(', ')}. Click to ask again.`)}">${always.length} always allowed ×</button>`);
  if (d.parent_session && d.parent_session.id) {
    const who = s.agent_profile ? `${s.agent_profile} · ` : '';
    items.push(`<button type="button" class="csl-item csl-fork" data-csl="fork" data-id="${esc(d.parent_session.id)}" title="Sub-agent chat: open the chat that delegated this task">↳ ${esc(who)}from ${esc(d.parent_session.name || 'parent chat')}</button>`);
  }
  if (d.forked_from && d.forked_from.id) {
    items.push(`<button type="button" class="csl-item csl-fork" data-csl="fork" data-id="${esc(d.forked_from.id)}" title="Open the chat this was forked from">⫝ ${esc(d.forked_from.name || 'source chat')}</button>`);
  }
  items.push('<span class="csl-spacer"></span>');
  items.push('<button type="button" class="csl-item csl-gear" data-csl="panel" aria-label="Chat settings" title="Chat settings"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></button>');
  line.innerHTML = items.join('');
}

async function onLineClick(e) {
  const b = e.target.closest('[data-csl]');
  if (!b) return;
  const act = b.dataset.csl;
  if (act === 'panel') togglePanel();
  else if (act === 'panel-tools') togglePanel('tools');
  else if (act === 'mode-menu') toggleModeMenu(b);
  else if (act === 'context') document.getElementById('chat-context-pill')?.click();
  else if (act === 'fork') window.sessionModule?.selectSession?.(b.dataset.id);
  else if (act === 'revoke') {
    try {
      await api(`/api/session/${encodeURIComponent(state.sessionId)}/approvals`, { method: 'DELETE' });
      if (state.data) state.data.always_allowed_tools = [];
      uiModule.showToast('Tools will ask for approval again in this chat');
      render();
    } catch (err) { uiModule.showToast(`Could not reset approvals: ${err.message}`, 'error'); }
  }
}

// ── approval mode quick menu ──────────────────────────────────────────────
function closeModeMenu() { $('csl-mode-menu')?.remove(); }
function toggleModeMenu(anchor) {
  if ($('csl-mode-menu')) { closeModeMenu(); return; }
  closePanel();
  const d = state.data || {};
  const current = (d.settings || {}).approval_mode || '';
  const menu = document.createElement('div');
  menu.id = 'csl-mode-menu';
  menu.className = 'csl-mode-menu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML = ['', ...Object.keys(MODE_INFO)].map((key) => {
    const info = key ? MODE_INFO[key] : { label: 'App default', desc: `Currently ${MODE_INFO[d.approval_mode]?.label || 'Auto'}.` };
    return `<button type="button" role="menuitemradio" aria-checked="${current === key}" data-mode="${esc(key)}"><b>${esc(info.label)}</b><small>${esc(info.desc)}</small></button>`;
  }).join('');
  menu.addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-mode]');
    if (!b) return;
    closeModeMenu();
    await save({ approval_mode: b.dataset.mode || null }, b.dataset.mode ? `Approvals: ${MODE_INFO[b.dataset.mode].label}` : 'Approvals follow the app default');
  });
  anchor.parentNode.insertBefore(menu, anchor);
  const r = anchor.getBoundingClientRect();
  const line = anchor.closest('#chat-status-line').getBoundingClientRect();
  menu.style.left = `${Math.max(0, r.left - line.left)}px`;
  const close = (e) => { if (!menu.contains(e.target) && e.target !== anchor) { closeModeMenu(); document.removeEventListener('mousedown', close, true); } };
  document.addEventListener('mousedown', close, true);
}

// ── settings panel ────────────────────────────────────────────────────────
function closePanel() {
  const p = $('chat-settings-panel');
  if (p) p.remove();
  document.removeEventListener('mousedown', outsideClose, true);
  document.removeEventListener('keydown', escClose, true);
}
function outsideClose(e) {
  const p = $('chat-settings-panel');
  if (p && !p.contains(e.target) && !e.target.closest('#chat-status-line')) closePanel();
}
function escClose(e) { if (e.key === 'Escape') { e.stopPropagation(); closePanel(); } }

async function togglePanel(section) {
  if ($('chat-settings-panel')) { closePanel(); return; }
  if (!state.sessionId) return;
  closeModeMenu();
  const line = ensureLine();
  const panel = document.createElement('div');
  panel.id = 'chat-settings-panel';
  panel.className = 'chat-settings-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Chat settings');
  panel.innerHTML = '<div class="csp-empty">Loading…</div>';
  line.parentNode.insertBefore(panel, line);
  document.addEventListener('mousedown', outsideClose, true);
  document.addEventListener('keydown', escClose, true);
  if (!state.tools) {
    try { state.tools = ((await api('/api/tools')).tools || []); } catch (_) { state.tools = []; }
  }
  renderPanel();
  if (section === 'tools') $('chat-settings-panel')?.querySelector('.csp-tools')?.scrollIntoView({ block: 'start' });
}

function renderPanel() {
  const panel = $('chat-settings-panel');
  if (!panel) return;
  const d = state.data || {};
  const s = d.settings || {};
  const mode = (s.approval_mode) || '';
  const effective = d.approval_mode || 'auto';
  const off = new Set(s.disabled_tools || []);
  const globallyOff = new Set((state.tools || []).filter((t) => t.enabled === false).map((t) => t.id));
  const modeRows = ['', ...Object.keys(MODE_INFO)].map((key) => {
    const info = key ? MODE_INFO[key] : { label: 'Default', desc: `Follow the app default (currently ${MODE_INFO[effective]?.label || effective} for this chat).` };
    return `<label class="csp-radio"><input type="radio" name="csp-mode" value="${esc(key)}"${mode === key ? ' checked' : ''}><span><b>${esc(info.label)}</b><small>${esc(info.desc)}</small></span></label>`;
  }).join('');
  const names = (state.tools || []).map((t) => t.id);
  const grouped = new Map();
  for (const name of names) {
    const group = (TOOL_GROUPS.find(([, test]) => test(name)) || ['Other'])[0];
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(name);
  }
  const order = [...TOOL_GROUPS.map(([g]) => g), 'Other'].filter((g) => grouped.has(g));
  const toolRows = order.map((group) => `
    <fieldset class="csp-group"><legend>${esc(group)}</legend>
      ${grouped.get(group).map((name) => {
        const global = globallyOff.has(name);
        return `<label class="csp-tool${global ? ' is-global' : ''}" title="${global ? 'Switched off for the whole app in Settings' : ''}"><input type="checkbox" data-tool="${esc(name)}"${!off.has(name) && !global ? ' checked' : ''}${global ? ' disabled' : ''}><code>${esc(name)}</code></label>`;
      }).join('')}
    </fieldset>`).join('');
  panel.innerHTML = `
    <div class="csp-head"><span class="csp-title">Chat settings</span><span class="csp-sub">Only this chat</span><button type="button" class="csp-close" data-csp="close" aria-label="Close">×</button></div>
    <section class="csp-section"><h4>Approvals</h4>${modeRows}</section>
    <section class="csp-section">
      <h4>Tools <span class="csp-count">${names.length - off.size - globallyOff.size} of ${names.length} on</span></h4>
      <div class="csp-tools-actions"><button type="button" class="csp-link" data-csp="all-on">Turn all on</button></div>
      <div class="csp-tools">${toolRows || '<div class="csp-empty">No tool list available.</div>'}</div>
    </section>
    <p class="csp-note">The mode, web, shell and workspace this chat last ran with are restored when you reopen it.</p>`;
  panel.querySelectorAll('input[name="csp-mode"]').forEach((input) => input.addEventListener('change', async () => {
    await save({ approval_mode: input.value || null }, input.value ? `Approvals: ${MODE_INFO[input.value].label}` : 'Approvals follow the app default');
  }));
  panel.querySelectorAll('input[data-tool]').forEach((input) => input.addEventListener('change', () => {
    const next = new Set((state.data?.settings?.disabled_tools) || []);
    if (input.checked) next.delete(input.dataset.tool); else next.add(input.dataset.tool);
    save({ disabled_tools: [...next] });
  }));
  panel.querySelector('[data-csp="close"]')?.addEventListener('click', closePanel);
  panel.querySelector('[data-csp="all-on"]')?.addEventListener('click', () => save({ disabled_tools: null }, 'All tools on for this chat'));
}

let _saveChain = Promise.resolve();
function save(patch, toast) {
  // Serialise writes so rapid checkbox clicks can't race each other.
  _saveChain = _saveChain.then(async () => {
    try {
      state.data = await patchSettings(patch);
      if (toast) uiModule.showToast(toast);
    } catch (err) {
      uiModule.showToast(`Could not save chat settings: ${err.message}`, 'error');
    }
    render();
    renderPanel();
  });
  return _saveChain;
}

// ── wiring ────────────────────────────────────────────────────────────────
function init() {
  document.addEventListener('odysseus:context-usage', (e) => {
    if (e.detail && e.detail.sessionId === state.sessionId) { state.context = e.detail; render(); }
  });
  document.addEventListener('odysseus:model-picked', () => setTimeout(render, 50));
  // A new approval decision may have added an always-allow grant.
  document.addEventListener('odysseus:approval-decided', () => {
    if (state.sessionId) api(`/api/session/${encodeURIComponent(state.sessionId)}/settings`).then((d) => { state.data = d; render(); }).catch(() => {});
  });
  // Follow the open chat. Chats are opened, created and materialised from
  // many places with no single event, so watch the id (as the Workbench does).
  const tick = () => {
    const sid = window.sessionModule?.getCurrentSessionId?.() || null;
    if (sid !== state.sessionId) onSessionSwitch(sid);
  };
  tick();
  setInterval(tick, 700);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

const chatSettings = { onSessionSwitch, refresh: () => state.sessionId && onSessionSwitch(state.sessionId), render };
window.chatSettingsModule = chatSettings;
export default chatSettings;
