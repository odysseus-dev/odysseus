// compare/selector.js — model selection modal
import state from './state.js';
import Storage from '../storage.js';
import { fetchModels, _persistSelections, getExcludedModels } from './models.js';
import { showScoreboard } from './scoreboard.js';
import { EYE_OPEN, EYE_CLOSED, ICON_DICE, ICON_PARALLEL, ICON_SEQUENTIAL, SAVE_ICON, WAVE_FRAMES, CHAT_ICON } from './icons.js';
import { _clearProbeWaves } from './probe.js';
import uiModule from '../ui.js';
import spinnerModule from '../spinner.js';
import themeModule from '../theme.js';

const escapeHtml = uiModule.esc;

// Match the Deep Research "Start" button (play icon + "Start", styled by
// .research-start-btn) so the two primary actions look identical.
const _CMP_PLAY_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
const _CMP_START_LABEL = _CMP_PLAY_ICON + ' Start';

/** Slot label: letters (A, B) in parallel, numbers (1, 2) in sequential */
function _slotChar(i) { return state._parallel ? String.fromCharCode(65 + i) : String(i + 1); }

/** Sync the Compare toolbar indicator button state. */
function _syncToolbarIndicator(active) {
  // The old red-accent "Compare active — click to deactivate" chip is no longer
  // shown — Compare is exited from its own header bar, so the input-bar tool
  // indicator was redundant. Keep it hidden regardless of state.
  const indicator = document.getElementById('compare-indicator-btn');
  if (indicator) {
    indicator.style.display = 'none';
    indicator.classList.remove('active');
  }
  // Notify app.js to update the plus-dot indicator
  document.dispatchEvent(new CustomEvent('overflow-state-change'));
}

/** Disable tool toggles (web, bash, RAG, research) for clean comparison. */
function disableToolToggles() {
  const ids = ['web-toggle', 'bash-toggle', 'rag-toggle', 'research-toggle'];
  state._savedToggles = {};
  ids.forEach(id => {
    const chk = document.getElementById(id);
    if (chk) {
      state._savedToggles[id] = chk.checked;
      if (chk.checked) { chk.checked = false; chk.dispatchEvent(new Event('change')); }
    }
  });
}

/** Restore tool toggles to pre-compare state. */
function restoreToolToggles() {
  if (!state._savedToggles) return;
  Object.entries(state._savedToggles).forEach(([id, wasChecked]) => {
    const chk = document.getElementById(id);
    if (chk && wasChecked && !chk.checked) { chk.checked = true; chk.dispatchEvent(new Event('change')); }
  });
  state._savedToggles = null;
}

/** Show model selection modal with dynamic model list + toggles. */
async function showModelSelector() {
  return new Promise((resolve) => {
    let models = [];
    let _modelsLoaded = false;

    const overlay = document.createElement('div');
    overlay.id = 'compare-model-overlay';
    overlay.className = 'modal';

    const content = document.createElement('div');
    content.className = 'modal-content';
    content.style.width = 'min(520px, 92vw)';

    // ── Header (draggable) ──
    const header = document.createElement('div');
    header.className = 'modal-header';

    const title = document.createElement('h4');
    title.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/></svg>Model Comparison';
    // Absorb the free space so the injected minimize (_) and close (✕) cluster
    // together on the right instead of being spread apart by space-between.
    title.style.marginRight = 'auto';
    header.appendChild(title);

    // Minimize (_) + Close (✕) grouped in one wrapper so they're always
    // adjacent on the right (the auto-injected minimize otherwise drifted
    // away from the close). The minimize carries .minimize-btn so the modal
    // manager wires it instead of injecting a second one.
    const headerCtrls = document.createElement('div');
    headerCtrls.style.cssText = 'display:flex;align-items:center;gap:6px;flex-shrink:0;';

    const headerMinBtn = document.createElement('button');
    headerMinBtn.type = 'button';
    headerMinBtn.className = 'modal-minimize-btn minimize-btn';
    headerMinBtn.title = 'Minimize';
    headerMinBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="5" y1="18" x2="19" y2="18"/></svg>';
    headerMinBtn.style.margin = '0';

    const headerCloseBtn = document.createElement('button');
    headerCloseBtn.className = 'close-btn';
    headerCloseBtn.innerHTML = '&#x2716;';
    headerCloseBtn.style.cssText = 'flex-shrink:0;margin:0;';
    headerCloseBtn.addEventListener('click', () => cleanup(false));

    headerCtrls.appendChild(headerMinBtn);
    headerCtrls.appendChild(headerCloseBtn);

    // Toggle icons container
    const toggleRow = document.createElement('div');
    toggleRow.style.cssText = 'display:flex;gap:4px;align-items:flex-start;margin-left:auto;margin-right:8px;';

    function _toggleLabel(text) {
      return '<span class="compare-toggle-label">' + text + '</span>';
    }

    state._blindMode = true;
    const blindBtn = document.createElement('button');
    blindBtn.type = 'button';
    blindBtn.className = 'compare-blind-toggle active';
    blindBtn.title = 'Blind Mode — hide model names until you vote';
    blindBtn.innerHTML = EYE_CLOSED + _toggleLabel('Blind');
    blindBtn.addEventListener('click', () => {
      state._blindMode = !state._blindMode;
      blindBtn.classList.toggle('active', state._blindMode);
      blindBtn.innerHTML = (state._blindMode ? EYE_CLOSED : EYE_OPEN) + _toggleLabel('Blind');
      // Turning off blind mode reveals shuffled models
      if (!state._blindMode && _shuffled) {
        _shuffled = false;
        diceBtn.classList.remove('active');
      }
      renderModelRows();
      // Mobile hides the button labels — surface the new state as a toast.
      uiModule.showToast('Mode: Blind ' + (state._blindMode ? 'on' : 'off'));
      _updateModeLabel();
      _setModeHint(state._blindMode
        ? '<span style="color:var(--color-blind-orange)">Blind mode</span>: model names stay hidden until you vote.'
        : '<span style="color:var(--color-blind-orange)">Blind mode off</span>: model names are shown.');
    });
    toggleRow.appendChild(blindBtn);

    // Parallel / Sequential toggle — right after blind
    state._parallel = true;
    const parallelBtn = document.createElement('button');
    parallelBtn.type = 'button';
    parallelBtn.className = 'compare-parallel-toggle active';
    parallelBtn.title = 'Parallel — run all models at once vs one at a time';
    parallelBtn.innerHTML = ICON_PARALLEL + _toggleLabel('Parallel');
    parallelBtn.addEventListener('click', () => {
      state._parallel = !state._parallel;
      parallelBtn.classList.toggle('active', state._parallel);
      parallelBtn.innerHTML = (state._parallel ? ICON_PARALLEL : ICON_SEQUENTIAL) + _toggleLabel(state._parallel ? 'Parallel' : 'Sequential');
      parallelBtn.title = state._parallel ? 'Switch to one at a time' : 'Run side by side';
      renderModelRows();
      uiModule.showToast('Mode: ' + (state._parallel ? 'Parallel' : 'Sequential'));
      _updateModeLabel();
      _setModeHint(state._parallel
        ? '<span style="color:#5b8def">Parallel</span>: all models answer at once, side by side.'
        : '<span style="color:#e0a050">Sequential</span>: models answer one at a time.');
    });
    toggleRow.appendChild(parallelBtn);

    // Dice / shuffle button — next to blind toggle
    const diceBtn = document.createElement('button');
    diceBtn.type = 'button';
    diceBtn.className = 'compare-dice-toggle';
    diceBtn.title = 'Shuffle — randomly pick models for each slot';
    diceBtn.innerHTML = ICON_DICE + _toggleLabel('Shuffle');
    diceBtn.addEventListener('click', () => {
      if (!_modelsLoaded) return;
      // Toggle off if already shuffled
      if (_shuffled) {
        _shuffled = false;
        diceBtn.classList.remove('active');
        renderModelRows();
        uiModule.showToast('Mode: Shuffle off');
        _updateModeLabel();
        _setModeHint('<span style="color:var(--red)">Shuffle off</span>: choose the models yourself.');
        return;
      }
      // Randomly pick models from filtered list for each slot
      const excluded = getExcludedModels();
      const pool = filteredModels().filter(m => !excluded.includes(m.id)).slice();
      if (pool.length === 0) return;
      for (let i = pool.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      for (let i = 0; i < selections.length; i++) {
        const m = pool[i % pool.length];
        selections[i] = { model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name, endpointName: m.endpointName || '' };
      }
      _shuffled = true;
      // Auto-enable blind mode so picks stay hidden
      if (!state._blindMode) {
        state._blindMode = true;
        blindBtn.classList.add('active');
        blindBtn.innerHTML = EYE_CLOSED + _toggleLabel('Blind');
      }
      renderModelRows();
      uiModule.showToast(state._blindMode ? 'Mode: Shuffle on · Blind on' : 'Mode: Shuffle on');
      _updateModeLabel();
      _setModeHint('<span style="color:var(--red)">Shuffle</span>: random models picked for each slot (auto-hidden).');
      // Show active state + spin only the dice icon
      diceBtn.classList.add('active');
      const diceSvg = diceBtn.querySelector('svg');
      if (diceSvg) {
        diceSvg.style.transition = 'transform 0.3s ease';
        diceSvg.style.transform = 'rotate(360deg)';
        setTimeout(() => { diceSvg.style.transition = ''; diceSvg.style.transform = ''; }, 300);
      }
    });
    toggleRow.appendChild(diceBtn);

    // (Pre-round "Shuffle models?" reminder removed at the user's request — the
    // running-state panes still show their own shuffle nudge.)
    function _remindShuffle() { /* no-op in the selector */ }

    state._continueChat = false;

    state._saveOnClose = false;
    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'compare-save-toggle';
    saveBtn.title = 'Save — keep sessions after closing compare';
    saveBtn.innerHTML = SAVE_ICON + _toggleLabel('Save');
    saveBtn.addEventListener('click', () => {
      state._saveOnClose = !state._saveOnClose;
      saveBtn.classList.toggle('active', state._saveOnClose);
      uiModule.showToast('Mode: Save ' + (state._saveOnClose ? 'on' : 'off'));
      _updateModeLabel();
      _setModeHint(state._saveOnClose
        ? '<span style="color:var(--color-save-green)">Save</span>: keep these sessions after you close Compare.'
        : '<span style="color:var(--color-save-green)">Save off</span>: sessions are discarded when you close Compare.');
    });
    toggleRow.appendChild(saveBtn);

    // Reset button
    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'compare-reset-toggle';
    resetBtn.title = 'Reset — restore all defaults';
    resetBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>' + _toggleLabel('Reset');
    resetBtn.addEventListener('click', () => {
      state._blindMode = true;
      blindBtn.classList.add('active');
      blindBtn.innerHTML = EYE_CLOSED + _toggleLabel('Blind');
      _shuffled = false;
      diceBtn.classList.remove('active');
      state._continueChat = false;
      state._saveOnClose = false;
      saveBtn.classList.remove('active');
      state._parallel = true;
      parallelBtn.classList.add('active');
      parallelBtn.innerHTML = ICON_PARALLEL + _toggleLabel('Parallel');
      selections = [null, null];
      renderModelRows();
    });
    toggleRow.appendChild(resetBtn);

    header.appendChild(headerCtrls);

    content.appendChild(header);

    // ── Body ──
    const body = document.createElement('div');
    body.className = 'modal-body';
    body.style.padding = '12px 16px';

    const desc = document.createElement('p');
    desc.style.cssText = 'color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:0.85em;margin:0 0 12px;';
    desc.textContent = 'Select models to compare side-by-side. Send the same prompt to all.';
    body.appendChild(desc);

    // Options row
    toggleRow.style.cssText = 'display:flex;gap:4px;align-items:flex-start;flex-wrap:wrap;';
    const modeWrap = document.createElement('div');
    modeWrap.className = 'compare-section';
    const modeLabel = document.createElement('div');
    modeLabel.className = 'compare-section-label';
    // The active modes (+colors) are appended in a span shown only on mobile,
    // where the toggle text labels are hidden so the icons alone are ambiguous.
    modeLabel.innerHTML = 'Mode: <span class="compare-mode-current"></span>';
    modeWrap.appendChild(modeLabel);
    modeWrap.appendChild(toggleRow);
    // Contextual one-liner describing the mode you just toggled.
    const modeHint = document.createElement('div');
    modeHint.className = 'compare-mode-hint';
    modeWrap.appendChild(modeHint);
    function _setModeHint(html) { modeHint.innerHTML = html || ''; }
    body.appendChild(modeWrap);

    // Reflect the active modes in the "Mode:" label, each in its icon's color.
    function _updateModeLabel() {
      const cur = modeLabel.querySelector('.compare-mode-current');
      if (!cur) return;
      const parts = [];
      if (state._blindMode) parts.push('<span style="color:var(--color-blind-orange)">Blind</span>');
      parts.push(state._parallel
        ? '<span style="color:#5b8def">Parallel</span>'
        : '<span style="color:#e0a050">Sequential</span>');
      if (_shuffled) parts.push('<span style="color:var(--red)">Shuffle</span>');
      if (state._saveOnClose) parts.push('<span style="color:var(--color-save-green)">Save</span>');
      cur.innerHTML = parts.join(', ');
    }

    // ── Type tabs (Chat / Agent / Search / Research) ──
    state._compareMode = 'chat';
    const typeWrap = document.createElement('div');
    typeWrap.className = 'compare-section';
    const typeLabel = document.createElement('div');
    typeLabel.className = 'compare-section-label';
    // The active type name (+icon) is appended in a span shown only on mobile,
    // where the tab text labels are hidden so the icons alone are ambiguous.
    typeLabel.innerHTML = 'Type: <span class="compare-type-current"></span>';
    typeWrap.appendChild(typeLabel);
    const tabBar = document.createElement('div');
    tabBar.className = 'compare-mode-tabs compare-type-tabs';
    // Agent — shell prompt `>_` (matches the bash-toggle-btn icon in the composer)
    const _ICON_AGENT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
    const _ICON_SEARCH = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
    // Research — magnifying glass with `+` (matches the sidebar Deep Research icon)
    const _ICON_RESEARCH = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
    const _modes = [
      { id: 'chat', label: 'Chat', icon: CHAT_ICON },
      { id: 'agent', label: 'Agent', icon: _ICON_AGENT },
      { id: 'search', label: 'Search', icon: _ICON_SEARCH },
      { id: 'research', label: 'Research', icon: _ICON_RESEARCH },
    ];
    _modes.forEach(m => {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'compare-mode-tab' + (m.id === 'chat' ? ' active' : '');
      tab.innerHTML = m.icon + '<span class="compare-toggle-label">' + m.label + '</span>';
      tab.dataset.mode = m.id;
      tab.addEventListener('click', () => setModeTab(m.id));
      tabBar.appendChild(tab);
    });
    // Reflect the active type in the "Type:" label (icon + name) for mobile.
    function _updateTypeLabel(mode) {
      const cur = typeLabel.querySelector('.compare-type-current');
      const m = _modes.find(x => x.id === mode);
      if (cur && m) cur.innerHTML = m.icon + '<span>' + m.label + '</span>';
    }
    _updateTypeLabel('chat');
    typeWrap.appendChild(tabBar);
    body.appendChild(typeWrap);

    // Per-tab selection memory
    const _tabSelections = { chat: null, agent: null, search: null, research: null };

    function setModeTab(mode) {
      if (!_modelsLoaded) return;
      // Save current tab's selections before switching
      _tabSelections[state._compareMode] = selections.map(s => s ? { ...s } : null);
      state._compareMode = mode;
      tabBar.querySelectorAll('.compare-mode-tab').forEach(t => t.classList.remove('active'));
      const activeTab = tabBar.querySelector(`[data-mode="${mode}"]`);
      if (activeTab) activeTab.classList.add('active');
      _updateTypeLabel(mode);
      _shuffled = false;
      diceBtn.classList.remove('active');
      // Search and Research default to sequential; others default to parallel
      if (mode === 'search' || mode === 'research') {
        state._parallel = false;
        parallelBtn.classList.remove('active');
        parallelBtn.innerHTML = ICON_SEQUENTIAL + _toggleLabel('Sequential');
      } else {
        state._parallel = true;
        parallelBtn.classList.add('active');
        parallelBtn.innerHTML = ICON_PARALLEL + _toggleLabel('Parallel');
      }
      // Restore saved selections for this tab, or default
      selections = _tabSelections[mode] ? _tabSelections[mode].slice() : [null, null];
      _updateModeLabel();
      _setModeHint('');
      renderModelRows();
    }
    // Tab click listeners are set in the loop above

    // ── Model list ──
    const listContainer = document.createElement('div');
    body.appendChild(listContainer);

    // Show loading state immediately with spinner
    const _loadingDiv = document.createElement('div');
    _loadingDiv.style.cssText = 'color:color-mix(in srgb, var(--fg) 40%, transparent);font-size:0.85em;padding:12px 0;text-align:left;';
    if (spinnerModule) {
      const _loadSpinner = spinnerModule.create('Loading models', 'right');
      _loadingDiv.appendChild(_loadSpinner.createElement());
      _loadSpinner.start();
    } else {
      _loadingDiv.textContent = 'Loading models\u2026';
    }
    listContainer.appendChild(_loadingDiv);

    // Restore last used selections from storage (per-mode)
    const _selKey = 'odysseus-compare-selections-' + (state._compareMode || 'chat');
    let selections = Storage.getJSON(_selKey) || Storage.getJSON('odysseus-compare-selections') || [];
    // Restore synthesis models for search/research
    if (state._compareMode === 'search' || state._compareMode === 'research') {
      const savedSynth = Storage.getJSON('odysseus-compare-synth-' + state._compareMode);
      if (savedSynth) state._searchSynthModels = savedSynth;
    }
    // Validate saved selections against available models (done after models load)
    let _needsValidation = selections.length > 0;
    let addBtn = null;
    let _shuffled = false;
    _updateModeLabel(); // initial readout (Blind + Parallel on by default)

    function filteredModels() {
      // Agent and Research modes use chat models
      const effectiveType = (state._compareMode === 'agent' || state._compareMode === 'research') ? 'chat' : state._compareMode;
      return models.filter(m => m.type === effectiveType);
    }

    function buildOption(m) {
      return {
        val: JSON.stringify({ model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name, endpointName: m.endpointName || '' }),
        label: m.endpointName ? `${m.name} (${m.endpointName})` : m.name,
      };
    }

    /** Build a searchable model picker (used when >5 models) */
    function _buildSearchablePicker(modelList, currentSel, slotIdx, onSelect) {
      const wrap = document.createElement('div');
      wrap.style.cssText = 'flex:1;position:relative;';

      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'Search models\u2026';
      input.className = 'cmp-form-control';
      input.style.cssText = 'width:100%;box-sizing:border-box;';
      // Mobile: suppress the on-screen keyboard so tapping the picker
      // opens the dropdown but doesn't shove a keyboard up over the list.
      // (Matches the +Model dropdown's mobile behavior.)
      if (window.innerWidth <= 768) {
        input.setAttribute('inputmode', 'none');
        input.setAttribute('readonly', 'readonly');
      }
      if (currentSel) {
        const m = modelList.find(m => m.id === currentSel.model && m.url === currentSel.endpoint)
          || modelList.find(m => m.id === currentSel.model);
        if (m) input.value = buildOption(m).label;
      } else {
        const fallback = modelList[Math.min(slotIdx, modelList.length - 1)];
        if (fallback) input.value = buildOption(fallback).label;
      }
      wrap.appendChild(input);

      const dropdown = document.createElement('div');
      dropdown.className = 'cmp-picker-dropdown';
      // Appended to document.body (NOT wrap) and position:fixed so it escapes
      // both the modal's overflow clipping AND any transform on the modal-content
      // (a transformed ancestor makes position:fixed clip to it — which was why
      // the dropdown kept cropping under the next row). Coords set in _placeDropdown.
      dropdown.style.cssText = 'display:none;position:fixed;max-height:200px;overflow-y:auto;background:var(--panel);border:1px solid var(--border);border-radius:6px;z-index:100000;box-shadow:0 4px 12px rgba(0,0,0,0.2);';
      document.body.appendChild(dropdown);

      function renderItems(query) {
        dropdown.innerHTML = '';
        const q = (query || '').toLowerCase();
        const matches = modelList.filter(m => {
          const label = buildOption(m).label.toLowerCase();
          return !q || label.includes(q);
        });
        if (matches.length === 0) {
          const empty = document.createElement('div');
          empty.style.cssText = 'padding:8px 12px;color:color-mix(in srgb, var(--fg) 40%, transparent);font-size:0.82em;font-style:italic;';
          empty.textContent = 'No matches';
          dropdown.appendChild(empty);
          return;
        }
        matches.forEach(m => {
          const opt = buildOption(m);
          const item = document.createElement('div');
          item.style.cssText = 'padding:6px 12px;cursor:pointer;font-size:0.85em;transition:background 0.08s;';
          item.textContent = opt.label;
          const isSelected = currentSel && currentSel.model === m.id && (currentSel.endpoint === m.url || !modelList.some(o => o.id === m.id && o !== m));
          if (isSelected) item.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)';
          item.addEventListener('mouseenter', () => { item.style.background = 'color-mix(in srgb, var(--fg) 10%, transparent)'; });
          item.addEventListener('mouseleave', () => { item.style.background = isSelected ? 'color-mix(in srgb, var(--fg) 8%, transparent)' : ''; });
          item.addEventListener('click', () => {
            const chosen = { model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name, endpointName: m.endpointName || '' };
            input.value = opt.label;
            currentSel = chosen;
            onSelect(chosen);
            dropdown.style.display = 'none';
            input.blur();
          });
          dropdown.appendChild(item);
        });
      }

      // Position the dropdown either below or above the input depending
      // on which side has more room — otherwise on a mobile bottom-sheet
      // a picker near the bottom of the screen would open downward and
      // either clip past the modal or extend off the viewport.
      const _placeDropdown = () => {
        const inRect = input.getBoundingClientRect();
        const vh = window.innerHeight;
        const vw = window.innerWidth;
        const below = vh - inRect.bottom;
        const above = inRect.top;
        const flipUp = below < 220 && above > below;
        // Horizontal: align to the input but clamp inside the viewport so it
        // never runs off the screen edge on mobile.
        const width = Math.min(inRect.width, vw - 16);
        let left = inRect.left;
        if (left + width > vw - 8) left = vw - 8 - width;
        if (left < 8) left = 8;
        dropdown.style.left = left + 'px';
        dropdown.style.width = width + 'px';
        // Vertical: flip above/below based on available room (fixed coords).
        if (flipUp) {
          dropdown.style.top = 'auto';
          dropdown.style.bottom = (vh - inRect.top + 2) + 'px';
          dropdown.style.maxHeight = Math.max(120, Math.min(280, above - 16)) + 'px';
        } else {
          dropdown.style.bottom = 'auto';
          dropdown.style.top = (inRect.bottom + 2) + 'px';
          dropdown.style.maxHeight = Math.max(120, Math.min(280, below - 16)) + 'px';
        }
      };
      input.addEventListener('focus', () => {
        input.value = '';
        renderItems('');
        dropdown.style.display = '';
        _placeDropdown();
      });
      input.addEventListener('input', () => {
        renderItems(input.value);
        dropdown.style.display = '';
        _placeDropdown();
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const first = dropdown.querySelector('div[style*="cursor:pointer"]');
          if (first) first.click();
        }
      });
      // Close on outside click. The dropdown lives in document.body, so check
      // both wrap and dropdown; and tear the dropdown down when the picker row
      // is removed from the DOM (rebuild) so it doesn't orphan in the body.
      function _closeHandler(e) {
        if (!wrap.contains(e.target) && !dropdown.contains(e.target)) {
          dropdown.style.display = 'none';
          if (currentSel) {
            const m = modelList.find(m => m.id === currentSel.model && m.url === currentSel.endpoint);
            if (m) input.value = buildOption(m).label;
          }
          if (!wrap.isConnected) {
            dropdown.remove();
            document.removeEventListener('click', _closeHandler, true);
          }
        }
      }
      setTimeout(() => document.addEventListener('click', _closeHandler, true), 0);

      return wrap;
    }

    function renderModelRows() {
      if (!_modelsLoaded) return;
      // The picker dropdowns live in document.body (to escape modal clipping);
      // clear any leftovers before rebuilding the rows so they don't orphan.
      document.querySelectorAll('.cmp-picker-dropdown').forEach(d => d.remove());

      // ── Search mode: show provider dropdowns ──
      if (state._compareMode === 'search') {
        listContainer.innerHTML = '';
        if (!state._cachedProviders) {
          listContainer.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 40%, transparent);font-size:0.85em;padding:12px 0;text-align:left;">Loading search providers\u2026</div>';
          fetch(`${state.API_BASE}/api/search/providers`).then(r => r.json()).then(providers => {
            state._cachedProviders = providers;
            renderModelRows();
          }).catch(() => {
            listContainer.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;padding:12px 0;">Failed to load search providers</div>';
          });
          return;
        }
        const available = state._cachedProviders.filter(p => p.available);
        if (available.length === 0) {
          listContainer.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 40%, transparent);font-size:0.85em;padding:12px 0;text-align:center;font-style:italic;">No search providers configured</div>';
          if (addBtn) addBtn.style.display = 'none';
          return;
        }
        // Ensure per-pane synth model array matches selections length
        if (!state._searchSynthModels) state._searchSynthModels = [];
        while (state._searchSynthModels.length < selections.length) state._searchSynthModels.push(null);

        const chatModels = state._cachedModels.filter(m => m.type === 'chat');
        const _seqStepS = !state._parallel ? Math.min(20, Math.floor(80 / Math.max(selections.length, 1))) : 0;

        selections.forEach((sel, idx) => {
          const row = document.createElement('div');
          row.className = 'cmp-model-row';
          if (_seqStepS) row.style.marginLeft = (idx * _seqStepS) + 'px';

          // Left label: number/letter or blind eye icon
          const lbl = document.createElement('span');
          lbl.className = 'cmp-row-label';
          if (state._blindMode) {
            lbl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';
          } else {
            lbl.textContent = _slotChar(idx);
          }
          row.appendChild(lbl);

          // Model picker (synthesis LLM) — searchable for large lists
          if (!state._searchSynthModels[idx] && chatModels.length > 0) {
            const fb = chatModels[Math.min(idx, chatModels.length - 1)];
            state._searchSynthModels[idx] = { model: fb.id, endpoint: fb.url, endpointId: fb.endpointId, name: fb.name };
          }
          if (chatModels.length >= 5) {
            const picker = _buildSearchablePicker(chatModels, state._searchSynthModels[idx], idx, (chosen) => {
              state._searchSynthModels[idx] = chosen;
            });
            row.appendChild(picker);
          } else {
            const modelSelect = document.createElement('select');
            modelSelect.className = 'cmp-form-control';
            modelSelect.style.flex = '1';
            chatModels.forEach(m => {
              const opt = document.createElement('option');
              opt.value = JSON.stringify({ model: m.id, endpoint: m.url, endpointId: m.endpointId, name: m.name, endpointName: m.endpointName || '' });
              opt.textContent = m.endpointName ? `${m.name} (${m.endpointName})` : m.name;
              if (state._searchSynthModels[idx] && state._searchSynthModels[idx].model === m.id) opt.selected = true;
              modelSelect.appendChild(opt);
            });
            modelSelect.addEventListener('change', () => {
              try { state._searchSynthModels[idx] = JSON.parse(modelSelect.value); } catch (e) {}
            });
            try { if (!state._searchSynthModels[idx]) state._searchSynthModels[idx] = JSON.parse(modelSelect.value); } catch (e) {}
            row.appendChild(modelSelect);
          }

          // Search provider picker (smaller)
          const provSelect = document.createElement('select');
          provSelect.className = 'cmp-form-control cmp-prov-select';
          available.forEach((p, pi) => {
            const optEl = document.createElement('option');
            optEl.value = JSON.stringify({ model: p.id, endpoint: '', endpointId: null, name: p.label, searchProvider: p.id });
            optEl.textContent = p.label;
            if (sel && sel.model === p.id) optEl.selected = true;
            else if (!sel && pi === Math.min(idx, available.length - 1)) optEl.selected = true;
            provSelect.appendChild(optEl);
          });
          provSelect.addEventListener('change', () => {
            try { selections[idx] = JSON.parse(provSelect.value); } catch (e) {}
          });
          try { if (!selections[idx]) selections[idx] = JSON.parse(provSelect.value); } catch (e) {}
          row.appendChild(provSelect);

          // X remove button when >2 slots
          if (selections.length > 2) {
            const rmBtn = document.createElement('button');
            rmBtn.type = 'button';
            rmBtn.textContent = '\u00d7';
            rmBtn.className = 'cmp-rm-btn';
            rmBtn.addEventListener('mouseenter', () => { rmBtn.style.opacity = '1'; rmBtn.style.color = 'var(--color-error)'; });
            rmBtn.addEventListener('mouseleave', () => { rmBtn.style.opacity = '0.3'; rmBtn.style.color = 'var(--fg)'; });
            rmBtn.addEventListener('click', () => { selections.splice(idx, 1); state._searchSynthModels.splice(idx, 1); renderModelRows(); });
            row.appendChild(rmBtn);
          }

          listContainer.appendChild(row);
        });
        if (addBtn) addBtn.style.display = selections.length >= 8 ? 'none' : '';
        return;
      }

      // ── Chat / Image / Agent / Research mode: show model dropdowns ──
      const filtered = filteredModels();
      listContainer.innerHTML = '';

      // Research mode needs search providers too — fetch if not cached
      const needsProviders = state._compareMode === 'research';
      if (needsProviders && !state._cachedProviders) {
        listContainer.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 40%, transparent);font-size:0.85em;padding:12px 0;">Loading search providers\u2026</div>';
        fetch(`${state.API_BASE}/api/search/providers`).then(r => r.json()).then(providers => {
          state._cachedProviders = providers;
          renderModelRows();
        }).catch(() => {
          state._cachedProviders = [];
          renderModelRows();
        });
        return;
      }

      if (filtered.length === 0) {
        const empty = document.createElement('div');
        empty.style.cssText = 'color:color-mix(in srgb, var(--fg) 40%, transparent);font-size:0.85em;padding:12px 0;text-align:center;font-style:italic;';
        empty.textContent = 'No ' + state._compareMode + ' models available';
        listContainer.appendChild(empty);
        if (addBtn) addBtn.style.display = 'none';
        return;
      }

      // Research: ensure per-pane provider array
      const researchProviders = needsProviders && state._cachedProviders ? state._cachedProviders.filter(p => p.available) : [];
      if (!state._searchSynthModels) state._searchSynthModels = [];
      while (state._searchSynthModels.length < selections.length) state._searchSynthModels.push(null);

      const _seqStep = !state._parallel ? Math.min(20, Math.floor(80 / Math.max(selections.length, 1))) : 0;
      selections.forEach((sel, idx) => {
        const row = document.createElement('div');
        row.className = 'cmp-model-row';
        if (_seqStep) row.style.marginLeft = (idx * _seqStep) + 'px';

        // Left label: number/letter or blind eye icon
        const lbl = document.createElement('span');
        lbl.className = 'cmp-row-label';
        if (state._blindMode) {
          lbl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';
        } else {
          lbl.textContent = _slotChar(idx);
        }
        row.appendChild(lbl);

        if (_shuffled) {
          const mask = document.createElement('div');
          mask.className = 'cmp-form-control';
          mask.style.cssText = 'flex:1;opacity:0.4;font-style:italic;';
          mask.textContent = 'Hidden';
          row.appendChild(mask);
        } else if (filtered.length >= 5) {
          const picker = _buildSearchablePicker(filtered, sel, idx, (chosen) => {
            selections[idx] = chosen;
            _remindShuffle();
          });
          if (!selections[idx]) {
            const fallback = filtered[Math.min(idx, filtered.length - 1)];
            selections[idx] = { model: fallback.id, endpoint: fallback.url, endpointId: fallback.endpointId, name: fallback.name };
          }
          row.appendChild(picker);
        } else {
          const select = document.createElement('select');
          select.className = 'cmp-form-control';
          select.style.flex = '1';
          filtered.forEach((m, mi) => {
            const opt = buildOption(m);
            const optEl = document.createElement('option');
            optEl.value = opt.val;
            optEl.textContent = opt.label;
            if (sel && sel.model === m.id && (sel.endpoint === m.url || !filtered.some(o => o.id === m.id && o !== m))) optEl.selected = true;
            else if (!sel && mi === Math.min(idx, filtered.length - 1)) optEl.selected = true;
            select.appendChild(optEl);
          });
          select.addEventListener('change', () => {
            try { selections[idx] = JSON.parse(select.value); } catch (e) { console.warn('Compare model select parse failed:', e); }
            _remindShuffle();
          });
          try { if (!selections[idx]) selections[idx] = JSON.parse(select.value); } catch (e) { console.warn('Compare model init parse failed:', e); }
          row.appendChild(select);
        }

        // Research mode: search provider picker next to model
        if (needsProviders && researchProviders.length > 0 && !_shuffled) {
          const provSelect = document.createElement('select');
          provSelect.className = 'cmp-form-control cmp-prov-select';
          provSelect.title = 'Search provider';
          researchProviders.forEach((p, pi) => {
            const optEl = document.createElement('option');
            optEl.value = p.id;
            optEl.textContent = p.label;
            if (state._searchSynthModels[idx] && state._searchSynthModels[idx] === p.id) optEl.selected = true;
            else if (!state._searchSynthModels[idx] && pi === 0) optEl.selected = true;
            provSelect.appendChild(optEl);
          });
          provSelect.addEventListener('change', () => { state._searchSynthModels[idx] = provSelect.value; });
          if (!state._searchSynthModels[idx]) state._searchSynthModels[idx] = provSelect.value;
          row.appendChild(provSelect);
        }

        // X remove button when >2 slots
        if (selections.length > 2) {
          const rmBtn = document.createElement('button');
          rmBtn.type = 'button';
          rmBtn.textContent = '\u00d7';
          rmBtn.className = 'cmp-rm-btn';
          rmBtn.addEventListener('mouseenter', () => { rmBtn.style.opacity = '1'; rmBtn.style.color = 'var(--color-error)'; });
          rmBtn.addEventListener('mouseleave', () => { rmBtn.style.opacity = '0.3'; rmBtn.style.color = 'var(--fg)'; });
          rmBtn.addEventListener('click', () => { selections.splice(idx, 1); if (state._searchSynthModels.length > idx) state._searchSynthModels.splice(idx, 1); renderModelRows(); });
          row.appendChild(rmBtn);
        }

        listContainer.appendChild(row);
      });
      if (addBtn) addBtn.style.display = (selections.length >= 8) ? 'none' : '';
    }

    // Default to 2 empty slots if no saved selections
    if (!selections.length || !selections.some(s => s !== null)) selections = [null, null];

    addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.style.cssText = 'display:none;align-items:center;gap:6px;background:none;border:1px dashed var(--border);color:var(--fg);border-radius:6px;cursor:pointer;padding:6px 12px;font-size:0.82em;opacity:0.6;transition:all 0.15s;margin-bottom:16px;width:100%;justify-content:center;';
    addBtn.textContent = '+ Add Model';
    addBtn.addEventListener('mouseenter', () => { addBtn.style.opacity = '1'; });
    addBtn.addEventListener('mouseleave', () => { addBtn.style.opacity = '0.6'; });
    addBtn.addEventListener('click', () => {
      if (selections.length >= 8) return;
      if (_shuffled) {
        // In shuffle mode every slot is a hidden, randomly-picked model — so a
        // new slot must get a random pool model too, not an empty picker.
        const excluded = getExcludedModels();
        const used = new Set(selections.filter(Boolean).map(s => s.model + '|' + s.endpoint));
        const pool = filteredModels().filter(m => !excluded.includes(m.id));
        const fresh = pool.filter(m => !used.has(m.id + '|' + m.url));
        const src = fresh.length ? fresh : pool;
        const pick = src.length ? src[Math.floor(Math.random() * src.length)] : null;
        selections.push(pick ? { model: pick.id, endpoint: pick.url, endpointId: pick.endpointId, name: pick.name, endpointName: pick.endpointName || '' } : null);
      } else {
        selections.push(null);
      }
      renderModelRows();
      _remindShuffle();
    });
    body.appendChild(addBtn);

    // ── Timeout input ──
    const timeoutRow = document.createElement('div');
    timeoutRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px;';
    const timeoutLabel = document.createElement('span');
    timeoutLabel.style.cssText = 'color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:0.82em;';
    timeoutLabel.textContent = 'Timeout:';
    const timeoutInput = document.createElement('input');
    timeoutInput.type = 'number';
    timeoutInput.min = '5';
    timeoutInput.max = '300';
    timeoutInput.value = String(state._timeout);
    timeoutInput.style.cssText = 'width:60px;padding:4px 8px;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;font-size:0.82em;text-align:center;-moz-appearance:textfield;';
    const timeoutSuffix = document.createElement('span');
    timeoutSuffix.style.cssText = 'color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:0.82em;';
    timeoutSuffix.textContent = 'seconds';
    timeoutRow.appendChild(timeoutLabel);
    timeoutRow.appendChild(timeoutInput);
    timeoutRow.appendChild(timeoutSuffix);

    // Scoreboard button
    const scoreBtn = document.createElement('button');
    scoreBtn.type = 'button';
    scoreBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px;"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>Scoreboard';
    scoreBtn.style.cssText = 'margin-left:auto;padding:4px 10px;background:transparent;color:var(--fg);border:1px solid var(--border);border-radius:4px;cursor:pointer;font-size:0.82em;opacity:0.7;position:relative;top:-5px;';
    scoreBtn.addEventListener('mouseenter', () => { scoreBtn.style.opacity = '1'; });
    scoreBtn.addEventListener('mouseleave', () => { scoreBtn.style.opacity = '0.7'; });
    scoreBtn.addEventListener('click', () => showScoreboard());
    timeoutRow.appendChild(scoreBtn);

    body.appendChild(timeoutRow);

    content.appendChild(body);

    // ── Footer with action buttons ──
    const footer = document.createElement('div');
    footer.className = 'modal-footer';
    footer.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;padding:14px 16px 10px;border-top:1px solid var(--border);';
    // Cancel button removed — the overlay's X / outside-click / Esc all
    // dismiss the popup, so the footer Cancel was redundant.
    const startBtn = document.createElement('button');
    startBtn.innerHTML = _CMP_START_LABEL;
    startBtn.className = 'research-start-btn';
    startBtn.disabled = true;
    // Pin to the same 30px box as Cancel so both buttons sit on the same line.
    startBtn.style.cssText = 'opacity:0.4;height:30px;box-sizing:border-box;align-items:center;';
    footer.appendChild(startBtn);
    content.appendChild(footer);

    overlay.appendChild(content);
    document.body.appendChild(overlay);

    // Make draggable via header
    if (themeModule && themeModule.makeDraggable) {
      themeModule.makeDraggable(content, header);
    }

    function cleanup(result) {
      overlay.remove();
      // Remove any body-appended picker dropdowns so they don't orphan.
      document.querySelectorAll('.cmp-picker-dropdown').forEach(d => d.remove());
      if (result) {
        state._selectedModels = selections.filter(Boolean);
        state._timeout = Math.max(5, parseInt(timeoutInput.value) || 30);
        // Persist selections for next time (save filtered, non-null entries)
        _persistSelections();
      }
      resolve(result);
    }
    // (cancelBtn removed — overlay X / outside-click / Esc still call cleanup)
    startBtn.addEventListener('click', async () => {
      if (!_modelsLoaded) return;
      let selected = selections.filter(Boolean);
      // Auto-populate any null selections from available models
      if (selected.length < selections.length) {
        const avail = state._compareMode === 'search' ? [] : filteredModels();
        selections.forEach((s, i) => {
          if (!s && avail.length > 0) {
            const fb = avail[Math.min(i, avail.length - 1)];
            selections[i] = { model: fb.id, endpoint: fb.url, endpointId: fb.endpointId, name: fb.name };
          }
        });
        selected = selections.filter(Boolean);
      }
      if (selected.length < 1) return;

      // For search mode, probe the synthesis LLM models instead of providers
      const modelsToProbe = (state._compareMode === 'search')
        ? (state._searchSynthModels || []).filter(Boolean)
        : selected;
      if (modelsToProbe.length < 1) { cleanup(true); return; }

      // ── Skip probe if all models already probed, go straight to start ──
      const allAlreadyProbed = modelsToProbe.every(m => state._probed.has(m.model));
      if (allAlreadyProbed) { cleanup(true); return; }

      // ── Check selected models before starting ──
      startBtn.disabled = true;
      startBtn.style.opacity = '0.6';

      const isBlind = state._blindMode || _shuffled;

      // Show probe overlay as a fixed modal
      const probeOverlay = document.createElement('div');
      probeOverlay.className = 'compare-probe-overlay';
      const probeCard = document.createElement('div');
      probeCard.className = 'compare-probe-card';
      probeCard.innerHTML = '<div class="compare-probe-title">Checking models...</div>';
      let _probeSkipped = false;
      const probeList = document.createElement('div');
      probeList.className = 'compare-probe-list';
      modelsToProbe.forEach((m, i) => {
        const row = document.createElement('div');
        row.className = 'compare-probe-row';
        row.dataset.model = m.model;
        row.dataset.idx = i;
        // In blind mode, hide name until failure — only show slot letter
        const name = m.name || m.model.split('/').pop();
        const displayName = isBlind ? `Model ${_slotChar(i)}` : escapeHtml(name);
        row._realName = name;
        row.innerHTML = `<span class="compare-probe-spinner">▁▂▃</span><span class="compare-probe-name">${displayName}</span><span class="compare-probe-status"></span>`;
        const waveEl = row.querySelector('.compare-probe-spinner');
        const waveFrames = WAVE_FRAMES;
        let waveIdx = 0;
        row._waveInterval = setInterval(() => {
          waveIdx = (waveIdx + 1) % waveFrames.length;
          if (waveEl && !waveEl.classList.contains('ok') && !waveEl.classList.contains('fail')) {
            waveEl.textContent = waveFrames[waveIdx];
          }
        }, 100);
        probeList.appendChild(row);
      });
      probeCard.appendChild(probeList);
      const skipBtn = document.createElement('button');
      skipBtn.textContent = 'Skip';
      skipBtn.className = 'cmp-btn-secondary';
      skipBtn.style.cssText = 'padding:4px 14px;font-size:11px;opacity:0.5;transition:opacity 0.15s;margin-top:8px;';
      skipBtn.addEventListener('mouseenter', () => { skipBtn.style.opacity = '1'; });
      skipBtn.addEventListener('mouseleave', () => { skipBtn.style.opacity = '0.5'; });
      skipBtn.addEventListener('click', () => {
        _probeSkipped = true;
        _clearProbeWaves();
        probeOverlay.remove();
        cleanup(true);
      });
      probeCard.appendChild(skipBtn);
      probeOverlay.appendChild(probeCard);
      // The CSS z-index for .compare-probe-overlay is 300, but modalManager
      // bumps each opened tool modal above that on every focus (_modalTopZ
      // starts at 300 and increments). So the compare modal often ends up
      // ABOVE the probe overlay, hiding it. Recompute from the compare
      // modal's current effective z-index so probe always sits one above.
      const _cmpModal = document.getElementById('compare-model-overlay');
      if (_cmpModal) {
        const _cmpZ = parseInt(getComputedStyle(_cmpModal).zIndex, 10) || 0;
        probeOverlay.style.setProperty('z-index', String(_cmpZ + 1), 'important');
      }
      document.body.appendChild(probeOverlay);

      // ESC to close probe overlay (stopPropagation prevents closing model selector too)
      const _probeEsc = (e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          e.preventDefault();
          _probeSkipped = true;
          _clearProbeWaves();
          probeOverlay.remove();
          document.removeEventListener('keydown', _probeEsc, false);
          startBtn.disabled = false;
          startBtn.innerHTML = _CMP_START_LABEL;
          startBtn.style.opacity = '1';
        }
      };
      document.addEventListener('keydown', _probeEsc, false);

      // Helper: probe a single model (skip image models — they use a different API)
      const _imageModelPrefixes = ['dall-e', 'gpt-image', 'chatgpt-image', 'stable-diffusion', 'sdxl', 'flux', 'midjourney'];
      function _isImageModel(modelId) {
        const lower = (modelId || '').toLowerCase();
        return _imageModelPrefixes.some(p => lower.includes(p));
      }
      async function _probeOne(m) {
        if (_isImageModel(m.model)) {
          return { status: 'ok', model: m.model, skipped: true, skipReason: 'Image' };
        }
        // Search mode — probe the LLM model normally (don't skip)
        if (state._compareMode === 'search' && !m.model) {
          return { status: 'ok', model: m.model, skipped: true, skipReason: 'No model' };
        }
        const res = await fetch(`${state.API_BASE}/api/probe-selected`, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ models: [{ endpoint_id: m.endpointId || '', model: m.model, endpoint: m.endpoint || '', with_tools: state._compareMode === 'agent' }] }),
        });
        const data = await res.json();
        return (data.results || [])[0] || { status: 'fail', error: 'No response' };
      }

      // Helper: update a probe row's visual state
      function _updateRow(idx, result) {
        const row = probeList.querySelector(`[data-idx="${idx}"]`);
        if (!row) return;
        // Stop wave animation
        if (row._waveInterval) { clearInterval(row._waveInterval); row._waveInterval = null; }
        const spinner = row.querySelector('.compare-probe-spinner');
        const status = row.querySelector('.compare-probe-status');
        if (result.status === 'ok') {
          spinner.textContent = '\u2713';
          spinner.classList.remove('fail');
          spinner.classList.add('ok');
          status.textContent = result.skipped ? (result.skipReason || 'Skipped') : (result.latency_ms ? `${result.latency_ms}ms` : 'OK');
          status.classList.remove('fail');
          status.classList.add('ok');
          row.classList.remove('fail');
          // Track as probed
          if (result.model) state._probed.add(result.model);
        } else {
          spinner.textContent = '\u2717';
          spinner.classList.remove('ok');
          spinner.classList.add('fail');
          status.textContent = '';
          status.classList.remove('ok');
          row.classList.add('fail');
          // Reveal real model name on failure (even in blind mode)
          if (isBlind && row._realName) {
            const nameEl = row.querySelector('.compare-probe-name');
            if (nameEl) nameEl.textContent = row._realName;
          }
          // Remove old detail/actions if retrying
          const oldDetail = row.nextElementSibling;
          if (oldDetail && oldDetail.classList.contains('compare-probe-detail')) oldDetail.remove();
          // Error + actions below the row
          const detail = document.createElement('div');
          detail.className = 'compare-probe-detail';
          detail.style.cssText = 'grid-column:1/-1;display:flex;align-items:flex-start;gap:6px;padding:4px 10px 6px;font-size:10px;opacity:0.6;background:color-mix(in srgb, var(--color-error, #f44) 5%, transparent);border-radius:4px;margin-top:-2px;';
          const errSpan = document.createElement('span');
          // Truncate long error messages
          const errText = (result.error || 'Failed');
          errSpan.textContent = errText.length > 80 ? errText.slice(0, 80) + '...' : errText;
          errSpan.title = errText;
          errSpan.style.cssText = 'flex:1;line-height:1.4;';
          detail.appendChild(errSpan);
          // Track timeout for retry doubling
          if (!row._probeTimeout) row._probeTimeout = 15000;
          if (result.error === 'Timeout') row._probeTimeout = Math.min(row._probeTimeout * 2, 120000);
          const retryBtn = document.createElement('button');
          retryBtn.className = 'compare-probe-action-btn';
          const retryLabel = result.error === 'Timeout' ? `Retry ${Math.round(row._probeTimeout / 1000)}s` : 'Retry';
          retryBtn.textContent = retryLabel;
          retryBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            detail.remove();
            if (isBlind) {
              const nameEl = row.querySelector('.compare-probe-name');
              if (nameEl) nameEl.textContent = `Model ${_slotChar(idx)}`;
            }
            const waveFrames2 = WAVE_FRAMES;
            let w2 = 0;
            spinner.classList.remove('ok', 'fail');
            spinner.style.color = '';
            row._waveInterval = setInterval(() => { w2 = (w2 + 1) % waveFrames2.length; spinner.textContent = waveFrames2[w2]; }, 100);
            row.classList.remove('fail');
            const r2 = await Promise.race([_probeOne(modelsToProbe[idx]), new Promise(r => setTimeout(() => r({ status: 'fail', error: 'Timeout' }), row._probeTimeout))]);
            _updateRow(idx, r2);
          });
          const swapBtn = document.createElement('button');
          swapBtn.className = 'compare-probe-action-btn';
          swapBtn.textContent = 'Swap';
          swapBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            _clearProbeWaves();
            probeOverlay.remove();
            _probeSkipped = true;
            startBtn.disabled = false;
            startBtn.innerHTML = _CMP_START_LABEL;
            startBtn.style.opacity = '1';
          });
          detail.appendChild(retryBtn);
          detail.appendChild(swapBtn);
          row.after(detail);
        }
      }

      try {
        // Probe all in parallel (with 15s timeout per model)
        const results = await Promise.all(modelsToProbe.map(m =>
          Promise.race([
            _probeOne(m),
            new Promise(r => setTimeout(() => r({ status: 'fail', error: 'Timeout' }), 15000))
          ])
        ));
        if (_probeSkipped) return;
        let allOk = true;
        let failCount = 0;

        for (let i = 0; i < results.length; i++) {
          _updateRow(i, results[i]);
          if (results[i].status !== 'ok') {
            allOk = false;
            failCount++;
          }
        }

        // In shuffle/blind mode: auto-swap failed models silently (not for search/research)
        if (!allOk && _shuffled && state._compareMode !== 'search' && state._compareMode !== 'research') {
          const excluded = getExcludedModels();
          const usedModels = new Set(selections.filter(Boolean).map(m => m.model));
          const pool = filteredModels().filter(m => !excluded.includes(m.id) && !usedModels.has(m.id));
          let poolIdx = 0;

          for (let i = 0; i < results.length; i++) {
            if (results[i].status !== 'ok') {
              const row = probeList.querySelector(`[data-idx="${i}"]`);
              // Restart wave in red to show swapping
              if (row) {
                const spinner = row.querySelector('.compare-probe-spinner');
                const status = row.querySelector('.compare-probe-status');
                if (spinner) {
                  spinner.classList.remove('ok', 'fail');
                  spinner.style.color = 'var(--color-error, #f44)';
                  const waveFrames = WAVE_FRAMES;
                  let wIdx = 0;
                  row._waveInterval = setInterval(() => { wIdx = (wIdx + 1) % waveFrames.length; spinner.textContent = waveFrames[wIdx]; }, 100);
                }
                if (status) status.textContent = 'Swapping...';
              }

              // Try up to 3 replacements with 10s timeout each
              let swapped = false;
              for (let attempt = 0; attempt < 3 && poolIdx < pool.length; attempt++) {
                const replacement = pool[poolIdx++];
                const probePromise = _probeOne({ model: replacement.id, endpoint: replacement.url, endpointId: replacement.endpointId });
                const timeoutPromise = new Promise(r => setTimeout(() => r({ status: 'timeout', error: 'Swap timed out' }), 10000));
                const probeResult = await Promise.race([probePromise, timeoutPromise]);
                if (probeResult.status === 'ok') {
                  selections[i] = { model: replacement.id, endpoint: replacement.url, endpointId: replacement.endpointId, name: replacement.name };
                  usedModels.add(replacement.id);
                  if (row && row._waveInterval) { clearInterval(row._waveInterval); row._waveInterval = null; }
                  _updateRow(i, probeResult);
                  swapped = true;
                  break;
                }
              }
              if (!swapped) {
                if (row && row._waveInterval) { clearInterval(row._waveInterval); row._waveInterval = null; }
                if (row) {
                  const spinner = row.querySelector('.compare-probe-spinner');
                  const status = row.querySelector('.compare-probe-status');
                  if (spinner) { spinner.textContent = '\u2717'; spinner.classList.add('fail'); spinner.style.color = ''; }
                  if (status) { status.textContent = 'No replacement'; }
                }
              }
            }
          }

          // Re-check if all are ok now
          const finalToProbe = (state._compareMode === 'search') ? (state._searchSynthModels || []).filter(Boolean) : selections.filter(Boolean);
          const finalResults = await Promise.all(finalToProbe.map(m => _probeOne(m)));
          allOk = finalResults.every(r => r.status === 'ok');
          failCount = finalResults.filter(r => r.status !== 'ok').length;
        }

        // ── Phase 2: For search/research, also check search providers ──
        if (allOk && (state._compareMode === 'search' || state._compareMode === 'research')) {
          const providers = state._compareMode === 'search'
            ? selected.map(s => ({ id: s.model, label: s.name }))
            : (state._searchSynthModels || []).map(p => typeof p === 'string' ? { id: p, label: p } : null).filter(Boolean);

          if (providers.length > 0) {
            const titleEl = probeOverlay.querySelector('.compare-probe-title');
            titleEl.textContent = 'Checking search providers...';

            // Add provider rows
            const providerRows = [];
            providers.forEach((p, i) => {
              const row = document.createElement('div');
              row.className = 'compare-probe-row';
              row.dataset.idx = 'p' + i;
              row.innerHTML = `<span class="compare-probe-spinner">▁▂▃</span><span class="compare-probe-name">${escapeHtml(p.label || p.id)}</span><span class="compare-probe-status"></span>`;
              const waveEl = row.querySelector('.compare-probe-spinner');
              const waveFrames = WAVE_FRAMES;
              let wIdx = 0;
              row._waveInterval = setInterval(() => {
                wIdx = (wIdx + 1) % waveFrames.length;
                if (waveEl && !waveEl.classList.contains('ok') && !waveEl.classList.contains('fail')) waveEl.textContent = waveFrames[wIdx];
              }, 100);
              probeList.appendChild(row);
              providerRows.push(row);
            });

            // Probe each provider with a test query
            const provResults = await Promise.all(providers.map(async (p) => {
              try {
                const fd = new FormData();
                fd.append('query', 'test');
                fd.append('provider', p.id);
                fd.append('count', '1');
                const r = await fetch(`${state.API_BASE}/api/search/query`, { method: 'POST', body: fd, credentials: 'same-origin' });
                const d = await r.json();
                return { status: d.error ? 'fail' : 'ok', error: d.error };
              } catch (e) {
                return { status: 'fail', error: e.message };
              }
            }));

            let searchAllOk = true;
            provResults.forEach((result, i) => {
              const row = providerRows[i];
              if (row._waveInterval) { clearInterval(row._waveInterval); row._waveInterval = null; }
              const spinner = row.querySelector('.compare-probe-spinner');
              const status = row.querySelector('.compare-probe-status');
              if (result.status === 'ok') {
                spinner.textContent = '\u2713'; spinner.classList.add('ok');
                status.textContent = 'OK'; status.classList.add('ok');
              } else {
                spinner.textContent = '\u2717'; spinner.classList.add('fail');
                status.textContent = result.error || 'Failed'; status.classList.add('fail');
                row.classList.add('fail');
                searchAllOk = false;
              }
            });

            if (!searchAllOk) {
              allOk = false;
              failCount += provResults.filter(r => r.status !== 'ok').length;
            }
          }
        }

        if (allOk) {
          // Don't hide the Skip button here — collapsing its space made the
          // card shrink and the title + rows jump ("quick cut"). On success the
          // whole overlay fades out a moment later, so just leave it in place.
          probeOverlay.querySelector('.compare-probe-title').textContent = 'All ready!';
          setTimeout(() => {
            probeOverlay.style.transition = 'opacity 0.3s ease';
            probeOverlay.style.opacity = '0';
            setTimeout(() => { _clearProbeWaves(); probeOverlay.remove(); cleanup(true); if (window._updateCheckBtnState) window._updateCheckBtnState(); }, 300);
          }, 400);
        } else {
          // Failed — the Skip button is replaced by the Go Back / Start Anyway row.
          skipBtn.style.display = 'none';
          // Some failed — show which ones
          const failedNames = [];
          probeList.querySelectorAll('.compare-probe-row.fail').forEach(row => {
            failedNames.push(row.querySelector('.compare-probe-name').textContent);
          });
          const titleEl = probeOverlay.querySelector('.compare-probe-title');
          titleEl.textContent = failedNames.length <= 2
            ? failedNames.join(' & ') + ' failed'
            : `${failCount} models failed`;
          const btnRow = document.createElement('div');
          btnRow.style.cssText = 'display:flex;gap:8px;justify-content:center;margin-top:12px;';
          const goBackBtn = document.createElement('button');
          goBackBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="15 18 9 12 15 6"/></svg>Go Back';
          goBackBtn.className = 'cmp-btn-secondary';
          goBackBtn.style.cssText = 'padding:5px 12px;font-size:12px;display:inline-flex;align-items:center;';
          goBackBtn.addEventListener('click', () => { _clearProbeWaves(); probeOverlay.remove(); startBtn.disabled = false; startBtn.innerHTML = _CMP_START_LABEL; startBtn.style.opacity = '1'; });
          const startAnywayBtn = document.createElement('button');
          startAnywayBtn.textContent = 'Start Anyway';
          startAnywayBtn.className = 'cmp-btn-primary';
          startAnywayBtn.style.cssText = 'padding:5px 12px;font-size:12px;';
          startAnywayBtn.addEventListener('click', () => { _clearProbeWaves(); probeOverlay.remove(); cleanup(true); });
          btnRow.appendChild(goBackBtn);
          btnRow.appendChild(startAnywayBtn);
          probeCard.appendChild(btnRow);
        }
      } catch (e) {
        // Probe failed entirely — let user start anyway
        console.error('Compare probe error:', e);
        _clearProbeWaves();
        probeOverlay.remove();
        startBtn.disabled = false;
        startBtn.innerHTML = _CMP_START_LABEL;
        startBtn.style.opacity = '1';
        cleanup(true);
      }
    });

    // ── Fetch models in background ──
    fetchModels().then(fetched => {
      models = fetched;
      state._cachedModels = fetched;
      _modelsLoaded = true;
      if (models.length < 1) {
        listContainer.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;padding:12px 0;text-align:center;">No models available</div>';
        return;
      }
      // Validate saved selections against available models
      if (_needsValidation && selections.length > 0) {
        selections = selections.map(sel => {
          if (!sel) return null;
          // Prefer exact match (model + endpoint), fall back to model ID only
          const exact = models.find(m => m.id === sel.model && m.url === sel.endpoint);
          if (exact) return { ...sel, endpoint: exact.url, endpointId: exact.endpointId, endpointName: exact.endpointName || sel.endpointName || '' };
          const byId = models.find(m => m.id === sel.model);
          if (byId) return { model: byId.id, endpoint: byId.url, endpointId: byId.endpointId, name: byId.name, endpointName: byId.endpointName || '' };
          return null;
        });
        // Keep nulls in place so slot positions are preserved
        if (!selections.some(s => s !== null)) selections = [null, null];
        _needsValidation = false;
      }
      if (!selections.length) selections = [null, null];
      startBtn.disabled = false;
      startBtn.style.opacity = '1';
      addBtn.style.display = 'flex';
      renderModelRows();
    }).catch(e => {
      console.error('Failed to fetch models for compare:', e);
      listContainer.innerHTML = '<div style="color:var(--color-error);font-size:0.85em;padding:12px 0;text-align:center;">Failed to load models</div>';
    });
  });
}

export { showModelSelector, disableToolToggles, restoreToolToggles, _syncToolbarIndicator };
