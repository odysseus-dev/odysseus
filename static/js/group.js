// static/js/group.js
// Group Chat — multi-model conversations (parallel or round-robin)

import uiModule from './ui.js';
import markdownModule from './markdown.js';
import chatRenderer from './chatRenderer.js';
import spinnerModule from './spinner.js';
import { providerLogo, providerLabel } from './providers.js';
import { PROMPT_TEMPLATES, getAllPresets } from './presets.js';
import { sortModelObjects } from './modelSort.js';
import Storage from './storage.js';

let API_BASE = '';
let _active = false;
let _models = [];          // [{mid, display, url, endpointId}]
let _participantSessions = [];  // session IDs for each model
const _groupParticipants = [];  // module-level participants list
let _abortControllers = [];
let _mode = 'round-robin';    // 'parallel' or 'round-robin'
let _roundRobinIdx = 0;
let _parentSessionId = null;
const GROUP_STATE_KEY = 'odysseus-group-state';

export function init(apiBase) {
  API_BASE = apiBase;
  // Initialize Group tab inside Characters modal
  setTimeout(_initGroupTab, 500);
}

function _initGroupTab() {
  const participantsEl = document.getElementById('group-participants');
  const addBtn = document.getElementById('group-add-btn');
  const startBtn = document.getElementById('save-custom-preset'); // main footer "Start" button
  const modeBtn = document.getElementById('group-mode-btn');
  if (!participantsEl || !addBtn) return;

  // _groupParticipants is at module scope
  let _modelsCache = null;

  async function _getModels() {
    if (_modelsCache) return _modelsCache;
    let items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
    if (!items || items.length === 0) {
      try {
        const res = await fetch(API_BASE + '/api/models', { credentials: 'same-origin' });
        items = (await res.json()).items || [];
      } catch (e) {}
    }
    const result = [];
    const seen = new Set();
    items.forEach(item => {
      if (item.offline) return;
      (item.models || []).concat(item.models_extra || []).forEach((mid, i) => {
        if (seen.has(mid)) return;
        seen.add(mid);
        const display = ((item.models_display || []).concat(item.models_extra_display || []))[i] || mid;
        result.push({ mid, display: display.split('/').pop(), url: item.url, endpointId: item.endpoint_id });
      });
    });
    _modelsCache = sortModelObjects(result);
    return _modelsCache;
  }

  // Tears down all document/window listeners and body-appended dropdown
  // attached to any in-flight picker row.  Must be called before clearing
  // participantsEl.innerHTML so the custom picker.remove() path is not the
  // only way listeners get unregistered.
  function _destroyPickerRow() {
    const existing = participantsEl.querySelector('.group-add-picker-row');
    if (existing && typeof existing._cleanup === 'function') {
      existing._cleanup();
    }
    // Belt-and-suspenders: also sweep any orphaned body-level dropdowns.
    document.querySelectorAll('.group-model-search-dropdown').forEach(el => el.remove());
  }

  function _render() {
    _destroyPickerRow();
    participantsEl.innerHTML = '';
    _groupParticipants.forEach((p, idx) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 8px;background:color-mix(in srgb, var(--fg) 4%, transparent);border:1px solid color-mix(in srgb, var(--border) 60%, transparent);border-radius:8px;transition:background 0.15s;';
      row.addEventListener('mouseenter', () => { row.style.background = 'color-mix(in srgb, var(--fg) 7%, transparent)'; });
      row.addEventListener('mouseleave', () => { row.style.background = 'color-mix(in srgb, var(--fg) 4%, transparent)'; });

      const label = p.character ? p.character.name : (p.model ? p.model.display : '?');
      const providerBadge = p.model ? (providerLabel(p.model.url) || '') : '';
      const logo = p.model ? (providerLogo(p.model.mid) || '') : '';

      const leftWrap = document.createElement('span');
      leftWrap.style.cssText = 'display:flex;align-items:center;gap:6px;flex:1;min-width:0;overflow:hidden;';

      if (logo) {
        const logoWrap = document.createElement('span');
        logoWrap.style.cssText = 'opacity:0.45;flex-shrink:0;display:flex;align-items:center;width:14px;height:14px;';
        logoWrap.innerHTML = logo.replace(/(width|height)="[^"]*"/g, '').replace('<svg', '<svg width="14" height="14"');
        leftWrap.appendChild(logoWrap);
      }

      const nameSpan = document.createElement('span');
      nameSpan.style.cssText = 'font-size:12px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;';
      nameSpan.textContent = label;
      leftWrap.appendChild(nameSpan);

      if (providerBadge) {
        const badge = document.createElement('span');
        badge.style.cssText = 'font-size:8px;font-weight:700;padding:1px 5px;border-radius:4px;background:color-mix(in srgb, var(--fg) 8%, transparent);border:1px solid color-mix(in srgb, var(--fg) 14%, transparent);font-family:monospace;letter-spacing:0.3px;text-transform:uppercase;opacity:0.65;flex-shrink:0;';
        badge.textContent = providerBadge;
        leftWrap.appendChild(badge);
      }

      row.appendChild(leftWrap);

      const removeBtn = document.createElement('button');
      removeBtn.style.cssText = 'background:none;border:none;color:var(--fg);opacity:0.4;cursor:pointer;font-size:15px;padding:0 2px;line-height:1;flex-shrink:0;transition:opacity 0.1s;';
      removeBtn.title = 'Remove';
      removeBtn.innerHTML = '&times;';
      removeBtn.addEventListener('mouseenter', () => { removeBtn.style.opacity = '0.8'; });
      removeBtn.addEventListener('mouseleave', () => { removeBtn.style.opacity = '0.4'; });
      removeBtn.addEventListener('click', () => { _groupParticipants.splice(idx, 1); _render(); });
      row.appendChild(removeBtn);

      participantsEl.appendChild(row);
    });
    // startBtn is shared — don't disable it
  }

  addBtn.addEventListener('click', async () => {
    const [models, characters] = await Promise.all([_getModels(), _getCharacterList()]);

    const picker = document.createElement('div');
    picker.className = 'group-add-picker-row';
    picker.style.cssText = 'display:flex;gap:4px;align-items:stretch;margin-top:4px;';

    const charSel = document.createElement('select');
    charSel.className = 'preset-input';
    charSel.style.cssText = 'font-size:11px;flex:1;box-sizing:border-box;';
    charSel.innerHTML = '<option value="">Empty...</option>' +
      characters.map(c => '<option value="' + c.id + '">' + uiModule.esc(c.name) + '</option>').join('');

    const modelSelWrapper = document.createElement('div');
    modelSelWrapper.style.cssText = 'position:relative;flex:1;display:flex;align-items:stretch;';

    const modelInput = document.createElement('input');
    modelInput.type = 'text';
    modelInput.className = 'preset-input';
    modelInput.placeholder = 'Search model…';
    modelInput.style.cssText = 'font-size:11px;width:100%;padding-left:8px;padding-right:22px;box-sizing:border-box;transition:border-color 0.15s,box-shadow 0.15s;';

    const chevron = document.createElement('span');
    chevron.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    chevron.style.cssText = 'position:absolute;right:7px;top:50%;transform:translateY(-50%);display:flex;align-items:center;opacity:0.5;pointer-events:none;color:var(--fg);';

    modelSelWrapper.appendChild(modelInput);
    modelSelWrapper.appendChild(chevron);

    const dropdown = document.createElement('div');
    dropdown.className = 'group-model-search-dropdown';
    dropdown.style.cssText = 'position:fixed;z-index:10000;background:var(--bg, #1e222a);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,0.45);max-height:200px;overflow-y:auto;display:none;padding:4px 0;';

    function positionDropdown() {
      const rect = modelInput.getBoundingClientRect();
      dropdown.style.left = rect.left + 'px';
      dropdown.style.top = (rect.bottom + 2) + 'px';
      dropdown.style.width = rect.width + 'px';
    }

    const scrollResizeHandler = () => {
      if (dropdown.style.display === 'block') {
        positionDropdown();
      }
    };
    window.addEventListener('scroll', scrollResizeHandler, { passive: true });
    window.addEventListener('resize', scrollResizeHandler, { passive: true });

    function renderList(filterText = '') {
      dropdown.innerHTML = '';
      const query = filterText.toLowerCase();
      const filtered = models.filter(m => {
        const provider = providerLabel(m.url) || 'API';
        const searchString = `${provider} ${m.display} ${m.mid}`.toLowerCase();
        return searchString.includes(query);
      });

      if (filtered.length === 0) {
        const noResult = document.createElement('div');
        noResult.style.cssText = 'padding:8px;font-size:11px;opacity:0.5;color:var(--fg);text-align:center;';
        noResult.textContent = 'No models found';
        dropdown.appendChild(noResult);
        return;
      }

      filtered.forEach(m => {
        const provider = providerLabel(m.url) || 'API';
        const item = document.createElement('div');
        item.style.cssText = 'display:flex;align-items:center;padding:6px 10px;font-size:11px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:background 0.1s, color 0.1s;color:var(--fg);margin:2px 4px;border-radius:4px;';

        const badgeSpan = document.createElement('span');
        badgeSpan.style.cssText = 'display:inline-block;font-size:8px;font-weight:700;padding:1px 4px;border-radius:4px;background:color-mix(in srgb, var(--fg) 8%, transparent);color:var(--fg);opacity:0.75;border:1px solid color-mix(in srgb, var(--fg) 12%, transparent);margin-right:8px;font-family:monospace;letter-spacing:0.3px;text-transform:uppercase;flex-shrink:0;';
        badgeSpan.textContent = provider;

        const nameSpan = document.createElement('span');
        nameSpan.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;';
        nameSpan.textContent = m.display;

        item.appendChild(badgeSpan);
        item.appendChild(nameSpan);

        item.addEventListener('mouseenter', () => {
          item.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)';
        });
        item.addEventListener('mouseleave', () => {
          item.style.background = '';
        });

        item.addEventListener('click', (e) => {
          e.stopPropagation();
          modelInput.value = m.display;
          dropdown.style.display = 'none';

          if (_groupParticipants.length >= 8) { uiModule.showToast('Max 8'); return; }
          const entry = { character: null, model: null };
          entry.model = m;
          if (charSel.value) entry.character = characters.find(c => c.id === charSel.value) || null;
          _groupParticipants.push(entry);
          picker.remove();
          _render();
        });

        dropdown.appendChild(item);
      });
    }

    modelInput.addEventListener('focus', () => {
      modelInput.style.borderColor = 'var(--accent, var(--red))';
      modelInput.style.boxShadow = '0 0 0 2px color-mix(in srgb, var(--accent, var(--red)) 18%, transparent)';
      if (!document.body.contains(dropdown)) document.body.appendChild(dropdown);
      dropdown.style.display = 'block';
      positionDropdown();
      renderList(modelInput.value);
    });

    modelInput.addEventListener('blur', () => {
      // Delay so a click inside the dropdown isn't eaten by the blur
      setTimeout(() => {
        if (dropdown.style.display === 'none') {
          modelInput.style.borderColor = '';
          modelInput.style.boxShadow = '';
        }
      }, 150);
    });

    modelInput.addEventListener('input', () => {
      dropdown.style.display = 'block';
      positionDropdown();
      renderList(modelInput.value);
    });

    modelInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const firstItem = dropdown.querySelector('div:not(:empty)');
        if (firstItem && firstItem.textContent !== 'No models found') {
          firstItem.click();
        }
      } else if (e.key === 'Escape') {
        dropdown.style.display = 'none';
        modelInput.blur();
      }
    });

    const clickOutsideHandler = (e) => {
      if (!modelSelWrapper.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.style.display = 'none';
      }
    };
    document.addEventListener('click', clickOutsideHandler);

    // Explicit cleanup function — called by _destroyPickerRow() (via _render())
    // before participantsEl.innerHTML is cleared, and also by the overridden
    // picker.remove() so direct DOM removal still unregisters listeners.
    function _cleanup() {
      dropdown.remove();
      document.removeEventListener('click', clickOutsideHandler);
      window.removeEventListener('scroll', scrollResizeHandler);
      window.removeEventListener('resize', scrollResizeHandler);
    }
    picker._cleanup = _cleanup;

    const originalRemove = picker.remove;
    picker.remove = function() {
      _cleanup();
      originalRemove.call(picker);
    };

    picker.appendChild(charSel);
    picker.appendChild(modelSelWrapper);
    participantsEl.appendChild(picker);
  });

  // Mode toggle — same style as Compare's parallel button
  if (modeBtn) {
    const ICON_PAR = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>';
    const ICON_SEQ = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="1.5" fill="currentColor"/><circle cx="4" cy="12" r="1.5" fill="currentColor"/><circle cx="4" cy="18" r="1.5" fill="currentColor"/></svg>';
    modeBtn.addEventListener('click', () => {
      _mode = _mode === 'parallel' ? 'round-robin' : 'parallel';
      modeBtn.classList.toggle('active', _mode === 'parallel');
      modeBtn.innerHTML = (_mode === 'parallel' ? ICON_PAR : ICON_SEQ) + '<span class="compare-toggle-label">' + (_mode === 'parallel' ? 'Parallel' : 'Sequential') + '</span>';
    });
  }

  // Hook into the main "Start" button — only act when Group tab is active
  if (startBtn) startBtn.addEventListener('click', async () => {
    const activeTab = document.querySelector('.preset-tab.active');
    if (!activeTab || activeTab.dataset.chartab !== 'group') return;
    // Get default model from current session as fallback
    const _defaultModel = (window.sessionModule && window.sessionModule.getSessions) ?
      (() => {
        const s = window.sessionModule.getSessions().find(x => x.id === window.sessionModule.getCurrentSessionId());
        if (s) return { mid: s.model, display: s.model.split('/').pop(), url: s.endpoint_url, endpointId: '' };
        return null;
      })() : null;

    const picked = _groupParticipants.map(p => {
      let m = p.model ? { ...p.model } : (_defaultModel ? { ..._defaultModel } : null);
      if (!m || !m.url) {
        console.warn('[group] Participant has no valid model:', p);
        return null;
      }
      if (p.character) m.character = { characterId: p.character.id, characterName: p.character.name, characterPrompt: p.character.prompt };
      return m;
    }).filter(Boolean);

    if (picked.length < 2) { uiModule.showToast('Need at least 2 participants — add models or characters'); return; }

    const modal = document.getElementById('custom-preset-modal');
    if (modal) modal.classList.add('hidden');

    setActive(true);
    if (window._syncGroupIndicator) window._syncGroupIndicator(true);
    if (window.sessionModule) window.sessionModule.setCurrentSessionId(null);
    const box = document.getElementById('chat-history');
    if (box) box.innerHTML = '';

    await startGroup(picked, 'group-' + Date.now());

    // Auto-save as preset if 2+ participants
    if (picked.length >= 2) {
      const presetData = {
        id: 'grp-' + Date.now(),
        name: picked.map(p => p._groupName || p.character?.characterName || p.display).join(' & '),
        mode: _mode,
        participants: picked.map(p => ({
          modelId: p.mid,
          modelDisplay: p.display,
          characterId: p.character?.characterId || null,
          characterName: p.character?.characterName || null,
        })),
      };
      try {
        const existing = await fetch(API_BASE + '/api/presets/groups', { credentials: 'same-origin' }).then(r => r.json());
        const groups = existing.groups || [];
        // Don't duplicate if same participants
        const sig = presetData.participants.map(p => p.modelId + ':' + (p.characterId || '')).sort().join(',');
        const exists = groups.some(g => (g.participants || []).map(p => p.modelId + ':' + (p.characterId || '')).sort().join(',') === sig);
        if (!exists) {
          groups.push(presetData);
          await fetch(API_BASE + '/api/presets/groups', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ groups }),
          });
        }
      } catch (e) {}
    }

    uiModule.showToast('Group chat ready — ' + picked.length + ' participants');
  });

  const groupTab = document.querySelector('.preset-tab[data-chartab="group"]');
  if (groupTab) groupTab.addEventListener('click', () => {
    _modelsCache = null;
    if (startBtn) startBtn.textContent = 'Start Group';
    _loadGroupPresets();
    if (_groupParticipants.length === 0) {
      setTimeout(() => addBtn.click(), 100);
    }
  });

  // Load and render saved group presets
  async function _loadGroupPresets() {
    try {
      const res = await fetch(API_BASE + '/api/presets/groups', { credentials: 'same-origin' });
      const data = await res.json();
      const groups = data.groups || [];
      // Render presets above participant list
      let presetsDiv = document.getElementById('group-presets-list');
      if (!presetsDiv) {
        presetsDiv = document.createElement('div');
        presetsDiv.id = 'group-presets-list';
        presetsDiv.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;';
        participantsEl.parentNode.insertBefore(presetsDiv, participantsEl);
      }
      presetsDiv.innerHTML = '';
      if (groups.length === 0) return;
      groups.forEach((g, idx) => {
        const chip = document.createElement('button');
        chip.className = 'preset-save-btn';
        chip.style.cssText = 'padding:3px 10px;font-size:11px;background:color-mix(in srgb, var(--fg) 5%, transparent);border:1px solid var(--border);';
        const chipLabel = document.createElement('span');
        chipLabel.textContent = g.name || 'Group ' + (idx + 1);
        chip.appendChild(chipLabel);
        const chipX = document.createElement('span');
        chipX.textContent = ' \u00d7';
        chipX.style.cssText = 'opacity:0.4;margin-left:4px;cursor:pointer;';
        chipX.addEventListener('click', (ev) => {
          ev.stopPropagation();
          groups.splice(idx, 1);
          fetch(API_BASE + '/api/presets/groups', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ groups }),
          }).then(() => _loadGroupPresets());
        });
        chip.appendChild(chipX);
        chip.title = (g.participants || []).map(p => p.characterName || p.modelDisplay || '?').join(', ');
        chip.addEventListener('click', async () => {
          // Load preset participants
          const [models, chars] = await Promise.all([_getModels(), _getCharacterList()]);
          _groupParticipants.length = 0;
          (g.participants || []).forEach(p => {
            const model = models.find(m => m.mid === p.modelId) || models[0];
            const entry = { model: model || null, character: null };
            if (p.characterId) {
              entry.character = chars.find(c => c.id === p.characterId) || null;
            }
            if (entry.model) _groupParticipants.push(entry);
          });
          _mode = g.mode || 'parallel';
          _render();
        });
        // Long-press / right-click to delete
        chip.addEventListener('contextmenu', async (e) => {
          e.preventDefault();
          if (await window.styledConfirm('Delete preset "' + (g.name || 'Group') + '"?', { confirmText: 'Delete', danger: true })) {
            groups.splice(idx, 1);
            fetch(API_BASE + '/api/presets/groups', {
              method: 'POST', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ groups }),
            }).then(() => _loadGroupPresets());
          }
        });
        presetsDiv.appendChild(chip);
      });
    } catch (e) { console.warn('[group] Failed to load presets:', e); }
  }
  // Restore button text when switching away from Group tab
  document.querySelectorAll('.preset-tab[data-chartab]').forEach(tab => {
    if (tab.dataset.chartab !== 'group') {
      tab.addEventListener('click', () => {
        if (startBtn) startBtn.textContent = 'Start';
      });
    }
  });
}

async function _getCharacterList() {
  // Built-in characters from PROMPT_TEMPLATES
  const chars = PROMPT_TEMPLATES.filter(t => t.isCharacter).map(t => ({
    id: t.id, name: t.name, prompt: t.prompt,
  }));
  // User-created characters from presets
  try {
    const allPresets = getAllPresets();
    if (allPresets && allPresets.custom && allPresets.custom.character_name) {
      chars.push({
        id: 'custom',
        name: allPresets.custom.character_name,
        prompt: allPresets.custom.system_prompt || allPresets.custom.prompt || '',
      });
    }
  } catch (e) {}
  // Load user templates and wait for them before returning.
  // The endpoint returns a JSON array directly (not {templates:[...]}).
  // All user templates are personas by definition — no isCharacter filter needed.
  try {
    const r = await fetch(API_BASE + '/api/presets/templates', { credentials: 'same-origin' });
    const data = await r.json();
    const templates = Array.isArray(data) ? data : (data.templates || []);
    templates.forEach(t => {
      if (t.id && t.name && !chars.find(c => c.id === t.id)) {
        chars.push({ id: t.id, name: t.name, prompt: t.system_prompt || t.prompt || '' });
      }
    });
  } catch (e) {}
  return chars;
}

export function isActive() { return _active; }
export function setActive(v) { _active = v; }
export function getMode() { return _mode; }
export function setMode(m) { _mode = m; }

// ── Model Picker ─────────────────────────────────────

export async function showModelPicker() {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.id = 'group-model-picker';

    const content = document.createElement('div');
    content.className = 'modal-content';
    content.style.width = 'min(480px, 92vw)';

    // Header
    const header = document.createElement('div');
    header.className = 'modal-header';
    header.innerHTML = '<h4>Group Chat — Select Models</h4>';
    const closeBtn = document.createElement('button');
    closeBtn.className = 'close-btn';
    closeBtn.innerHTML = '&#x2716;';
    closeBtn.addEventListener('click', () => { overlay.remove(); resolve(null); });
    header.appendChild(closeBtn);

    // Body
    const body = document.createElement('div');
    body.className = 'modal-body';

    // Mode toggle
    const modeRow = document.createElement('div');
    modeRow.style.cssText = 'display:flex;gap:8px;margin-bottom:10px;align-items:center;font-size:12px;';
    modeRow.innerHTML = `
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
        <input type="radio" name="group-mode" value="parallel" ${_mode === 'parallel' ? 'checked' : ''}> All respond
      </label>
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
        <input type="radio" name="group-mode" value="round-robin" ${_mode === 'round-robin' ? 'checked' : ''}> Round-robin
      </label>
    `;
    body.appendChild(modeRow);

    // Search
    const search = document.createElement('input');
    search.type = 'text';
    search.placeholder = 'Filter models…';
    search.className = 'memory-search-input';
    search.style.marginBottom = '8px';
    body.appendChild(search);

    // Model list
    const list = document.createElement('div');
    list.style.cssText = 'max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:2px;';
    body.appendChild(list);

    // Selected count + start button
    const footer = document.createElement('div');
    footer.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-top:10px;';
    footer.innerHTML = `
      <span id="group-selected-count" style="font-size:11px;opacity:0.5;">0 selected</span>
      <button id="group-start-btn" class="btn-primary" disabled style="padding:6px 16px;font-size:12px;">Start Group Chat</button>
    `;
    body.appendChild(footer);

    content.appendChild(header);
    content.appendChild(body);
    overlay.appendChild(content);
    overlay.style.display = 'flex';
    document.body.appendChild(overlay);

    // Get all available models — try cached first, fetch if empty
    const selected = new Set();
    let _cachedModels = null;
    async function getAllModels() {
      if (_cachedModels) return _cachedModels;
      let items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
      // Fallback: fetch from API if cache is empty
      if (!items || items.length === 0) {
        try {
          const res = await fetch(API_BASE + '/api/models', { credentials: 'same-origin' });
          const data = await res.json();
          items = data.items || [];
        } catch (e) { console.warn('[group] Failed to fetch models:', e); }
      }
      const result = [];
      const seen = new Set();
      items.forEach(item => {
        if (item.offline) return;
        (item.models || []).concat(item.models_extra || []).forEach((mid, i) => {
          if (seen.has(mid)) return;
          seen.add(mid);
          const display = ((item.models_display || []).concat(item.models_extra_display || []))[i] || mid;
          result.push({ mid, display: display.split('/').pop(), url: item.url, endpointId: item.endpoint_id, epName: item.endpoint_name || '' });
        });
      });
      _cachedModels = sortModelObjects(result);
      return _cachedModels;
    }

    async function render(filter) {
      list.innerHTML = '<div style="opacity:0.4;padding:8px;font-size:12px;">Loading models…</div>';
      const all = await getAllModels();
      const q = (filter || '').toLowerCase();
      all.forEach(m => {
        if (q && !m.mid.toLowerCase().includes(q) && !m.display.toLowerCase().includes(q) && !m.epName.toLowerCase().includes(q)) return;
        const row = document.createElement('div');
        row.className = 'memory-item';
        row.style.cssText = 'padding:6px 8px;cursor:pointer;' + (selected.has(m.mid) ? 'background:color-mix(in srgb, var(--accent, var(--red)) 12%, transparent);' : '');
        const logo = providerLogo(m.mid);
        row.innerHTML = `
          <input type="checkbox" ${selected.has(m.mid) ? 'checked' : ''} style="margin-right:6px;">
          ${logo ? '<span style="opacity:0.5;margin-right:4px;">' + logo + '</span>' : ''}
          <span style="flex:1;font-size:12px;">${uiModule.esc(m.display)}</span>
          <span style="font-size:10px;opacity:0.3;">${uiModule.esc(m.epName)}</span>
        `;
        row.addEventListener('click', (e) => {
          if (e.target.tagName === 'INPUT') return;
          const cb = row.querySelector('input[type=checkbox]');
          cb.checked = !cb.checked;
          cb.dispatchEvent(new Event('change'));
        });
        row.querySelector('input').addEventListener('change', (e) => {
          if (e.target.checked) {
            if (selected.size >= 8) { e.target.checked = false; uiModule.showToast('Max 8 models'); return; }
            selected.add(m.mid);
          } else {
            selected.delete(m.mid);
          }
          document.getElementById('group-selected-count').textContent = selected.size + ' selected';
          document.getElementById('group-start-btn').disabled = selected.size < 2;
          row.style.background = selected.has(m.mid) ? 'color-mix(in srgb, var(--accent, var(--red)) 12%, transparent)' : '';
        });
        list.appendChild(row);
      });
    }

    search.addEventListener('input', () => render(search.value));
    render();

    // Mode toggle
    modeRow.querySelectorAll('input[name=group-mode]').forEach(r => {
      r.addEventListener('change', () => { _mode = r.value; });
    });

    // Start button
    document.getElementById('group-start-btn').addEventListener('click', async () => {
      const all = await getAllModels();
      const picked = all.filter(m => selected.has(m.mid));

      // Step 2: Character assignment
      body.innerHTML = '';
      const stepTitle = document.createElement('div');
      stepTitle.style.cssText = 'font-size:12px;opacity:0.5;margin-bottom:8px;';
      stepTitle.textContent = 'Assign characters (optional)';
      body.appendChild(stepTitle);

      // Build character options
      const characters = await _getCharacterList();
      const assignments = {}; // mid -> {characterId, characterName, characterPrompt}

      for (const m of picked) {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);';
        const logo = providerLogo(m.mid);
        row.innerHTML = `
          ${logo ? '<span style="opacity:0.5;">' + logo + '</span>' : ''}
          <span style="flex:1;font-size:12px;font-weight:500;">${uiModule.esc(m.display)}</span>
        `;
        const sel = document.createElement('select');
        sel.style.cssText = 'font-size:11px;padding:3px 6px;border-radius:4px;border:1px solid var(--border);background:var(--bg);color:var(--fg);max-width:140px;';
        let optsHtml = '<option value="">No character</option>';
        characters.forEach(c => {
          optsHtml += `<option value="${c.id}">${uiModule.esc(c.name)}</option>`;
        });
        sel.innerHTML = optsHtml;
        sel.addEventListener('change', () => {
          if (sel.value) {
            const ch = characters.find(c => c.id === sel.value);
            assignments[m.mid] = { characterId: ch.id, characterName: ch.name, characterPrompt: ch.prompt };
          } else {
            delete assignments[m.mid];
          }
        });
        row.appendChild(sel);
        body.appendChild(row);
      }

      // Go button
      const goBtn = document.createElement('button');
      goBtn.className = 'btn-primary';
      goBtn.style.cssText = 'margin-top:10px;padding:6px 16px;font-size:12px;width:100%;';
      goBtn.textContent = 'Start Group Chat';
      goBtn.addEventListener('click', () => {
        // Attach character info to picked models
        picked.forEach(m => {
          if (assignments[m.mid]) {
            m.character = assignments[m.mid];
          }
        });
        overlay.remove();
        resolve(picked);
      });
      body.appendChild(goBtn);
    });

    // Click outside to close
    overlay.addEventListener('click', (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } });
    search.focus();
  });
}

// ── Start / Stop ─────────────────────────────────────

export async function startGroup(models, parentSessionId) {
  _models = models;
  _active = true;
  _roundRobinIdx = 0;
  _participantSessions = [];

  // Create a real parent session for persistence
  const groupName = '[GRP] ' + models.map(m => m._groupName || m.character?.characterName || m.display).join(', ');
  try {
    const pfd = new FormData();
    pfd.append('name', groupName);
    pfd.append('endpoint_url', models[0].url);
    pfd.append('model', models[0].mid);
    pfd.append('skip_validation', 'true');
    if (models[0].endpointId) pfd.append('endpoint_id', models[0].endpointId);
    const pres = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: pfd, credentials: 'same-origin' });
    const pdata = await pres.json();
    _parentSessionId = pdata.id;
    // Register as group session for sidebar icon
    try {
      const storedGroupSessions = Storage.getJSON('odysseus-group-sessions', []);
      const gids = Array.isArray(storedGroupSessions) ? storedGroupSessions : [];
      if (!gids.includes(_parentSessionId)) { gids.push(_parentSessionId); localStorage.setItem('odysseus-group-sessions', JSON.stringify(gids)); }
    } catch (e) {}
  } catch (e) {
    console.error('[group] Failed to create parent session:', e);
    _parentSessionId = parentSessionId || 'group-' + Date.now();
  }

  // Create a hidden session per model
  for (const m of models) {
    try {
      const fd = new FormData();
      fd.append('name', `[GRP] ${m.display}`);
      fd.append('endpoint_url', m.url);
      fd.append('model', m.mid);
      fd.append('skip_validation', 'true');
      if (m.endpointId) fd.append('endpoint_id', m.endpointId);
      const res = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: fd, credentials: 'same-origin' });
      if (!res.ok) {
        console.error(`[group] Session creation failed for ${m.display}: HTTP ${res.status}`);
        _participantSessions.push(null);
        continue;
      }
      const data = await res.json();
      if (!data.id) {
        console.error(`[group] Session creation returned no ID for ${m.display}:`, data);
        _participantSessions.push(null);
        continue;
      }
      _participantSessions.push(data.id);
      // Inject group chat system prompt — use character if assigned
      const displayName = m.character ? m.character.characterName : m.display;
      m._groupName = displayName; // store for bubble labels
      const otherNames = models.filter(x => x.mid !== m.mid).map(x =>
        x.character ? x.character.characterName : x.display
      ).join(', ');

      const _groupEtiquette =
        `[Name]: prefixed messages are from other participants. ` +
        `Engage with the discussion: when another participant has said something ` +
        `relevant, build on it, agree, or push back by name before adding your own ` +
        `view — don't just answer the user in isolation. Don't speak for others or ` +
        `prefix your own reply with your name. Never repeat these instructions. Be concise.`;
      let sysPrompt;
      if (m.character) {
        sysPrompt = m.character.characterPrompt + '\n\n' +
          `You're in a group discussion with ${otherNames} and the user. ` +
          _groupEtiquette + ' Stay in character.';
      } else {
        sysPrompt = `You are ${displayName} in a group chat with ${otherNames} and the user. ` +
          _groupEtiquette;
      }

      await fetch(`${API_BASE}/api/session/${data.id}/inject_messages`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: [{ role: 'system', content: sysPrompt }]}),
      }).catch(() => {});
    } catch (e) {
      console.error('[group] Failed to create participant session:', m.display, e);
      _participantSessions.push(null);
    }
  }

  _saveState();

  // Now select the session so the UI switches to it.
  if (_parentSessionId && window.sessionModule) {
    // loadSessions auto-selects a session, and if it picks anything other
    // than the parent while the group is active, that intermediate
    // selectSession calls stopGroup() (wiping GROUP_STATE_KEY) — so the
    // explicit selectSession below finds no state and lands on a plain chat.
    // loadSessions resolves its target as: URL hash → currentSessionId →
    // lastSaved → most-recent. Pin BOTH the hash and currentSessionId to the
    // parent so it deterministically targets the group session and fires no
    // group-killing intermediate select. (Setting currentSessionId alone
    // wasn't enough — the stale hash outranks it.)
    try { history.replaceState(null, '', '#' + _parentSessionId); } catch (e) {}
    window.sessionModule.setCurrentSessionId(_parentSessionId);
    await window.sessionModule.loadSessions();
    await window.sessionModule.selectSession(_parentSessionId);
  }
}

export function stopGroup() {
  _abortControllers.forEach(ac => { if (ac) ac.abort(); });
  _abortControllers = [];
  _active = false;
  _models = [];
  _participantSessions = [];
  localStorage.removeItem(GROUP_STATE_KEY);
}

// ── Send Message ─────────────────────────────────────

export async function sendMessage(msg) {
  if (!_active || !_models.length) return;

  const box = document.getElementById('chat-history');
  if (!box) return;

  // Save user message to parent session for persistence
  if (_parentSessionId) {
    fetch(`${API_BASE}/api/session/${_parentSessionId}/inject_messages`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [{ role: 'user', content: msg }] }),
    }).catch(() => {});
  }

  if (_mode === 'parallel') {
    await _sendParallel(msg, box);
  } else {
    await _sendRoundRobin(msg, box);
  }
}

function _createGroupBubble(model, box) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-ai msg-group';
  wrap.style.position = 'relative';

  // Role label — use character name if assigned, otherwise model name
  const roleLabel = model._groupName || (model.character ? model.character.characterName : chatRenderer.shortModel(model.mid));
  const roleTs = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  wrap.innerHTML = `<div class="role">${uiModule.esc(roleLabel)} <span class="role-timestamp">${roleTs}</span></div><div class="body"></div>`;
  chatRenderer.applyModelColor(wrap.querySelector('.role'), model.mid);

  // Spinner — identical to chat.js line 3062
  const spinner = spinnerModule.create('Generating response', 'right');
  const bodyDiv = wrap.querySelector('.body');
  bodyDiv.appendChild(spinner.createElement());
  spinner.start();
  wrap._spinner = spinner;

  box.appendChild(wrap);
  return wrap;
}

async function _sendParallel(msg, box) {
  const holders = _models.map(m => _createGroupBubble(m, box));
  uiModule.scrollHistory();

  // Stream all models in parallel
  _abortControllers = _models.map(() => new AbortController());
  const results = await Promise.allSettled(_models.map((m, i) =>
    _streamToHolder(i, _participantSessions[i], msg, holders[i], _abortControllers[i])
  ));
  _abortControllers = [];

  // They answered simultaneously so they couldn't react this turn, but inject
  // each response into the others' sessions so they're aware of each other on
  // the next message and can remark on it.
  await _syncAllResponses(holders);
}

async function _sendRoundRobin(msg, box) {
  // Randomize who goes first each message — shuffle participant indices
  // (Fisher–Yates) instead of a fixed rotation, so the order varies turn to
  // turn. Each model still takes its turn seeing all responses already given
  // this round (and prior rounds, via the cross-session injection below), so
  // later responders can react to earlier ones.
  const order = _models.map((_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  for (let turn = 0; turn < order.length; turn++) {
    const idx = order[turn];
    const m = _models[idx];

    const wrap = _createGroupBubble(m, box);
    uiModule.scrollHistory();

    const ac = new AbortController();
    _abortControllers = [ac];
    await _streamToHolder(idx, _participantSessions[idx], msg, wrap, ac);
    _abortControllers = [];

    // After each response, inject it into all OTHER participant sessions
    const response = wrap.dataset.raw || '';
    if (response) {
      for (let j = 0; j < _participantSessions.length; j++) {
        if (j === idx || !_participantSessions[j]) continue;
        try {
          await fetch(`${API_BASE}/api/session/${_participantSessions[j]}/inject_messages`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: [{
              role: 'user',
              content: `[${m._groupName || m.display}]: ${response}`
            }]}),
          });
        } catch (e) { console.warn('[group] sync failed:', e); }
      }
    }
  }
  // Order is randomized per-message now, so _roundRobinIdx no longer drives
  // turn order; left in state for backward compat only.
  _saveState();
}

/** After parallel responses, inject each model's response into all other sessions. */
async function _syncAllResponses(holders) {
  for (let i = 0; i < holders.length; i++) {
    const response = holders[i].dataset.raw || '';
    if (!response) continue;
    const model = _models[i];
    for (let j = 0; j < _participantSessions.length; j++) {
      if (j === i || !_participantSessions[j]) continue;
      try {
        await fetch(`${API_BASE}/api/session/${_participantSessions[j]}/inject_messages`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages: [{
            role: 'user',
            content: `[${model._groupName || model.display}]: ${response}`
          }]}),
        });
      } catch (e) { /* silent */ }
    }
  }
}

async function _streamToHolder(modelIdx, sessionId, msg, holderEl, abortCtrl) {
  if (!sessionId) {
    holderEl.querySelector('.body').innerHTML = '<i style="opacity:0.5;">[Session creation failed]</i>';
    return;
  }

  const fd = new FormData();
  fd.append('message', msg);
  fd.append('session', sessionId);

  let accumulated = '';
  let _buffer = '';
  let _firstToken = true;
  const bodyEl = holderEl.querySelector('.body');

  try {
    const res = await fetch(`${API_BASE}/api/chat_stream`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      signal: abortCtrl.signal,
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      _buffer += decoder.decode(value, { stream: true });

      // Process complete lines
      const lines = _buffer.split('\n');
      _buffer = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        if (line === 'data: [DONE]') continue;

        try {
          const json = JSON.parse(line.slice(6));

          // Text delta (OpenAI format)
          if (json.choices?.[0]?.delta?.content) {
            if (_firstToken) { _firstToken = false; if (holderEl._spinner) { holderEl._spinner.destroy(); delete holderEl._spinner; } bodyEl.innerHTML = ''; }
            accumulated += json.choices[0].delta.content;
            bodyEl.innerHTML = markdownModule.processWithThinking(
              markdownModule.squashOutsideCode(accumulated)
            );
            uiModule.scrollHistory();
          }
          // Text delta (Odysseus format)
          else if (json.delta !== undefined) {
            if (_firstToken) { _firstToken = false; if (holderEl._spinner) { holderEl._spinner.destroy(); delete holderEl._spinner; } bodyEl.innerHTML = ''; }
            // Handle thinking tags from vLLM
            let _d = json.delta;
            if (json.thinking) {
              if (!accumulated.includes('<think>')) _d = '<think>' + _d;
            } else if (accumulated.includes('<think>') && !accumulated.includes('</think>')) {
              _d = '</think>' + _d;
            }
            accumulated += _d;
            bodyEl.innerHTML = markdownModule.processWithThinking(
              markdownModule.squashOutsideCode(accumulated)
            );
            uiModule.scrollHistory();
          }
          // Agent tool events
          else if (json.type === 'tool_start') {
            const toolDiv = document.createElement('div');
            toolDiv.className = 'agent-tool-event';
            toolDiv.style.cssText = 'font-size:11px;opacity:0.5;padding:2px 0;font-family:monospace;';
            toolDiv.textContent = `⚙ ${json.tool || 'tool'}${json.command ? ': ' + json.command.substring(0, 60) : ''}`;
            bodyEl.appendChild(toolDiv);
          }
          else if (json.type === 'tool_output') {
            const outDiv = document.createElement('div');
            outDiv.className = 'agent-tool-output';
            outDiv.style.cssText = 'font-size:10px;opacity:0.4;padding:2px 0;font-family:monospace;max-height:60px;overflow:hidden;';
            outDiv.textContent = (json.output || '').substring(0, 200);
            bodyEl.appendChild(outDiv);
          }
          // Generated image
          else if (json.type === 'generated_image' && json.url) {
            const safeImageUrl = chatRenderer.safeDisplayImageSrc(json.url);
            if (safeImageUrl) {
              const img = document.createElement('img');
              img.src = safeImageUrl;
              img.style.cssText = 'max-width:100%;border-radius:8px;margin:8px 0;';
              img.loading = 'lazy';
              bodyEl.appendChild(img);
            }
          }
          // Error
          else if (json.error) {
            const errDiv = document.createElement('div');
            errDiv.style.cssText = 'color:var(--color-error);font-style:italic;padding:4px 0;';
            errDiv.textContent = `[Error: ${json.error}]`;
            bodyEl.appendChild(errDiv);
          }
        } catch (e) { /* skip unparseable */ }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') return;
    console.error('[group] Stream error:', e);
    bodyEl.innerHTML += '<div style="color:var(--color-error);font-style:italic;">[Stream error]</div>';
  }

  // Final render with footer
  if (accumulated) {
    bodyEl.innerHTML = markdownModule.processWithThinking(
      markdownModule.squashOutsideCode(accumulated)
    );
    if (window.hljs) holderEl.querySelectorAll('pre code').forEach(b => window.hljs.highlightElement(b));
    if (markdownModule.renderMermaid) markdownModule.renderMermaid(holderEl);
    holderEl.appendChild(chatRenderer.createMsgFooter(holderEl));
  } else if (!bodyEl.querySelector('.agent-tool-event') && !bodyEl.querySelector('img')) {
    bodyEl.innerHTML = '<i style="opacity:0.5;">[No response]</i>';
  }

  holderEl.dataset.raw = accumulated;
  holderEl.dataset.groupModel = _models[modelIdx].mid;

  // Save response to parent session for persistence
  if (accumulated && _parentSessionId) {
    const gName = _models[modelIdx]._groupName || _models[modelIdx].display;
    fetch(`${API_BASE}/api/session/${_parentSessionId}/inject_messages`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [{
        role: 'assistant', content: accumulated,
        metadata: { group_model: gName, model: _models[modelIdx].mid }
      }]}),
    }).catch(() => {});
  }
}

// ── State Persistence ────────────────────────────────

function _saveState() {
  try {
    localStorage.setItem(GROUP_STATE_KEY, JSON.stringify({
      active: _active,
      mode: _mode,
      models: _models,
      participantSessions: _participantSessions,
      parentSessionId: _parentSessionId,
      roundRobinIdx: _roundRobinIdx,
    }));
  } catch (e) {}
}

export function restoreState(sessionId) {
  try {
    const s = JSON.parse(localStorage.getItem(GROUP_STATE_KEY) || 'null');
    if (s && s.active && s.parentSessionId === sessionId) {
      _active = true;
      _mode = s.mode || 'parallel';
      _models = s.models || [];
      _participantSessions = s.participantSessions || [];
      _parentSessionId = s.parentSessionId;
      _roundRobinIdx = s.roundRobinIdx || 0;
      return true;
    }
  } catch (e) {}
  return false;
}

export function getModels() { return _models; }
export function getModelCount() { return _models.length; }

const groupModule = {
  init, isActive, setActive, getMode, setMode, showModelPicker,
  startGroup, stopGroup, sendMessage, restoreState,
  getModels, getModelCount,
};

export default groupModule;
window.groupModule = groupModule;
