// compare/index.js — orchestrator module (public API)
/**
 * Model A/B Comparison module.
 * Builds its own multi-pane grid layout (up to 8 models).
 * Sends same prompt to all models in parallel, lets user vote.
 *
 * Uses show/hide on the original container children instead of
 * innerHTML replacement, so event listeners on the input bar,
 * compare button, mode toggle, etc. are preserved.
 */

// ── Submodule imports ──
import state from './state.js';
import { EVAL_PROMPTS, WAVE_FRAMES,
  ICON_DICE, ICON_EXPAND, ICON_COLLAPSE, ICON_CLOSE,
  ICON_REROLL, ICON_COPY, ICON_PLAY, ICON_CODE,
  ICON_PARALLEL, ICON_SEQUENTIAL,
  EYE_OPEN, EYE_CLOSED, SAVE_ICON, CHAT_ICON,
  SEND_SVG, VOTES_STORAGE_KEY,
} from './icons.js';
import { fetchModels, _persistSelections, _modelDisplayNames, getExcludedModels, setExcludedModels } from './models.js';
import { showModelSelector, disableToolToggles, restoreToolToggles, _syncToolbarIndicator } from './selector.js';
import { _checkUnprobed, _clearProbeWaves } from './probe.js';
import { streamToPane, _renderSearchResults, _runSynthForPane, _formatMs, registerStreamActions } from './stream.js';
import {
  stopAll, stopPane, rerollPane, shufflePanePositions, resetCompare,
  _addPane, _removePane, toggleExpandPane, togglePanePreview, copyPaneResponse,
  _showModelSwapDropdown, _createAndAppendPane, _autoPreviewHtml,
  registerPaneActions,
} from './panes.js';
import { handleVote, buildVoteBar, addFinishBadge, spawnConfetti, _saveVote, registerCompareActions } from './vote.js';
import { showScoreboard } from './scoreboard.js';

// ── External dependency imports ──
import Storage from '../storage.js';
import uiModule from '../ui.js';
import sessionModule from '../sessions.js';
import spinnerModule from '../spinner.js';
import themeModule from '../theme.js';
import presetsModule from '../presets.js';
import markdownModule from '../markdown.js';

var escapeHtml = uiModule.esc;

/** Slot label: letters (A, B) in parallel, numbers (1, 2) in sequential */
function _slotChar(i) { return state._parallel ? String.fromCharCode(65 + i) : String(i + 1); }

// ────────────────────────────────────────────────────────────────────────────
// ── Toolbar indicator sync ──
// ────────────────────────────────────────────────────────────────────────────
// ── init ──
// ────────────────────────────────────────────────────────────────────────────

function init(apiBase) {
  state.API_BASE = apiBase;
  // Clean up unsaved compare sessions on page close/refresh
  window.addEventListener('beforeunload', () => {
    if (!state._saveOnClose && state._paneSessionIds.length > 0) {
      // sendBeacon uses POST — use the bulk delete endpoint
      navigator.sendBeacon(
        `${state.API_BASE}/api/sessions/bulk-delete`,
        new Blob([JSON.stringify({ ids: state._paneSessionIds })], { type: 'application/json' })
      );
    }
  });
}

// ────────────────────────────────────────────────────────────────────────────
// ── isCompareActive ──
// ────────────────────────────────────────────────────────────────────────────

function isCompareActive() {
  return state.isActive;
}

// ────────────────────────────────────────────────────────────────────────────
// ── closeCompare ──
// ────────────────────────────────────────────────────────────────────────────

/** Close compare mode (public API for toolbar indicator). */
function closeCompare() {
  if (state.isActive) deactivate(true);
}

// ────────────────────────────────────────────────────────────────────────────
// ── toggleMode ──
// ────────────────────────────────────────────────────────────────────────────

/** Toggle compare mode — shows model selector, then builds UI. */
async function toggleMode() {
  if (state.isActive) {
    deactivate(true);
    return false;
  }
  if (state._openingSelector) return false;

  state._openingSelector = true;
  try {
    const confirmed = await showModelSelector();
    if (!confirmed) return false;

    state.isActive = true;
    _syncToolbarIndicator(true);
    await _buildCompareUI();
    return true;
  } catch (err) {
    console.error('Compare toggleMode error:', err);
    return false;
  } finally {
    state._openingSelector = false;
  }
}

// ────────────────────────────────────────────────────────────────────────────
// ── deactivate ──
// ────────────────────────────────────────────────────────────────────────────

async function deactivate(teardown) {
  // Abort any in-flight streams
  state._abortControllers.forEach(ac => { if (ac) ac.abort(); });
  state._abortControllers = [];

  // Move sessions to compare folder if saving
  if (state._saveOnClose && state._paneSessionIds.length > 0) {
    const modelShorts = _modelDisplayNames(state._selectedModels);
    const folderName = 'Compare: ' + modelShorts.join(' vs ');
    await Promise.all(state._paneSessionIds.map(sid =>
      fetch(`${state.API_BASE}/api/session/${sid}`, {
        method: 'PATCH', body: new URLSearchParams({ folder: folderName })
      }).catch(() => {})
    ));
  }

  // Capture session IDs to delete before resetting state
  const sessionIdsToDelete = (!state._saveOnClose && teardown && state._paneSessionIds.length > 0)
    ? [...state._paneSessionIds] : [];

  removeOverlays();
  state.isActive = false;
  state._streaming = false;
  state._paneSessionIds = [];
  state._paneMetrics = [];
  state._finishOrder = 0;
  state._paneElapsed = [];
  state._saveOnClose = false;
  state._continueChat = false;
  state._probed.clear();
  state._expectedAnswer = '';
  _syncToolbarIndicator(false);

  // Restore main textarea placeholder
  const msgTA = document.getElementById('message');
  if (msgTA) msgTA.placeholder = '';

  // Restore toolbar indicator display states and pointer events
  Object.entries(state._savedIndicatorDisplay).forEach(([id, display]) => {
    const el = document.getElementById(id);
    if (el) { el.style.display = display; el.style.pointerEvents = ''; }
  });
  state._savedIndicatorDisplay = {};

  // Unlock mode toggle
  const _modeToggleR = document.querySelector('.mode-toggle');
  if (_modeToggleR) { _modeToggleR.style.pointerEvents = ''; _modeToggleR.style.opacity = ''; }

  // Restore tool toggle pointer events
  ['overflow-plus-btn', 'web-toggle-btn', 'bash-toggle-btn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.pointerEvents = '';
  });

  // Restore agent/chat mode to what it was before compare
  const _ts = Storage.loadToggleState();
  _ts.mode = state._savedMode;
  Storage.saveToggleState(_ts);
  const _ab2 = document.getElementById('mode-agent-btn'), _cb2 = document.getElementById('mode-chat-btn');
  if (_ab2 && _cb2) { _ab2.classList.toggle('active', state._savedMode === 'agent'); _cb2.classList.toggle('active', state._savedMode === 'chat'); }
  document.querySelectorAll('[data-mode-tool]').forEach(b => { b.style.display = state._savedMode === 'agent' ? '' : 'none'; });

  // Delete unsaved sessions, then reload
  if (teardown) {
    if (sessionIdsToDelete.length > 0) {
      // keepalive ensures requests complete even during page navigation
      await Promise.all(sessionIdsToDelete.map(sid =>
        fetch(`${state.API_BASE}/api/session/${sid}`, { method: 'DELETE', keepalive: true }).catch(() => {})
      ));
    }
    location.href = location.pathname;
  }
}

// ────────────────────────────────────────────────────────────────────────────
// ── _buildCompareUI ──
// ────────────────────────────────────────────────────────────────────────────

/** Build the compare UI: sessions, header bar, grid of panes, vote bar, eval dropdown. */
async function _buildCompareUI() {
  if (state._selectedModels.length < 1) {
    if (uiModule) uiModule.showError('Select at least 1 model');
    return;
  }

  const n = state._selectedModels.length;
  const modelShorts = _modelDisplayNames(state._selectedModels);
  _persistSelections();

  // 1. Create sessions (skip for search mode — no LLM sessions needed)
  if (state._compareMode !== 'search') {
    const sessionIds = [];
    for (let i = 0; i < n; i++) {
      const m = state._selectedModels[i];
      const fd = new FormData();
      // Blind mode: name the session by its neutral slot so the sidebar /
      // GET /api/sessions can't de-anonymize the comparison (issue #1285).
      fd.append('name', '[CMP] ' + (state._blindMode ? 'Model ' + _slotChar(i) : modelShorts[i]));
      fd.append('endpoint_url', m.endpoint || '');
      fd.append('model', m.model || '');
      if (m.endpointId) {
        fd.append('endpoint_id', m.endpointId);
        fd.append('skip_validation', 'true');
      }
      const res = await fetch(`${state.API_BASE}/api/session`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error('Failed to create session for ' + modelShorts[i]);
      const data = await res.json();
      sessionIds.push(data.id);
    }
    state._paneSessionIds = sessionIds;
  } else {
    state._paneSessionIds = [];
  }
  state._paneMetrics = state._selectedModels.map(() => null);
  state._abortControllers = state._selectedModels.map(() => null);

  // 2. Auto-collapse sidebar if many panes
  if (n > 3) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar && !sidebar.classList.contains('hidden')) {
      sidebar.classList.add('hidden');
      state._sidebarWasHidden = true;
      const iconRail = document.getElementById('icon-rail');
      if (iconRail) iconRail.classList.remove('rail-hidden');
      if (typeof window.syncRailSide === 'function') window.syncRailSide();
    }
  }

  // 3. Hide mobile new-chat button during compare
  const _mobileNewBtn = document.getElementById('mobile-new-chat-btn');
  if (_mobileNewBtn) {
    _mobileNewBtn.dataset.cmpWasDisplay = _mobileNewBtn.style.display;
    _mobileNewBtn.style.display = 'none';
  }

  // 4. Save toolbar indicator display states before hiding
  const indicatorIds = ['overflow-tts-btn', 'overflow-attach-btn', 'overflow-rag-btn', 'overflow-research-btn', 'overflow-doc-btn', 'rag-indicator-btn', 'research-toggle-btn'];
  state._savedIndicatorDisplay = {};
  indicatorIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) state._savedIndicatorDisplay[id] = el.style.display;
  });

  // 5. Save current mode and lock to the right one for this compare type
  const _toggleState = Storage.loadToggleState();
  state._savedMode = _toggleState.mode || 'chat';
  const _targetMode = (state._compareMode === 'agent') ? 'agent' : 'chat';
  _toggleState.mode = _targetMode;
  Storage.saveToggleState(_toggleState);
  const _ab = document.getElementById('mode-agent-btn'), _cb = document.getElementById('mode-chat-btn');
  if (_ab && _cb) {
    _ab.classList.toggle('active', _targetMode === 'agent');
    _cb.classList.toggle('active', _targetMode === 'chat');
  }
  const _modeToggle = document.querySelector('.mode-toggle');
  if (_modeToggle) { _modeToggle.style.pointerEvents = 'none'; _modeToggle.style.opacity = '0.4'; }

  // 6. Force tool toggles per compare mode
  disableToolToggles();
  if (state._compareMode === 'search') {
    const webChk = document.getElementById('web-toggle');
    if (webChk && !webChk.checked) { webChk.checked = true; webChk.dispatchEvent(new Event('change')); }
    const webBtn = document.getElementById('web-toggle-btn');
    if (webBtn) webBtn.classList.add('active');
  } else if (state._compareMode === 'research') {
    const resChk = document.getElementById('research-toggle');
    if (resChk && !resChk.checked) { resChk.checked = true; resChk.dispatchEvent(new Event('change')); }
    const resBtn = document.getElementById('research-toggle-btn');
    if (resBtn) { resBtn.style.display = ''; resBtn.classList.add('active'); }
  }

  // 7. Hide existing chat container children (preserves event listeners)
  const container = document.getElementById('chat-container');
  state._compareElements = [];
  Array.from(container.children).forEach(child => {
    if (child.style.display === 'none') return;
    child.dataset.cmpHidden = '1';
    child.style.display = 'none';
  });
  container.classList.add('compare-active');

  // 8. Header bar
  const cols = Math.min(n, 4);
  const headerBar = document.createElement('div');
  headerBar.className = 'compare-header-bar';
  headerBar.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:6px 10px;flex-shrink:0;';
  const headerLabel = document.createElement('span');
  headerLabel.style.cssText = 'font-size:10px;font-weight:400;color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;';
  const _modeLabel = ({ search: ' search providers', agent: ' agents', research: ' research models' }[state._compareMode] || ' models');
  headerLabel.textContent = 'Comparing' + _modeLabel + (state._blindMode ? ' (blind)' : '') + ' · ' + state._timeout + 's timeout';
  // Left side: the Compare tool icon (two side-by-side panes, matching the
  // rail/sidebar icon) + the label. Other tool headers carry their icon; this
  // one was missing it.
  const headerLeft = document.createElement('div');
  headerLeft.style.cssText = 'display:flex;align-items:center;min-width:0;';
  const headerIcon = document.createElement('span');
  headerIcon.style.cssText = 'display:inline-flex;flex-shrink:0;margin-right:6px;opacity:0.85;';
  headerIcon.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="8" height="18" rx="1"/><rect x="14" y="3" width="8" height="18" rx="1"/></svg>';
  headerLeft.appendChild(headerIcon);
  headerLeft.appendChild(headerLabel);
  headerBar.appendChild(headerLeft);

  const headerActions = document.createElement('div');
  headerActions.style.cssText = 'display:flex;align-items:center;gap:2px;';

  const _btnCSS = 'background:none;border:1px solid var(--border);color:var(--fg);cursor:pointer;padding:3px 10px;font-size:11px;font-weight:600;opacity:0.7;transition:all 0.15s;line-height:1;border-radius:4px;display:inline-flex;align-items:center;font-family:inherit;';

  const checkBtn = document.createElement('button');
  checkBtn.id = 'compare-check-btn';
  checkBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M20 6L9 17l-5-5"/></svg><span style="font-size:11px;margin-left:3px;">Probe</span>';
  checkBtn.title = 'Probe unverified models with a small test request';
  checkBtn.style.cssText = _btnCSS;
  checkBtn.addEventListener('click', () => _checkUnprobed());
  headerActions.appendChild(checkBtn);

  // Check button is dynamic: only visible when at least one selected model
  // hasn't been probed yet. Show right after add/change, hide after success.
  window._updateCheckBtnState = function() {
    const btn = document.getElementById('compare-check-btn');
    if (!btn) return;
    const hasUnprobed = state._selectedModels.some(m => !state._probed.has(m.model));
    btn.style.display = hasUnprobed ? '' : 'none';
  };

  // (Scoreboard button moved into the vote bar, next to Tie — see vote.js.)

  const exportWrap = document.createElement('div');
  exportWrap.style.cssText = 'position:relative;display:inline-flex;';
  const exportBtn = document.createElement('button');
  exportBtn.id = 'compare-export-btn';
  exportBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span style="font-size:11px;margin-left:3px;">Export</span>';
  exportBtn.title = 'Export options';
  exportBtn.style.cssText = _btnCSS;
  exportBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    _toggleExportMenu(exportBtn);
  });
  exportWrap.appendChild(exportBtn);
  headerActions.appendChild(exportWrap);

  const shuffleBtn = document.createElement('button');
  shuffleBtn.id = 'compare-shuffle-btn';
  shuffleBtn.innerHTML = ICON_DICE + '<span style="font-size:11px;margin-left:3px;">Shuffle</span>';
  shuffleBtn.title = 'Shuffle pane positions';
  shuffleBtn.style.cssText = _btnCSS;
  shuffleBtn.addEventListener('click', () => shufflePanePositions());
  headerActions.appendChild(shuffleBtn);

  const addBtn = document.createElement('button');
  addBtn.id = 'compare-add-btn';
  addBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg><span style="font-size:11px;margin-left:3px;">Add</span>';
  addBtn.title = 'Add model pane';
  addBtn.style.cssText = _btnCSS;
  addBtn.addEventListener('click', () => _addPane(addBtn));
  headerActions.appendChild(addBtn);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'compare-close-btn';
  closeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  closeBtn.title = 'Close compare mode';
  // Match Export/Score/Shuffle/Model styling so the X sits flush with
  // the rest of the toolbar instead of being a 24×24 bordered square.
  closeBtn.style.cssText = _btnCSS;
  closeBtn.addEventListener('click', () => deactivate(true));
  headerActions.appendChild(closeBtn);

  // Move Export to the far left of the action cluster (per user preference).
  headerActions.insertBefore(exportWrap, headerActions.firstChild);

  headerBar.appendChild(headerActions);
  container.appendChild(headerBar);
  state._compareElements.push(headerBar);

  // Initial visibility — hidden if all current models are already probed
  window._updateCheckBtnState();

  // 9. Grid of panes
  const grid = document.createElement('div');
  grid.className = 'compare-grid';
  grid.dataset.cols = String(cols);
  for (let i = 0; i < n; i++) {
    const label = state._blindMode ? 'Model ' + _slotChar(i) : modelShorts[i];
    const pane = document.createElement('div');
    pane.className = 'compare-pane';
    pane.dataset.pane = String(i);
    pane.innerHTML =
      '<div class="pane-header">' +
        '<button class="pane-title pane-title-btn" id="cmp-title-' + i + '" data-pane="' + i + '" type="button">' + escapeHtml(label) + ' <span class="pane-title-caret">&#x25BE;</span></button>' +
        '<span class="pane-timer" id="cmp-timer-' + i + '"></span>' +
        '<span class="pane-finish-badge" id="cmp-badge-' + i + '"></span>' +
        '<div class="pane-actions">' +
          '<button class="pane-action-btn pane-stop-btn" data-action="stop" data-pane="' + i + '" title="Stop" style="display:none;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>' +
          '<button class="pane-action-btn pane-preview-btn" data-action="preview" data-pane="' + i + '" id="cmp-preview-' + i + '" title="Run preview" style="display:none;">' + ICON_PLAY + '</button>' +
          '<button class="pane-action-btn" data-action="reroll" data-pane="' + i + '" title="Re-roll">' + ICON_REROLL + '</button>' +
          '<button class="pane-action-btn" data-action="copy" data-pane="' + i + '" title="Copy">' + ICON_COPY + '</button>' +
          '<button class="pane-action-btn" data-action="expand" data-pane="' + i + '" title="Expand">' + ICON_EXPAND + '</button>' +
          '<button class="pane-action-btn pane-close-btn" data-action="close" data-pane="' + i + '" title="Remove pane">' + ICON_CLOSE + '</button>' +
        '</div>' +
      '</div>' +
      '<div class="chat-history" id="cmp-history-' + i + '"></div>' +
      '<iframe class="compare-pane-iframe" id="cmp-iframe-' + i + '" sandbox="allow-scripts" style="display:none;"></iframe>' +
      '<div class="pane-vote-footer">' +
        '<button class="pane-vote-btn" data-pane="' + i + '" type="button" disabled style="opacity:0.4;">' +
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;vertical-align:-2px;"><polyline points="20 6 9 17 4 12"/></svg>' +
          '<span class="pane-vote-label">Vote ' + escapeHtml(label) + '</span>' +
        '</button>' +
      '</div>';
    grid.appendChild(pane);
  }
  grid.addEventListener('click', (e) => {
    const voteBtn = e.target.closest('.pane-vote-btn');
    if (voteBtn) {
      e.stopPropagation();
      if (voteBtn.disabled) return;
      const idx = parseInt(voteBtn.dataset.pane);
      handleVote(idx);
      return;
    }
    const actionBtn = e.target.closest('.pane-action-btn');
    if (actionBtn) {
      e.stopPropagation();
      const action = actionBtn.dataset.action;
      const idx = parseInt(actionBtn.dataset.pane);
      if (action === 'stop') stopPane(idx);
      else if (action === 'copy') copyPaneResponse(idx);
      else if (action === 'reroll') rerollPane(idx);
      else if (action === 'expand') toggleExpandPane(idx, actionBtn);
      else if (action === 'preview') togglePanePreview(idx);
      else if (action === 'close') _removePane(idx);
      return;
    }
    const titleBtn = e.target.closest('.pane-title-btn');
    if (titleBtn) {
      e.stopPropagation();
      const idx = parseInt(titleBtn.dataset.pane);
      _showModelSwapDropdown(idx, titleBtn);
    }
  });
  container.appendChild(grid);
  state._compareElements.push(grid);

  // 10. Vote bar placeholder
  const voteBar = document.createElement('div');
  voteBar.id = 'compare-vote-bar';
  voteBar.className = 'compare-vote-bar';
  container.appendChild(voteBar);
  state._compareElements.push(voteBar);
  buildVoteBar(n);

  if (state._blindMode && n > 1) shufflePanePositions();

  // 11. Move chat input bar to the bottom of the container
  const inputBar = document.querySelector('.chat-input-bar');
  if (inputBar) {
    inputBar.style.display = '';
    if (inputBar.dataset.cmpHidden) delete inputBar.dataset.cmpHidden;
    container.appendChild(inputBar);
  }
  const msgTA = document.getElementById('message');
  if (msgTA) {
    msgTA.placeholder = 'Enter prompt for all models...';
    requestAnimationFrame(() => msgTA.focus());
  }

  // Eval-prompts picker — sits inside the message box at top-right (where
  // model-picker normally lives). Model-picker is irrelevant during compare,
  // so hide it and restore on deactivate via the wrap's _cleanup.
  _setupEvalPicker();

  // 12. Hide tool buttons that don't apply during compare
  ['overflow-tts-btn', 'overflow-attach-btn', 'overflow-rag-btn', 'overflow-research-btn', 'overflow-doc-btn', 'rag-indicator-btn', 'web-toggle-btn', 'bash-toggle-btn', 'overflow-plus-btn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.style.display = 'none'; el.style.pointerEvents = 'none'; }
  });
  if (state._compareMode !== 'research') {
    const resBtn = document.getElementById('research-toggle-btn');
    if (resBtn) { resBtn.style.display = 'none'; resBtn.style.pointerEvents = 'none'; }
  }
  document.querySelectorAll('[data-mode-tool]').forEach(b => { b.style.display = 'none'; });

  _setSendBtn('send');
}

// ────────────────────────────────────────────────────────────────────────────
// ── _setSendBtn ──
// ────────────────────────────────────────────────────────────────────────────

function _setSendBtn(mode) {
  const btn = document.querySelector('.send-btn');
  if (!btn) return;
  if (mode === 'stop') {
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    btn.title = 'Stop all models';
    btn.dataset.mode = 'streaming';
    btn.classList.remove('mic-mode', 'newchat-mode');
  } else {
    btn.dataset.mode = '';
    btn.innerHTML = SEND_SVG;
    btn.style.color = '';
    btn.title = 'Send to all models';
    btn.classList.remove('mic-mode', 'newchat-mode', 'newchat-expanded');
  }
}

// ────────────────────────────────────────────────────────────────────────────
// ── handleCompareSubmit ──
// ────────────────────────────────────────────────────────────────────────────

/**
 * Handle submit from the main chat input while compare is active.
 * Called by app.js submit guard.
 */
function handleCompareSubmit(e) {
  // If streaming, act as stop button
  if (state._streaming) {
    stopAll();
    return;
  }
  const input = document.getElementById('message');
  const message = input ? input.value.trim() : '';
  if (!message) return;
  input.value = '';
  // Reset textarea height
  input.style.height = '';
  // Notify input listeners (eval-picker visibility, autosize, etc.) that the
  // textarea is empty again — programmatic clears don't fire `input` natively.
  input.dispatchEvent(new Event('input', { bubbles: true }));
  // Mobile: dismiss the on-screen keyboard after the prompt is sent so the
  // user sees the streaming output instead of the typing area. A plain blur()
  // is often ignored on Firefox mobile, so toggle readonly around it (and blur
  // the active element too) to reliably collapse the keyboard.
  // Mobile keyboard dismiss — use the SAME proven logic as the main chat send
  // (chat.js handleChatSubmit). Compare returns early in that flow, so it never
  // reached this code; replicating it here is what actually works on Firefox
  // mobile (readonly + blur, then drop readonly only once the blur is confirmed
  // or the user taps to type again — avoids the keyboard bouncing back up).
  if (window.innerWidth <= 768) {
    try {
      input.setAttribute('readonly', 'readonly');
      input.blur();
      // Setting readonly on an ALREADY-FOCUSED textarea doesn't dismiss the
      // keyboard on Firefox, and blur() is often ignored — so the readonly-only
      // approach works only when the input happened not to be focused at send
      // time (inconsistent between 1st/2nd prompt). Deterministically pull focus
      // off the textarea by focusing a throwaway readonly input, then drop it.
      const tmp = document.createElement('input');
      tmp.setAttribute('readonly', 'readonly');
      tmp.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;border:0;padding:0;';
      document.body.appendChild(tmp);
      tmp.focus();
      setTimeout(() => { try { tmp.blur(); tmp.remove(); } catch {} }, 50);
      const _dropReadonly = () => { try { input.removeAttribute('readonly'); } catch {} };
      setTimeout(() => {
        if (document.activeElement === input) {
          input.addEventListener('pointerdown', _dropReadonly, { once: true });
          input.addEventListener('focus', _dropReadonly, { once: true });
        } else {
          _dropReadonly();
        }
      }, 120);
    } catch {}
  }
  _executeCompare(message);
}

// ────────────────────────────────────────────────────────────────────────────
// ── _executeCompare ──
// ────────────────────────────────────────────────────────────────────────────

/**
 * Send prompt to all panes, stream responses.
 * Works for both first and follow-up messages.
 */
async function _executeCompare(message) {
  if (state._streaming) return;
  if (state._selectedModels.length < 1) return;

  // New round — allow voting again and clear the previous round's win/lose/tie
  // styling (pane highlight + the Winner!/= title decorations), otherwise the
  // old result stays stuck on the panes through the next prompt.
  state._voted = false;
  for (let i = 0; i < state._selectedModels.length; i++) {
    const pane = document.querySelector('.compare-pane[data-pane="' + i + '"]');
    if (pane) {
      pane.classList.remove('winner', 'loser');
      // Clear the previous round's Failed/Timeout badge and the eval ✓/✗ grade.
      pane.querySelector('.pane-grade-badge')?.remove();
    }
    const fb = document.getElementById('cmp-badge-' + i);
    if (fb) { fb.textContent = ''; fb.style.color = ''; }
    const titleEl = document.getElementById('cmp-title-' + i);
    if (titleEl) {
      const label = state._blindMode
        ? 'Model ' + _slotChar(i)
        : ((state._selectedModels[i] && state._selectedModels[i].name) || 'Model ' + _slotChar(i));
      titleEl.innerHTML = escapeHtml(label) + ' <span class="pane-title-caret">&#x25BE;</span>';
    }
  }

  state._streaming = true;
  state._lastPrompt = message;
  _setSendBtn('stop');
  // Disable header buttons during streaming
  document.querySelectorAll('#compare-shuffle-btn, #compare-check-btn, #compare-add-btn').forEach(b => {
    b.disabled = true; b.style.opacity = '0.25'; b.style.pointerEvents = 'none';
  });

  // ── Search mode: direct API calls, no SSE streaming ──
  if (state._compareMode === 'search') {
    try {
      const n = state._selectedModels.length;

      // Clear previous vote buttons on follow-up
      const voteBar = document.getElementById('compare-vote-bar');
      if (voteBar) voteBar.innerHTML = '';

      // Add user query + spinner to each pane
      for (let i = 0; i < n; i++) {
        const hist = document.getElementById('cmp-history-' + i);
        if (!hist) continue;
        const userMsg = document.createElement('div');
        userMsg.className = 'msg msg-user';
        userMsg.innerHTML = '<div class="role">You</div><div class="body"></div>';
        userMsg.querySelector('.body').textContent = message;
        hist.appendChild(userMsg);

        const aiMsg = document.createElement('div');
        aiMsg.className = 'msg msg-ai';
        aiMsg.innerHTML = '<div class="role">Search</div><div class="body"></div>';
        const aiBody = aiMsg.querySelector('.body');
        if (spinnerModule) {
          const spinner = spinnerModule.create('Searching...', 'right');
          aiBody.appendChild(spinner.createElement());
          spinner.start();
        }
        hist.appendChild(aiMsg);
        hist.scrollTop = hist.scrollHeight;
      }

      // Fire searches — parallel or sequential based on _parallel setting
      const t0 = performance.now();
      state._abortControllers = state._selectedModels.map(() => new AbortController());

      async function _searchOne(m, i) {
        const fd = new FormData();
        fd.append('query', message);
        fd.append('provider', m.model);
        fd.append('count', '10');
        try {
          const res = await fetch(`${state.API_BASE}/api/search/query`, { method: 'POST', body: fd, signal: state._abortControllers[i].signal });
          const data = await res.json();
          return { idx: i, data };
        } catch (err) {
          return { idx: i, data: { results: [], error: err.name === 'AbortError' ? 'Stopped' : err.message } };
        }
      }

      let results;
      const _seqSynthDone = new Set();
      if (state._parallel) {
        results = await Promise.all(state._selectedModels.map((m, i) => _searchOne(m, i)));
      } else {
        // Sequential — run one at a time, dim waiting panes
        results = [];
        const panes = document.querySelectorAll('.compare-pane');
        panes.forEach((p, i) => { if (i > 0) p.style.opacity = '0.4'; });
        for (let i = 0; i < state._selectedModels.length; i++) {
          const pane = panes[i];
          if (pane) pane.style.opacity = '1';
          results.push(await _searchOne(state._selectedModels[i], i));
          // Render this result immediately
          const { idx, data } = results[results.length - 1];
          const hist = document.getElementById('cmp-history-' + idx);
          if (hist) {
            const aiMsg = hist.querySelector('.msg-ai:last-child');
            if (aiMsg) {
              const aiBody = aiMsg.querySelector('.body');
              aiBody.innerHTML = '';
              if (data.error) {
                aiBody.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;">Error: ' + escapeHtml(data.error) + '</div>';
              } else if (!data.results || data.results.length === 0) {
                aiBody.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 50%, transparent);font-size:0.85em;font-style:italic;">No results found</div>';
              } else {
                aiBody.appendChild(_renderSearchResults(data));
              }
              const footer = document.createElement('div'); footer.className = 'msg-footer';
              const span = document.createElement('span'); span.className = 'response-metrics';
              const parts = [];
              if (data.results) parts.push(data.results.length + ' results');
              if (data.time) parts.push(data.time + 's');
              span.textContent = parts.join(' | '); footer.appendChild(span); aiMsg.appendChild(footer);
              hist.scrollTop = hist.scrollHeight;
              const _pe = document.querySelector(`.compare-pane[data-pane="${idx}"]`);
              if (_pe) _pe.querySelectorAll('.pane-needs-response').forEach(b => b.style.display = '');
            }
          }
          // Sequential: run synthesis for this pane immediately before moving to next
          _seqSynthDone.add(idx);
          if (!data.error && data.results && data.results.length > 0) {
            const modelToUse = state._searchSynthModels?.[idx] || null;
            if (modelToUse) {
              const seqHist = document.getElementById('cmp-history-' + idx);
              if (seqHist) {
                const synthMsg = document.createElement('div');
                synthMsg.className = 'msg msg-ai';
                synthMsg.innerHTML = '<div class="role">Analysis</div><div class="body"></div>';
                const synthBody = synthMsg.querySelector('.body');
                let spinner = null;
                if (spinnerModule) { spinner = spinnerModule.create('Analyzing...', 'right'); synthBody.appendChild(spinner.createElement()); spinner.start(); }
                seqHist.appendChild(synthMsg);
                seqHist.scrollTop = seqHist.scrollHeight;
                const resultsText = data.results.map((r, ri) => `[${ri + 1}] ${r.title}\n${r.snippet || ''}\nURL: ${r.url}`).join('\n\n');
                const synthPrompt = `Analyze these search results for the query "${message}". Summarize the key findings, note any consensus or conflicting information, and provide a brief synthesis.\n\nSearch Results:\n${resultsText}`;
                await _runSynthForPane(modelToUse, synthPrompt, synthBody, spinner, seqHist);
              }
            }
          }
        }
        // Reset opacity
        panes.forEach(p => { p.style.opacity = ''; });
      }
      // Render results into each pane
      for (const { idx, data } of results) {
        const hist = document.getElementById('cmp-history-' + idx);
        if (!hist) continue;
        const aiMsg = hist.querySelector('.msg-ai:last-child');
        if (!aiMsg) continue;
        const aiBody = aiMsg.querySelector('.body');
        aiBody.innerHTML = '';

        if (data.error) {
          aiBody.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;">Error: ' + escapeHtml(data.error) + '</div>';
        } else if (!data.results || data.results.length === 0) {
          aiBody.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 50%, transparent);font-size:0.85em;font-style:italic;">No results found</div>';
        } else {
          aiBody.appendChild(_renderSearchResults(data));
        }

        // Footer metrics
        const footer = document.createElement('div');
        footer.className = 'msg-footer';
        const span = document.createElement('span');
        span.className = 'response-metrics';
        const parts = [];
        if (data.results) parts.push(data.results.length + ' results');
        if (data.time) parts.push(data.time + 's');
        span.textContent = parts.join(' | ');
        footer.appendChild(span);
        aiMsg.appendChild(footer);

        hist.scrollTop = hist.scrollHeight;
        // Show reroll/copy buttons for search results
        const _paneEl = document.querySelector(`.compare-pane[data-pane="${idx}"]`);
        if (_paneEl) _paneEl.querySelectorAll('.pane-needs-response').forEach(b => b.style.display = '');
      }

      // ── Synthesis: send results to LLM for analysis (respects _parallel setting) ──
      if (state._searchSynthModels) {
        // Build list of synthesis tasks
        const synthTasks = [];
        for (let i = 0; i < results.length; i++) {
          const { idx, data } = results[i];
          // Skip panes already synthesized in sequential mode
          if (_seqSynthDone.has(idx)) continue;
          if (data.error || !data.results || data.results.length === 0) continue;

          const modelToUse = state._searchSynthModels?.[idx] || null;
          if (!modelToUse) continue;

          const hist = document.getElementById('cmp-history-' + idx);
          if (!hist) continue;

          // Add synthesis message with spinner
          const synthMsg = document.createElement('div');
          synthMsg.className = 'msg msg-ai';
          synthMsg.innerHTML = '<div class="role">Analysis</div><div class="body"></div>';
          const synthBody = synthMsg.querySelector('.body');
          let spinner = null;
          if (spinnerModule) {
            spinner = spinnerModule.create('Analyzing...', 'right');
            synthBody.appendChild(spinner.createElement());
            spinner.start();
          }
          hist.appendChild(synthMsg);
          // Auto-scroll to show the Analysis message
          hist.scrollTop = hist.scrollHeight;

          // Build synthesis prompt
          const resultsText = data.results.map((r, ri) =>
            `[${ri + 1}] ${r.title}\n${r.snippet || ''}\nURL: ${r.url}`
          ).join('\n\n');

          const synthPrompt = `Analyze these search results for the query "${message}". Summarize the key findings, note any consensus or conflicting information, and provide a brief synthesis.\n\nSearch Results:\n${resultsText}`;

          synthTasks.push({ idx, modelToUse, synthBody, synthMsg, spinner, hist, synthPrompt });
        }

        // Run synthesis streams (parallel or sequential based on _parallel flag)
        const runSynthesis = async (task) => _runSynthForPane(task.modelToUse, task.synthPrompt, task.synthBody, task.spinner, task.hist);

        if (state._parallel) {
          await Promise.all(synthTasks.map(runSynthesis));
        } else {
          for (const task of synthTasks) {
            await runSynthesis(task);
          }
        }
      }

      buildVoteBar(n);
    } catch (err) {
      console.error('Search compare error:', err);
      if (uiModule) uiModule.showError('Search compare failed: ' + err.message);
    } finally {
      state._streaming = false;
      _setSendBtn('send');
    }
    return;
  }

  // ── Chat / Image mode ──
  const isFollowUp = document.getElementById('cmp-history-0')?.querySelector('.msg-ai');

  try {
    const n = state._selectedModels.length;

    if (isFollowUp) {
      const voteBar = document.getElementById('compare-vote-bar');
      if (voteBar) {
        voteBar.innerHTML = '';
        voteBar.classList.add('hidden');
      }
    }

    // ── Add user + AI bubbles to each pane ──
    const aiElements = [];
    for (let i = 0; i < n; i++) {
      const hist = document.getElementById('cmp-history-' + i);
      if (!hist) { aiElements.push(null); continue; }

      const userMsg = document.createElement('div');
      userMsg.className = 'msg msg-user';
      userMsg.innerHTML = '<div class="role">You</div><div class="body"></div>';
      userMsg.querySelector('.body').textContent = message;
      hist.appendChild(userMsg);

      const aiMsg = document.createElement('div');
      aiMsg.className = 'msg msg-ai';
      aiMsg.innerHTML = '<div class="role">AI</div><div class="body"></div>';
      const aiBody = aiMsg.querySelector('.body');
      if (spinnerModule) {
        // In sequential mode, only first pane says "Processing", rest say "Waiting"
        const label = (!state._parallel && i > 0)
          ? 'Waiting for Model ' + _slotChar(i - 1) + '...'
          : 'Processing...';
        const spinner = spinnerModule.create(label, 'right');
        aiBody.appendChild(spinner.createElement());
        spinner.start();
        aiMsg._spinner = spinner;
      }
      hist.appendChild(aiMsg);
      hist.scrollTop = hist.scrollHeight;
      aiElements.push(aiMsg);
    }

    // ── Auto-extend timeout ──
    const researchChk = document.getElementById('research-toggle');
    const webChkT = document.getElementById('web-toggle');
    const noTimeLimit = state._compareMode === 'research' || (researchChk && researchChk.checked);
    const needsLongTimeout = state._compareMode === 'agent' || (webChkT && webChkT.checked);
    const runTimeout = noTimeLimit ? 999999 : needsLongTimeout ? Math.max(state._timeout, 300) : state._timeout;

    // ── Pre-search if web toggle is on (share same results across all panes) ──
    let sharedSearchContext = null;
    let sharedSearchSources = null;
    const webChk = document.getElementById('web-toggle');
    const toggleState = Storage.loadToggleState();
    const isAgentMode = (toggleState.mode || 'chat') === 'agent';
    const webOn = webChk && webChk.checked;
    // In agent mode, web_search is a tool (handled per-pane); in chat mode, pre-search and share
    if (webOn && !isAgentMode) {
      try {
        const fd = new FormData();
        fd.append('query', message);
        const searchRes = await fetch(`${state.API_BASE}/api/search`, { method: 'POST', body: fd });
        if (searchRes.ok) {
          const searchData = await searchRes.json();
          if (searchData.context) sharedSearchContext = searchData.context;
          if (searchData.sources) sharedSearchSources = searchData.sources;
        }
      } catch (err) {
        console.warn('Compare pre-search failed, panes will search individually:', err);
      }
    }

    // ── Show vote bar immediately so user can vote anytime ──
    buildVoteBar(n);

    // ── Stream all panes (parallel or sequential based on _parallel flag) ──
    state._finishOrder = 0;
    state._paneElapsed = new Array(n).fill(null);
    state._paneMetrics = new Array(n).fill(null);
    state._abortControllers = new Array(n).fill(null);

    if (state._parallel) {
      // Run all panes at once
      await Promise.all(state._paneSessionIds.map((sid, i) =>
        streamToPane(i, sid, message, aiElements[i], { searchContext: sharedSearchContext, timeout: runTimeout })
      ));
    } else {
      // Run one pane at a time (sequential) — active pane full opacity, others dimmed
      const allPanes = document.querySelectorAll('.compare-pane');
      allPanes.forEach(p => { p.style.transition = 'opacity 0.4s ease'; });
      // Dim all except first
      allPanes.forEach((p, idx) => { p.style.opacity = idx === 0 ? '1' : '0.35'; });

      for (let i = 0; i < state._paneSessionIds.length; i++) {
        // Update spinner
        if (aiElements[i] && aiElements[i]._spinner) {
          aiElements[i]._spinner.updateLabel('Processing...');
        }

        await streamToPane(i, state._paneSessionIds[i], message, aiElements[i], { searchContext: sharedSearchContext, timeout: runTimeout });

        // Swap opacity: dim current, brighten next
        if (allPanes[i]) allPanes[i].style.opacity = '0.35';
        if (i + 1 < allPanes.length && allPanes[i + 1]) {
          allPanes[i + 1].style.opacity = '1';
        }
      }

      // Restore all pane opacities when done
      allPanes.forEach(p => { p.style.opacity = ''; p.style.transition = ''; });
    }

    // Re-focus main input for follow-up
    if (state._continueChat) {
      const ta = document.getElementById('message');
      if (ta) ta.focus();
    }

  } catch (err) {
    console.error('Compare error:', err);
    if (uiModule) uiModule.showError('Compare failed: ' + err.message);
  } finally {
    state._streaming = false;
    _setSendBtn('send');
    // Re-enable header buttons
    document.querySelectorAll('#compare-shuffle-btn, #compare-check-btn, #compare-add-btn').forEach(b => {
      b.disabled = false; b.style.opacity = '0.7'; b.style.pointerEvents = '';
    });
  }
}

// showModelSelector imported from ./selector.js

// ────────────────────────────────────────────────────────────────────────────
// ── cleanupResults / removeOverlays ──
// ────────────────────────────────────────────────────────────────────────────

/**
 * Build a markdown comparison of the current panes (prompt + each model's
 * response + metrics + grade) and copy it to the clipboard. Lets users save
 * or share a side-by-side at a glance.
 */
// Build the comparison markdown string. Shared by all export paths.
function _buildComparisonMarkdown() {
  const grid = document.querySelector('.compare-grid');
  if (!grid) return null;
  const panes = grid.querySelectorAll('.compare-pane');
  if (!panes.length) return null;
  const prompt = state._lastPrompt || '(no prompt yet — run a comparison first)';
  const expected = state._expectedAnswer || '';
  const date = new Date().toISOString().slice(0, 19).replace('T', ' ');
  let md = '# Compare\n\n';
  md += '**When:** ' + date + '\n';
  md += '**Type:** ' + (state._compareMode || 'chat') + (state._blindMode ? ' (blind)' : '') + '\n';
  md += '**Prompt:**\n\n```\n' + prompt + '\n```\n\n';
  if (expected) md += '**Expected answer:** `' + expected + '`\n\n';
  panes.forEach((pane, i) => {
    const m = state._selectedModels[i];
    const name = m ? (m.name || m.model) + (m.endpointName ? ' (' + m.endpointName + ')' : '') : 'Model ' + (i + 1);
    const body = pane.querySelector('.compare-text-content, .msg-body, .body');
    const text = body ? (body.innerText || body.textContent || '').trim() : '';
    const metrics = state._paneMetrics[i];
    const grade = pane.querySelector('.pane-grade-badge');
    const gradeMark = grade ? (grade.classList.contains('pass') ? ' ✓' : ' ✗') : '';
    md += '## ' + name + gradeMark + '\n\n';
    if (metrics) {
      const bits = [];
      if (metrics.output_tokens != null) bits.push(metrics.output_tokens + ' tokens');
      if (metrics.tokens_per_second != null) bits.push(metrics.tokens_per_second + ' tok/s');
      if (metrics.response_time != null) bits.push(metrics.response_time + 's');
      if (bits.length) md += '_' + bits.join(' · ') + '_\n\n';
    }
    md += text ? text + '\n\n' : '_(no response)_\n\n';
    md += '---\n\n';
  });
  return md;
}

let _exportMenuEl = null;
function _toggleExportMenu(btn) {
  if (_exportMenuEl) { _closeExportMenu(); return; }
  const r = btn.getBoundingClientRect();
  const m = document.createElement('div');
  m.className = 'compare-export-menu';
  m.style.cssText = 'position:fixed;z-index:10001;top:' + (r.bottom + 4) + 'px;left:' + r.left + 'px;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:4px;font-size:12px;display:flex;flex-direction:column;min-width:170px;';
  const opts = [
    { label: 'Copy as Markdown', fn: () => _exportCopyMarkdown(btn) },
    { label: 'Download .md',     fn: () => _exportDownloadMarkdown() },
    { label: 'Print / Save PDF', fn: () => _exportPrint() },
  ];
  for (const o of opts) {
    const item = document.createElement('button');
    item.type = 'button';
    item.textContent = o.label;
    item.style.cssText = 'background:none;border:none;color:var(--fg);text-align:left;padding:8px 12px;border-radius:6px;cursor:pointer;font:inherit;font-size:12px;';
    item.addEventListener('mouseenter', () => { item.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; });
    item.addEventListener('mouseleave', () => { item.style.background = 'none'; });
    item.addEventListener('click', () => { _closeExportMenu(); o.fn(); });
    m.appendChild(item);
  }
  document.body.appendChild(m);
  _exportMenuEl = m;
  setTimeout(() => document.addEventListener('click', _closeExportMenu, { once: true }), 0);
}
function _closeExportMenu() {
  if (_exportMenuEl) { _exportMenuEl.remove(); _exportMenuEl = null; }
}

async function _exportCopyMarkdown(_btn) {
  const md = _buildComparisonMarkdown();
  if (!md) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(md);
    } else {
      // Avoid the focus-stealing textarea fallback when the modern API
      // is available — that path briefly flashes the page as the
      // textarea is added/focused/removed.
      const ta = document.createElement('textarea');
      ta.value = md;
      ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
      document.body.appendChild(ta);
      ta.select(); document.execCommand('copy'); ta.remove();
    }
    try { window.uiModule?.showToast?.('Copied comparison to clipboard'); } catch {}
  } catch (e) {
    try { window.uiModule?.showToast?.('Copy failed'); } catch {}
  }
}

function _exportDownloadMarkdown() {
  const md = _buildComparisonMarkdown();
  if (!md) return;
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const blob = new Blob([md], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'compare-' + ts + '.md';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function _exportPrint() {
  const md = _buildComparisonMarkdown();
  if (!md) return;
  // Render the markdown as a quick HTML view in a new window and trigger
  // the system print dialog — user can pick "Save as PDF" from there.
  const w = window.open('', '_blank');
  if (!w) return;
  try { w.opener = null; } catch (_) {}
  const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const html = '<!doctype html><meta charset="utf-8"><title>Compare export</title>' +
    '<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:780px;margin:32px auto;padding:0 24px;line-height:1.55;color:#222}' +
    'pre{background:#f5f5f5;border-radius:6px;padding:10px;white-space:pre-wrap}' +
    'h1{margin-top:0}h2{border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:32px}' +
    'hr{border:none;border-top:1px solid #ccc;margin:24px 0}' +
    '</style><body><pre style="background:none;padding:0">' + escape(md) + '</pre>' +
    '<script>window.onload=()=>setTimeout(()=>window.print(),100)<\/script>';
  w.document.write(html);
  w.document.close();
}

async function _exportComparison(btn) {
  const grid = document.querySelector('.compare-grid');
  if (!grid) return;
  const panes = grid.querySelectorAll('.compare-pane');
  if (!panes.length) return;

  const prompt = state._lastPrompt || '(no prompt yet — run a comparison first)';
  const expected = state._expectedAnswer || '';
  const date = new Date().toISOString().slice(0, 19).replace('T', ' ');

  let md = '# Compare\n\n';
  md += '**When:** ' + date + '\n';
  md += '**Type:** ' + (state._compareMode || 'chat') + (state._blindMode ? ' (blind)' : '') + '\n';
  md += '**Prompt:**\n\n```\n' + prompt + '\n```\n\n';
  if (expected) md += '**Expected answer:** `' + expected + '`\n\n';

  panes.forEach((pane, i) => {
    const m = state._selectedModels[i];
    const name = m ? (m.name || m.model) + (m.endpointName ? ' (' + m.endpointName + ')' : '') : 'Model ' + (i + 1);
    const body = pane.querySelector('.compare-text-content, .msg-body, .body');
    const text = body ? (body.innerText || body.textContent || '').trim() : '';
    const metrics = state._paneMetrics[i];
    const grade = pane.querySelector('.pane-grade-badge');
    const gradeMark = grade ? (grade.classList.contains('pass') ? ' ✓' : ' ✗') : '';

    md += '## ' + name + gradeMark + '\n\n';
    if (metrics) {
      const bits = [];
      if (metrics.output_tokens != null) bits.push(metrics.output_tokens + ' tokens');
      if (metrics.tokens_per_second != null) bits.push(metrics.tokens_per_second + ' tok/s');
      if (metrics.response_time != null) bits.push(metrics.response_time + 's');
      if (bits.length) md += '_' + bits.join(' · ') + '_\n\n';
    }
    md += text ? text + '\n\n' : '_(no response)_\n\n';
    md += '---\n\n';
  });

  // Copy to clipboard
  const origLabel = btn ? btn.innerHTML : '';
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(md);
    } else {
      const ta = document.createElement('textarea');
      ta.value = md; document.body.appendChild(ta);
      ta.select(); document.execCommand('copy'); ta.remove();
    }
    if (btn) {
      btn.innerHTML = '<span style="font-size:11px;">Copied!</span>';
      setTimeout(() => { btn.innerHTML = origLabel; }, 1500);
    }
  } catch (e) {
    if (btn) {
      btn.innerHTML = '<span style="font-size:11px;color:var(--color-error);">Failed</span>';
      setTimeout(() => { btn.innerHTML = origLabel; }, 2000);
    }
  }
}

/**
 * Build the eval-prompts picker — shown only during compare. Mirrors the
 * absolute-positioned model-picker location (top-right of .chat-input-top)
 * and is auto-cleaned up by the standard _compareElements teardown.
 */
function _setupEvalPicker() {
  const inputTop = document.querySelector('.chat-input-top');
  if (!inputTop) return;

  const escapeHtml = uiModule.esc;

  // Hide the model-picker so eval-prompts can occupy the same slot
  const modelWrap = document.getElementById('model-picker-wrap');
  const prevModelDisplay = modelWrap ? modelWrap.style.display : '';
  if (modelWrap) modelWrap.style.display = 'none';

  const wrap = document.createElement('div');
  wrap.className = 'cmp-eval-wrap';
  wrap.id = 'cmp-eval-wrap';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'cmp-eval-btn';
  btn.className = 'cmp-eval-btn';
  btn.title = 'Insert an evaluation prompt';
  btn.innerHTML =
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
    + '<span class="cmp-eval-label">Eval prompts</span>'
    + '<svg class="cmp-eval-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

  const menu = document.createElement('div');
  menu.className = 'cmp-eval-menu hidden';
  menu.id = 'cmp-eval-menu';

  function _renderItems() {
    const mode = state._compareMode || 'chat';
    // research/html aren't first-class compare types — fall back gracefully
    const key = EVAL_PROMPTS[mode] ? mode
      : (mode === 'research' ? 'search' : 'chat');
    const list = EVAL_PROMPTS[key] || [];

    if (!list.length) {
      menu.innerHTML = '<div class="cmp-eval-empty">No prompts for this type</div>';
      return;
    }
    // Group by sub-category in original order
    const order = [];
    const groups = {};
    for (const p of list) {
      const sub = p.sub || 'Other';
      if (!groups[sub]) { groups[sub] = []; order.push(sub); }
      groups[sub].push(p);
    }
    let html = '';
    for (const sub of order) {
      html += '<div class="cmp-eval-group-label">' + escapeHtml(sub) + '</div>';
      for (const p of groups[sub]) {
        const data = encodeURIComponent(p.prompt);
        const ans = p.answer ? ' data-answer="' + encodeURIComponent(p.answer) + '"' : '';
        const checkMark = p.answer ? '<span class="cmp-eval-item-tick" title="Has expected answer">✓</span>' : '';
        html += '<button type="button" class="cmp-eval-item" data-prompt="' + data + '"' + ans + '>'
          + escapeHtml(p.label) + checkMark + '</button>';
      }
    }
    menu.innerHTML = html;
    menu.querySelectorAll('.cmp-eval-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const ta = document.getElementById('message');
        if (ta) {
          ta.value = decodeURIComponent(item.dataset.prompt);
          ta.dispatchEvent(new Event('input', { bubbles: true }));
          ta.focus();
        }
        const ans = item.dataset.answer ? decodeURIComponent(item.dataset.answer) : '';
        _showExpectedAnswer(ans);
        menu.classList.add('hidden');
      });
    });
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden')) {
      _renderItems();
      menu.classList.remove('hidden');
    } else {
      menu.classList.add('hidden');
    }
  });

  const _onDocClick = (e) => {
    if (!wrap.contains(e.target)) menu.classList.add('hidden');
  };
  document.addEventListener('click', _onDocClick);

  wrap.appendChild(btn);
  wrap.appendChild(menu);
  inputTop.appendChild(wrap);

  // Expected-answer chip — placed above the chat-input-bar (outside it), so
  // it floats over the compare grid right before the message box. Shows when
  // a graded prompt is picked so the eval-runner can verify model output.
  const hintChip = document.createElement('div');
  hintChip.className = 'cmp-eval-expected hidden';
  hintChip.id = 'cmp-eval-expected';
  hintChip.innerHTML =
    '<span class="cmp-eval-expected-label">Expected:</span>'
    + ' <strong class="cmp-eval-expected-value"></strong>'
    + ' <button type="button" class="cmp-eval-expected-close" title="Dismiss">×</button>';
  // Anchor the floating panel against the input bar (needs position:relative
  // — added via CSS rule on .chat-input-bar:has(.cmp-eval-expected) below).
  const inputBar = document.querySelector('.chat-input-bar');
  if (inputBar) {
    inputBar.appendChild(hintChip);
  } else {
    inputTop.appendChild(hintChip);
  }
  hintChip.querySelector('.cmp-eval-expected-close').addEventListener('click', (e) => {
    e.stopPropagation();
    hintChip.classList.add('hidden');
    state._expectedAnswer = '';
  });

  function _showExpectedAnswer(answer) {
    state._expectedAnswer = answer || '';
    if (!answer) {
      hintChip.classList.add('hidden');
      return;
    }
    hintChip.querySelector('.cmp-eval-expected-value').textContent = answer;
    hintChip.classList.remove('hidden');
  }

  // Hide the picker when the textarea has any user text (it's only useful
  // when starting fresh). Reappears when cleared. The expected-answer
  // chip stays put across sends — clearing it on every empty-textarea
  // tick wiped state._expectedAnswer before grading could read it, so
  // pane ✓/✗ badges never appeared. The chip is only cleared via its
  // own dismiss button (or when the user picks a new eval).
  const ta = document.getElementById('message');
  const _syncEvalVisibility = () => {
    const hasText = ta && ta.value.trim().length > 0;
    wrap.style.display = hasText ? 'none' : '';
    if (hasText) menu.classList.add('hidden');
  };
  if (ta) ta.addEventListener('input', _syncEvalVisibility);
  _syncEvalVisibility();

  // Stash cleanup so cleanupResults() can detach the doc listener and
  // restore the model-picker when compare deactivates.
  wrap._cleanup = () => {
    document.removeEventListener('click', _onDocClick);
    if (ta) ta.removeEventListener('input', _syncEvalVisibility);
    if (modelWrap) modelWrap.style.display = prevModelDisplay || '';
    if (hintChip.parentNode) hintChip.remove();
  };
  state._compareElements.push(wrap);
}

/** Remove compare UI elements and restore original view. */
function cleanupResults() {
  // Remove all compare elements
  state._compareElements.forEach(el => {
    if (el._cleanup) el._cleanup();
    if (el._cleanupInput) el._cleanupInput();
    if (el.parentNode) el.remove();
  });
  state._compareElements = [];

  // Remove any stray compare/probe overlays
  document.querySelectorAll('.compare-probe-overlay').forEach(el => el.remove());

  // Restore sidebar
  if (state._sidebarWasHidden) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) sidebar.classList.remove('hidden');
    state._sidebarWasHidden = false;
  }
  const _mobileNewRestore = document.getElementById('mobile-new-chat-btn');
  if (_mobileNewRestore && _mobileNewRestore.dataset.cmpWasDisplay !== undefined) {
    _mobileNewRestore.style.display = _mobileNewRestore.dataset.cmpWasDisplay;
    delete _mobileNewRestore.dataset.cmpWasDisplay;
  }
  state._hasVisibleResults = false;

  // Hard reload the page to cleanly restore all UI state
  window.location.reload();
}

function removeOverlays() {
  const bar = document.getElementById('compare-vote-bar');
  if (bar) bar.remove();
  const modal = document.getElementById('compare-model-overlay');
  if (modal) modal.remove();
  const probe = document.querySelector('.compare-probe-overlay');
  if (probe) probe.remove();
}

// ────────────────────────────────────────────────────────────────────────────
// ── showShufflePoolEditor ──
// ────────────────────────────────────────────────────────────────────────────

/** Shuffle pool editor — lets users exclude broken models from the dice. */
async function showShufflePoolEditor() {
  let models;
  try { models = await fetchModels(); } catch (e) {
    if (uiModule) uiModule.showError('Failed to load models');
    return;
  }

  const overlay = document.createElement('div');
  overlay.className = 'modal';
  overlay.id = 'shuffle-pool-overlay';

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.width = '420px';

  const header = document.createElement('div');
  header.className = 'modal-header';
  header.innerHTML = '<h4>Shuffle Pool</h4>';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'close-btn';
  closeBtn.innerHTML = '&#x2716;';
  closeBtn.addEventListener('click', () => overlay.remove());
  header.appendChild(closeBtn);
  content.appendChild(header);

  const body = document.createElement('div');
  body.className = 'modal-body';
  body.style.padding = '12px 16px';

  const desc = document.createElement('p');
  desc.style.cssText = 'color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:0.85em;margin:0 0 12px;';
  desc.textContent = 'Uncheck models to exclude them from random shuffle. They can still be picked manually.';
  body.appendChild(desc);

  const list = document.createElement('div');
  list.style.cssText = 'max-height:400px;overflow-y:auto;';

  const excluded = getExcludedModels();

  // Group by type
  const groups = { chat: [], image: [] };
  models.forEach(m => { if (groups[m.type]) groups[m.type].push(m); });

  Object.entries(groups).forEach(([type, items]) => {
    if (items.length === 0) return;
    const heading = document.createElement('div');
    heading.style.cssText = 'font-size:0.78em;font-weight:600;color:color-mix(in srgb, var(--fg) 50%, transparent);text-transform:uppercase;letter-spacing:0.5px;padding:8px 4px 4px;';
    heading.textContent = type === 'chat' ? 'Chat Models' : 'Image Models';
    list.appendChild(heading);

    items.forEach(m => {
      const row = document.createElement('label');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:5px 4px;cursor:pointer;font-size:0.85em;color:var(--fg);border-radius:4px;';
      row.addEventListener('mouseenter', () => { row.style.background = 'color-mix(in srgb, var(--fg) 4%, transparent)'; });
      row.addEventListener('mouseleave', () => { row.style.background = ''; });
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = !excluded.includes(m.id);
      chk.addEventListener('change', () => {
        const exc = getExcludedModels();
        if (chk.checked) {
          const idx = exc.indexOf(m.id);
          if (idx >= 0) exc.splice(idx, 1);
        } else {
          if (!exc.includes(m.id)) exc.push(m.id);
        }
        setExcludedModels(exc);
      });
      const label = document.createElement('span');
      label.textContent = m.endpointName ? m.name + ' (' + m.endpointName + ')' : m.name;
      row.appendChild(chk);
      row.appendChild(label);
      list.appendChild(row);
    });
  });

  body.appendChild(list);
  content.appendChild(body);
  overlay.appendChild(content);
  document.body.appendChild(overlay);

  if (themeModule && themeModule.makeDraggable) {
    themeModule.makeDraggable(content, header);
  }
}

// ────────────────────────────────────────────────────────────────────────────
// ── Register cross-module callbacks ──
// ────────────────────────────────────────────────────────────────────────────

registerCompareActions({ stopAll, resetCompare });
registerStreamActions({ rerollPane, autoPreviewHtml: _autoPreviewHtml });
registerPaneActions({ setSendBtn: _setSendBtn, deactivate, streamToPane, renderSearchResults: _renderSearchResults, fetchModels });

// ────────────────────────────────────────────────────────────────────────────
// ── Public API ──
// ────────────────────────────────────────────────────────────────────────────

export { EVAL_PROMPTS, showScoreboard, handleCompareSubmit };

const compareModule = {
  init,
  toggleMode,
  handleCompareSubmit,
  isActive: isCompareActive,
  hasVisibleResults: () => state._hasVisibleResults,
  deactivate,
  closeCompare,
  cleanupResults,
  showShufflePoolEditor,
  showScoreboard,
};

export default compareModule;
window.compareModule = compareModule;
