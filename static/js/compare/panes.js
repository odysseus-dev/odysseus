// compare/panes.js — pane lifecycle, actions, layout
import state from './state.js';
import { _persistSelections } from './models.js';
import { buildVoteBar } from './vote.js?v=20260828resendcaldrag1';
import {
  ICON_REROLL, ICON_COPY, ICON_EXPAND, ICON_COLLAPSE, ICON_CLOSE,
  ICON_PLAY, ICON_CODE, SEND_SVG, ICON_DICE,
} from './icons.js?v=20260908compareprompts1';
import { _clearProbeWaves } from './probe.js';
import Storage from '../storage.js';
import uiModule from '../ui.js?v=20260916largetoolscroll1';
import spinnerModule from '../spinner.js';
import { bindMenuDismiss } from '../escMenuStack.js';

var escapeHtml = uiModule.esc;

// ── Lazy-registered functions from compare.js (avoids circular imports) ──
let _setSendBtn = null;
let _deactivate = null;
let _streamToPane = null;
let _renderSearchResults = null;
let _fetchModels = null;

/** Register external functions that live in compare.js or sibling modules. */
function registerPaneActions({ setSendBtn, deactivate, streamToPane, renderSearchResults, fetchModels }) {
  if (setSendBtn) _setSendBtn = setSendBtn;
  if (deactivate) _deactivate = deactivate;
  if (streamToPane) _streamToPane = streamToPane;
  if (renderSearchResults) _renderSearchResults = renderSearchResults;
  if (fetchModels) _fetchModels = fetchModels;
}

/** Slot label: A/B/C in parallel mode, 1/2/3 in sequential. */
function _slotChar(i) { return state._parallel ? String.fromCharCode(65 + i) : String(i + 1); }

/** Keep Shuffle compact until a larger comparison makes it useful as a
 * direct action. It returns to the kebab after being used. */
function syncShuffleButtonPlacement(showDirect = state._selectedModels.length > 2) {
  const shuffleBtn = document.getElementById('compare-shuffle-btn');
  const moreMenu = document.querySelector('.compare-more-menu');
  const moreWrap = document.querySelector('.compare-more-wrap');
  const headerActions = moreWrap?.parentElement;
  if (!shuffleBtn || !moreMenu || !moreWrap || !headerActions) return;
  if (showDirect) {
    if (shuffleBtn.parentElement !== headerActions) headerActions.insertBefore(shuffleBtn, moreWrap);
  } else if (shuffleBtn.parentElement !== moreMenu) {
    moreMenu.appendChild(shuffleBtn);
  }
}

function _paneModeBadgeHtml(paneIdx) {
  const mode = String(state._compareMode || 'chat');
  if (mode !== 'search') return '';
  const label = ({ agent: 'Agent', search: 'Search', research: 'Research' }[mode] || 'Chat');
  const detail = ({ agent: 'tools', search: 'web', research: 'sources' }[mode] || 'plain');
  return '<span class="pane-mode-badge pane-mode-' + escapeHtml(mode) + '" title="' + escapeHtml(label + ' mode') + '">' +
    '<span class="pane-mode-dot" aria-hidden="true"></span>' +
    '<span class="pane-mode-label">' + escapeHtml(label) + '</span>' +
    (mode === 'agent' ? '' : '<span class="pane-mode-detail">' + escapeHtml(detail) + '</span>') +
    '</span>';
}

const PANE_SETTINGS_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06A2 2 0 1 1 7.04 4.3l.06.06A1.65 1.65 0 0 0 8.92 4a1.65 1.65 0 0 0 1-1.51V2a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82 1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>';

function paneSettingsButtonHtml(paneIdx) {
  return '<button type="button" class="pane-action-btn pane-settings-btn" data-action="settings" data-pane="' + Number(paneIdx) + '" title="Inference settings" aria-label="Inference settings" aria-haspopup="dialog" aria-expanded="false"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>';
}

function _paneModelRequiresThinking(paneIdx) {
  const modelId = String(state._selectedModels[paneIdx]?.model || '').toLowerCase().split(':', 1)[0];
  return modelId === 'x-ai/grok-4.5' || modelId === 'grok-4.5';
}

async function _savePaneGenerationSettings(paneIdx, change) {
  const sid = state._paneSessionIds[paneIdx];
  if (!sid) return false;
  const current = state._paneGenerationSettings[paneIdx] || {};
  const next = {
    thinking_mode: current.thinking_mode || '',
    temperature_override: current.temperature_override ?? null,
    max_tokens_override: current.max_tokens_override ?? null,
    ...change,
  };
  try {
    const res = await fetch(`${state.API_BASE}/api/session/${encodeURIComponent(sid)}/generation-settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(next),
    });
    if (!res.ok) throw new Error(await res.text());
    state._paneGenerationSettings[paneIdx] = { ...next, ...await res.json() };
    return true;
  } catch (err) {
    uiModule.showError(`Could not save pane settings: ${err.message || err}`);
    return false;
  }
}

function togglePaneSettings(paneIdx, anchorBtn) {
  const existing = document.querySelector('.compare-pane-settings-popup');
  if (existing) {
    const same = existing.dataset.pane === String(paneIdx);
    if (typeof existing._dismiss === 'function') existing._dismiss(); else existing.remove();
    if (same) return;
  }
  const settings = state._paneGenerationSettings[paneIdx] || {
    thinking_mode: '', temperature_override: null, max_tokens_override: null,
  };
  const popup = document.createElement('div');
  popup.className = 'chat-context-popup compare-pane-settings-popup';
  popup.dataset.pane = String(paneIdx);
  popup.setAttribute('role', 'dialog');
  popup.setAttribute('aria-label', 'Inference settings');
  popup.innerHTML = '<div class="chat-context-popup-title">Inference settings</div>';

  const thinkingRequired = _paneModelRequiresThinking(paneIdx);
  const thinkingOn = thinkingRequired || settings.thinking_mode === 'on';
  const thinkingRow = document.createElement('div');
  thinkingRow.className = 'chat-context-toggle-row';
  thinkingRow.innerHTML = '<div class="chat-context-toggle-copy"><span>Thinking</span><span class="chat-context-toggle-state">' + (thinkingRequired ? 'Required' : (thinkingOn ? 'On' : 'Off')) + '</span></div>';
  const thinkingToggle = document.createElement('button');
  thinkingToggle.type = 'button';
  thinkingToggle.className = `chat-context-toggle${thinkingOn ? ' active' : ''}`;
  thinkingToggle.setAttribute('role', 'switch');
  thinkingToggle.setAttribute('aria-checked', thinkingOn ? 'true' : 'false');
  thinkingToggle.disabled = thinkingRequired;
  if (thinkingRequired) thinkingToggle.title = 'This model requires reasoning';
  thinkingToggle.addEventListener('click', async () => {
    if (thinkingRequired) return;
    const next = !thinkingToggle.classList.contains('active');
    thinkingToggle.disabled = true;
    if (await _savePaneGenerationSettings(paneIdx, { thinking_mode: next ? 'on' : 'off' })) {
      thinkingToggle.classList.toggle('active', next);
      thinkingToggle.setAttribute('aria-checked', next ? 'true' : 'false');
      thinkingRow.querySelector('.chat-context-toggle-state').textContent = next ? 'On' : 'Off';
    }
    thinkingToggle.disabled = false;
  });
  thinkingRow.appendChild(thinkingToggle);
  popup.appendChild(thinkingRow);

  const addSlider = (label, value, min, max, step, formatter, key, normalize) => {
    const row = document.createElement('div');
    row.className = 'chat-context-threshold-row';
    row.innerHTML = '<div class="chat-context-threshold-top"><span>' + label + '</span><span>' + formatter(value) + '</span></div>';
    const input = document.createElement('input');
    Object.assign(input, { type: 'range', min: String(min), max: String(max), step: String(step), value: String(value), className: 'chat-context-threshold-slider preset-range' });
    input.addEventListener('input', () => { row.querySelector('.chat-context-threshold-top span:last-child').textContent = formatter(Number(input.value)); });
    input.addEventListener('change', async () => {
      input.disabled = true;
      await _savePaneGenerationSettings(paneIdx, { [key]: normalize(Number(input.value)) });
      input.disabled = false;
    });
    row.appendChild(input);
    popup.appendChild(row);
  };
  addSlider('Temperature', settings.temperature_override ?? 1, 0, 2, 0.1, value => Number(value).toFixed(1), 'temperature_override', value => value);
  addSlider('Max tokens', settings.max_tokens_override ?? 8448, 256, 8448, 256, value => value > 8192 ? 'No limit' : Number(value).toLocaleString(), 'max_tokens_override', value => value > 8192 ? null : value);

  document.body.appendChild(popup);
  const rect = anchorBtn.getBoundingClientRect();
  const margin = 8;
  const popupRect = popup.getBoundingClientRect();
  popup.style.left = Math.max(margin, Math.min(rect.left, window.innerWidth - popupRect.width - margin)) + 'px';
  popup.style.top = Math.max(margin, Math.min(rect.bottom + 5, window.innerHeight - popupRect.height - margin)) + 'px';
  anchorBtn.setAttribute('aria-expanded', 'true');
  bindMenuDismiss(popup, () => {
    popup.remove();
    anchorBtn.setAttribute('aria-expanded', 'false');
  }, event => !popup.contains(event.target) && event.target !== anchorBtn);
}

function _isMobileCompare() {
  return window.matchMedia('(max-width: 768px)').matches;
}

function _mobilePaneLabel(index) {
  if (state._blindMode) return 'Model ' + _slotChar(index);
  return state._selectedModels[index]?.name || 'Model ' + (index + 1);
}

/** Keep the phone tab strip and its single visible pane in sync. */
function refreshMobilePaneTabs(preferredIndex = state._activeMobilePane) {
  const tabs = document.querySelector('.compare-mobile-tabs');
  const grid = document.querySelector('.compare-grid');
  if (!tabs || !grid) return;

  const panes = Array.from(grid.querySelectorAll(':scope > .compare-pane'));
  if (!panes.length) {
    tabs.replaceChildren();
    return;
  }

  state._activeMobilePane = Math.max(0, Math.min(Number(preferredIndex) || 0, panes.length - 1));
  const retained = new Set();
  panes.forEach((pane, index) => {
    const paneId = 'cmp-pane-' + index;
    const tabId = 'cmp-mobile-tab-' + index;
    pane.id = paneId;
    pane.setAttribute('role', 'tabpanel');
    pane.setAttribute('aria-labelledby', tabId);

    let tab = tabs.querySelector(`[data-pane="${index}"]`);
    if (!tab) {
      tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'compare-mobile-tab';
      tab.setAttribute('role', 'tab');
      tab.innerHTML = '<span class="compare-mobile-tab-slot"></span><span class="compare-mobile-tab-label"></span><span class="compare-mobile-tab-state" aria-hidden="true"></span>';
    }
    retained.add(tab);
    tab.id = tabId;
    tab.dataset.pane = String(index);
    tab.setAttribute('aria-controls', paneId);
    tab.querySelector('.compare-mobile-tab-slot').textContent = _slotChar(index);
    tab.querySelector('.compare-mobile-tab-label').textContent = _mobilePaneLabel(index);
    tab.classList.toggle('is-streaming', pane.classList.contains('is-streaming'));
    tab.classList.toggle('is-awaiting-input', pane.classList.contains('is-awaiting-input'));
    tab.classList.toggle('is-done', pane.classList.contains('is-done'));
    tab.classList.toggle('is-failed', pane.classList.contains('is-failed'));
    tabs.appendChild(tab);
  });

  tabs.querySelectorAll('.compare-mobile-tab').forEach(tab => {
    if (!retained.has(tab)) tab.remove();
  });

  let addButton = tabs.querySelector('.compare-mobile-add');
  if (!addButton && typeof tabs._onAddPane === 'function') {
    addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'compare-mobile-add';
    addButton.title = 'Add model';
    addButton.setAttribute('aria-label', 'Add model');
    addButton.innerHTML = '<span aria-hidden="true">+</span><span>Add</span>';
    addButton.addEventListener('click', (event) => {
      event.stopPropagation();
      tabs._onAddPane(addButton);
    });
  }
  if (addButton) {
    addButton.disabled = !!state._streaming;
    addButton.style.display = panes.length >= 8 ? 'none' : '';
    tabs.appendChild(addButton);
  }

  const mobile = _isMobileCompare();
  panes.forEach((pane, index) => {
    const active = index === state._activeMobilePane;
    pane.classList.toggle('compare-pane-mobile-active', active);
    if (mobile) pane.setAttribute('aria-hidden', active ? 'false' : 'true');
    else pane.removeAttribute('aria-hidden');
  });
  tabs.querySelectorAll('.compare-mobile-tab').forEach((tab, index) => {
    const active = index === state._activeMobilePane;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
    tab.tabIndex = active ? 0 : -1;
  });
}

function activateMobilePane(index, { focus = false } = {}) {
  state._activeMobilePane = index;
  refreshMobilePaneTabs(index);
  const tab = document.querySelector(`.compare-mobile-tab[data-pane="${state._activeMobilePane}"]`);
  if (tab && _isMobileCompare()) {
    tab.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    if (focus) tab.focus();
  }
}

/** Mount a horizontally scrollable tab row without unmounting inactive streams. */
function mountMobilePaneTabs(container, grid, onAddPane) {
  const tabs = document.createElement('div');
  tabs.className = 'compare-mobile-tabs';
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', 'Compared models');
  tabs._onAddPane = onAddPane;
  container.insertBefore(tabs, grid);

  const onClick = (event) => {
    const tab = event.target.closest('.compare-mobile-tab');
    if (tab) activateMobilePane(Number(tab.dataset.pane));
  };
  const onKeyDown = (event) => {
    if (!event.target.closest('.compare-mobile-tab')) return;
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const last = Math.max(0, state._selectedModels.length - 1);
    let next = state._activeMobilePane;
    if (event.key === 'ArrowLeft') next = Math.max(0, next - 1);
    if (event.key === 'ArrowRight') next = Math.min(last, next + 1);
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = last;
    activateMobilePane(next, { focus: true });
  };
  const onResize = () => refreshMobilePaneTabs();
  tabs.addEventListener('click', onClick);
  tabs.addEventListener('keydown', onKeyDown);
  window.addEventListener('resize', onResize);

  // On phones, swipe across the response card to move to the adjacent model.
  // Keep the gesture vertical-scroll friendly and don't steal touches from
  // buttons, links, selects, or editable response content.
  let swipeStart = null;
  const onTouchStart = (event) => {
    if (!_isMobileCompare() || event.touches.length !== 1) {
      swipeStart = null;
      return;
    }
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('button, a, select, input, textarea, [contenteditable="true"]')) {
      swipeStart = null;
      return;
    }
    const touch = event.touches[0];
    swipeStart = { x: touch.clientX, y: touch.clientY };
  };
  const onTouchEnd = (event) => {
    if (!swipeStart || !_isMobileCompare() || event.changedTouches.length !== 1) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - swipeStart.x;
    const dy = touch.clientY - swipeStart.y;
    swipeStart = null;
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
    const last = Math.max(0, state._selectedModels.length - 1);
    const next = dx < 0
      ? Math.min(last, state._activeMobilePane + 1)
      : Math.max(0, state._activeMobilePane - 1);
    if (next !== state._activeMobilePane) activateMobilePane(next);
  };
  grid.addEventListener('touchstart', onTouchStart, { passive: true });
  grid.addEventListener('touchend', onTouchEnd, { passive: true });

  const observer = new MutationObserver(() => refreshMobilePaneTabs());
  observer.observe(grid, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['class', 'data-pane'],
  });
  tabs._cleanup = () => {
    observer.disconnect();
    window.removeEventListener('resize', onResize);
    grid.removeEventListener('touchstart', onTouchStart);
    grid.removeEventListener('touchend', onTouchEnd);
  };
  refreshMobilePaneTabs(0);
  return tabs;
}

function _showShuffleNotice() {
  const grid = document.querySelector('.compare-grid');
  if (!grid) return;
  grid.querySelector('.compare-shuffle-notice')?.remove();
  const notice = document.createElement('div');
  notice.className = 'compare-shuffle-notice';
  notice.setAttribute('role', 'status');
  notice.setAttribute('aria-live', 'polite');
  notice.innerHTML = '<span class="compare-shuffle-notice-icon" aria-hidden="true">' + ICON_DICE + '</span><span>Shuffling</span>';
  grid.appendChild(notice);
  requestAnimationFrame(() => notice.classList.add('show'));
  setTimeout(() => {
    notice.classList.remove('show');
    notice.addEventListener('transitionend', () => notice.remove(), { once: true });
    setTimeout(() => notice.remove(), 220);
  }, 1060);
}

// ── Stop / reroll ──

function stopAll() {
  state._abortControllers.forEach(ac => { if (ac) ac.abort(); });
  state._abortControllers = [];
  state._streaming = false;
  if (_setSendBtn) _setSendBtn('send');
  // Re-enable header buttons
  document.querySelectorAll('#compare-shuffle-btn, #compare-check-btn, #compare-add-btn').forEach(b => {
    b.disabled = false; b.style.opacity = '0.7'; b.style.pointerEvents = '';
  });
  document.querySelectorAll('.compare-pane').forEach(pane => {
    pane.classList.remove('is-streaming', 'is-awaiting-input');
  });
}

function stopPane(paneIdx) {
  const ac = state._abortControllers[paneIdx];
  if (ac) {
    ac.abort();
    state._abortControllers[paneIdx] = null;
  }
  // Hide stop button, show reroll
  const pane = document.querySelector(`.compare-pane[data-pane="${paneIdx}"]`);
  if (pane) {
    pane.classList.remove('is-streaming', 'is-awaiting-input', 'is-done');
    pane.classList.add('is-failed');
    const stopBtn = pane.querySelector('.pane-stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';
    pane.querySelectorAll('.pane-needs-response').forEach(b => b.style.display = '');
  }
  // Remove spinner if present
  const hist = document.getElementById('cmp-history-' + paneIdx);
  if (hist) {
    const lastAi = hist.querySelector('.msg-ai:last-child');
    if (lastAi && lastAi._spinner) { lastAi._spinner.destroy(); lastAi._spinner = null; }
    const body = lastAi && lastAi.querySelector('.body');
    if (body && !body.textContent.trim()) {
      body.innerHTML = '<span style="opacity:0.4;font-style:italic;">Stopped</span>';
    }
  }
}

async function rerollPane(paneIdx, overrideTimeout) {
  // Allow reroll even while other panes stream — just stop this pane first
  if (state._abortControllers[paneIdx]) stopPane(paneIdx);
  const hist = document.getElementById('cmp-history-' + paneIdx);
  // Reset preview state
  const _ri = document.getElementById('cmp-iframe-' + paneIdx);
  if (_ri) { _ri.srcdoc = ''; _ri.style.display = 'none'; _ri._htmlCode = null; }
  const _rp = document.getElementById('cmp-preview-' + paneIdx);
  if (_rp) { _rp.style.display = 'none'; _rp.classList.remove('active'); }
  if (hist) hist.style.display = '';
  if (!hist) return;
  const userBodies = hist.querySelectorAll('.msg-user .body');
  const firstUserText = userBodies.length > 0 ? userBodies[0].textContent : '';
  if (!firstUserText) return;

  // Clear all messages and start fresh
  hist.innerHTML = '';
  const userMsg = document.createElement('div');
  userMsg.className = 'msg msg-user';
  userMsg.innerHTML = '<div class="role">You</div><div class="body">' + escapeHtml(firstUserText) + '</div>';
  hist.appendChild(userMsg);

  // Reset badge and timer
  const badge = document.getElementById('cmp-badge-' + paneIdx);
  if (badge) { badge.textContent = ''; badge.style.color = ''; }
  const timer = document.getElementById('cmp-timer-' + paneIdx);
  if (timer) timer.textContent = '';
  const summary = document.getElementById('cmp-summary-' + paneIdx);
  if (summary) { summary.textContent = ''; summary.title = ''; }

  // Search mode: re-query the search provider
  if (state._compareMode === 'search') {
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

    const m = state._selectedModels[paneIdx];
    const fd = new FormData();
    fd.append('query', firstUserText);
    fd.append('provider', m.model);
    fd.append('count', '10');
    try {
      const ac = new AbortController();
      state._abortControllers[paneIdx] = ac;
      const t0 = performance.now();
      const res = await fetch(`${state.API_BASE}/api/search/query`, { method: 'POST', body: fd, signal: ac.signal });
      const data = await res.json();
      const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
      aiBody.innerHTML = '';
      if (data.error) {
        aiBody.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;">Error: ' + escapeHtml(data.error) + '</div>';
      } else if (!data.results || data.results.length === 0) {
        aiBody.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 50%, transparent);font-size:0.85em;font-style:italic;">No results found</div>';
      } else {
        aiBody.appendChild(_renderSearchResults(data));
      }
      const footer = document.createElement('div');
      footer.className = 'msg-footer';
      const span = document.createElement('span');
      span.className = 'response-metrics';
      const parts = [];
      if (data.results) parts.push(data.results.length + ' results');
      parts.push(elapsed + 's');
      span.textContent = parts.join(' | ');
      footer.appendChild(span);
      aiMsg.appendChild(footer);
    } catch (err) {
      aiBody.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;">Error: ' + escapeHtml(err.message) + '</div>';
    }
    state._abortControllers[paneIdx] = null;
    hist.scrollTop = hist.scrollHeight;
    return;
  }

  // Chat/agent mode: stream via session
  const aiMsg = document.createElement('div');
  aiMsg.className = 'msg msg-ai';
  aiMsg.innerHTML = '<div class="role">AI</div><div class="body"></div>';
  const aiBody = aiMsg.querySelector('.body');
  if (spinnerModule) {
    const label = overrideTimeout ? 'Retrying (' + overrideTimeout + 's)...' : 'Re-rolling...';
    const spinner = spinnerModule.create(label, 'right');
    aiBody.appendChild(spinner.createElement());
    spinner.start();
    aiMsg._spinner = spinner;
  }
  hist.appendChild(aiMsg);
  hist.scrollTop = hist.scrollHeight;

  const opts = { skipBadge: true };
  if (overrideTimeout) opts.timeout = overrideTimeout;
  await _streamToPane(paneIdx, state._paneSessionIds[paneIdx], firstUserText, aiMsg, opts);
}

// ── Expand / preview / copy ──

function toggleExpandPane(paneIdx, btn) {
  const grid = document.querySelector('.compare-grid');
  if (!grid) return;
  const panes = grid.querySelectorAll('.compare-pane');
  const target = panes[paneIdx];
  if (!target) return;

  if (target.classList.contains('expanded')) {
    target.classList.remove('expanded');
    panes.forEach(p => { p.style.display = ''; });
    if (btn) btn.innerHTML = ICON_EXPAND;
  } else {
    target.classList.add('expanded');
    panes.forEach((p, i) => { if (i !== paneIdx) p.style.display = 'none'; });
    if (btn) btn.innerHTML = ICON_COLLAPSE;
  }
}

/**
 * After streaming finishes, check for HTML code in the response.
 * If found, show the play button in the header. User clicks to run.
 */
function _autoPreviewHtml(paneIdx, accumulated) {
  if (!accumulated) return;
  const htmlCode = _extractHtmlFromText(accumulated);
  if (!htmlCode) return;

  const iframe = document.getElementById('cmp-iframe-' + paneIdx);
  const previewBtn = document.getElementById('cmp-preview-' + paneIdx);
  if (!iframe || !previewBtn) return;

  // Store the HTML on the iframe for when user clicks play
  iframe._htmlCode = htmlCode;

  // Show the play button
  previewBtn.style.display = '';
  previewBtn.innerHTML = ICON_PLAY;
  previewBtn.title = 'Run preview';
}

/** Toggle between iframe preview and code view for a pane. */
function togglePanePreview(paneIdx) {
  const iframe = document.getElementById('cmp-iframe-' + paneIdx);
  const hist = document.getElementById('cmp-history-' + paneIdx);
  const btn = document.getElementById('cmp-preview-' + paneIdx);
  if (!iframe || !hist || !btn) return;

  const showingPreview = iframe.style.display !== 'none';
  if (showingPreview) {
    // Switch to code view
    iframe.style.display = 'none';
    hist.style.display = '';
    btn.innerHTML = ICON_PLAY;
    btn.title = 'Run preview';
    btn.classList.remove('active');
  } else {
    // Switch to preview — load on first click
    if (iframe._htmlCode) iframe.srcdoc = iframe._htmlCode;
    iframe.style.display = '';
    hist.style.display = 'none';
    btn.innerHTML = ICON_CODE;
    btn.title = 'Show code';
    btn.classList.add('active');
  }
}

/** Extract full HTML document from raw accumulated text. */
function _extractHtmlFromText(text) {
  // 1. Try markdown code fences
  const fenceRe = /`{3,}(?:html)?\s*\r?\n([\s\S]*?)`{3,}/gi;
  let match;
  while ((match = fenceRe.exec(text)) !== null) {
    const code = match[1].trim();
    if (/<!doctype\s+html|<html[\s>]/i.test(code)) return code;
  }
  // 2. Bare HTML
  const bare = text.match(/(<!doctype\s+html[\s\S]*<\/html>)/i)
    || text.match(/(<html[\s>][\s\S]*<\/html>)/i);
  if (bare) return bare[1].trim();
  return null;
}

async function copyPaneResponse(paneIdx) {
  const hist = document.getElementById('cmp-history-' + paneIdx);
  if (!hist) return;
  const aiMsgs = hist.querySelectorAll('.msg-ai');
  if (aiMsgs.length === 0) return;
  const lastAi = aiMsgs[aiMsgs.length - 1];
  // For image panes, copy the prompt text
  const text = lastAi._imageData ? (lastAi._imageData.prompt || '') : (lastAi.querySelector('.body')?.textContent || '');
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  if (uiModule) uiModule.showToast(lastAi._imageData ? 'Prompt copied!' : 'Copied!');
}

// ── Add / create / remove panes ──

/** Show a model picker dropdown anchored to the "+" button in the pane header. */
async function _addPane(anchorBtn) {
  if (state._streaming) return;
  const _effectiveType = (state._compareMode === 'agent' || state._compareMode === 'research') ? 'chat' : state._compareMode;
  const filtered = state._cachedModels.filter(m => m.type === _effectiveType);
  if (!filtered.length) return;

  // Toggle existing dropdown
  const existing = document.querySelector('.add-pane-dropdown');
  if (existing) { if (typeof existing._dismiss === 'function') existing._dismiss(); else existing.remove(); return; }

  const dropdown = document.createElement('div');
  dropdown.className = 'add-pane-dropdown';
  let closeMenu = () => dropdown.remove();

  // Search input for large model lists
  if (filtered.length >= 5) {
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search models\u2026';
    searchInput.className = 'add-pane-search';
    searchInput.addEventListener('input', () => {
      const q = searchInput.value.toLowerCase().trim();
      dropdown.querySelectorAll('.pane-model-item').forEach(item => {
        item.style.display = item.textContent.toLowerCase().includes(q) ? '' : 'none';
      });
    });
    searchInput.addEventListener('click', (e) => e.stopPropagation());
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const first = dropdown.querySelector('.pane-model-item:not([style*="display: none"])');
        if (first) first.click();
      }
    });
    dropdown.appendChild(searchInput);
    // Desktop: auto-focus the search box so the user can start typing.
    // Mobile: skip — auto-focus pops the on-screen keyboard and covers
    // the model list. The user can tap the search box if they want to
    // filter, otherwise they just tap a model directly.
    if (window.innerWidth > 768) setTimeout(() => searchInput.focus(), 0);
  }

  filtered.forEach(m => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'pane-model-item';
    const label = m.endpointName ? m.name + ' (' + m.endpointName + ')' : m.name;
    item.textContent = label;
    const alreadyUsed = state._selectedModels.some(s => s.model === m.id && s.endpointId === m.endpointId);
    if (alreadyUsed) item.classList.add('current');

    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      closeMenu();
      await _createAndAppendPane(m);
    });
    dropdown.appendChild(item);
  });

  // Position dropdown relative to the viewport (position: fixed) so it
  // can't end up off-screen even when the toolbar has scrolled or the
  // chat-container is wider than the viewport.
  const btnRect = anchorBtn.getBoundingClientRect();
  dropdown.style.position = 'fixed';
  dropdown.style.right = 'auto';
  dropdown.style.bottom = 'auto';
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const margin = 8;
  // Render off-screen first so we can measure the dropdown's actual size.
  // Clamp the width to the viewport up front so long model names can't push
  // the dropdown off the screen edge, and lift z-index above the panes.
  dropdown.style.left = '-9999px';
  dropdown.style.top = '0';
  dropdown.style.maxWidth = (vw - margin * 2) + 'px';
  dropdown.style.zIndex = '100000';
  document.body.appendChild(dropdown);
  const ddRect = dropdown.getBoundingClientRect();
  const ddW = ddRect.width;
  const ddH = ddRect.height;
  // Align the dropdown's right edge with the button, then clamp so it stays
  // within the viewport. This keeps the picker attached to the Add flap
  // instead of opening at an unrelated edge of the compare surface.
  let left = btnRect.right - ddW;
  left = Math.max(margin, Math.min(left, vw - margin - ddW));
  // Vertical: drop below the button if there's room, otherwise above.
  const spaceBelow = vh - btnRect.bottom;
  const spaceAbove = btnRect.top;
  let top;
  if (anchorBtn.classList.contains('compare-add-flap')) {
    top = Math.max(margin, Math.min(btnRect.bottom + 6, vh - margin - ddH));
  } else if (spaceBelow >= ddH + margin || spaceBelow >= spaceAbove) {
    top = Math.min(btnRect.bottom + 4, vh - margin - Math.min(ddH, vh - margin * 2));
  } else {
    top = Math.max(margin, btnRect.top - 4 - ddH);
  }
  dropdown.style.left = left + 'px';
  dropdown.style.top = top + 'px';
  dropdown.style.right = 'auto';
  dropdown.style.bottom = 'auto';
  dropdown.style.maxHeight = Math.min(ddH, vh - margin * 2) + 'px';

  // Close on outside click or Escape (the latter via the registry).
  closeMenu = bindMenuDismiss(dropdown, () => dropdown.remove(), (e) => !dropdown.contains(e.target) && e.target !== anchorBtn);}

/** Create a new pane for the given model and append it to the compare grid. */
async function _createAndAppendPane(m) {
  const i = state._selectedModels.length;  // New index

  // Create session
  const fd = new FormData();
  // Blind mode: neutral slot name only — never leak the model (issue #1285).
  fd.append('name', '[CMP] ' + (state._blindMode ? 'Model ' + _slotChar(i) : m.name));
  fd.append('endpoint_url', m.url || '');
  fd.append('model', m.id || '');
  if (m.endpointId) {
    fd.append('endpoint_id', m.endpointId);
    fd.append('skip_validation', 'true');
  }
  const res = await fetch(`${state.API_BASE}/api/session`, { method: 'POST', body: fd });
  if (!res.ok) return;
  const data = await res.json();

  // Update arrays
  state._selectedModels.push({ model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name, endpointName: m.endpointName || '' });
  state._paneSessionIds.push(data.id);
  state._paneGenerationSettings.push({ thinking_mode: '', temperature_override: null, max_tokens_override: null });
  state._paneMetrics.push(null);
  state._abortControllers.push(null);
  _persistSelections();
  if (window._updateCheckBtnState) window._updateCheckBtnState();

  // Build pane DOM
  const label = state._blindMode ? 'Model ' + _slotChar(i) : m.name;
  const pane = document.createElement('div');
  pane.className = 'compare-pane';
  pane.dataset.pane = String(i);
  pane.innerHTML =
    '<div class="pane-header">' +
      '<div class="pane-header-row pane-header-primary"><button class="pane-title pane-title-btn" id="cmp-title-' + i + '" data-pane="' + i + '" type="button">' + escapeHtml(label) + ' <span class="pane-title-caret">&#x25BE;</span></button><div class="pane-primary-actions">' +
        '<button class="pane-action-btn" data-action="expand" data-pane="' + i + '" title="Expand">' + ICON_EXPAND + '</button>' +
        paneSettingsButtonHtml(i) +
        '<button class="close-btn pane-close-btn" data-action="close" data-pane="' + i + '" title="Remove pane"></button></div></div>' +
      '<div class="pane-header-row pane-header-secondary"><div class="pane-stats">' + _paneModeBadgeHtml(i) +
        '<span class="pane-timer" id="cmp-timer-' + i + '"></span><span class="pane-summary" id="cmp-summary-' + i + '" role="button" tabindex="0" aria-label="Show response metrics"></span><span class="pane-finish-badge" id="cmp-badge-' + i + '"></span></div>' +
      '<div class="pane-actions">' +
        '<button class="pane-action-btn pane-stop-btn" data-action="stop" data-pane="' + i + '" title="Stop" style="display:none;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>' +
        '<button class="pane-action-btn pane-preview-btn" data-action="preview" data-pane="' + i + '" id="cmp-preview-' + i + '" title="Run preview" style="display:none;">' + ICON_PLAY + '</button>' +
        '<button class="pane-action-btn pane-needs-response" data-action="reroll" data-pane="' + i + '" title="Re-roll" style="display:none;">' + ICON_REROLL + '</button>' +
        '<button class="pane-action-btn pane-needs-response" data-action="copy" data-pane="' + i + '" title="Copy" style="display:none;">' + ICON_COPY + '</button></div></div>' +
    '</div>' +
    '<div class="chat-history" id="cmp-history-' + i + '"></div>' +
    '<iframe class="compare-pane-iframe" id="cmp-iframe-' + i + '" sandbox="allow-scripts" style="display:none;"></iframe>' +
    '<div class="pane-vote-footer">' +
      '<button class="pane-vote-btn" data-pane="' + i + '" type="button" disabled style="opacity:0.4;">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;vertical-align:-2px;"><polyline points="20 6 9 17 4 12"/></svg>' +
        '<span class="pane-vote-label">Vote ' + escapeHtml(label) + '</span>' +
      '</button>' +
    '</div>';

  // Append to grid
  const grid = document.querySelector('.compare-grid');
  grid.appendChild(pane);

  // Update grid columns
  const n = state._selectedModels.length;
  grid.dataset.cols = String(Math.min(n, 4));
  syncShuffleButtonPlacement(n > 2);
  refreshMobilePaneTabs(i);
  if (_isMobileCompare()) {
    activateMobilePane(i);
    requestAnimationFrame(() => {
      document.querySelector('.compare-mobile-add')?.scrollIntoView({ block: 'nearest', inline: 'end', behavior: 'smooth' });
    });
  }

  // Update header label
  const headerSpan = document.querySelector('.compare-active > div:first-child span');
  if (headerSpan) {
    const modeLabel = ({ search: ' search providers', agent: ' agents', research: ' research models' }[state._compareMode] || ' models');
    headerSpan.textContent = 'Comparing' + modeLabel +
      (state._blindMode ? ' (blind)' : '') + ' \u00b7 ' + state._timeout + 's timeout';
  }

  // Rebuild vote bar
  buildVoteBar(n);

}

/** Remove a pane from the compare grid. If only 1 remains, exit compare mode. */
function _removePane(paneIdx) {
  if (state._streaming) return;

  // Abort if streaming
  if (state._abortControllers[paneIdx]) state._abortControllers[paneIdx].abort();

  // Delete the session
  const sid = state._paneSessionIds[paneIdx];
  if (sid) {
    fetch(`${state.API_BASE}/api/session/${sid}`, { method: 'DELETE' }).catch(() => {});
  }

  // Remove from arrays
  state._selectedModels.splice(paneIdx, 1);
  state._paneSessionIds.splice(paneIdx, 1);
  state._paneGenerationSettings.splice(paneIdx, 1);
  state._paneMetrics.splice(paneIdx, 1);
  state._abortControllers.splice(paneIdx, 1);
  _persistSelections();
  if (window._updateCheckBtnState) window._updateCheckBtnState();

  // If no panes left, exit compare mode
  if (state._selectedModels.length === 0) {
    if (_deactivate) _deactivate(true);
    return;
  }

  // Rebuild pane DOM — re-index all panes so IDs stay consistent
  const grid = document.querySelector('.compare-grid');
  grid.querySelectorAll('.compare-pane').forEach(p => p.remove());

  const n = state._selectedModels.length;
  syncShuffleButtonPlacement(n > 2);
  for (let i = 0; i < n; i++) {
    const label = state._blindMode ? 'Model ' + _slotChar(i) : state._selectedModels[i].name;
    const pane = document.createElement('div');
    pane.className = 'compare-pane';
    pane.dataset.pane = String(i);
    pane.innerHTML =
      '<div class="pane-header">' +
        '<div class="pane-header-row pane-header-primary"><button class="pane-title pane-title-btn" id="cmp-title-' + i + '" data-pane="' + i + '" type="button">' + escapeHtml(label) + ' <span class="pane-title-caret">&#x25BE;</span></button><div class="pane-primary-actions">' +
          '<button class="pane-action-btn" data-action="expand" data-pane="' + i + '" title="Expand">' + ICON_EXPAND + '</button>' +
          paneSettingsButtonHtml(i) +
          '<button class="close-btn pane-close-btn" data-action="close" data-pane="' + i + '" title="Remove pane"></button></div></div>' +
        '<div class="pane-header-row pane-header-secondary"><div class="pane-stats">' + _paneModeBadgeHtml(i) +
          '<span class="pane-timer" id="cmp-timer-' + i + '"></span><span class="pane-summary" id="cmp-summary-' + i + '" role="button" tabindex="0" aria-label="Show response metrics"></span><span class="pane-finish-badge" id="cmp-badge-' + i + '"></span></div>' +
        '<div class="pane-actions">' +
          '<button class="pane-action-btn pane-stop-btn" data-action="stop" data-pane="' + i + '" title="Stop" style="display:none;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>' +
          '<button class="pane-action-btn pane-preview-btn" data-action="preview" data-pane="' + i + '" id="cmp-preview-' + i + '" title="Run preview" style="display:none;">' + ICON_PLAY + '</button>' +
          '<button class="pane-action-btn pane-needs-response" data-action="reroll" data-pane="' + i + '" title="Re-roll" style="display:none;">' + ICON_REROLL + '</button>' +
          '<button class="pane-action-btn pane-needs-response" data-action="copy" data-pane="' + i + '" title="Copy" style="display:none;">' + ICON_COPY + '</button></div></div>' +
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

  // Update grid columns
  grid.dataset.cols = String(Math.min(n, 4));

  // Update header label
  const headerSpan = document.querySelector('.compare-active > div:first-child span');
  if (headerSpan) {
    const modeLabel = ({ search: ' search providers', agent: ' agents', research: ' research models' }[state._compareMode] || ' models');
    headerSpan.textContent = 'Comparing' + modeLabel +
      (state._blindMode ? ' (blind)' : '') + ' \u00b7 ' + state._timeout + 's timeout';
  }

  // Rebuild vote bar
  buildVoteBar(n);
  refreshMobilePaneTabs(Math.min(paneIdx, n - 1));
}

/** Show a dropdown under the pane title to swap the model for that pane. */
function _showModelSwapDropdown(paneIdx, titleBtn) {
  // Don't allow swaps while streaming
  if (state._streaming) {
    uiModule.showToast('Stop the response or wait for it to finish before changing models.');
    return;
  }

  // Remove any existing dropdown
  const existing = document.querySelector('.pane-model-dropdown');
  if (existing) { if (typeof existing._dismiss === 'function') existing._dismiss(); else existing.remove(); return; }

  const _effectiveType = (state._compareMode === 'agent' || state._compareMode === 'research') ? 'chat' : state._compareMode;
  const filtered = state._cachedModels.filter(m => m.type === _effectiveType);
  if (filtered.length === 0) return;

  const dropdown = document.createElement('div');
  dropdown.className = 'pane-model-dropdown';
  let closeMenu = () => dropdown.remove();

  filtered.forEach(m => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'pane-model-item';
    const label = m.endpointName ? m.name + ' (' + m.endpointName + ')' : m.name;
    item.textContent = label;
    // Highlight current model
    if (state._selectedModels[paneIdx] && state._selectedModels[paneIdx].model === m.id
        && state._selectedModels[paneIdx].endpointId === m.endpointId) {
      item.classList.add('current');
    }
    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      closeMenu();

      const fd = new FormData();
      // Blind mode: neutral slot name only — never leak the model (issue #1285).
      fd.append('name', '[CMP] ' + (state._blindMode ? 'Model ' + _slotChar(paneIdx) : m.name));
      fd.append('endpoint_url', m.url || '');
      fd.append('model', m.id || '');
      if (m.endpointId) {
        fd.append('endpoint_id', m.endpointId);
        fd.append('skip_validation', 'true');
      }
      let newSessionId = '';
      try {
        const res = await fetch(`${state.API_BASE}/api/session`, { method: 'POST', body: fd });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        newSessionId = data.id || '';
        if (!newSessionId) throw new Error('Missing session id');
      } catch (err) {
        console.error('Failed to create session for swapped model:', err);
        if (uiModule?.showError) uiModule.showError('Failed to swap compare model: ' + (err?.message || 'unknown'));
        return;
      }

      const oldSid = state._paneSessionIds[paneIdx];
      state._selectedModels[paneIdx] = {
        model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name,
      };
      state._paneSessionIds[paneIdx] = newSessionId;
      await _savePaneGenerationSettings(paneIdx, {});
      _persistSelections();
      if (window._updateCheckBtnState) window._updateCheckBtnState();
      if (oldSid) {
        fetch(`${state.API_BASE}/api/session/${oldSid}`, { method: 'DELETE' }).catch(() => {});
      }

      // Update title display
      const titleEl = document.getElementById('cmp-title-' + paneIdx);
      if (titleEl) {
        const displayName = state._blindMode
          ? 'Model ' + _slotChar(paneIdx)
          : m.name;
        titleEl.innerHTML = escapeHtml(displayName) + ' <span class="pane-title-caret">&#x25BE;</span>';
      }

      // Clear pane history for fresh start
      const hist = document.getElementById('cmp-history-' + paneIdx);
      if (hist) { hist.innerHTML = ''; hist.style.display = ''; }
      const iframe = document.getElementById('cmp-iframe-' + paneIdx);
      if (iframe) { iframe.srcdoc = ''; iframe.style.display = 'none'; iframe._htmlCode = null; }
      const previewBtn = document.getElementById('cmp-preview-' + paneIdx);
      if (previewBtn) { previewBtn.style.display = 'none'; previewBtn.classList.remove('active'); }
      const badge = document.getElementById('cmp-badge-' + paneIdx);
      if (badge) { badge.textContent = ''; badge.style.color = ''; }
      const summary = document.getElementById('cmp-summary-' + paneIdx);
      if (summary) { summary.textContent = ''; summary.title = ''; }
      refreshMobilePaneTabs(paneIdx);
    });
    dropdown.appendChild(item);
  });

  // Position relative to the viewport (fixed) and append to document.body so
  // the dropdown can't be clipped by the narrow pane's overflow or run off the
  // screen edge on mobile (matches the "+" add-pane picker behaviour).
  const rect = titleBtn.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight, margin = 8;
  dropdown.style.position = 'fixed';
  dropdown.style.zIndex = '100000';
  dropdown.style.maxWidth = (vw - margin * 2) + 'px';
  dropdown.style.overflowY = 'auto';
  dropdown.style.left = '-9999px';
  dropdown.style.top = '0';
  document.body.appendChild(dropdown);
  const ddRect = dropdown.getBoundingClientRect();
  const ddW = ddRect.width, ddH = ddRect.height;
  let left = rect.left;
  if (left + ddW > vw - margin) left = vw - margin - ddW;
  if (left < margin) left = margin;
  const spaceBelow = vh - rect.bottom, spaceAbove = rect.top;
  let top;
  if (spaceBelow >= ddH + margin || spaceBelow >= spaceAbove) {
    top = Math.min(rect.bottom + 4, vh - margin - Math.min(ddH, vh - margin * 2));
  } else {
    top = Math.max(margin, rect.top - 4 - ddH);
  }
  dropdown.style.left = left + 'px';
  dropdown.style.top = top + 'px';
  dropdown.style.maxHeight = Math.min(ddH, vh - margin * 2) + 'px';

  // Close on outside click or Escape (the latter via the registry).
  closeMenu = bindMenuDismiss(dropdown, () => dropdown.remove(), (e) => !dropdown.contains(e.target) && e.target !== titleBtn);}

// ── Shuffle / reset ──

function shufflePanePositions() {
  if (state._streaming) return;
  // Remove shuffle prompt bubble if present
  const shuffleBtn = document.getElementById('compare-shuffle-btn');
  if (shuffleBtn) { const b = shuffleBtn.querySelector('div'); if (b) b.remove(); }
  const n = state._selectedModels.length;
  if (n < 2) return;
  _showShuffleNotice();

  // Fisher-Yates shuffle to get new order
  const indices = Array.from({ length: n }, (_, i) => i);
  for (let i = indices.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [indices[i], indices[j]] = [indices[j], indices[i]];
  }

  // Reorder internal state
  const newModels = indices.map(i => state._selectedModels[i]);
  const newSessionIds = indices.map(i => state._paneSessionIds[i]);
  const newGenerationSettings = indices.map(i => state._paneGenerationSettings[i]);
  const newMetrics = indices.map(i => state._paneMetrics[i]);

  // Collect pane contents (HTML) before swapping
  const paneContents = [];
  const paneClasses = [];
  for (let i = 0; i < n; i++) {
    const hist = document.getElementById('cmp-history-' + i);
    paneContents.push(hist ? hist.innerHTML : '');
    const pane = document.querySelector(`.compare-pane[data-pane="${i}"]`);
    paneClasses.push(pane ? { winner: pane.classList.contains('winner'), loser: pane.classList.contains('loser') } : {});
  }

  // Apply shuffled state
  state._selectedModels = newModels;
  state._paneSessionIds = newSessionIds;
  state._paneGenerationSettings = newGenerationSettings;
  state._paneMetrics = newMetrics;

  // Spin the shuffle button dice icon
  const shuffleBtn2 = document.getElementById('compare-shuffle-btn');
  if (shuffleBtn2) {
    const diceSvg = shuffleBtn2.querySelector('svg');
    if (diceSvg) {
      diceSvg.style.transition = 'transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
      diceSvg.style.transform = 'rotate(360deg)';
      setTimeout(() => { diceSvg.style.transition = ''; diceSvg.style.transform = ''; }, 400);
    }
  }

  // Shake panes and flash titles
  for (let i = 0; i < n; i++) {
    const pane = document.querySelector(`.compare-pane[data-pane="${i}"]`);
    if (pane) {
      pane.style.animation = 'pane-shake 0.3s ease';
      pane.addEventListener('animationend', () => { pane.style.animation = ''; }, { once: true });
    }
    const titleEl = document.getElementById('cmp-title-' + i);
    if (titleEl) {
      titleEl.style.transition = 'opacity 0.12s ease, transform 0.12s ease';
      titleEl.style.opacity = '0.3';
      titleEl.style.transform = 'scale(0.9)';
      titleEl.innerHTML = '?';
    }
    const hist = document.getElementById('cmp-history-' + i);
    if (hist) {
      hist.style.transition = 'opacity 0.15s ease';
      hist.style.opacity = '0';
    }
  }

  setTimeout(() => {
    for (let i = 0; i < n; i++) {
      const hist = document.getElementById('cmp-history-' + i);
      const pane = document.querySelector(`.compare-pane[data-pane="${i}"]`);
      const titleEl = document.getElementById('cmp-title-' + i);
      const badge = document.getElementById('cmp-badge-' + i);
      const src = indices[i];

      if (hist) hist.innerHTML = paneContents[src];
      if (titleEl) {
        const lbl = state._blindMode ? 'Model ' + _slotChar(i) : state._selectedModels[i].name;
        titleEl.innerHTML = escapeHtml(lbl) + ' <span class="pane-title-caret">&#x25BE;</span>';
        titleEl.style.transition = 'opacity 0.25s ease, transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1)';
        titleEl.style.opacity = '1';
        titleEl.style.transform = 'scale(1)';
      }
      if (badge) { badge.textContent = ''; badge.style.color = ''; }
      if (pane) {
        pane.classList.toggle('winner', !!paneClasses[src].winner);
        pane.classList.toggle('loser', !!paneClasses[src].loser);
      }
      if (hist) {
        hist.style.transition = 'opacity 0.25s ease';
        hist.style.opacity = '1';
      }
    }
  }, 200);

  // Re-enable blind mode after shuffle
  state._blindMode = true;

  // Rebuild vote bar with new labels
  setTimeout(() => {
    buildVoteBar(n);
    refreshMobilePaneTabs();
  }, 250);
}

function resetCompare() {
  if (state._streaming) stopAll();
  const n = state._selectedModels.length;

  // Clear last prompt so vote buttons are disabled until next prompt
  state._lastPrompt = '';
  state._expectedAnswer = '';
  const expected = document.getElementById('cmp-eval-expected');
  if (expected) {
    expected.classList.add('hidden');
    const value = expected.querySelector('.cmp-eval-expected-value');
    if (value) value.textContent = '';
  }

  // Reset finish badges, titles, winner/loser state
  state._finishOrder = 0;
  state._paneMetrics = new Array(n).fill(null);
  const panes = document.querySelectorAll('.compare-pane');
  for (let i = 0; i < n; i++) {
    const badge = document.getElementById('cmp-badge-' + i);
    if (badge) { badge.textContent = ''; badge.style.color = ''; }
    const summary = document.getElementById('cmp-summary-' + i);
    if (summary) { summary.textContent = ''; summary.title = ''; }
    const titleEl = document.getElementById('cmp-title-' + i);
    if (titleEl) {
      const lbl = state._blindMode ? 'Model ' + _slotChar(i) : state._selectedModels[i].name;
      titleEl.innerHTML = escapeHtml(lbl) + ' <span class="pane-title-caret">&#x25BE;</span>';
    }
    if (panes[i]) {
      panes[i].classList.remove('winner', 'loser', 'is-streaming', 'is-awaiting-input', 'is-done', 'is-failed');
    }

    // Clear all messages from pane history
    const hist = document.getElementById('cmp-history-' + i);
    if (hist) { hist.innerHTML = ''; hist.style.display = ''; }

    // Reset iframe preview
    const iframe = document.getElementById('cmp-iframe-' + i);
    if (iframe) { iframe.srcdoc = ''; iframe.style.display = 'none'; iframe._htmlCode = null; }
    const previewBtn = document.getElementById('cmp-preview-' + i);
    if (previewBtn) { previewBtn.style.display = 'none'; previewBtn.classList.remove('active'); }
  }

  // Re-enable vote bar
  buildVoteBar(n);

  // Focus input for next prompt
  const ta = document.getElementById('message');
  if (ta) ta.focus();
}

export {
  registerPaneActions,
  stopAll,
  stopPane,
  rerollPane,
  toggleExpandPane,
  togglePanePreview,
  _autoPreviewHtml,
  _extractHtmlFromText,
  copyPaneResponse,
  _addPane,
  _createAndAppendPane,
  _removePane,
  _showModelSwapDropdown,
  shufflePanePositions,
  resetCompare,
  mountMobilePaneTabs,
  syncShuffleButtonPlacement,
  activateMobilePane,
  refreshMobilePaneTabs,
  paneSettingsButtonHtml,
  togglePaneSettings,
};
