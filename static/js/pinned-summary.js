import uiModule from './ui.js';

const STORAGE_KEY = 'odysseus-pinned-summary-open';
const ICONS = {
  changes: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 12h6M12 9v6"/></svg>',
  local: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="13" rx="2"/><path d="M8 21h8M12 18v3"/></svg>',
  branch: '<svg viewBox="0 0 24 24"><circle cx="6" cy="4" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 6v12M8 8c5 0 8 0 8-2"/></svg>',
  commit: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M3 12h6M15 12h6"/></svg>',
  compare: '<svg viewBox="0 0 24 24"><path d="M7 3v14M7 17l-3-3M7 17l3-3M17 21V7M17 7l-3 3M17 7l3 3"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 6v5h-5M4 18v-5h5"/><path d="M18 11a7 7 0 0 0-12-3M6 13a7 7 0 0 0 12 3"/></svg>',
};

let panel;
let toggle;
let body;
let currentStatus = null;
let refreshTimer = null;

function basename(path) {
  const parts = String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || path || 'Workspace';
}

function isOpen() {
  return panel?.classList.contains('is-open');
}

function setOpen(open, persist = true) {
  if (!panel || !toggle) return;
  panel.classList.toggle('is-open', open);
  panel.setAttribute('aria-hidden', String(!open));
  toggle.setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('pinned-summary-visible', open);
  if (persist) localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
  if (open) refresh();
}

function putInComposer(text) {
  const input = document.getElementById('message');
  if (!input) return;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  uiModule?.showToast?.('Added to chat');
}

function row(icon, label, value = '', className = '') {
  return `<div class="pinned-summary-row ${className}"><span class="pinned-summary-icon">${icon}</span><span class="pinned-summary-label">${uiModule.esc(label)}</span>${value ? `<span class="pinned-summary-value">${value}</span>` : ''}</div>`;
}

function action(icon, label, command, suffix = '') {
  return `<button type="button" class="pinned-summary-action" data-command="${uiModule.esc(command)}"><span class="pinned-summary-icon">${icon}</span><span>${uiModule.esc(label)}</span>${suffix}</button>`;
}

function render(status) {
  if (!body) return;
  currentStatus = status;
  if (!status?.is_git) {
    body.innerHTML = `${row(ICONS.local, basename(status?.path), `<span title="${uiModule.esc(status?.path || '')}">${uiModule.esc(status?.path || 'No workspace selected')}</span>`)}<div class="pinned-summary-empty">This workspace is not a Git repository.</div>`;
    return;
  }
  const changeValue = status.changed_files
    ? `<span class="git-additions">+${Number(status.additions || 0).toLocaleString()}</span> <span class="git-deletions">-${Number(status.deletions || 0).toLocaleString()}</span>`
    : '<span class="git-clean">Clean</span>';
  const sync = [status.ahead ? `${status.ahead} ahead` : '', status.behind ? `${status.behind} behind` : ''].filter(Boolean).join(' · ');
  const files = (status.files || []).slice(0, 12);
  body.innerHTML = `
    <section class="pinned-summary-section">
      ${row(ICONS.changes, 'Changes', changeValue, 'pinned-summary-changes')}
      ${row(ICONS.local, basename(status.path), `<span title="${uiModule.esc(status.path)}">Local</span>`)}
      ${row(ICONS.branch, status.branch || 'Detached HEAD', sync ? uiModule.esc(sync) : '')}
      ${action(ICONS.commit, status.ahead ? 'Commit or push' : 'Commit or push', `Review the changes in ${status.path}, then commit and push them safely.`)}
      ${action(ICONS.compare, 'Compare branch', `Compare ${status.branch || 'the current branch'} with its base branch and summarize the meaningful differences.`, '<span class="pinned-summary-arrow">↗</span>')}
    </section>
    <section class="pinned-summary-section pinned-summary-files-section">
      <div class="pinned-summary-section-title"><span>Changed files</span><button type="button" id="pinned-summary-refresh" title="Refresh Git status" aria-label="Refresh Git status">${ICONS.refresh}</button></div>
      <div class="pinned-summary-files">
        ${files.length ? files.map(file => `<button type="button" class="pinned-summary-file" data-file="${uiModule.esc(file.path)}" title="${uiModule.esc(file.path)}"><span class="pinned-summary-file-status ${file.status === '??' ? 'untracked' : ''}">${uiModule.esc(file.status)}</span><span>${uiModule.esc(file.path)}</span>${file.staged ? '<span class="pinned-summary-staged">staged</span>' : ''}</button>`).join('') : '<div class="pinned-summary-empty">Working tree clean</div>'}
        ${(status.files || []).length > files.length ? `<div class="pinned-summary-more">+${status.files.length - files.length} more files</div>` : ''}
      </div>
    </section>`;
  body.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => putInComposer(button.dataset.command)));
  body.querySelectorAll('[data-file]').forEach(button => button.addEventListener('click', () => putInComposer(`Review the changes in ${button.dataset.file}.`)));
  body.querySelector('#pinned-summary-refresh')?.addEventListener('click', refresh);
}

async function refresh() {
  const workspace = window.workspaceModule?.getWorkspace?.() || '';
  if (!workspace || !body) {
    render({ is_git: false, path: workspace });
    return;
  }
  body.classList.add('is-refreshing');
  try {
    const response = await fetch(`/api/workspace/status?path=${encodeURIComponent(workspace)}`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Workspace status unavailable');
    render(await response.json());
  } catch (_) {
    if (!currentStatus) body.innerHTML = '<div class="pinned-summary-empty">Could not read Git status.</div>';
  } finally {
    body.classList.remove('is-refreshing');
  }
}

function init() {
  panel = document.getElementById('pinned-summary');
  toggle = document.getElementById('pinned-summary-toggle');
  body = document.getElementById('pinned-summary-body');
  if (!panel || !toggle || !body) return;
  toggle.addEventListener('click', () => setOpen(!isOpen()));
  document.getElementById('pinned-summary-close')?.addEventListener('click', () => setOpen(false));
  document.addEventListener('odysseus-workspace-change', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && isOpen()) refresh(); });
  const saved = localStorage.getItem(STORAGE_KEY);
  setOpen(saved === null ? window.innerWidth >= 1180 : saved === '1', false);
  refreshTimer = window.setInterval(() => { if (isOpen() && !document.hidden) refresh(); }, 30000);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

export default { init, refresh, setOpen };
