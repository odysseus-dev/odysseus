// compare/panes.js — pane lifecycle, actions, layout
import state from './state.js';
import { _persistSelections } from './models.js';
import { buildVoteBar } from './vote.js';
import {
  ICON_REROLL, ICON_COPY, ICON_EXPAND, ICON_COLLAPSE, ICON_CLOSE,
  ICON_PLAY, ICON_CODE, SEND_SVG,
} from './icons.js';
import { _clearProbeWaves } from './probe.js';
import Storage from '../storage.js';
import uiModule from '../ui.js';
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
  // Horizontal: align dropdown's right edge with the button's, then
  // clamp so the dropdown stays within [margin, vw - margin].
  let left = btnRect.right - ddW;
  if (left + ddW > vw - margin) left = vw - margin - ddW;
  if (left < margin) left = margin;
  // Vertical: drop below the button if there's room, otherwise above.
  const spaceBelow = vh - btnRect.bottom;
  const spaceAbove = btnRect.top;
  let top;
  if (spaceBelow >= ddH + margin || spaceBelow >= spaceAbove) {
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
      '<button class="pane-title pane-title-btn" id="cmp-title-' + i + '" data-pane="' + i + '" type="button">' + escapeHtml(label) + ' <span class="pane-title-caret">&#x25BE;</span></button>' +
      '<span class="pane-timer" id="cmp-timer-' + i + '"></span>' +
        '<span class="pane-finish-badge" id="cmp-badge-' + i + '"></span>' +
      '<div class="pane-actions">' +
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

  // Append to grid
  const grid = document.querySelector('.compare-grid');
  grid.appendChild(pane);

  // Update grid columns
  const n = state._selectedModels.length;
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

  // Prompt to shuffle in blind mode — tooltip bubble next to Shuffle button
  if (state._blindMode && n > 2) {
    const shuffleBtn = document.getElementById('compare-shuffle-btn');
    if (shuffleBtn) {
      const bubble = document.createElement('div');
      bubble.style.cssText = 'position:absolute;top:100%;right:0;margin-top:6px;background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:11px;white-space:nowrap;z-index:10000;box-shadow:0 4px 12px rgba(0,0,0,0.25);pointer-events:none;opacity:0;transition:opacity 0.2s;';
      bubble.textContent = 'Shuffle models?';
      shuffleBtn.style.position = 'relative';
      shuffleBtn.appendChild(bubble);
      requestAnimationFrame(() => { bubble.style.opacity = '1'; });
      setTimeout(() => { bubble.style.opacity = '0'; setTimeout(() => bubble.remove(), 200); }, 4000);
    }
  }
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
  for (let i = 0; i < n; i++) {
    const label = state._blindMode ? 'Model ' + _slotChar(i) : state._selectedModels[i].name;
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
          '<button class="pane-action-btn pane-needs-response" data-action="reroll" data-pane="' + i + '" title="Re-roll" style="display:none;">' + ICON_REROLL + '</button>' +
          '<button class="pane-action-btn pane-needs-response" data-action="copy" data-pane="' + i + '" title="Copy" style="display:none;">' + ICON_COPY + '</button>' +
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
}

/** Show a dropdown under the pane title to swap the model for that pane. */
function _showModelSwapDropdown(paneIdx, titleBtn) {
  // Don't allow swaps while streaming
  if (state._streaming) return;

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

      // Update the model for this pane and persist
      state._selectedModels[paneIdx] = {
        model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name,
      };
      _persistSelections();
      if (window._updateCheckBtnState) window._updateCheckBtnState();

      // Delete old session, create new one
      const oldSid = state._paneSessionIds[paneIdx];
      if (oldSid) {
        fetch(`${state.API_BASE}/api/session/${oldSid}`, { method: 'DELETE' }).catch(() => {});
      }
      const fd = new FormData();
      // Blind mode: neutral slot name only — never leak the model (issue #1285).
      fd.append('name', '[CMP] ' + (state._blindMode ? 'Model ' + _slotChar(paneIdx) : m.name));
      fd.append('endpoint_url', m.url || '');
      fd.append('model', m.id || '');
      if (m.endpointId) {
        fd.append('endpoint_id', m.endpointId);
        fd.append('skip_validation', 'true');
      }
      try {
        const res = await fetch(`${state.API_BASE}/api/session`, { method: 'POST', body: fd });
        const data = await res.json();
        state._paneSessionIds[paneIdx] = data.id;
      } catch (err) {
        console.error('Failed to create session for swapped model:', err);
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

  // Fisher-Yates shuffle to get new order
  const indices = Array.from({ length: n }, (_, i) => i);
  for (let i = indices.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [indices[i], indices[j]] = [indices[j], indices[i]];
  }

  // Reorder internal state
  const newModels = indices.map(i => state._selectedModels[i]);
  const newSessionIds = indices.map(i => state._paneSessionIds[i]);
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
  setTimeout(() => buildVoteBar(n), 250);
}

function resetCompare() {
  if (state._streaming) stopAll();
  const n = state._selectedModels.length;

  // Clear last prompt so vote buttons are disabled until next prompt
  state._lastPrompt = '';

  // Reset finish badges, titles, winner/loser state
  state._finishOrder = 0;
  state._paneMetrics = new Array(n).fill(null);
  const panes = document.querySelectorAll('.compare-pane');
  for (let i = 0; i < n; i++) {
    const badge = document.getElementById('cmp-badge-' + i);
    if (badge) { badge.textContent = ''; badge.style.color = ''; }
    const titleEl = document.getElementById('cmp-title-' + i);
    if (titleEl) {
      const lbl = state._blindMode ? 'Model ' + _slotChar(i) : state._selectedModels[i].name;
      titleEl.innerHTML = escapeHtml(lbl) + ' <span class="pane-title-caret">&#x25BE;</span>';
    }
    if (panes[i]) { panes[i].classList.remove('winner', 'loser'); }

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
};
