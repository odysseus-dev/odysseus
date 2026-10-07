/**
 * Deep Research side panel — open/close, form, job rendering, library.
 */
import * as jobs from './jobs.js?v=20260910researcherrorpersist1';
import themeModule from '../theme.js?v=20260911organsrain1';
import createResearchSynapse from '../researchSynapse.js?v=20260910roundlabels2';
import spinnerModule from '../spinner.js';
import { sortModelIds } from '../modelSort.js';
import { searchProviderLogo } from '../searchProviderIcons.js';
import { orderActionMenuItems, actionMenuRank, SELECT_MENU_ICON } from '../actionMenuOrder.js';
import { bindMenuDismiss } from '../escMenuStack.js';

// jobId -> { synapse, status } — survives across _renderJobs() rebuilds so
// the SVG keeps its accumulated nodes/edges between progress events.
const _jobSynapses = new Map();
// Running cards rebuild whenever research progress changes. Keep a per-job
// collapse choice outside the DOM so a user can inspect another job without
// the next progress event reopening it.
const _collapsedActiveJobIds = new Set();
const _vizCollapseIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>';
const _vizExpandIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

let _open = false;
let _researchRailUnread = false;
let _onDocKeydown = null;
let _apiBase = '';
let _endpoints = [];
let _markdownModule = null;
let _sessionModule = null;
let _settingsCollapsed = false;
let _researchTab = 'research';
let _historySearch = '';
let _historySort = 'recent';
let _historyFilter = 'all';
let _historySelectMode = false;
const _historySelectedIds = new Set();
let _visibleHistoryIds = [];
let _historyCascadePending = false;
const _researchPickers = new Map();
let _researchPickerCleanup = [];
const _SETTINGS_KEY = 'odysseus-research-settings';
const _COLLAPSE_KEY = 'odysseus-research-settings-collapsed';

try { _settingsCollapsed = localStorage.getItem(_COLLAPSE_KEY) === '1'; } catch {}

function _playHistoryCascade() {
  const list = document.getElementById('research-past-list');
  if (!list?.querySelector('.research-job-card')) {
    _historyCascadePending = true;
    return;
  }
  _historyCascadePending = false;
  list.classList.remove('doclib-just-opened');
  void list.offsetWidth;
  list.classList.add('doclib-just-opened');
  setTimeout(() => list.classList.remove('doclib-just-opened'), 900);
}

function _saveSettingsToStorage() {
  try {
    localStorage.setItem(_SETTINGS_KEY, JSON.stringify({
      max_rounds: document.getElementById('research-rounds')?.value || '0',
      search_provider: document.getElementById('research-search-provider')?.value || '',
      endpoint_id: document.getElementById('research-endpoint')?.value || '',
      model: document.getElementById('research-model')?.value || '',
      category: document.getElementById('research-category')?.value || '',
    }));
  } catch {}
}

function _loadSettingsFromStorage() {
  try {
    const raw = localStorage.getItem(_SETTINGS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function _showBadge() {
  const btn = document.getElementById('tool-research-btn');
  if (!btn || btn.querySelector('.research-badge')) return;
  const dot = document.createElement('span');
  dot.className = 'research-badge';
  btn.appendChild(dot);
}

function _clearBadge() {
  const dot = document.querySelector('#tool-research-btn .research-badge');
  if (dot) dot.remove();
}

// Live sidebar/rail feedback — mirrors the cookbook pattern. While
// research jobs are running, the rail button pulses; errors flag red;
// nothing running clears it. Panel-independent so it works with the
// modal closed. Called from _renderJobs on every job-state change.
function _syncResearchRail() {
  let running = 0, errored = 0, runningJob = null;
  try {
    for (const j of jobs.getJobs()) {
      if (j.status === 'running' || j.status === 'queued') {
        running++;
        if (j.status === 'running' && !runningJob) runningJob = j;
      } else if (j.status === 'error') errored++;
    }
  } catch { return; }
  const railBtn = document.getElementById('rail-research');
  const toolBtn = document.getElementById('tool-research-btn');
  // Historical failures belong in Research history; they must not keep the
  // mini-sidebar notification lit after the panel has been opened.
  const active = running > 0 || _researchRailUnread;
  // Shared flag so sessions.js:_updateRailNotifs (which lights the same
  // rail button for INLINE research mode) ORs with us instead of
  // clobbering — otherwise a session re-render would clear our dot.
  window._researchJobsActive = active;
  if (railBtn) {
    railBtn.classList.remove('rail-notify', 'rail-notify-success', 'rail-notify-error', 'research-notif-active');
    if (active) {
      railBtn.classList.add('rail-notify', errored && running > 0 ? 'rail-notify-error' : 'rail-notify-success', 'research-notif-active');
    }
  }
  if (toolBtn) {
    toolBtn.classList.toggle('research-notif-active', active);
    toolBtn.style.opacity = active ? '1' : '';
    // Sidebar feedback while running — a small pulsing dot + round text,
    // same style as Cookbook's running indicator (no glow).
    let wrap = toolBtn.querySelector('.research-sb-running');
    if (running > 0) {
      if (!wrap) {
        wrap = document.createElement('span');
        wrap.className = 'research-sb-running';
        wrap.innerHTML = '<span class="research-sb-status"></span><span class="research-sb-dot"></span>';
        toolBtn.appendChild(wrap);
      }
      const round = runningJob && runningJob.progress && runningJob.progress.round;
      // Show the full round label (empty until the first round lands).
      // Only update when we actually have a round — don't blank it out on
      // progress ticks that lack one, or it flickers on/off between rounds.
      if (round) wrap.querySelector('.research-sb-status').textContent = `Round ${round}`;
    } else if (wrap) {
      wrap.remove();
    }
  }
  // Orbiting edge animation: faster when a job is running, slower while idle
  // (ambient). The rAF loop in _ensureOrbit drives --research-orbit-angle on
  // the pane element — CSS-only @property animation silently no-op'd in some
  // browsers, so JS drives it for universal compatibility.
  _orbitSpeedDegPerSec = running > 0 ? 60 : 22;  // 6s/rev vs ~16s/rev
  _ensureOrbit();
  if (window._syncRailDynamic) window._syncRailDynamic();
}

// ── Orbit-angle rAF driver ─────────────────────────────────────
// Universally-supported alternative to a CSS @property angle animation.
// Walks --research-orbit-angle on the #research-pane element every frame
// while the panel is open. Stops itself when the pane is gone.
let _orbitRAF = null;
let _orbitAngle = 0;
let _orbitLastTs = 0;
let _orbitSpeedDegPerSec = 22;  // idle ambient default
function _ensureOrbit() {
  if (_orbitRAF) return;
  _orbitLastTs = 0;
  const tick = (ts) => {
    const pane = document.getElementById('research-pane');
    if (!pane) { _orbitRAF = null; return; }  // panel closed → stop loop
    if (_orbitLastTs) {
      const dt = (ts - _orbitLastTs) / 1000;
      _orbitAngle = (_orbitAngle + _orbitSpeedDegPerSec * dt) % 360;
      pane.style.setProperty('--research-orbit-angle', _orbitAngle.toFixed(2) + 'deg');
    }
    _orbitLastTs = ts;
    _orbitRAF = requestAnimationFrame(tick);
  };
  _orbitRAF = requestAnimationFrame(tick);
}

/** Fetch the count of saved research items and populate the header chip. */
async function _updateResearchCount() {
  const el = document.getElementById('research-stats');
  if (!el) return;
  try {
    const res = await fetch('/api/research/library?limit=1', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    const n = data.total || 0;
    el.textContent = n + (n === 1 ? ' research' : ' research');
  } catch {}
}

const _searchIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
const _magnifyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>';
const _researchIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 18h8"/><path d="M3 22h18"/><path d="M14 22a7 7 0 1 0 0-14h-1"/><path d="M9 14h2"/><path d="M9 12a2 2 0 0 1-2-2V6h4v4a2 2 0 0 1-2 2Z"/><path d="M12 6V3a1 1 0 0 0-1-1H9a1 1 0 0 0-1 1v3"/></svg>';
const _closeIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
const _playIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
const _selectIcon = SELECT_MENU_ICON;
const _cancelIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
const _trashIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>';
const _externalIcon = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
const _copyIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
const _retryIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>';
const _chevronIcon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
const _historyIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>';
const _editIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
const _chatIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
const _moreIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';

export function init(apiBase, markdownMod, sessionMod) {
  _apiBase = apiBase;
  _markdownModule = markdownMod;
  _sessionModule = sessionMod;
  jobs.init(apiBase);
  jobs.setRenderCallback(_renderJobs);
  jobs.onComplete(() => {
    if (!_open) {
      _researchRailUnread = true;
      _showBadge();
      _syncResearchRail();
    }
  });
}

export function isOpen() { return _open; }
export function toggle() {
  if (_open) {
    // If minimized, restore instead of closing
    const overlay = document.getElementById('research-overlay');
    if (overlay && overlay.style.display === 'none') {
      overlay.style.display = '';
      const btn = document.getElementById('tool-research-btn');
      if (btn) btn.classList.remove('minimized');
      return;
    }
    closePanel();
  } else {
    openPanel();
  }
}

export function openPanel(focusJobId) {
  if (_open) {
    const overlay = document.getElementById('research-overlay');
    if (overlay && overlay.style.display === 'none') {
      overlay.style.display = '';
      const btn = document.getElementById('tool-research-btn');
      if (btn) btn.classList.remove('minimized');
    }
    document.body.classList.add('research-panel-view');
    if (focusJobId) _focusJob(focusJobId);
    return;
  }
  _open = true;
  _researchRailUnread = false;
  _syncResearchRail();
  _researchTab = 'research';

  const container = document.getElementById('chat-container');
  if (!container) return;

  document.body.classList.add('research-panel-view');
  const btn = document.getElementById('tool-research-btn');
  if (btn) btn.classList.add('active');

  const overlay = document.createElement('div');
  overlay.id = 'research-overlay';
  overlay.className = 'modal research-overlay';

  // Use the same stable desktop frame as Memory/Skills.
  const pane = document.createElement('div');
  pane.id = 'research-pane';
  pane.className = 'modal-content doclib-modal-content research-pane';
  // Mobile: full-screen so the content has room and the jobs list can scroll
  // inside it. Desktop gets a definite height from the first paint. Leaving
  // this content-sized made endpoint/model hydration grow the pane after it
  // opened, which is especially jarring beside an already-open document.
  pane.style.cssText = (window.innerWidth <= 768)
    ? 'width:100vw;max-width:100vw;height:90dvh;max-height:90dvh;border-radius:14px 14px 0 0;background:var(--bg);'
    : 'width:min(560px, 90vw);height:78vh;max-height:78vh;background:var(--bg);';
  pane.innerHTML = _buildPanelHTML();

  overlay.appendChild(pane);
  document.body.appendChild(overlay);

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closePanel();
  });

  // Document-level ESC handler — overlay-only listener never fired because
  // overlay isn't focused. Tracked in module scope so closePanel can detach.
  _onDocKeydown = (e) => {
    if (e.key === 'Escape' && _open) {
      e.preventDefault();
      const openPicker = document.querySelector('.research-picker.open');
      if (openPicker) {
        _closeResearchPickers();
        openPicker.querySelector('.research-picker-btn')?.focus();
        return;
      }
      closePanel();
    }
  };
  document.addEventListener('keydown', _onDocKeydown);

  // Make the pane draggable by its header — same pattern as Library/Calendar.
  const paneHeader = pane.querySelector('.research-pane-header');
  if (themeModule && themeModule.makeDraggable && paneHeader) {
    themeModule.makeDraggable(pane, paneHeader);
  }

  _wireEvents(pane);
  _loadEndpoints().then(_restoreSavedSettings);
  _clearBadge();
  _updateResearchCount();
  jobs.refreshLibrary?.({ force: true });

  if ('Notification' in window && Notification.permission === 'default') {
    try { Notification.requestPermission(); } catch {}
  }

  if (focusJobId) _focusJob(focusJobId);
}

// Scroll to + highlight a research job card by session id. Used by the
// chat anchor-link delegate ([Topic](#research-<session_id>)).
function _focusJob(jobId) {
  if (!jobId) return;
  // jobs may still be loading from /api/research/active — retry a few times.
  let tries = 0;
  const tryFocus = () => {
    const card = document.querySelector(`[data-job-id="${jobId}"]`);
    if (card) {
      // Deep links must reveal the containing tab before scrolling. The
      // composer tab is the default, so an existing card can still be hidden.
      const tab = card.closest('[data-research-panel]')?.dataset.researchPanel;
      if (tab) _setResearchTab(tab);
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('research-card-flash');
      setTimeout(() => card.classList.remove('research-card-flash'), 2000);
      return;
    }
    if (tries++ < 8) setTimeout(tryFocus, 400);
  };
  setTimeout(tryFocus, 200);
}

export function closePanel() {
  if (!_open) return;
  _open = false;

  if (_onDocKeydown) {
    document.removeEventListener('keydown', _onDocKeydown);
    _onDocKeydown = null;
  }

  _researchPickerCleanup.forEach(cleanup => cleanup());
  _researchPickerCleanup = [];
  _researchPickers.clear();

  document.body.classList.remove('research-panel-view');
  const btn = document.getElementById('tool-research-btn');
  if (btn) btn.classList.remove('active');

  const overlay = document.getElementById('research-overlay');
  if (overlay) overlay.remove();
}

function _buildPanelHTML() {
  const searchProviders = ['', 'searxng', 'duckduckgo', 'tavily', 'brave', 'google', 'serper'];
  const providerOpts = searchProviders.map(p =>
    `<option value="${p}">${p || 'Default'}</option>`
  ).join('');

  let roundOpts = '<option value="0" selected>Auto</option>';
  for (let i = 1; i <= 20; i++) {
    roundOpts += `<option value="${i}">${i}</option>`;
  }

  const settingsHidden = _settingsCollapsed ? ' style="display:none"' : '';
  const chevronCls = _settingsCollapsed ? ' collapsed' : '';

  return `
    <div class="modal-header research-pane-header">
      <h4><span class="research-pane-title-icon">${_searchIcon}</span><span>Deep Research</span></h4>
      <div class="research-pane-header-actions">
        <button id="research-panel-minimize" class="modal-minimize-btn" type="button" title="Minimize"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="5" y1="18" x2="19" y2="18"/></svg></button>
        <button id="research-panel-close" class="close-btn" title="Close">&#x2716;</button>
      </div>
    </div>
    <div class="modal-body research-pane-body" data-no-swipe-dismiss>
      <div class="memory-tabs research-tabs" role="tablist" aria-label="Deep Research views">
        <button type="button" class="memory-tab active" data-research-tab="research" role="tab" aria-selected="true">${_searchIcon} Research</button>
        <button type="button" class="memory-tab" data-research-tab="history" role="tab" aria-selected="false">${_historyIcon} History <span id="research-history-count" class="memory-count" style="font-size:0.8em;opacity:0.6;font-weight:normal;margin-left:4px"></span></button>
        <button type="button" class="memory-tab research-active-tab" data-research-tab="active" role="tab" aria-selected="false" hidden>${_playIcon} Active <span id="research-active-count" class="memory-count"></span></button>
      </div>
      <div class="memory-tab-panel research-tab-panel" data-research-panel="research" role="tabpanel">
      <div class="admin-card research-new-job">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">
          <h2 style="margin:0;padding:0;line-height:1;display:inline-flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent, var(--red))" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><path d="M6 18h8"/><path d="M3 22h18"/><path d="M14 22a7 7 0 1 0 0-14h-1"/><path d="M9 14h2"/><path d="M9 12a2 2 0 0 1-2-2V6h4v4a2 2 0 0 1-2 2Z"/><path d="M12 6V3a1 1 0 0 0-1-1H9a1 1 0 0 0-1 1v3"/></svg>Research <span id="research-stats" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal"></span></h2>
        </div>
        <p class="memory-desc doclib-desc research-new-job-desc">
          <span>Multi-step web research with an LLM-in-the-loop agent</span>
        </p>
        <textarea id="research-query" class="research-query" placeholder="Set sail on a question — Odysseus will chart the course." rows="4"></textarea>
        <button id="research-settings-toggle" class="research-settings-toggle${chevronCls}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;opacity:0.85;flex-shrink:0;"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>Settings<span class="research-settings-chevron">${_chevronIcon}</span>
        </button>
        <div id="research-settings-body" class="research-settings-row"${settingsHidden}>
          <label class="research-setting">
            <span class="research-setting-label">Rounds <span class="hwfit-help-chip hwfit-help-chip-inline" title="How many search → read → reflect rounds the agent runs.">?</span></span>
            <select id="research-rounds">${roundOpts}</select>
          </label>
          <label class="research-setting">
            <span class="research-setting-label">Format <span class="hwfit-help-chip hwfit-help-chip-inline" title="Auto lets the LLM pick the output shape.">?</span></span>
            <select id="research-category">
              <option value="" selected>Auto</option>
              <option value="product">Product</option>
              <option value="comparison">Compare</option>
              <option value="howto">How-to</option>
              <option value="factcheck">Fact-check</option>
            </select>
          </label>
          <label class="research-setting">
            <span class="research-setting-label">Search engine</span>
            <select id="research-search-provider">${providerOpts}</select>
          </label>
          <label class="research-setting">
            <span class="research-setting-label">Endpoint</span>
            <select id="research-endpoint"><option value="">Default</option></select>
          </label>
          <label class="research-setting">
            <span class="research-setting-label">Model</span>
            <select id="research-model"><option value="">Default</option></select>
          </label>
        </div>
        <div class="research-controls-row">
          <button id="research-add-btn" class="memory-toolbar-btn research-add-btn"><span class="research-add-plus">+</span> Queue</button>
          <button id="research-start-btn" class="research-start-btn">${_playIcon} Start</button>
        </div>
      </div>
      </div>
      <div class="memory-tab-panel research-tab-panel hidden" data-research-panel="active" role="tabpanel" hidden>
        <div class="research-tab-heading"><h2>Active</h2><span class="memory-count">Queued and running research</span></div>
        <div id="research-active-list" class="research-jobs-list" data-no-swipe-dismiss></div>
      </div>
      <div class="memory-tab-panel research-tab-panel hidden" data-research-panel="history" role="tabpanel" hidden>
        <div class="admin-card research-history-card">
          <div class="research-history-title-row">
            <h2>${_researchIcon}<span>Research</span><span id="research-history-head-count" class="memory-count research-history-head-count"></span></h2>
          </div>
          <p class="memory-desc doclib-desc research-history-desc">Completed research reports saved in your library.</p>
          <div class="memory-toolbar research-history-toolbar">
            <div class="memory-toolbar-row research-history-toolbar-row">
              <select class="memory-sort-select" id="research-history-sort" aria-label="Sort research" title="Sort research">
                <option value="recent">Recent</option>
                <option value="oldest">Oldest</option>
                <option value="sources">Most sources</option>
                <option value="alpha">A-Z</option>
              </select>
              <button type="button" class="memory-toolbar-btn" id="research-history-select-btn" title="Select research">${_selectIcon} Select</button>
            </div>
            <div class="research-history-search-wrap">
              ${_magnifyIcon}
              <input type="text" id="research-history-search" class="memory-search-input" placeholder="Search research..." aria-label="Search research" autocomplete="off">
            </div>
            <div id="research-history-filters" class="skills-summary-strip" aria-label="Research categories"></div>
          </div>
          <div id="research-history-bulk" class="memory-bulk-bar hidden">
            <label class="memory-bulk-check-all" title="Select all research currently shown"><input type="checkbox" id="research-history-select-all"> All in view</label>
            <span id="research-history-selected-count">0 Selected</span>
            <button type="button" id="research-history-bulk-delete" class="memory-toolbar-btn danger" title="Delete selected research" disabled>${_trashIcon} Delete</button>
            <button type="button" id="research-history-bulk-cancel" class="memory-toolbar-btn active" title="Cancel selection" aria-label="Cancel selection">${_cancelIcon}</button>
          </div>
          <div id="research-past-list" class="doclib-grid memory-list research-jobs-list" data-no-swipe-dismiss></div>
        </div>
      </div>
    </div>
  `;
}

/** Fade/slide a card out, then run the removal — matches cookbook's smooth exit. */
function _animateOutThenRemove(el, removeFn) {
  if (!el || !el.style) { removeFn(); return; }
  el.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
  el.style.opacity = '0';
  el.style.transform = 'translateX(-10px)';
  setTimeout(removeFn, 320);
}

/** Dismiss the mobile keyboard by stealing focus into a throwaway readonly
 *  input (blur() alone is often ignored on Firefox mobile). */
function _dismissKeyboard(input) {
  try {
    if (input) input.blur();
    const tmp = document.createElement('input');
    tmp.setAttribute('readonly', 'readonly');
    tmp.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;border:0;padding:0;';
    document.body.appendChild(tmp);
    tmp.focus();
    setTimeout(() => { try { tmp.blur(); tmp.remove(); } catch {} }, 60);
  } catch {}
}

/** Reset the category selector back to "Auto" (called after each start). */
function _resetCategoryToAuto() {
  const sel = document.getElementById('research-category');
  if (sel) {
    sel.value = '';
    _refreshResearchPicker(sel.id);
  }
}

function _wireEvents(pane) {
  pane.querySelector('#research-panel-close').addEventListener('click', closePanel);
  pane.querySelector('#research-panel-minimize')?.addEventListener('click', () => {
    const overlay = document.getElementById('research-overlay');
    if (overlay) overlay.style.display = 'none';
    const btn = document.getElementById('tool-research-btn');
    if (btn) btn.classList.add('minimized');
  });
  pane.querySelector('#research-start-btn').addEventListener('click', _handleStart);
  pane.querySelector('#research-add-btn').addEventListener('click', _handleAdd);
  pane.querySelectorAll('[data-research-tab]').forEach((tab) => {
    tab.addEventListener('click', () => _setResearchTab(tab.dataset.researchTab));
  });
  pane.querySelector('#research-history-search')?.addEventListener('input', (e) => {
    _historySearch = e.currentTarget.value;
    _renderJobs();
  });
  pane.querySelector('#research-history-sort')?.addEventListener('change', (e) => {
    _historySort = e.currentTarget.value;
    _renderJobs();
  });
  pane.querySelector('#research-history-select-btn')?.addEventListener('click', () => {
    _historySelectMode ? _exitHistorySelectMode() : _enterHistorySelectMode();
  });
  pane.querySelector('#research-history-select-all')?.addEventListener('change', (e) => {
    if (e.currentTarget.checked) _visibleHistoryIds.forEach(id => _historySelectedIds.add(id));
    else _visibleHistoryIds.forEach(id => _historySelectedIds.delete(id));
    _updateHistoryBulkBar();
    _renderJobs();
  });
  pane.querySelector('#research-history-bulk-cancel')?.addEventListener('click', _exitHistorySelectMode);
  pane.querySelector('#research-history-bulk-delete')?.addEventListener('click', _bulkDeleteHistory);
  pane.querySelector('#research-history-clear')?.addEventListener('click', (e) => {
    e.stopPropagation();
    _clearHistory();
  });

  pane.querySelector('#research-settings-toggle').addEventListener('click', () => {
    const body = document.getElementById('research-settings-body');
    const btn = document.getElementById('research-settings-toggle');
    if (!body || !btn) return;
    _settingsCollapsed = !_settingsCollapsed;
    body.style.display = _settingsCollapsed ? 'none' : '';
    btn.classList.toggle('collapsed', _settingsCollapsed);
    try { localStorage.setItem('odysseus-research-settings-collapsed', _settingsCollapsed ? '1' : '0'); } catch {}
  });

  const queryInput = pane.querySelector('#research-query');
  queryInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      _handleStart();
    }
  });

  const endpointSelect = pane.querySelector('#research-endpoint');
  endpointSelect.addEventListener('change', () => _populateModels(endpointSelect.value));
  const modelSelect = pane.querySelector('#research-model');
  modelSelect.addEventListener('change', () => {
    // The model menu is deliberately cross-endpoint. Selecting a model must
    // therefore carry its owning endpoint along with it instead of sending a
    // model name to whichever endpoint happened to be selected before.
    const selected = modelSelect.options[modelSelect.selectedIndex];
    const endpointId = selected?.dataset.endpointId;
    if (endpointId && endpointSelect.value !== endpointId) {
      endpointSelect.value = endpointId;
      _refreshResearchPicker(endpointSelect.id);
    }
  });

  _setupResearchPickers(pane);

  _renderJobs();
}

function _setResearchTab(tab) {
  const pane = document.getElementById('research-pane');
  if (!pane) return;
  const activeTab = pane.querySelector('[data-research-tab="active"]');
  if (tab === 'active' && activeTab?.hidden) tab = 'history';
  if (!['research', 'active', 'history'].includes(tab)) tab = 'research';
  const enteringHistory = tab === 'history' && _researchTab !== 'history';
  _researchTab = tab;
  pane.classList.toggle('research-results-view', tab !== 'research');
  pane.querySelectorAll('[data-research-tab]').forEach((button) => {
    const selected = button.dataset.researchTab === tab;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  pane.querySelectorAll('[data-research-panel]').forEach((panel) => {
    const selected = panel.dataset.researchPanel === tab;
    panel.classList.toggle('hidden', !selected);
    panel.hidden = !selected;
  });
  if (enteringHistory) _playHistoryCascade();
}

function _syncResearchTabs(allJobs) {
  const active = allJobs.filter(j => j.status === 'queued' || j.status === 'running');
  const history = allJobs.length - active.length;
  const activeTab = document.querySelector('[data-research-tab="active"]');
  const activeCount = document.getElementById('research-active-count');
  const historyCount = document.getElementById('research-history-count');
  if (activeTab) {
    activeTab.hidden = active.length === 0;
    activeTab.setAttribute('aria-hidden', active.length ? 'false' : 'true');
  }
  if (activeCount) activeCount.textContent = active.length ? active.length : '';
  if (historyCount) historyCount.textContent = history ? history : '';
  if (!active.length && _researchTab === 'active') _researchTab = 'history';
  _setResearchTab(_researchTab);
}

function _readSettings() {
  const category = document.getElementById('research-category')?.value || undefined;
  const settings = {
    max_rounds: parseInt(document.getElementById('research-rounds')?.value || '0', 10),
    search_provider: document.getElementById('research-search-provider')?.value || undefined,
    endpoint_id: document.getElementById('research-endpoint')?.value || undefined,
    model: document.getElementById('research-model')?.value || undefined,
    category: category || undefined,
  };
  const epSel = document.getElementById('research-endpoint');
  if (epSel && epSel.value) {
    const opt = epSel.options[epSel.selectedIndex];
    settings._endpointName = opt?.textContent || '';
  }
  const modelSel = document.getElementById('research-model');
  if (modelSel && modelSel.value) settings._modelName = modelSel.value;
  Object.keys(settings).forEach(k => { if (!settings[k]) delete settings[k]; });
  return settings;
}

function _handleAdd() {
  const queryEl = document.getElementById('research-query');
  const query = (queryEl?.value || '').trim();
  if (!query) { queryEl?.focus(); return; }
  _saveSettingsToStorage();
  jobs.addToQueue(query, _readSettings());
  _setResearchTab('active');
  queryEl.value = '';
  queryEl.focus();
}

// Move a job's data back into the compose form so user can edit and re-queue
function _editJob(job) {
  const queryEl = document.getElementById('research-query');
  if (queryEl) {
    queryEl.value = job.query || '';
    queryEl.focus();
    queryEl.setSelectionRange(queryEl.value.length, queryEl.value.length);
  }
  // Restore category
  const cat = job.category || '';
  const catSel = document.getElementById('research-category');
  if (catSel) catSel.value = cat;
  // Restore settings
  const s = job.settings || {};
  const roundsEl = document.getElementById('research-rounds');
  if (roundsEl && s.max_rounds) roundsEl.value = s.max_rounds;
  const spEl = document.getElementById('research-search-provider');
  if (spEl && s.search_provider) spEl.value = s.search_provider;
  const epEl = document.getElementById('research-endpoint');
  if (epEl && s.endpoint_id) {
    epEl.value = s.endpoint_id;
    _populateModels(s.endpoint_id);
  }
  const mEl = document.getElementById('research-model');
  if (mEl && s.model) mEl.value = s.model;
  _syncAllResearchPickers();
  // Remove the old job so clicking Start/Queue makes a fresh one
  jobs.removeJob(job.id);
  // Scroll the form into view
  queryEl?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function _handleStart() {
  const queryEl = document.getElementById('research-query');
  const startBtn = document.getElementById('research-start-btn');
  const query = (queryEl?.value || '').trim();

  // Start All mode is only for an empty compose field. A freshly typed query
  // always launches immediately, even when older queued jobs exist.
  const queuedCount = jobs.getJobs().filter(j => j.status === 'queued').length;
  if (!query && queuedCount > 1) {
    _resetCategoryToAuto();
    if (window.innerWidth <= 768) _dismissKeyboard(queryEl);
    const total = jobs.getJobs().filter(j => j.status === 'queued').length;
    _setResearchTab('active');
    _promptParallelOrSequential(total, startBtn);
    return;
  }

  // Visual + spinner feedback while the launch request is in flight
  const _setBusy = (busy) => {
    if (!startBtn) return;
    if (busy) {
      startBtn.disabled = true;
      startBtn.dataset._origHTML = startBtn.dataset._origHTML || startBtn.innerHTML;
      startBtn.innerHTML = '';
      try {
        const _wp = spinnerModule.createWhirlpool(14);
        _wp.element.style.cssText += ';vertical-align:middle;margin-right:5px;position:relative;top:-1px;';
        startBtn.appendChild(_wp.element);
      } catch {}
      startBtn.appendChild(document.createTextNode('Starting'));
      startBtn.classList.add('research-start-busy');
    } else {
      startBtn.disabled = false;
      startBtn.classList.remove('research-start-busy');
      if (startBtn.dataset._origHTML) {
        startBtn.innerHTML = startBtn.dataset._origHTML;
      }
    }
  };

  // Show busy briefly for click feedback. Don't await the full launch —
  // the per-job card immediately shows "Starting..." progress, and the
  // backend POST can take a while.
  _setBusy(true);
  setTimeout(() => _setBusy(false), 1500);

  const _mobile = window.innerWidth <= 768;
  if (!query) {
    jobs.startAllQueued();
    _setResearchTab('active');
    _resetCategoryToAuto();
    if (_mobile) _dismissKeyboard(queryEl);
    return;
  }
  _saveSettingsToStorage();
  const settings = _readSettings();
  queryEl.value = '';
  // startJob adds the job synchronously before its first await. Switch after
  // that call so the Active tab is visible and _setResearchTab does not
  // redirect to History because it still appears hidden.
  const startPromise = jobs.startJob(query, settings);
  _setResearchTab('active');
  // Mobile: drop the keyboard after sending; desktop: keep focus for fast follow-ups.
  if (_mobile) _dismissKeyboard(queryEl); else queryEl.focus();
  _resetCategoryToAuto();
  startPromise.then((job) => {
    // A rejected launch used to be rendered as a finished history item before
    // the user could read why it failed. Keep immediate request failures in
    // the compose view and preserve the query for a corrected retry.
    if (job?.status !== 'error') return;
    const detail = job.errorMsg || 'Unable to start research.';
    jobs.removeJob(job.id);
    queryEl.value = query;
    _setResearchTab('research');
    if (typeof uiModule !== 'undefined' && uiModule?.showError) {
      uiModule.showError(`Research did not start: ${detail}`);
    }
  }).catch((e) => {
    queryEl.value = query;
    _setResearchTab('research');
    if (typeof uiModule !== 'undefined' && uiModule?.showError) {
      uiModule.showError(`Research did not start: ${e?.message || 'Unable to start research.'}`);
    }
  });
}

async function _restoreSavedSettings() {
  const saved = _loadSettingsFromStorage() || {};
  if (saved.category !== undefined) {
    const catSel = document.getElementById('research-category');
    if (catSel) catSel.value = saved.category;
  }
  // Rounds intentionally defaults to "Auto" on every open — don't restore.
  // Users can pick a specific cap each time if needed.
  const search = document.getElementById('research-search-provider');
  if (search && saved.search_provider !== undefined) search.value = saved.search_provider;

  // The compose panel must start from the Deep Research default configured in
  // Settings. Previously it only restored an old local panel selection, so a
  // fresh panel stayed at "Default" and its model picker had no models.
  let configured = {};
  if (!saved.endpoint_id) {
    try {
      const response = await fetch(`${_apiBase}/api/auth/settings`, { credentials: 'same-origin' });
      if (response.ok) configured = await response.json() || {};
    } catch (_) { /* The endpoint list remains usable without this preference. */ }
  }
  const ep = document.getElementById('research-endpoint');
  const endpointId = saved.endpoint_id || configured.research_endpoint_id || '';
  const modelId = saved.model || configured.research_model || '';
  if (ep && endpointId && Array.from(ep.options).some(option => option.value === endpointId)) {
    ep.value = endpointId;
    _populateModels(endpointId);
    const model = document.getElementById('research-model');
    if (model && modelId && Array.from(model.options).some(option => option.value === modelId)) {
      model.value = modelId;
    }
  }
  _syncAllResearchPickers();
}

async function _loadEndpoints() {
  try {
    const res = await fetch(`${_apiBase}/api/model-endpoints`, { credentials: 'same-origin' });
    if (!res.ok) return;
    _endpoints = await res.json();
    const sel = document.getElementById('research-endpoint');
    if (!sel) return;
    _endpoints.filter(e => e.is_enabled && String(e.model_type || 'llm').toLowerCase() === 'llm').forEach(ep => {
      const opt = document.createElement('option');
      opt.value = ep.id;
      opt.textContent = ep.name || ep.base_url;
      sel.appendChild(opt);
    });
    // Populate the model menu immediately. It should remain useful even when
    // the optional Settings default has not loaded yet.
    _populateModels();
    _refreshResearchPicker(sel.id);
  } catch {}
}

function _populateModels(endpointId = '') {
  const sel = document.getElementById('research-model');
  if (!sel) return;
  const selectedModel = sel.value;
  sel.innerHTML = '<option value="">Default</option>';
  // A model is the useful choice here, not an endpoint. Keep every enabled
  // LLM endpoint in one menu and annotate models with their API name. The
  // selected option stores its endpoint id, which the change handler above
  // applies before a research job starts.
  const endpoints = _endpoints.filter(ep => (
    ep.is_enabled && String(ep.model_type || 'llm').toLowerCase() === 'llm'
  ));
  const seenModels = new Set();
  endpoints.forEach(ep => {
    sortModelIds(Array.isArray(ep.models) ? ep.models : []).forEach(model => {
      // The custom picker selects by option value. Keep that value unique so
      // duplicate model ids advertised by two APIs cannot pick the wrong API.
      if (seenModels.has(model)) return;
      seenModels.add(model);
      const opt = document.createElement('option');
      opt.value = model;
      opt.dataset.endpointId = ep.id;
      opt.textContent = endpoints.length > 1
        ? `${model} · ${ep.name || ep.base_url}`
        : model;
      sel.appendChild(opt);
    });
  });
  if (selectedModel && Array.from(sel.options).some(option => option.value === selectedModel)) {
    sel.value = selectedModel;
  }
  _refreshResearchPicker(sel.id);
}

// ── Job rendering ──

const _HISTORY_FILTERS = [
  ['all', 'All'],
  ['', 'Standard'],
  ['product', 'Product'],
  ['comparison', 'Comparison'],
  ['howto', 'How-to'],
  ['factcheck', 'Fact-check'],
  ['landscape', 'Landscape'],
];

function _historyCategory(job) {
  return job.category || '';
}

function _renderHistoryFilters(items) {
  const el = document.getElementById('research-history-filters');
  if (!el) return;
  const counts = new Map();
  items.forEach(job => counts.set(_historyCategory(job), (counts.get(_historyCategory(job)) || 0) + 1));
  el.innerHTML = _HISTORY_FILTERS
    .filter(([value]) => value === 'all' || counts.has(value))
    .map(([value, label]) => {
      const active = _historyFilter === value ? ' active' : '';
      const count = value === 'all' ? items.length : (counts.get(value) || 0);
      return `<button type="button" class="skills-summary-chip${active}" data-research-filter="${_esc(value)}" title="Show ${_esc(label.toLowerCase())} research"><span>${_esc(label)}</span><strong>${count}</strong></button>`;
    }).join('');
  el.querySelectorAll('[data-research-filter]').forEach(button => {
    button.addEventListener('click', () => {
      _historyFilter = button.dataset.researchFilter;
      _renderJobs();
    });
  });
}

function _getVisibleHistory(items) {
  const search = _historySearch.trim().toLowerCase();
  let visible = items.filter(job => {
    if (_historyFilter !== 'all' && _historyCategory(job) !== _historyFilter) return false;
    if (!search) return true;
    const haystack = [job.query, job.category, _CAT_LABELS[job.category] || '', job.status]
      .filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(search);
  });
  const time = job => Number(job.startedAt) || 0;
  if (_historySort === 'oldest') visible.sort((a, b) => time(a) - time(b));
  else if (_historySort === 'sources') visible.sort((a, b) => (b.sources?.length ?? b.sourceCount ?? 0) - (a.sources?.length ?? a.sourceCount ?? 0) || time(b) - time(a));
  else if (_historySort === 'alpha') visible.sort((a, b) => (a.query || '').localeCompare(b.query || ''));
  else visible.sort((a, b) => time(b) - time(a));
  return visible;
}

function _updateHistoryBulkBar() {
  const bar = document.getElementById('research-history-bulk');
  const button = document.getElementById('research-history-select-btn');
  const count = document.getElementById('research-history-selected-count');
  const deleteButton = document.getElementById('research-history-bulk-delete');
  const all = document.getElementById('research-history-select-all');
  if (bar) bar.classList.toggle('hidden', !_historySelectMode);
  if (button) {
    button.classList.toggle('active', _historySelectMode);
    button.innerHTML = _historySelectMode ? `${_cancelIcon} Cancel` : `${_selectIcon} Select`;
  }
  if (count) count.textContent = `${_historySelectedIds.size} Selected`;
  if (deleteButton) deleteButton.disabled = _historySelectedIds.size === 0;
  if (all) all.checked = _visibleHistoryIds.length > 0 && _visibleHistoryIds.every(id => _historySelectedIds.has(id));
}

function _enterHistorySelectMode() {
  _historySelectMode = true;
  _historySelectedIds.clear();
  _updateHistoryBulkBar();
  _renderJobs();
}

function _exitHistorySelectMode() {
  _historySelectMode = false;
  _historySelectedIds.clear();
  _updateHistoryBulkBar();
  _renderJobs();
}

async function _deleteHistoryJobs(ids) {
  const selected = new Set(ids);
  const selectedJobs = jobs.getJobs().filter(job => selected.has(job.id));
  await Promise.all(selectedJobs.filter(job => job.status === 'done').map(async job => {
    try { await fetch(`${_apiBase}/api/research/${job.id}`, { method: 'DELETE', credentials: 'same-origin' }); } catch {}
  }));
  selectedJobs.forEach(job => jobs.removeJob(job.id));
}

async function _bulkDeleteHistory() {
  if (!_historySelectedIds.size) return;
  const ids = [..._historySelectedIds];
  if (window.styledConfirm) {
    const ok = await window.styledConfirm(`Delete ${ids.length} research ${ids.length === 1 ? 'report' : 'reports'}?`, { confirmText: 'Delete', danger: true });
    if (!ok) return;
  }
  await _deleteHistoryJobs(ids);
  _exitHistorySelectMode();
}

async function _clearHistory() {
  const history = jobs.getJobs().filter(job => job.status !== 'queued' && job.status !== 'running');
  if (!history.length) return;
  if (window.styledConfirm) {
    const ok = await window.styledConfirm(`Clear ${history.length} research ${history.length === 1 ? 'report' : 'reports'} from history?`, { confirmText: 'Clear all', danger: true });
    if (!ok) return;
  }
  await _deleteHistoryJobs(history.map(job => job.id));
}

function _renderJobs() {
  // Keep the rail/sidebar indicator in sync on every job-state change,
  // even when the panel is closed (no container yet).
  _syncResearchRail();
  const allJobs = jobs.getJobs();
  const activeList = document.getElementById('research-active-list');
  const pastList = document.getElementById('research-past-list');
  if (!activeList || !pastList) return;

  const active = allJobs.filter(j => j.status === 'queued' || j.status === 'running');
  const past = allJobs.filter(j => j.status !== 'queued' && j.status !== 'running').reverse();
  const visiblePast = _getVisibleHistory(past);
  _visibleHistoryIds = visiblePast.map(job => job.id);
  _renderHistoryFilters(past);
  _syncResearchTabs(allJobs);

  // Active cards are rebuilt on every progress event. Preserve an open
  // overflow menu across that rebuild so live research updates do not make
  // the kebab appear to open and immediately close.
  const openActiveOverflowIds = new Set(
    [...activeList.querySelectorAll('.research-job-overflow.open')]
      .map(overflow => overflow.closest('[data-job-id]')?.dataset.jobId)
      .filter(Boolean),
  );
  activeList.querySelectorAll('.research-job-overflow.open').forEach((overflow) => {
    overflow.querySelector('.research-job-more')?.click();
  });

  activeList.innerHTML = '';
  pastList.innerHTML = '';

  const statsEl = document.getElementById('research-stats');
  if (statsEl) {
    statsEl.textContent = past.length + ' research';
  }
  const historyHeadCount = document.getElementById('research-history-head-count');
  if (historyHeadCount) historyHeadCount.textContent = `${visiblePast.length} of ${past.length}`;
  _historySelectedIds.forEach(id => {
    if (!past.some(job => job.id === id)) _historySelectedIds.delete(id);
  });
  _updateHistoryBulkBar();

  // The main Start button doubles as "Start All (N)" when more than one job
  // is queued — clicking it then opens the parallel/sequential picker. No
  // separate queue-bar button (that was the redundant second button).
  const queued = active.filter(j => j.status === 'queued');
  const startBtn = document.getElementById('research-start-btn');
  if (startBtn && !startBtn.classList.contains('research-start-busy')) {
    startBtn.innerHTML = queued.length > 1
      ? `${_playIcon} Start All (${queued.length})`
      : `${_playIcon} Start`;
    startBtn.dataset._origHTML = startBtn.innerHTML;
  }

  // Clean up synapses for jobs that finished or disappeared. complete()
  // marks the SVG green for ~800ms before destroy removes it.
  const liveIds = new Set(allJobs.filter(j => j.status === 'running').map(j => j.id));
  for (const jobId of _collapsedActiveJobIds) {
    if (!liveIds.has(jobId)) _collapsedActiveJobIds.delete(jobId);
  }
  for (const [jobId, entry] of _jobSynapses) {
    if (liveIds.has(jobId)) continue;
    try { entry.synapse.complete(); } catch {}
    setTimeout(() => { try { entry.synapse.destroy(); } catch {} }, 800);
    _jobSynapses.delete(jobId);
  }

  const appendCards = (list, items, emptyText) => {
    if (!items.length) {
      list.innerHTML = `<div class="research-empty">${emptyText}</div>`;
      return;
    }
    items.forEach(job => list.appendChild(_buildJobCard(job)));
  };
  appendCards(activeList, active, 'No active research.');
  appendCards(pastList, visiblePast, past.length ? 'No research matches your filters.' : 'No research history yet.');
  openActiveOverflowIds.forEach((jobId) => {
    activeList.querySelector(`[data-job-id="${CSS.escape(jobId)}"] .research-job-more`)?.click();
  });
  if (_historyCascadePending && _researchTab === 'history') _playHistoryCascade();
}

/** Pick parallel vs sequential as a small popover anchored to the
 *  Start-All button. Drops down by default; flips to drop-up if there
 *  isn't enough room below the button. Outside-click / Esc dismiss. */
function _promptParallelOrSequential(count, anchorBtn) {
  // Strip any prior instance so a second click closes-then-reopens cleanly.
  const existing = document.getElementById('research-run-mode-popover');
  if (existing) { existing.remove(); return; }
  if (!anchorBtn) return;

  const rect = anchorBtn.getBoundingClientRect();
  const pop = document.createElement('div');
  pop.id = 'research-run-mode-popover';
  pop.className = 'research-run-mode-popover';
  // Same parallel / sequential glyphs the model-comparison picker uses.
  const ICON_PARALLEL = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>';
  const ICON_SEQUENTIAL = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="1.5" fill="currentColor"/><circle cx="4" cy="12" r="1.5" fill="currentColor"/><circle cx="4" cy="18" r="1.5" fill="currentColor"/></svg>';
  pop.innerHTML =
    '<button class="research-run-mode-row" data-mode="parallel">' + ICON_PARALLEL + '<span class="rrm-title">Parallel</span></button>'
    + '<button class="research-run-mode-row" data-mode="sequential">' + ICON_SEQUENTIAL + '<span class="rrm-title">Sequential</span></button>';
  document.body.appendChild(pop);

  // Position: prefer dropping down from the button's bottom-right corner.
  // If there isn't enough room below the viewport, flip to drop-up above.
  const popHeight = pop.offsetHeight;
  const margin = 6;
  const spaceBelow = window.innerHeight - rect.bottom;
  const goUp = spaceBelow < popHeight + margin && rect.top > popHeight + margin;
  const top = goUp ? (rect.top - popHeight - margin) : (rect.bottom + margin);
  // Right-align to the button so the menu doesn't extend off-screen on the right
  const right = Math.max(8, window.innerWidth - rect.right);
  pop.style.top = `${Math.round(top)}px`;
  pop.style.right = `${Math.round(right)}px`;
  pop.classList.add(goUp ? 'rrm-up' : 'rrm-down');

  let unregister = () => {};
  const close = () => {
    pop.remove();
    unregister();
    unregister = () => {};
  };
  unregister = bindMenuDismiss(pop, close, e => !(pop.contains(e.target) || e.target === anchorBtn));

  pop.querySelectorAll('.research-run-mode-row').forEach(b => {
    b.addEventListener('click', () => {
      const mode = b.dataset.mode;
      close();
      if (mode === 'parallel') jobs.startAllQueued();
      else jobs.startAllQueuedSequential();
    });
  });
}

function _buildJobCard(job) {
  const card = document.createElement('div');
  const standardVariant = !job.category ? _researchVisualVariant(job) : null;
  const isHistoryCard = job.status !== 'queued' && job.status !== 'running';
  card.className = `doclib-card memory-item research-job-card ${job.status}${job._fromLibrary ? ' from-library' : ''}${standardVariant !== null ? ` research-standard research-standard-v${standardVariant}` : ''}`;
  card.dataset.jobId = job.id;
  if (job.category) card.dataset.category = job.category;

  const elapsed = jobs.formatElapsed(job.elapsed || 0);
  const modelTag = (job.modelName || job.settings?._modelName)
    ? `<span class="research-job-model">${_esc(job.modelName || job.settings._modelName)}</span>` : '';

  if (job.status === 'queued') {
    const rounds = job.settings?.max_rounds;
    const roundsLabel = rounds === -1 ? 'Explain only' : (!rounds ? 'Auto rounds' : `${rounds} rounds`);
    const epName = job.settings?._endpointName || '';
    const mName = job.settings?._modelName || '';
    const meta = [mName, epName, roundsLabel].filter(Boolean).join(' -- ');
    card.innerHTML = `
      <div class="research-job-header">
        <span class="research-job-query">${_esc(job.query)}</span>
      </div>
      <div class="research-job-queued-meta">${_esc(meta)}</div>
      <div class="research-job-actions">
        <button class="research-job-action" data-action="start" title="Start">${_playIcon} Start</button>
        <button class="research-job-action" data-action="edit" title="Edit query">${_editIcon} Edit</button>
        ${_jobOverflowHTML([{ action: 'remove', icon: _cancelIcon, label: 'Remove from queue' }])}
      </div>
    `;
    const overflow = _wireJobOverflow(card);
    card.querySelector('[data-action="start"]').addEventListener('click', (e) => {
      e.stopPropagation(); jobs.startQueued(job.id);
    });
    card.querySelector('[data-action="edit"]').addEventListener('click', (e) => {
      e.stopPropagation(); _editJob(job);
    });
    card.querySelector('[data-menu-action="remove"]').addEventListener('click', (e) => {
      e.stopPropagation(); overflow.close(); jobs.removeJob(job.id);
    });

  } else if (job.status === 'running') {
    // Auto mode (max_rounds=0/undefined) — show round number without total,
    // and base the progress bar on a heuristic cap of 8 rounds.
    const userMaxR = job.settings?.max_rounds || 0;
    const phaseMaxR = userMaxR || 0;  // 0 = formatPhase shows "Round X" without total
    const phase = jobs.formatPhase(job.progress, phaseMaxR);
    const round = job.progress?.round || 0;
    const barCap = userMaxR || 8;
    const explainOnly = job.mode === 'explain' || job.settings?.max_rounds === -1;
    const pct = explainOnly ? 68 : Math.min(100, Math.round((round / barCap) * 100));
    const hasLiveNavigationTrace = Array.isArray(job.navigation_trace) && job.navigation_trace.length;
    const hasLiveActionTrace = Array.isArray(job.action_trace) && job.action_trace.length;
    const liveTrace = hasLiveNavigationTrace || hasLiveActionTrace
      ? `<div class="research-navigation-trace research-navigation-trace-live">${_renderNavigationTrace(job.navigation_trace, job.action_trace)}</div>`
      : '';
    const expanded = !_collapsedActiveJobIds.has(job.id);
    card.classList.toggle('research-active-expanded', expanded);
    card.innerHTML = `
      <div class="research-job-header research-active-card-header" role="button" tabindex="0" aria-expanded="${expanded}" title="${expanded ? 'Hide live research' : 'Show live research'}">
        <span class="research-job-query">${_esc(job.query)}</span>
        ${modelTag}
        <span class="research-job-time">${elapsed}</span>
        <span class="research-active-card-chevron" aria-hidden="true">${expanded ? _vizCollapseIcon : _vizExpandIcon}</span>
        ${_jobOverflowHTML([{ action: 'cancel', icon: _cancelIcon, label: 'Cancel research', danger: true }])}
      </div>
      <div class="research-active-detail"${expanded ? '' : ' hidden'}>
        <div class="research-job-phase">${phase}</div>
        <div class="research-job-synapse-host" data-synapse-host="${job.id}"></div>
        <div class="research-progress-bar"><div class="research-progress-fill" style="width:${pct}%"></div></div>
        ${liveTrace}
      </div>
    `;
    const overflow = _wireJobOverflow(card);
    card.querySelector('[data-menu-action="cancel"]').addEventListener('click', (e) => {
      e.stopPropagation(); overflow.close(); jobs.cancelJob(job.id);
    });
    const header = card.querySelector('.research-active-card-header');
    const toggleDetail = () => {
      const shouldExpand = _collapsedActiveJobIds.has(job.id);
      if (shouldExpand) _collapsedActiveJobIds.delete(job.id);
      else _collapsedActiveJobIds.add(job.id);
      const detail = card.querySelector('.research-active-detail');
      card.classList.toggle('research-active-expanded', shouldExpand);
      detail.hidden = !shouldExpand;
      header.setAttribute('aria-expanded', shouldExpand ? 'true' : 'false');
      header.setAttribute('title', shouldExpand ? 'Hide live research' : 'Show live research');
      header.querySelector('.research-active-card-chevron').innerHTML = shouldExpand ? _vizCollapseIcon : _vizExpandIcon;
    };
    header.addEventListener('click', (e) => {
      if (e.target.closest('button, .research-job-menu')) return;
      toggleDetail();
    });
    header.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      toggleDetail();
    });
    // Attach (or re-attach) the live synapse visualization. Created once per
    // job so animations/state persist across the _renderJobs() rebuilds that
    // fire on every progress event.
    const host = card.querySelector('.research-job-synapse-host');
    let entry = _jobSynapses.get(job.id);
    if (!entry) {
      const synapse = createResearchSynapse(host, {
        query: job.query || '',
        startedAt: job.startedAt || (Date.now() - (job.elapsed || 0) * 1000),
        compact: true,
      });
      entry = { synapse, status: 'running' };
      _jobSynapses.set(job.id, entry);
    } else {
      // Move the existing element into the freshly-rendered host
      host.appendChild(entry.synapse.element);
    }
    // Push the current progress state
    if (job.progress) {
      entry.synapse.setPhase(job.progress.phase, job.progress);
      if (typeof job.progress.round === 'number') entry.synapse.setRound(job.progress.round);
      if (typeof job.progress.total_sources === 'number') entry.synapse.setSourceCount(job.progress.total_sources);
    }

  } else if (job.status === 'done') {
    // Library-loaded jobs have sources=null but pre-set sourceCount; fresh jobs
    // populate sources directly. Prefer the pre-set count if present.
    const srcCount = job.sources?.length ?? job.sourceCount ?? 0;
    // 0 sources = the research couldn't gather/extract anything — flag it.
    const explainOnly = job.mode === 'explain' || job.settings?.max_rounds === -1;
    const failed = srcCount === 0 && !explainOnly;
    if (failed) card.classList.add('research-job-failed');
    // "visual" describes how this report is presented, not its research
    // category. Keeping it beside every title made History noisy, so omit
    // only that label while retaining meaningful category/failure badges.
    const doneBadge = '';
    const failNote = failed
      ? `<div class="research-job-failnote">Couldn't extract, try again or change Settings.</div>`
      : '';
    const thumbSource = (job.sources || []).find(s => s && (s.image || s.og_image));
    const thumbUrl = job.thumbnail || thumbSource?.image || thumbSource?.og_image || '';
    const thumbnail = thumbUrl
      ? `<span class="research-job-thumb-frame"><img class="research-job-thumb" src="${_esc(thumbUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer"></span>`
      : '<span class="research-job-thumb-frame research-job-thumb-empty" aria-hidden="true"></span>';
    card.innerHTML = `
      <div class="research-job-header">
        <span class="research-job-query">${_esc(job.query)}</span>${doneBadge}
        <button type="button" class="task-status-badge task-state-badge research-job-report-badge" data-action="report" title="Open visual report">${_externalIcon}<span class="task-state-label">Visual</span></button>
        <button type="button" class="task-status-badge task-state-badge research-job-discuss-badge" data-action="chat" title="Open follow-up chat with this research as context">${_chatIcon}<span class="task-state-label">Chat</span></button>
        ${_jobOverflowHTML([
          { action: 'copy', icon: _copyIcon, label: 'Copy report' },
          { action: 'dismiss', icon: _cancelIcon, label: 'Hide from list' },
          { action: 'delete', icon: _trashIcon, label: 'Delete from disk', danger: true },
        ])}
      </div>
      <div class="research-job-summary">${thumbnail}<div class="research-job-summary-copy"><span class="research-job-meta">${elapsed} · ${explainOnly ? 'model only' : `${srcCount} sources`}</span>${failNote}</div></div>
    `;
    const thumbFrame = card.querySelector('.research-job-thumb-frame');
    if (thumbFrame && thumbUrl) {
      thumbFrame.setAttribute('role', 'button');
      thumbFrame.setAttribute('tabindex', '0');
      thumbFrame.setAttribute('title', 'Open visual report');
      const openReport = (e) => {
        e.preventDefault();
        e.stopPropagation();
        window.open(`${_apiBase}/api/research/report/${job.id}`, '_blank');
      };
      thumbFrame.addEventListener('click', openReport);
      thumbFrame.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') openReport(e);
      });
    }
    const overflow = _wireJobOverflow(card);
    card.querySelector('[data-menu-action="copy"]').addEventListener('click', async (e) => {
      e.stopPropagation();
      const btn = e.currentTarget; // capture before await — currentTarget becomes null after
      if (!job.result) await _ensureResult(job);
      _copyResult(job, btn);
      overflow.close();
    });
    card.querySelector('[data-action="report"]').addEventListener('click', (e) => {
      e.stopPropagation();
      window.open(`${_apiBase}/api/research/report/${job.id}`, '_blank');
    });
    card.querySelector('[data-action="chat"]').addEventListener('click', (e) => {
      e.stopPropagation();
      _chatAboutResearch(job.id, e.currentTarget);
    });
    card.querySelector('[data-menu-action="delete"]').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (window.styledConfirm) {
        const ok = await window.styledConfirm('Delete this research? This permanently removes it from disk.', { confirmText: 'Delete', danger: true });
        if (!ok) return;
      }
      try { await fetch(`${_apiBase}/api/research/${job.id}`, { method: 'DELETE', credentials: 'same-origin' }); } catch {}
      overflow.close();
      _animateOutThenRemove(card, () => jobs.removeJob(job.id));
    });
    card.querySelector('[data-menu-action="dismiss"]').addEventListener('click', (e) => {
      e.stopPropagation();
      overflow.close();
      _animateOutThenRemove(card, () => jobs.removeJob(job.id));
    });

  } else {
    const errMsg = job.errorMsg ? `<span class="research-job-error research-job-title-error" title="${_esc(job.errorMsg)}">${_esc(job.errorMsg)}</span>` : '';
    card.innerHTML = `
      <div class="research-job-header">
        <span class="research-job-query">${_esc(job.query)}</span>
        ${errMsg}
        <button type="button" class="task-status-badge task-state-badge research-job-retry-badge" data-action="retry" title="Retry">${_retryIcon}<span class="task-state-label">Retry</span></button>
        <button type="button" class="task-status-badge task-state-badge research-job-edit-badge" data-action="edit" title="Edit and retry">${_editIcon}<span class="task-state-label">Edit</span></button>
        ${_jobOverflowHTML([{ action: 'dismiss', icon: _cancelIcon, label: 'Hide from list' }])}
      </div>
    `;
    const overflow = _wireJobOverflow(card);
    card.querySelector('[data-action="retry"]').addEventListener('click', (e) => {
      e.stopPropagation();
      jobs.retryJob(job.id);
      _setResearchTab('active');
    });
    card.querySelector('[data-action="edit"]').addEventListener('click', (e) => {
      e.stopPropagation(); _editJob(job);
    });
    card.querySelector('[data-menu-action="dismiss"]').addEventListener('click', (e) => {
      e.stopPropagation(); overflow.close(); jobs.removeJob(job.id);
    });
  }

  if (_historySelectMode && isHistoryCard) {
    const header = card.querySelector('.research-job-header');
    if (header) {
      const label = document.createElement('label');
      label.className = 'research-history-card-select';
      label.title = 'Select research';
      label.innerHTML = `<input type="checkbox" class="memory-select-cb research-history-select-cb" ${_historySelectedIds.has(job.id) ? 'checked' : ''} aria-label="Select research">`;
      // Card-level handlers must never consume an individual selection click.
      const stopCardInteraction = (e) => e.stopPropagation();
      ['pointerdown', 'mousedown', 'click'].forEach(type => label.addEventListener(type, stopCardInteraction));
      const checkbox = label.querySelector('input');
      checkbox.addEventListener('click', stopCardInteraction);
      checkbox.addEventListener('change', (e) => {
        if (e.currentTarget.checked) _historySelectedIds.add(job.id);
        else _historySelectedIds.delete(job.id);
        _updateHistoryBulkBar();
      });
      header.prepend(label);
      card.classList.add('research-history-selectable');
      card.addEventListener('click', (e) => {
        if (e.target.closest('button, a, input, label, .research-job-menu')) return;
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }
  }

  return card;
}

const _CAT_ICONS = {
  product:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="8" width="18" height="13" rx="2"/><path d="M7 8V5a5 5 0 0 1 10 0v3"/></svg>',
  comparison: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v18"/><path d="M16 3v18"/><path d="M3 8h5"/><path d="M16 16h5"/></svg>',
  howto:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  landscape:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
  factcheck:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>',
};

const _CAT_LABELS = {
  product: 'Product',
  comparison: 'Comparison',
  howto: 'How-to Guide',
  landscape: 'Landscape',
  factcheck: 'Fact-check',
};

const _STANDARD_CAT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4.5 4.5"/><path d="M11 8v6M8 11h6"/></svg>';

function _jobOverflowHTML(items) {
  const orderedItems = orderActionMenuItems(items);
  const menuItems = orderedItems.map((item, index) => `${index > 0 && actionMenuRank(item) >= 700 && actionMenuRank(orderedItems[index - 1]) < 700 ? '<div class="dropdown-divider"></div>' : ''}<button type="button" role="menuitem" data-menu-action="${item.action}" class="dropdown-item-compact${item.danger ? ' dropdown-item-danger' : ''}">${item.icon}<span>${item.label}</span></button>`).join('');
  return `
    <div class="research-job-overflow">
      <button type="button" class="research-job-action research-job-more" data-action="more" title="More actions" aria-label="More actions" aria-haspopup="menu" aria-expanded="false">${_moreIcon}</button>
      <div class="dropdown session-dropdown-menu research-job-menu" role="menu" hidden>
        ${menuItems}
      </div>
    </div>`;
}

function _wireJobOverflow(card) {
  const wrap = card.querySelector('.research-job-overflow');
  const button = wrap?.querySelector('.research-job-more');
  const menu = wrap?.querySelector('.research-job-menu');
  if (!wrap || !button || !menu) return { close() {} };
  let unregister = () => {};
  const close = () => {
    menu.hidden = true;
    menu.style.display = 'none';
    wrap.classList.remove('open');
    button.setAttribute('aria-expanded', 'false');
    unregister();
    unregister = () => {};
  };
  const open = () => {
    document.querySelectorAll('.research-job-overflow.open').forEach(other => {
      if (other !== wrap) other.querySelector('.research-job-more')?.click();
    });
    menu.hidden = false;
    menu.style.display = 'block';
    wrap.classList.add('open');
    button.setAttribute('aria-expanded', 'true');
    unregister = bindMenuDismiss(menu, () => { close(); button.focus(); }, e => !wrap.contains(e.target));
    menu.querySelector('button')?.focus();
  };
  button.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    menu.hidden ? open() : close();
  });
  menu.addEventListener('click', e => e.stopPropagation());
  return { close };
}

function _researchPickerIcon(selectId, value) {
  if (selectId === 'research-category') return value ? (_CAT_ICONS[value] || '') : _STANDARD_CAT_ICON;
  if (selectId === 'research-search-provider') return value ? searchProviderLogo(value) : _searchIcon;
  return '';
}

function _closeResearchPickers(exceptId = '') {
  _researchPickers.forEach((picker, id) => {
    if (id !== exceptId) picker.close();
  });
}

function _refreshResearchPicker(selectId) {
  _researchPickers.get(selectId)?.refresh();
}

function _syncAllResearchPickers() {
  _researchPickers.forEach(picker => picker.refresh());
}

function _setupResearchPickers(pane) {
  const ids = [
    'research-rounds',
    'research-category',
    'research-search-provider',
    'research-endpoint',
    'research-model',
  ];
  ids.forEach(id => {
    const select = pane.querySelector(`#${id}`);
    if (!select || select.dataset.researchPickerBound === '1') return;
    select.dataset.researchPickerBound = '1';
    select.classList.add('research-native-select');

    const picker = document.createElement('div');
    picker.className = 'research-picker';
    const menuId = `${id}-menu`;
    picker.innerHTML = `
      <button type="button" class="research-picker-btn" aria-haspopup="listbox" aria-expanded="false" aria-controls="${menuId}">
        <span class="research-picker-current"></span>
        ${_chevronIcon}
      </button>
      <div class="research-picker-menu" id="${menuId}" role="listbox" hidden></div>
    `;
    select.insertAdjacentElement('afterend', picker);

    const button = picker.querySelector('.research-picker-btn');
    const current = picker.querySelector('.research-picker-current');
    const menu = picker.querySelector('.research-picker-menu');
    let unregister = () => {};
    const close = () => {
      menu.hidden = true;
      picker.classList.remove('open');
      button.setAttribute('aria-expanded', 'false');
      unregister();
      unregister = () => {};
    };
    const focusItem = (offset) => {
      const items = Array.from(menu.querySelectorAll('.research-picker-option'));
      if (!items.length) return;
      const currentIndex = Math.max(0, items.indexOf(document.activeElement));
      items[(currentIndex + offset + items.length) % items.length].focus();
    };
    const open = () => {
      _closeResearchPickers(id);
      menu.hidden = false;
      picker.classList.add('open');
      button.setAttribute('aria-expanded', 'true');
      unregister = bindMenuDismiss(menu, close, e => picker.contains(e.target));
      menu.querySelector('.active')?.focus();
    };
    const selectValue = (value) => {
      if (select.value !== value) {
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      refresh();
      close();
      button.focus();
    };
    const refresh = () => {
      const selected = select.options[select.selectedIndex] || select.options[0];
      if (!selected) return;
      const icon = _researchPickerIcon(id, selected.value);
      current.innerHTML = `${icon ? `<span class="research-picker-icon">${icon}</span>` : ''}<span class="research-picker-label">${_esc(selected.textContent)}</span>`;
      menu.innerHTML = Array.from(select.options).map(option => {
        const optionIcon = _researchPickerIcon(id, option.value);
        const active = option.value === select.value ? ' active' : '';
        const safeValue = _esc(option.value).replace(/"/g, '&quot;');
        return `<button type="button" class="research-picker-option${active}" role="option" aria-selected="${option.value === select.value}" data-value="${safeValue}">${optionIcon ? `<span class="research-picker-icon">${optionIcon}</span>` : ''}<span>${_esc(option.textContent)}</span><span class="research-picker-check">&#10003;</span></button>`;
      }).join('');
      menu.querySelectorAll('.research-picker-option').forEach(optionButton => {
        optionButton.addEventListener('click', e => {
          e.preventDefault();
          e.stopPropagation();
          selectValue(optionButton.dataset.value || '');
        });
        optionButton.addEventListener('keydown', e => {
          if (e.key === 'ArrowDown') { e.preventDefault(); focusItem(1); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); focusItem(-1); }
          else if (e.key === 'Home') { e.preventDefault(); menu.querySelector('.research-picker-option')?.focus(); }
          else if (e.key === 'End') { e.preventDefault(); Array.from(menu.querySelectorAll('.research-picker-option')).pop()?.focus(); }
          else if (e.key === 'Escape') { e.preventDefault(); close(); button.focus(); }
        });
      });
    };

    button.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      menu.hidden ? open() : close();
    });
    button.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        open();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        close();
      }
    });
    select.addEventListener('change', refresh);
    const controller = { close, refresh };
    _researchPickers.set(id, controller);
    refresh();
  });
}

function _researchVisualVariant(job) {
  // Color belongs to the report format, not to an individual report.
  return 0;
}

function _renderResult(job) {
  if (!job.result) return '<div class="research-job-loading">Loading result...</div>';
  const cat = job.category || '';
  const isStandard = !cat;
  const catIcon = _CAT_ICONS[cat] || (isStandard ? _STANDARD_CAT_ICON : '');
  const catLabel = _CAT_LABELS[cat] || (isStandard ? 'Research' : '');
  const heroVariant = isStandard ? ` research-hero-v${_researchVisualVariant(job)}` : '';

  let html = '';

  // Standard reports keep one stable identity; specialized formats use
  // their category palette.
  if (cat && catIcon) {
    html += `
      <div class="research-hero research-hero-${cat}">
        <span class="research-hero-icon">${catIcon}</span>
        <div class="research-hero-text">
          <div class="research-hero-label">${catLabel}</div>
          <div class="research-hero-query">${_esc(job.query)}</div>
        </div>
      </div>
    `;
  } else if (isStandard && catIcon) {
    html += `
      <div class="research-hero research-hero-standard${heroVariant}">
        <span class="research-hero-icon">${catIcon}</span>
        <div class="research-hero-text">
          <div class="research-hero-label">${catLabel}</div>
          <div class="research-hero-query">${_esc(job.query)}</div>
        </div>
      </div>
    `;
  }

  if (job.sources?.length) {
    html += '<div class="research-job-sources">';
    for (const s of job.sources.slice(0, 10)) {
      const title = _esc(s.title || s.url || '');
      const url = _safeSourceHref(s.url);
      const badges = _researchSourceBadges(s);
      html += url
        ? `<a href="${url}" target="_blank" rel="noopener" class="research-source-link">${title}${badges}</a>`
        : `<span class="research-source-link">${title}${badges}</span>`;
    }
    if (job.sources.length > 10) html += `<span class="research-source-more">+${job.sources.length - 10} more</span>`;
    html += '</div>';
  }
  if (job.source_state) {
    html += `<div class="research-source-state">${_esc(job.source_state)}</div>`;
  }
  if (Array.isArray(job.navigation_trace) && job.navigation_trace.length) {
    html += `<div class="research-navigation-trace">${_renderNavigationTrace(job.navigation_trace, job.action_trace)}</div>`;
  } else if (Array.isArray(job.action_trace) && job.action_trace.length) {
    html += `<div class="research-navigation-trace">${_renderActionTrace(job.action_trace)}</div>`;
  }

  const bodyCls = `research-job-report-body${cat ? ' research-body-' + cat : ''}`;
  if (_markdownModule) {
    html += `<div class="${bodyCls}">${_markdownModule.renderContent(job.result)}</div>`;
  } else {
    html += `<div class="${bodyCls}"><pre>${_esc(job.result)}</pre></div>`;
  }
  return html;
}

async function _ensureResult(job) {
  if (job.result) return;
  try {
    const res = await fetch(`${_apiBase}/api/research/result-peek/${job.id}`, {
      method: 'POST', credentials: 'same-origin',
    });
    if (!res.ok) return;
    const d = await res.json();
    job.result = d.result;
    job.sources = d.sources;
    job.findings = d.raw_findings;
    job.analyzed_urls = d.analyzed_urls;
    job.source_state = d.source_state;
    job.source_coverage = d.source_coverage || {};
    job.navigation_trace = d.navigation_trace;
    job.action_trace = d.action_trace;
  } catch {}
}

function _renderActionTrace(trace) {
  const rows = (Array.isArray(trace) ? trace : []).slice(-8).map((item) => {
    const round = Number.isFinite(Number(item.round)) ? `Round ${Number(item.round)}` : 'Plan';
    const tool = _esc(item.tool || 'tool');
    const target = _esc(item.query || item.url || '');
    const skipped = item.status === 'skipped';
    const label = skipped ? `${tool}: ${target}` : `${tool}: ${target}`;
    const requestedMeta = item.requested_by && item.requested_by !== item.tool
      ? `via ${_esc(item.requested_by)}`
      : '';
    const meta = skipped
      ? `<span class="research-nav-meta">skipped${item.reason ? ` · ${_esc(item.reason)}` : ''}</span>`
      : (item.source ? `<span class="research-nav-meta">${_esc(item.source)}${requestedMeta ? ` · ${requestedMeta}` : ''}</span>` : (requestedMeta ? `<span class="research-nav-meta">${requestedMeta}</span>` : ''));
    return `<div class="research-nav-row research-action-row${skipped ? ' research-action-skipped' : ''}"><span class="research-nav-tool">${round}</span><span class="research-nav-target">${label}</span>${meta}</div>`;
  }).join('');
  return rows;
}

function _renderNavigationTrace(trace, actionTrace = []) {
  const plan = _renderActionTrace(actionTrace);
  const navigation = Array.isArray(trace) ? trace : [];
  const rows = navigation.slice(-8).map((item) => {
    const tool = _esc(item.tool || 'tool');
    const status = _esc(item.status || 'unknown');
    const target = _esc(item.query || item.title || item.url || '');
    const meta = [];
    if (Number.isFinite(Number(item.results))) meta.push(`${Number(item.results)} results`);
    if (item.source_kind) meta.push(_esc(item.source_kind));
    if (Number.isFinite(Number(item.source_score))) meta.push(`${Number(item.source_score)}/100`);
    if (item.retrieval && item.retrieval !== 'fetch') meta.push(_esc(item.retrieval));
    const metaHtml = meta.length ? `<span class="research-nav-meta">${meta.join(' · ')}</span>` : '';
    return `<div class="research-nav-row"><span class="research-nav-tool">${tool}</span><span class="research-nav-target">${target}</span><span class="research-nav-status">${status}</span>${metaHtml}</div>`;
  }).join('');
  const navigationHtml = rows
    ? `<div class="research-nav-title">Navigation</div>${rows}`
    : '';
  return `${plan}${plan && navigationHtml ? '<div class="research-nav-spacer"></div>' : ''}${navigationHtml}`;
}

async function _copyResult(job, btn) {
  if (!job.result) return;
  let text = `# ${job.query}\n\n${job.result}`;
  if (job.findings?.length) {
    text += '\n\n---\n## Raw Findings\n';
    for (const f of job.findings) {
      text += `\n### ${f.title || 'Untitled'}\nSource: ${f.url || ''}\n${f.summary || ''}\n`;
    }
  }
  if (job.sources?.length) {
    const srcList = job.sources.map(s => `- [${s.title || s.url}](${s.url})`).join('\n');
    text += `\n\n---\n## Sources\n${srcList}`;
  }
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch {}
  if (!ok) {
    // Fallback for non-secure contexts (HTTP self-host) where navigator.clipboard
    // is unavailable. The textarea must be in-viewport and focusable for Firefox
    // Android / iOS Safari to allow execCommand('copy').
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.readOnly = false;
    ta.contentEditable = 'true';
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { ta.setSelectionRange(0, text.length); } catch {}
    try {
      const sel = window.getSelection();
      if (sel && (!sel.rangeCount || sel.isCollapsed)) {
        const range = document.createRange();
        range.selectNodeContents(ta);
        sel.removeAllRanges();
        sel.addRange(range);
        ta.setSelectionRange(0, text.length);
      }
    } catch {}
    try { ok = document.execCommand('copy'); } catch {}
    ta.remove();
  }
  if (btn) {
    const orig = btn.innerHTML;
    if (ok) {
      btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
      btn.classList.add('research-job-action-copied');
      setTimeout(() => { btn.innerHTML = orig; btn.classList.remove('research-job-action-copied'); }, 2000);
    } else {
      btn.innerHTML = `${_cancelIcon} Failed`;
      setTimeout(() => { btn.innerHTML = orig; }, 2000);
    }
  }
}

// ── Chat about this research (server-side spinoff) ──

async function _chatAboutResearch(researchId, btn) {
  if (!researchId) return;
  const origLabel = btn ? btn.innerHTML : '';
  let whirlpool = null;
  if (btn) {
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.innerHTML = '';
    try {
      whirlpool = spinnerModule.createWhirlpool(13);
      whirlpool.element.style.cssText += ';margin:0;';
      btn.appendChild(whirlpool.element);
    } catch {
      btn.innerHTML = origLabel;
    }
  }
  try {
    const res = await fetch(`${_apiBase}/api/research/spinoff/${researchId}`, {
      method: 'POST', credentials: 'same-origin',
    });
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).detail || ''; } catch {}
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const payload = await res.json();
    if (_sessionModule && _sessionModule.selectSession && payload.session_id) {
      if (_sessionModule.loadSessions) await _sessionModule.loadSessions().catch(() => {});
      await _sessionModule.selectSession(payload.session_id);
      closePanel();
    } else if (payload.session_id) {
      window.location.hash = '#' + payload.session_id;
      window.location.reload();
    } else {
      // 200 OK but no session_id — server contract violation. Don't leave
      // the button stuck on 'Creating…'; surface the failure instead.
      throw new Error('Server returned no session id');
    }
  } catch (e) {
    if (whirlpool) whirlpool.destroy();
    if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); btn.innerHTML = origLabel; }
    alert('Could not start follow-up chat: ' + e.message);
  }
}

function _esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function _safeSourceHref(raw) {
  try {
    const parsed = new URL(String(raw || '').trim(), window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return _esc(parsed.href);
  } catch {}
  return '';
}

function _researchSourceBadges(source) {
  const badges = [];
  const kind = String(source?.source_kind || '').trim();
  const retrieval = String(source?.retrieval || '').trim();
  const score = Number(source?.source_score);
  if (kind) badges.push(`<span class="research-source-badge">${_esc(kind)}</span>`);
  if (retrieval && retrieval !== 'fetch') badges.push(`<span class="research-source-badge">${_esc(retrieval)}</span>`);
  if (Number.isFinite(score)) badges.push(`<span class="research-source-badge">${Math.max(0, Math.min(100, Math.round(score)))}</span>`);
  return badges.length ? `<span class="research-source-badges">${badges.join('')}</span>` : '';
}
