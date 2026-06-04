// Engineering Missions cockpit.
// Runtime module for the portfolio-facing GitHub PR review workflow.

import uiModule from './ui.js';
import markdownModule from './markdown.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

let API_BASE = window.location.origin;
let _open = false;
let _missions = [];
let _current = null;
let _loading = false;

const REVIEW_ICON_PATH = 'M4 19V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM13 3v6h6M8 14h8M8 18h5';

function esc(value) {
  return uiModule.esc(String(value ?? ''));
}

function statusClass(status) {
  return `engineering-status engineering-status-${esc(status || 'queued')}`;
}

function formatDate(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function modalHtml() {
  return `
    <div class="modal-content engineering-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px">
            <path d="${REVIEW_ICON_PATH}"/>
          </svg>
          Engineering Missions
        </h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="engineering-close">✖</button>
      </div>
      <div class="modal-body engineering-body">
        <div class="engineering-left">
          <div class="admin-card engineering-run-card">
            <div class="engineering-card-head">
              <h2>PR Review Mission</h2>
              <span id="engineering-run-state" class="engineering-pill">ready</span>
            </div>
            <form id="engineering-pr-form" class="engineering-form">
              <label class="engineering-label" for="engineering-pr-url">GitHub PR URL</label>
              <div class="engineering-input-row">
                <input id="engineering-pr-url" class="memory-search-input engineering-url-input" type="url" placeholder="https://github.com/owner/repo/pull/123" autocomplete="off" />
                <button id="engineering-run-btn" class="memory-toolbar-btn engineering-run-btn" type="submit">Run</button>
              </div>
              <label class="engineering-check-row">
                <input id="engineering-ai-toggle" type="checkbox" checked />
                <span>Use configured reviewer model when available</span>
              </label>
            </form>
          </div>
          <div class="admin-card engineering-history-card">
            <div class="engineering-card-head">
              <h2>Mission History</h2>
              <button id="engineering-refresh" class="memory-toolbar-btn" type="button">Refresh</button>
            </div>
            <div id="engineering-history" class="engineering-history"></div>
          </div>
        </div>
        <div class="engineering-right">
          <div class="admin-card engineering-report-card">
            <div class="engineering-card-head engineering-report-head">
              <div>
                <h2 id="engineering-report-title">Review Report</h2>
                <div id="engineering-report-subtitle" class="engineering-subtitle"></div>
              </div>
              <div class="engineering-actions">
                <a id="engineering-open-page" class="memory-toolbar-btn engineering-action-link" href="/engineering" target="_self" aria-disabled="true">Open Page</a>
                <a id="engineering-export-md" class="memory-toolbar-btn engineering-action-link" href="#" download aria-disabled="true">Markdown</a>
                <a id="engineering-export-json" class="memory-toolbar-btn engineering-action-link" href="#" download aria-disabled="true">JSON</a>
                <button id="engineering-publish-report" class="memory-toolbar-btn" type="button" disabled>Publish Link</button>
                <button id="engineering-revoke-report" class="memory-toolbar-btn" type="button" hidden>Revoke</button>
                <button id="engineering-copy-report" class="memory-toolbar-btn" type="button" disabled>Copy Markdown</button>
              </div>
            </div>
            <div id="engineering-timeline" class="engineering-timeline"></div>
            <div id="engineering-report" class="engineering-report">
              <div class="doclib-empty">Paste a public GitHub PR URL to start.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function ensureModal() {
  let modal = document.getElementById('engineering-modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'engineering-modal';
  modal.innerHTML = modalHtml();
  document.body.appendChild(modal);

  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  makeWindowDraggable(modal, { content, header });

  modal.querySelector('#engineering-close')?.addEventListener('click', close);
  modal.addEventListener('click', (event) => {
    if (uiModule.isTouchInsideModal()) return;
    if (event.target === modal) close();
  });
  modal.querySelector('#engineering-refresh')?.addEventListener('click', () => loadMissions());
  modal.querySelector('#engineering-copy-report')?.addEventListener('click', () => {
    if (_current?.report_markdown) uiModule.copyToClipboard(_current.report_markdown);
  });
  modal.querySelector('#engineering-publish-report')?.addEventListener('click', () => publishCurrentMission());
  modal.querySelector('#engineering-revoke-report')?.addEventListener('click', () => revokeCurrentMission());
  modal.querySelector('#engineering-pr-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    runMission();
  });

  Modals.register('engineering-modal', {
    railBtnId: 'rail-engineering',
    sidebarBtnId: 'tool-engineering-btn',
    restoreFn: () => open(),
    closeFn: () => close(),
    label: 'Engineering',
    icon: REVIEW_ICON_PATH,
  });

  return modal;
}

function setRunState(text, busy = false) {
  const state = document.getElementById('engineering-run-state');
  const button = document.getElementById('engineering-run-btn');
  if (state) state.textContent = text;
  if (button) {
    button.disabled = busy;
    button.textContent = busy ? 'Running' : 'Run';
  }
}

async function requestJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

function missionPageUrl(mission = _current) {
  return mission?.id ? `/engineering/missions/${encodeURIComponent(mission.id)}` : '/engineering';
}

function missionExportUrl(kind, mission = _current) {
  return mission?.id ? `${API_BASE}/api/engineering-missions/${encodeURIComponent(mission.id)}/export/${kind}` : '#';
}

function missionIdFromPath() {
  const match = window.location.pathname.match(/^\/engineering\/missions\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : '';
}

async function selectMission(id, pushUrl = true) {
  const cached = _missions.find((m) => m.id === id);
  _current = cached || _current;
  render();
  try {
    _current = await requestJson(`${API_BASE}/api/engineering-missions/${encodeURIComponent(id)}`);
    if (pushUrl) {
      try { history.pushState({}, '', missionPageUrl(_current)); } catch {}
    }
    render();
  } catch (error) {
    uiModule.showError(error.message);
  }
}

async function loadMissions(selectFirst = false) {
  try {
    const data = await requestJson(`${API_BASE}/api/engineering-missions`);
    _missions = data.items || [];
    if (selectFirst && !_current && _missions.length) _current = _missions[0];
    render();
  } catch (error) {
    uiModule.showError(`Engineering missions failed to load: ${error.message}`);
  }
}

async function loadMissionFromPath() {
  const id = missionIdFromPath();
  if (!id) return false;
  await selectMission(id, false);
  return true;
}

async function runMission() {
  if (_loading) return;
  const input = document.getElementById('engineering-pr-url');
  const ai = document.getElementById('engineering-ai-toggle');
  const prUrl = (input?.value || '').trim();
  if (!prUrl) {
    uiModule.showError('Paste a GitHub PR URL first.');
    return;
  }
  _loading = true;
  setRunState('running', true);
  _current = {
    status: 'running',
    title: 'PR Review Mission',
    target_url: prUrl,
    audit_log: [
      { title: 'Mission queued', status: 'running', detail: 'Waiting for GitHub and diff analysis.' },
      { title: 'Fetch PR metadata', status: 'queued' },
      { title: 'Analyze diff', status: 'queued' },
      { title: 'Synthesize review', status: 'queued' },
    ],
    report_markdown: '',
  };
  render();
  try {
    const mission = await requestJson(`${API_BASE}/api/engineering-missions/pr-review`, {
      method: 'POST',
      body: JSON.stringify({ pr_url: prUrl, include_ai: !!ai?.checked }),
    });
    _current = mission;
    await loadMissions(false);
    _current = _missions.find((m) => m.id === mission.id) || mission;
    try { history.pushState({}, '', missionPageUrl(_current)); } catch {}
    uiModule.showToast('Engineering mission complete');
  } catch (error) {
    uiModule.showError(error.message);
    _current = { ..._current, status: 'failed', error: error.message };
  } finally {
    _loading = false;
    setRunState('ready', false);
    render();
  }
}

async function publishCurrentMission() {
  if (!_current?.id) return;
  try {
    const mission = await requestJson(`${API_BASE}/api/engineering-missions/${encodeURIComponent(_current.id)}/share`, { method: 'POST' });
    _current = mission;
    _missions = _missions.map((item) => item.id === mission.id ? mission : item);
    render();
    if (mission.public_url) uiModule.copyToClipboard(mission.public_url);
    uiModule.showToast('Public report link ready');
  } catch (error) {
    uiModule.showError(error.message);
  }
}

async function revokeCurrentMission() {
  if (!_current?.id) return;
  try {
    const mission = await requestJson(`${API_BASE}/api/engineering-missions/${encodeURIComponent(_current.id)}/share/revoke`, { method: 'POST' });
    _current = mission;
    _missions = _missions.map((item) => item.id === mission.id ? mission : item);
    render();
    uiModule.showToast('Public report link revoked');
  } catch (error) {
    uiModule.showError(error.message);
  }
}

function renderHistory() {
  const host = document.getElementById('engineering-history');
  if (!host) return;
  if (!_missions.length) {
    host.innerHTML = '<div class="doclib-empty">No missions yet.</div>';
    return;
  }
  host.innerHTML = _missions.map((mission) => `
    <button class="engineering-history-item${_current?.id === mission.id ? ' active' : ''}" data-id="${esc(mission.id)}">
      <span class="engineering-history-main">
        <span class="engineering-history-title">${esc(mission.title || 'Engineering Mission')}</span>
        <span class="engineering-history-url">${esc(mission.target_url || '')}</span>
      </span>
      <span class="${statusClass(mission.status)}">${esc(mission.status)}</span>
      <span class="engineering-history-time">${esc(formatDate(mission.updated_at))}</span>
    </button>
  `).join('');
  host.querySelectorAll('[data-id]').forEach((button) => {
    button.addEventListener('click', () => selectMission(button.dataset.id));
  });
}

function renderTimeline() {
  const host = document.getElementById('engineering-timeline');
  if (!host) return;
  const steps = _current?.audit_log || [];
  if (!steps.length) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = steps.map((step) => `
    <div class="engineering-step ${esc(step.status || 'queued')}">
      <span class="engineering-step-dot"></span>
      <span class="engineering-step-copy">
        <span class="engineering-step-title">${esc(step.title || step.stage || 'Step')}</span>
        ${step.detail ? `<span class="engineering-step-detail">${esc(step.detail)}</span>` : ''}
      </span>
    </div>
  `).join('');
}

function renderReport() {
  const title = document.getElementById('engineering-report-title');
  const subtitle = document.getElementById('engineering-report-subtitle');
  const report = document.getElementById('engineering-report');
  const copy = document.getElementById('engineering-copy-report');
  const openPage = document.getElementById('engineering-open-page');
  const exportMd = document.getElementById('engineering-export-md');
  const exportJson = document.getElementById('engineering-export-json');
  const publish = document.getElementById('engineering-publish-report');
  const revoke = document.getElementById('engineering-revoke-report');
  if (!report) return;
  const hasReport = !!_current?.report_markdown;
  if (title) title.textContent = _current?.title || 'Review Report';
  if (subtitle) {
    const bits = [];
    if (_current?.status) bits.push(_current.status);
    if (_current?.summary) bits.push(_current.summary);
    subtitle.textContent = bits.join(' - ');
  }
  if (copy) copy.disabled = !hasReport;
  if (openPage) {
    openPage.href = _current?.id ? missionPageUrl(_current) : '/engineering';
    openPage.setAttribute('aria-disabled', _current?.id ? 'false' : 'true');
  }
  if (exportMd) {
    exportMd.href = hasReport ? missionExportUrl('markdown') : '#';
    exportMd.setAttribute('aria-disabled', hasReport ? 'false' : 'true');
  }
  if (exportJson) {
    exportJson.href = hasReport ? missionExportUrl('json') : '#';
    exportJson.setAttribute('aria-disabled', hasReport ? 'false' : 'true');
  }
  if (publish) {
    publish.disabled = !hasReport;
    publish.textContent = _current?.public_report ? 'Copy Link' : 'Publish Link';
    publish.title = _current?.public_url || '';
  }
  if (revoke) {
    revoke.hidden = !_current?.public_report;
  }
  if (!_current) {
    report.innerHTML = '<div class="doclib-empty">Paste a public GitHub PR URL to start.</div>';
    return;
  }
  if (_current.status === 'failed') {
    report.innerHTML = `<div class="doclib-empty engineering-error">${esc(_current.error || 'Mission failed.')}</div>`;
    return;
  }
  if (!_current.report_markdown) {
    report.innerHTML = '<div class="doclib-empty">Mission is running...</div>';
    return;
  }
  report.innerHTML = markdownModule.mdToHtml(_current.report_markdown);
  try { markdownModule.renderMermaid(report); } catch {}
}

function render() {
  renderHistory();
  renderTimeline();
  renderReport();
}

export function open() {
  const modal = ensureModal();
  _open = true;
  modal.classList.remove('hidden');
  modal.style.display = '';
  document.getElementById('tool-engineering-btn')?.classList.add('active');
  if (!window.location.pathname.startsWith('/engineering')) {
    try { history.pushState({}, '', '/engineering'); } catch {}
  }
  (async () => {
    await loadMissions(false);
    const loadedFromPath = await loadMissionFromPath();
    if (!loadedFromPath && !_current && _missions.length) {
      _current = _missions[0];
      render();
    }
  })();
}

export function close() {
  const modal = document.getElementById('engineering-modal');
  _open = false;
  document.getElementById('tool-engineering-btn')?.classList.remove('active');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
  if (window.location.pathname.startsWith('/engineering')) {
    try { history.pushState({}, '', '/'); } catch {}
  }
}

export function toggle() {
  if (Modals.toggle('engineering-modal')) return;
  if (_open) close();
  else open();
}

export function isOpen() {
  return _open;
}

export function init(apiBase) {
  API_BASE = apiBase || API_BASE;
  if (window.location.pathname.startsWith('/engineering')) {
    setTimeout(() => open(), 60);
  }
}

export default { init, open, close, toggle, isOpen };
