// static/js/council.js
/**
 * Council of Models — Multi-agent named deliberation, debate, and consensus synthesis.
 * Supports up to 6 models (local & cloud), custom names/personas, multi-round
 * cross-examination, and unified verdict synthesis.
 */

import uiModule from './ui.js';
import markdownModule from './markdown.js';
import Storage from './storage.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { sortModelObjects } from './modelSort.js';

let API_BASE = '';
let _modal = null;
let _open = false;
let _abortController = null;
let _isDiscussing = false;

function _renderMarkdown(text) {
  if (!text) return '';
  try {
    if (markdownModule?.mdToHtml) return markdownModule.mdToHtml(text);
    if (markdownModule?.renderMarkdown) return markdownModule.renderMarkdown(text);
    if (markdownModule?.renderContent) return markdownModule.renderContent(text);
  } catch (e) {
    console.warn('[Council] Markdown render fallback:', e);
  }
  return uiModule.esc(text).replace(/\n/g, '<br/>');
}

const MAX_MEMBERS = 6;
const MIN_MEMBERS = 2;
const STORAGE_ROSTER_KEY = 'odysseus-council-roster';
const STORAGE_ROUNDS_KEY = 'odysseus-council-rounds';

const COUNCIL_AVATAR_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4'
];

const DEFAULT_MEMBERS = [
  { id: 'm-1', name: 'Joana', model: '', endpoint_id: '', endpoint_url: '', persona: 'Pragmatic & Architecture-focused' },
  { id: 'm-2', name: 'Roseann', model: '', endpoint_id: '', endpoint_url: '', persona: 'Critical & Devil\'s Advocate' },
  { id: 'm-3', name: 'Marcus', model: '', endpoint_id: '', endpoint_url: '', persona: 'User Experience & Developer Ergonomics' },
];

const TOPIC_SUGGESTIONS = [
  'Why is Laravel a good framework?',
  'What is the best movie of 2026 and why?',
  'Monolith vs Microservices for a high-growth startup?',
  'Is Python or Rust better for AI backend services in 2026?',
  'What are the most impactful emerging AI architectural patterns?'
];

let _members = [];
let _rounds = 2;
let _cachedModels = null;
let _activeDiscussionData = null;

export function init(apiBase) {
  API_BASE = apiBase || window.location.origin;
  _loadState();
}

function _loadState() {
  try {
    const savedRoster = Storage.getJSON(STORAGE_ROSTER_KEY, null);
    if (Array.isArray(savedRoster) && savedRoster.length >= MIN_MEMBERS) {
      _members = savedRoster.slice(0, MAX_MEMBERS);
    } else {
      _members = JSON.parse(JSON.stringify(DEFAULT_MEMBERS));
    }
    const savedRounds = parseInt(localStorage.getItem(STORAGE_ROUNDS_KEY) || '2', 10);
    _rounds = Math.max(1, Math.min(3, savedRounds || 2));
  } catch (e) {
    _members = JSON.parse(JSON.stringify(DEFAULT_MEMBERS));
    _rounds = 2;
  }
}

function _saveState() {
  try {
    Storage.setJSON(STORAGE_ROSTER_KEY, _members);
    localStorage.setItem(STORAGE_ROUNDS_KEY, String(_rounds));
  } catch (e) {}
}

export function isOpen() {
  return _open;
}

export function isCouncilOpen() {
  return _open;
}

export function openCouncil() {
  if (_open && _modal && !_modal.classList.contains('hidden')) return;
  _open = true;
  const modal = _getModal();
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  document.body.classList.add('council-active');

  const btn = document.getElementById('tool-council-btn');
  if (btn) btn.classList.add('active');
  const rail = document.getElementById('rail-council');
  if (rail) rail.classList.add('active');

  _loadModels().then(() => {
    _renderSetup();
  });
}

export function closeCouncil() {
  if (_isDiscussing) {
    if (!confirm('A council discussion is currently in progress. Close and abort?')) {
      return;
    }
    stopDiscussion();
  }
  _open = false;
  if (_modal) {
    _modal.classList.add('hidden');
    _modal.style.display = 'none';
  }
  document.body.classList.remove('council-active');

  const btn = document.getElementById('tool-council-btn');
  if (btn) btn.classList.remove('active');
  const rail = document.getElementById('rail-council');
  if (rail) rail.classList.remove('active');
}

export function toggleCouncil() {
  if (_open) closeCouncil();
  else openCouncil();
}

async function _loadModels() {
  if (_cachedModels && _cachedModels.length > 0) return _cachedModels;
  try {
    const res = await fetch(`${API_BASE}/api/models`, { credentials: 'same-origin' });
    const data = await res.json();
    const list = [];
    if (data.items && Array.isArray(data.items)) {
      data.items.forEach(item => {
        const displayNames = item.models_display || item.models || [];
        const extraDisplay = item.models_extra_display || item.models_extra || [];
        (item.models || []).forEach((mid, i) => {
          list.push({
            id: mid,
            name: (displayNames[i] || mid).split('/').pop(),
            endpointId: item.endpoint_id || '',
            endpointName: item.endpoint_name || '',
            url: item.url || '',
          });
        });
        (item.models_extra || []).forEach((mid, i) => {
          list.push({
            id: mid,
            name: (extraDisplay[i] || mid).split('/').pop(),
            endpointId: item.endpoint_id || '',
            endpointName: item.endpoint_name || '',
            url: item.url || '',
          });
        });
      });
    }
    _cachedModels = sortModelObjects(list);
    return _cachedModels;
  } catch (e) {
    console.warn('[Council] Failed to load models:', e);
    return [];
  }
}

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'council-modal';
  _modal.className = 'modal council-modal';
  _modal.style.display = 'none';

  _modal.innerHTML = `
    <div class="modal-content council-modal-content" role="dialog" aria-label="Council of Models">
      <div class="modal-header council-header">
        <div class="council-title-wrap">
          <svg class="council-title-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
            <circle cx="9" cy="7" r="4"/>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
          </svg>
          <div class="council-title-text">
            <h4>Council of Models</h4>
            <span class="council-subtitle">Multi-Agent Deliberation & Consensus Engine</span>
          </div>
        </div>
        <div class="council-header-actions">
          <button type="button" class="council-header-btn" id="council-presets-btn" title="Saved Councils">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
            <span>Presets</span>
          </button>
          <button type="button" class="council-header-btn" id="council-history-btn" title="Past Deliberations">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            <span>History</span>
          </button>
          <button type="button" class="council-header-btn" id="council-logs-btn" title="View Deliberation Logs">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
            <span>Logs</span>
          </button>
          <button class="close-btn" id="council-close-btn" title="Close Council" aria-label="Close">✕</button>
        </div>
      </div>
      <div class="modal-body council-body" id="council-body">
        <!-- Dynamic Content Rendered Here -->
      </div>
    </div>
  `;

  document.body.appendChild(_modal);

  const closeBtn = _modal.querySelector('#council-close-btn');
  if (closeBtn) closeBtn.addEventListener('click', closeCouncil);
  _modal.addEventListener('click', (e) => {
    if (e.target === _modal) closeCouncil();
  });

  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) {
    makeWindowDraggable(_modal, { content, header });
  }

  // Register with ModalManager
  Modals.register('council-modal', {
    railBtnId: 'rail-council',
    sidebarBtnId: 'tool-council-btn',
    restoreFn: () => {
      _open = true;
      _modal.classList.remove('hidden');
      _modal.style.display = 'flex';
      document.body.classList.add('council-active');
    },
    closeFn: () => {
      closeCouncil();
    }
  });

  return _modal;
}

function _renderSetup() {
  const body = document.getElementById('council-body');
  if (!body) return;

  const models = _cachedModels || [];

  // Default models if empty
  if (models.length > 0) {
    _members.forEach((m, idx) => {
      if (!m.model || !models.some(cand => cand.id === m.model)) {
        const fallback = models[idx % models.length];
        m.model = fallback.id;
        m.endpoint_id = fallback.endpointId || '';
        m.endpoint_url = fallback.url || '';
      }
    });
  }

  body.innerHTML = `
    <div class="council-chamber-container">
      <!-- Roster Builder -->
      <div class="council-section-header">
        <div class="council-section-title">
          <span>Council Members (${_members.length}/${MAX_MEMBERS})</span>
          <span class="council-section-sub">Configure up to 6 local & cloud models with distinct identities</span>
        </div>
        ${_members.length < MAX_MEMBERS ? `
          <button type="button" class="btn-secondary council-add-btn" id="council-add-member-btn">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Add Member
          </button>
        ` : ''}
      </div>

      <div class="council-roster-grid" id="council-roster-grid">
        ${_members.map((m, idx) => {
          const color = COUNCIL_AVATAR_COLORS[idx % COUNCIL_AVATAR_COLORS.length];
          const initial = (m.name || 'M').charAt(0).toUpperCase();
          return `
            <div class="council-member-card" data-idx="${idx}">
              <div class="council-member-card-header">
                <div class="council-avatar" style="background-color: ${color};">
                  ${uiModule.esc(initial)}
                </div>
                <input type="text" class="council-name-input" data-field="name" value="${uiModule.esc(m.name)}" placeholder="Member Name" title="Custom name (e.g. Joana, Roseann)" />
                ${_members.length > MIN_MEMBERS ? `
                  <button type="button" class="council-remove-btn" data-remove="${idx}" title="Remove member">✕</button>
                ` : ''}
              </div>
              <div class="council-member-fields">
                <div class="council-field-row">
                  <label>Model</label>
                  <select class="council-select" data-field="model">
                    ${models.map(cand => {
                      const sel = cand.id === m.model ? 'selected' : '';
                      const epLabel = cand.endpointName ? ` (${cand.endpointName})` : '';
                      return `<option value="${cand.id}" data-epid="${cand.endpointId}" data-url="${cand.url}" ${sel}>${uiModule.esc(cand.name)}${uiModule.esc(epLabel)}</option>`;
                    }).join('')}
                  </select>
                </div>
                <div class="council-field-row">
                  <label>Persona / Stance</label>
                  <input type="text" class="council-persona-input" data-field="persona" value="${uiModule.esc(m.persona || '')}" placeholder="e.g. Pragmatic Architect, Critic, UI Expert..." />
                </div>
              </div>
            </div>
          `;
        }).join('')}
      </div>

      <!-- Deliberation Topic & Config -->
      <div class="council-deliberation-box">
        <div class="council-topic-label">
          <span>Topic for Deliberation</span>
          <span class="council-topic-hint">The Council will state positions, cross-examine colleagues by name, and synthesize a verdict</span>
        </div>
        <textarea id="council-topic-input" class="council-topic-input" rows="3" placeholder="Enter the topic or question for the Council (e.g. 'Why is Laravel a good framework?')..."></textarea>
        
        <!-- Suggestions Chips -->
        <div class="council-suggestions-row">
          <span class="council-suggestions-label">Try:</span>
          ${TOPIC_SUGGESTIONS.map(s => `
            <button type="button" class="council-chip" data-topic="${uiModule.esc(s)}">${uiModule.esc(s)}</button>
          `).join('')}
        </div>

        <div class="council-controls-row">
          <div class="council-rounds-wrap">
            <label for="council-rounds-select">Discussion Rounds:</label>
            <select id="council-rounds-select" class="council-select council-rounds-select">
              <option value="1" ${_rounds === 1 ? 'selected' : ''}>1 Round (Opening + Verdict)</option>
              <option value="2" ${_rounds === 2 ? 'selected' : ''}>2 Rounds (Opening + Cross-Debate + Verdict)</option>
              <option value="3" ${_rounds === 3 ? 'selected' : ''}>3 Rounds (Deep Multi-Turn Debate + Verdict)</option>
            </select>
          </div>

          <div class="council-action-buttons">
            <button type="button" class="btn-secondary" id="council-save-preset-btn">Save Lineup</button>
            <button type="button" class="btn-primary council-start-btn" id="council-start-btn">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              Convene Council
            </button>
          </div>
        </div>
      </div>
    </div>
  `;

  _bindSetupEvents();
}

function _bindSetupEvents() {
  const body = document.getElementById('council-body');
  if (!body) return;

  // Member card inputs
  body.querySelectorAll('.council-name-input').forEach(input => {
    const card = input.closest('.council-member-card');
    const idx = parseInt(card.dataset.idx, 10);
    input.addEventListener('input', () => {
      _members[idx].name = input.value.trim() || `Member ${idx + 1}`;
      const avatar = card.querySelector('.council-avatar');
      if (avatar) avatar.textContent = _members[idx].name.charAt(0).toUpperCase();
      _saveState();
    });
  });

  body.querySelectorAll('.council-persona-input').forEach(input => {
    const card = input.closest('.council-member-card');
    const idx = parseInt(card.dataset.idx, 10);
    input.addEventListener('input', () => {
      _members[idx].persona = input.value;
      _saveState();
    });
  });

  body.querySelectorAll('.council-select[data-field="model"]').forEach(sel => {
    const card = sel.closest('.council-member-card');
    const idx = parseInt(card.dataset.idx, 10);
    sel.addEventListener('change', () => {
      const opt = sel.options[sel.selectedIndex];
      _members[idx].model = sel.value;
      _members[idx].endpoint_id = opt.dataset.epid || '';
      _members[idx].endpoint_url = opt.dataset.url || '';
      _saveState();
    });
  });

  // Remove member button
  body.querySelectorAll('.council-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.remove, 10);
      if (_members.length > MIN_MEMBERS) {
        _members.splice(idx, 1);
        _saveState();
        _renderSetup();
      }
    });
  });

  // Add member button
  const addBtn = document.getElementById('council-add-member-btn');
  if (addBtn) {
    addBtn.addEventListener('click', () => {
      if (_members.length < MAX_MEMBERS) {
        const nextNum = _members.length + 1;
        const models = _cachedModels || [];
        const modelPick = models[nextNum % models.length] || {};
        _members.push({
          id: `m-${Date.now().toString(36)}`,
          name: `Councilor ${nextNum}`,
          model: modelPick.id || '',
          endpoint_id: modelPick.endpointId || '',
          endpoint_url: modelPick.url || '',
          persona: 'Domain Specialist',
        });
        _saveState();
        _renderSetup();
      }
    });
  }

  // Topic suggestion chips
  body.querySelectorAll('.council-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const topicInput = document.getElementById('council-topic-input');
      if (topicInput) {
        topicInput.value = chip.dataset.topic;
        topicInput.focus();
      }
    });
  });

  // Rounds selector
  const roundsSel = document.getElementById('council-rounds-select');
  if (roundsSel) {
    roundsSel.addEventListener('change', () => {
      _rounds = parseInt(roundsSel.value, 10) || 2;
      _saveState();
    });
  }

  // Save preset button
  const savePresetBtn = document.getElementById('council-save-preset-btn');
  if (savePresetBtn) {
    savePresetBtn.addEventListener('click', _promptSavePreset);
  }

  // Start discussion button
  const startBtn = document.getElementById('council-start-btn');
  if (startBtn) {
    startBtn.addEventListener('click', () => {
      const topicInput = document.getElementById('council-topic-input');
      const topic = topicInput ? topicInput.value.trim() : '';
      if (!topic) {
        uiModule.showToast('Please enter a topic for the Council to discuss');
        if (topicInput) topicInput.focus();
        return;
      }
      startDiscussion(topic);
    });
  }

  // Header buttons
  const presetsBtn = document.getElementById('council-presets-btn');
  if (presetsBtn) presetsBtn.addEventListener('click', _showPresetsModal);

  const historyBtn = document.getElementById('council-history-btn');
  if (historyBtn) historyBtn.addEventListener('click', _showHistoryModal);

  const logsBtn = document.getElementById('council-logs-btn');
  if (logsBtn) logsBtn.addEventListener('click', _showLogsModal);
}

export async function startDiscussion(topic) {
  if (_isDiscussing) return;
  _isDiscussing = true;
  _activeDiscussionData = {
    topic,
    members: JSON.parse(JSON.stringify(_members)),
    rounds: _rounds,
    roundData: {},
    synthesis: '',
    startTime: Date.now(),
  };

  _renderActiveDiscussion();

  _abortController = new AbortController();

  try {
    const payload = {
      topic,
      members: _members,
      rounds: _rounds,
    };

    const res = await fetch(`${API_BASE}/api/council/discuss`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
      signal: _abortController.signal,
    });

    if (!res.ok) {
      const errJson = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(errJson.detail || `Server returned ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop();

      for (const part of parts) {
        if (!part.trim()) continue;
        const lines = part.split(/\r?\n/);
        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data:')) {
            const rawJson = trimmed.substring(5).trim();
            if (rawJson && rawJson !== '[DONE]') {
              try {
                const event = JSON.parse(rawJson);
                _handleStreamEvent(event);
              } catch (e) {
                console.warn('[Council] Failed to parse SSE event:', e, rawJson);
              }
            }
          }
        }
      }
    }

    _finalizeDiscussion();
  } catch (e) {
    if (e.name === 'AbortError') {
      _updateStatusBadge('Discussion Stopped by User', 'stopped');
    } else {
      console.error('[Council] Deliberation error:', e);
      _updateStatusBadge(`Error: ${e.message}`, 'error');
      uiModule.showToast(`Council error: ${e.message}`);
    }
  } finally {
    _isDiscussing = false;
    _abortController = null;
    const stopBtn = document.getElementById('council-stop-btn');
    if (stopBtn) {
      stopBtn.style.display = 'none';
    }
    const newBtn = document.getElementById('council-new-btn');
    if (newBtn) {
      newBtn.style.display = 'inline-flex';
    }
  }
}

export function stopDiscussion() {
  if (_abortController) {
    try {
      _abortController.abort();
    } catch (_) {}
  }
  _isDiscussing = false;
  _clearAllActiveMembers();
  _updateStatusBadge('Discussion Stopped by User', 'stopped');
  const stopBtn = document.getElementById('council-stop-btn');
  if (stopBtn) stopBtn.style.display = 'none';
  const newBtn = document.getElementById('council-new-btn');
  if (newBtn) newBtn.style.display = 'inline-flex';
  uiModule.showToast('Council deliberation stopped.');
}

function _renderActiveDiscussion() {
  const body = document.getElementById('council-body');
  if (!body || !_activeDiscussionData) return;

  const { topic, members, rounds } = _activeDiscussionData;

  body.innerHTML = `
    <div class="council-arena-container">
      <!-- Arena Header -->
      <div class="council-arena-header">
        <div class="council-arena-topic-wrap">
          <div class="council-arena-tag">Deliberation in Progress</div>
          <h3 class="council-arena-topic">${uiModule.esc(topic)}</h3>
        </div>
        <div class="council-arena-actions">
          <div class="council-status-badge" id="council-live-status">
            <span class="council-live-dot"></span>
            <span id="council-status-text">Convening Council...</span>
          </div>
          <button type="button" class="btn-danger council-control-btn" id="council-stop-btn" title="Stop deliberation">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
            Stop
          </button>
          <button type="button" class="btn-secondary council-control-btn" id="council-new-btn" style="display:none;" title="Start a new deliberation">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
            New Topic
          </button>
          <button type="button" class="btn-secondary council-control-btn" id="council-copy-btn" title="Copy transcript to clipboard">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            Copy
          </button>
        </div>
      </div>

      <!-- Members Active Row -->
      <div class="council-live-members-row">
        ${members.map((m, idx) => {
          const color = COUNCIL_AVATAR_COLORS[idx % COUNCIL_AVATAR_COLORS.length];
          return `
            <div class="council-live-member-pill" id="live-member-pill-${m.id}" style="--member-color: ${color};">
              <span class="council-avatar-small" style="background-color: ${color};">${m.name.charAt(0).toUpperCase()}</span>
              <span class="council-live-name">${uiModule.esc(m.name)}</span>
              <span class="council-live-model-tag">${uiModule.esc(m.model.split('/').pop())}</span>
              <span class="council-member-indicator" id="indicator-${m.id}"></span>
            </div>
          `;
        }).join('')}
      </div>

      <!-- Deliberation Stream Feed (Unified Scroll Container) -->
      <div class="council-arena-feed" id="council-arena-feed">
        <!-- Rounds & Speeches land here -->

        <!-- Final Consensus Box (Hidden until synthesis, scrolls together with debate) -->
        <div class="council-verdict-card hidden" id="council-verdict-card">
          <div class="council-verdict-header">
            <div class="council-verdict-title">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>
              <span>Council Verdict & Consensus Answer</span>
            </div>
            <span class="council-verdict-badge">Official Consensus</span>
          </div>
          <div class="council-verdict-body markdown-body" id="council-verdict-body">
            <div class="council-verdict-placeholder">Synthesizing deliberations...</div>
          </div>
        </div>
      </div>
    </div>
  `;

  // Bind arena buttons
  const stopBtn = document.getElementById('council-stop-btn');
  if (stopBtn) stopBtn.addEventListener('click', stopDiscussion);

  const newBtn = document.getElementById('council-new-btn');
  if (newBtn) newBtn.addEventListener('click', _renderSetup);

  const copyBtn = document.getElementById('council-copy-btn');
  if (copyBtn) copyBtn.addEventListener('click', _copyDiscussionTranscript);
}

function _findMember(memberId, memberName) {
  if (!_activeDiscussionData || !Array.isArray(_activeDiscussionData.members)) return null;
  return _activeDiscussionData.members.find(m =>
    (memberId && m.id === memberId) ||
    (memberName && m.name && m.name.toLowerCase() === memberName.toLowerCase())
  );
}

function _ensureSpeechBubble(round, memberId, memberName) {
  const mObj = _findMember(memberId, memberName) || { id: memberId || 'm', name: memberName || 'Councilor' };
  const effectiveId = mObj.id || memberId || memberName;

  let bubble = document.getElementById(`speech-${round}-${effectiveId}`);
  if (!bubble) {
    const speechesCont = document.getElementById(`council-round-speeches-${round}`);
    if (speechesCont) {
      const idx = _activeDiscussionData.members.findIndex(m => m.id === mObj.id || m.name === mObj.name);
      const color = COUNCIL_AVATAR_COLORS[idx >= 0 ? (idx % COUNCIL_AVATAR_COLORS.length) : 0];
      const initial = (mObj.name || memberName || 'C').charAt(0).toUpperCase();

      bubble = document.createElement('div');
      bubble.id = `speech-${round}-${effectiveId}`;
      bubble.className = 'council-speech-bubble';
      bubble.style.setProperty('--speech-color', color);
      bubble.innerHTML = `
        <div class="council-speech-header">
          <div class="council-avatar-small" style="background-color: ${color};">${uiModule.esc(initial)}</div>
          <span class="council-speaker-name">${uiModule.esc(mObj.name || memberName || 'Councilor')}</span>
          ${mObj.persona ? `<span class="council-speaker-persona">${uiModule.esc(mObj.persona)}</span>` : ''}
          <span class="council-speaker-model">${uiModule.esc((mObj.model || '').split('/').pop())}</span>
        </div>
        <div class="council-speech-content markdown-body" id="speech-content-${round}-${effectiveId}">
          <span class="council-formulating-placeholder" style="opacity:0.45; font-style:italic;">Formulating position...</span>
        </div>
      `;
      speechesCont.appendChild(bubble);
    }
  }
  return bubble;
}

function _handleStreamEvent(event) {
  const feed = document.getElementById('council-arena-feed');
  if (!feed) return;

  switch (event.type) {
    case 'start': {
      if (event.members && Array.isArray(event.members)) {
        _activeDiscussionData.members = event.members;
      }
      break;
    }

    case 'round_start': {
      const rNum = event.round;
      _updateStatusBadge(`Round ${rNum}: ${event.label}`, 'active');
      let roundSec = document.getElementById(`council-round-section-${rNum}`);
      if (!roundSec) {
        roundSec = document.createElement('div');
        roundSec.id = `council-round-section-${rNum}`;
        roundSec.className = 'council-round-section';
        roundSec.innerHTML = `
          <div class="council-round-divider">
            <span>Round ${rNum} — ${uiModule.esc(event.label)}</span>
          </div>
          <div class="council-round-speeches" id="council-round-speeches-${rNum}"></div>
        `;
        const verdictCard = document.getElementById('council-verdict-card');
        if (verdictCard && verdictCard.parentNode === feed) {
          feed.insertBefore(roundSec, verdictCard);
        } else {
          feed.appendChild(roundSec);
        }

        // Pre-render placeholders for each member so all members appear immediately
        if (_activeDiscussionData && Array.isArray(_activeDiscussionData.members)) {
          _activeDiscussionData.members.forEach(m => {
            _ensureSpeechBubble(rNum, m.id, m.name);
          });
        }
      }
      break;
    }

    case 'member_start': {
      const { round, member_id, member_name } = event;
      const mObj = _findMember(member_id, member_name);
      const effectiveId = mObj ? mObj.id : (member_id || member_name);
      _clearAllActiveMembers();
      _setMemberActive(effectiveId, true);
      _updateStatusBadge(`Councilor ${member_name} speaking...`, 'active');
      const bubble = _ensureSpeechBubble(round, effectiveId, member_name);
      if (bubble) {
        bubble.classList.add('speaking-active');
        bubble.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      break;
    }

    case 'member_chunk': {
      const { round, member_id, member_name, delta } = event;
      const mObj = _findMember(member_id, member_name);
      const effectiveId = mObj ? mObj.id : (member_id || member_name);
      _setMemberActive(effectiveId, true);
      _ensureSpeechBubble(round, effectiveId, member_name);

      const contentEl = document.getElementById(`speech-content-${round}-${effectiveId}`);
      if (contentEl) {
        if (!_activeDiscussionData.roundData[round]) _activeDiscussionData.roundData[round] = {};
        _activeDiscussionData.roundData[round][effectiveId] = (_activeDiscussionData.roundData[round][effectiveId] || '') + delta;
        contentEl.innerHTML = _renderMarkdown(_activeDiscussionData.roundData[round][effectiveId]);
      }
      feed.scrollTop = feed.scrollHeight;
      break;
    }

    case 'member_error': {
      const { member_name, error } = event;
      if (uiModule && uiModule.showToast) {
        uiModule.showToast(`Councilor ${member_name}: ${error}`, 'warning');
      }
      break;
    }

    case 'member_done': {
      const { round, member_id, member_name, content } = event;
      const mObj = _findMember(member_id, member_name);
      const effectiveId = mObj ? mObj.id : (member_id || member_name);
      _setMemberActive(effectiveId, false);

      const bubble = document.getElementById(`speech-${round}-${effectiveId}`);
      if (bubble) {
        bubble.classList.remove('speaking-active');
      }
      if (!_activeDiscussionData.roundData[round]) _activeDiscussionData.roundData[round] = {};
      _activeDiscussionData.roundData[round][effectiveId] = content;
      _ensureSpeechBubble(round, effectiveId, member_name);

      const contentEl = document.getElementById(`speech-content-${round}-${effectiveId}`);
      if (contentEl) {
        const text = content || _activeDiscussionData.roundData[round]?.[effectiveId] || '(No response recorded)';
        contentEl.innerHTML = _renderMarkdown(text);
      }
      break;
    }

    case 'synthesis_start': {
      _clearAllActiveMembers();
      _updateStatusBadge('Synthesizing Council Consensus...', 'active');
      const card = document.getElementById('council-verdict-card');
      if (card) {
        card.classList.remove('hidden');
        card.scrollIntoView({ behavior: 'smooth' });
      }
      break;
    }

    case 'synthesis_chunk': {
      const vBody = document.getElementById('council-verdict-body');
      if (vBody) {
        _activeDiscussionData.synthesis += event.delta;
        vBody.innerHTML = _renderMarkdown(_activeDiscussionData.synthesis);
      }
      feed.scrollTop = feed.scrollHeight;
      break;
    }

    case 'synthesis_done': {
      _activeDiscussionData.synthesis = event.verdict;
      const vBody = document.getElementById('council-verdict-body');
      if (vBody) {
        vBody.innerHTML = _renderMarkdown(event.verdict || '(No synthesis verdict produced)');
      }
      break;
    }

    case 'complete': {
      _finalizeDiscussion();
      break;
    }
  }
}

function _setMemberActive(memberId, isActive) {
  const pill = document.getElementById(`live-member-pill-${memberId}`);
  if (pill) {
    pill.classList.toggle('speaking', isActive);
  }
}

function _clearAllActiveMembers() {
  document.querySelectorAll('.council-live-member-pill').forEach(p => p.classList.remove('speaking'));
}

function _updateStatusBadge(text, state) {
  const statusEl = document.getElementById('council-status-text');
  const badge = document.getElementById('council-live-status');
  if (statusEl) statusEl.textContent = text;
  if (badge) {
    badge.className = `council-status-badge status-${state}`;
  }
}

function _finalizeDiscussion() {
  _clearAllActiveMembers();
  _updateStatusBadge('Deliberation Concluded', 'completed');

  // Save to history
  if (_activeDiscussionData && _activeDiscussionData.synthesis) {
    fetch(`${API_BASE}/api/council/history`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(_activeDiscussionData),
    }).catch(() => {});
  }
}

function _copyDiscussionTranscript() {
  if (!_activeDiscussionData) return;
  const { topic, members, roundData, synthesis } = _activeDiscussionData;

  let text = `# Council of Models Deliberation\n\n**Topic:** ${topic}\n\n`;
  text += `**Council Members:**\n`;
  members.forEach(m => {
    text += `- **${m.name}** (${m.model}${m.persona ? ` — ${m.persona}` : ''})\n`;
  });
  text += `\n---\n\n`;

  Object.keys(roundData).forEach(r => {
    text += `## Round ${r}\n\n`;
    members.forEach(m => {
      const speech = roundData[r]?.[m.id] || '(No statement)';
      text += `### ${m.name} (${m.model}):\n${speech}\n\n`;
    });
  });

  if (synthesis) {
    text += `\n---\n\n## Official Council Verdict & Consensus\n\n${synthesis}\n`;
  }

  navigator.clipboard.writeText(text).then(() => {
    uiModule.showToast('Council transcript copied to clipboard!');
  }).catch(() => {
    uiModule.showToast('Failed to copy transcript');
  });
}

// ── Presets Modal ──────────────────────────────────────────

async function _promptSavePreset() {
  const name = prompt('Enter a name for this Council Lineup:', 'My Custom Council');
  if (!name || !name.trim()) return;

  try {
    const res = await fetch(`${API_BASE}/api/council/presets`, { credentials: 'same-origin' });
    const data = await res.json();
    const presets = data.presets || [];

    presets.push({
      id: `preset-${Date.now().toString(36)}`,
      name: name.trim(),
      members: JSON.parse(JSON.stringify(_members)),
    });

    await fetch(`${API_BASE}/api/council/presets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ presets }),
    });

    uiModule.showToast(`Saved preset "${name}"!`);
  } catch (e) {
    uiModule.showToast('Failed to save preset: ' + e.message);
  }
}

async function _showPresetsModal() {
  try {
    const res = await fetch(`${API_BASE}/api/council/presets`, { credentials: 'same-origin' });
    const data = await res.json();
    const presets = data.presets || [];

    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.style.zIndex = '400';
    overlay.innerHTML = `
      <div class="modal-content" style="width: min(500px, 92vw);">
        <div class="modal-header">
          <h4>Saved Council Presets</h4>
          <button class="close-btn" id="close-presets-submodal">✕</button>
        </div>
        <div class="modal-body" style="max-height: 400px; overflow-y: auto;">
          ${presets.length === 0 ? '<p style="opacity:0.6; text-align:center; padding: 20px;">No saved presets yet. Click "Save Lineup" in the Council chamber.</p>' : `
            <div style="display:flex; flex-direction:column; gap: 8px;">
              ${presets.map((p, idx) => `
                <div class="council-preset-row" style="display:flex; align-items:center; justify-content:space-between; padding: 10px; border-radius: 8px; background: color-mix(in srgb, var(--fg) 4%, transparent);">
                  <div>
                    <div style="font-weight:600; font-size:13px;">${uiModule.esc(p.name)}</div>
                    <div style="font-size:11px; opacity:0.6;">${(p.members || []).map(m => uiModule.esc(m.name)).join(', ')} (${p.members?.length || 0} members)</div>
                  </div>
                  <div style="display:flex; gap:6px;">
                    <button type="button" class="btn-secondary load-preset-btn" data-idx="${idx}" style="padding: 4px 10px; font-size:11px;">Load</button>
                    <button type="button" class="btn-danger del-preset-btn" data-idx="${idx}" style="padding: 4px 8px; font-size:11px;">✕</button>
                  </div>
                </div>
              `).join('')}
            </div>
          `}
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    overlay.querySelector('#close-presets-submodal').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    overlay.querySelectorAll('.load-preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.dataset.idx, 10);
        const chosen = presets[idx];
        if (chosen && chosen.members) {
          _members = JSON.parse(JSON.stringify(chosen.members));
          _saveState();
          _renderSetup();
          overlay.remove();
          uiModule.showToast(`Loaded preset "${chosen.name}"`);
        }
      });
    });

    overlay.querySelectorAll('.del-preset-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const idx = parseInt(btn.dataset.idx, 10);
        presets.splice(idx, 1);
        await fetch(`${API_BASE}/api/council/presets`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ presets }),
        });
        overlay.remove();
        _showPresetsModal();
      });
    });
  } catch (e) {
    uiModule.showToast('Failed to load presets: ' + e.message);
  }
}

async function _showHistoryModal() {
  try {
    const res = await fetch(`${API_BASE}/api/council/history`, { credentials: 'same-origin' });
    const data = await res.json();
    const history = data.history || [];

    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.style.zIndex = '400';
    overlay.innerHTML = `
      <div class="modal-content" style="width: min(600px, 94vw);">
        <div class="modal-header">
          <h4>Past Council Deliberations</h4>
          <button class="close-btn" id="close-history-submodal">✕</button>
        </div>
        <div class="modal-body" style="max-height: 480px; overflow-y: auto;">
          ${history.length === 0 ? '<p style="opacity:0.6; text-align:center; padding: 20px;">No past deliberations saved yet.</p>' : `
            <div style="display:flex; flex-direction:column; gap: 10px;">
              ${history.map((h, idx) => `
                <div class="council-history-row" style="padding: 12px; border-radius: 8px; background: color-mix(in srgb, var(--fg) 4%, transparent); border: 1px solid var(--border);">
                  <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 6px;">
                    <div style="font-weight:600; font-size:13px; color:var(--brand-color);">${uiModule.esc(h.topic || 'Untitled Topic')}</div>
                    <button type="button" class="btn-danger del-history-btn" data-id="${h.id}" style="padding: 2px 6px; font-size:10px;">✕</button>
                  </div>
                  <div style="font-size:11px; opacity:0.7; margin-bottom: 6px;">
                    ${(h.members || []).map(m => m.name).join(', ')} • ${new Date(h.created_at || Date.now()).toLocaleString()}
                  </div>
                  <button type="button" class="btn-secondary load-history-btn" data-idx="${idx}" style="padding: 4px 10px; font-size:11px;">View Transcript</button>
                </div>
              `).join('')}
            </div>
          `}
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    overlay.querySelector('#close-history-submodal').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    overlay.querySelectorAll('.load-history-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.dataset.idx, 10);
        const item = history[idx];
        if (item) {
          _activeDiscussionData = JSON.parse(JSON.stringify(item));
          _renderActiveDiscussion();
          const feed = document.getElementById('council-arena-feed');
          if (feed && item.roundData) {
            Object.keys(item.roundData).forEach(r => {
              _handleStreamEvent({ type: 'round_start', round: parseInt(r, 10), label: `Round ${r}` });
              Object.keys(item.roundData[r]).forEach(mId => {
                const mObj = item.members.find(m => m.id === mId) || { name: 'Member' };
                _handleStreamEvent({
                  type: 'member_done',
                  round: parseInt(r, 10),
                  member_id: mId,
                  member_name: mObj.name,
                  content: item.roundData[r][mId],
                });
              });
            });
          }
          if (item.synthesis) {
            _handleStreamEvent({ type: 'synthesis_done', verdict: item.synthesis });
            const card = document.getElementById('council-verdict-card');
            if (card) card.classList.remove('hidden');
          }
          _updateStatusBadge('Saved Transcript', 'completed');
          const stopBtn = document.getElementById('council-stop-btn');
          if (stopBtn) stopBtn.style.display = 'none';
          const newBtn = document.getElementById('council-new-btn');
          if (newBtn) newBtn.style.display = 'inline-flex';
          overlay.remove();
        }
      });
    });

    overlay.querySelectorAll('.del-history-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        await fetch(`${API_BASE}/api/council/history/${id}`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        overlay.remove();
        _showHistoryModal();
      });
    });
  } catch (e) {
    uiModule.showToast('Failed to load history: ' + e.message);
  }
}

async function _showLogsModal() {
  let logText = 'Loading deliberation logs...';
  try {
    const res = await fetch(`${API_BASE}/api/council/logs?lines=250`);
    if (res.ok) {
      const data = await res.json();
      logText = data.logs || 'No logs recorded yet.';
    } else {
      logText = `Failed to fetch logs (Status: ${res.status})`;
    }
  } catch (e) {
    logText = `Error fetching logs: ${e.message}`;
  }

  const overlay = document.createElement('div');
  overlay.className = 'modal';
  overlay.style.zIndex = '400';
  overlay.innerHTML = `
    <div class="modal-content" style="width: min(720px, 94vw); max-height: 85vh; display: flex; flex-direction: column;">
      <div class="modal-header">
        <h4>Council Deliberation Logs</h4>
        <div style="display:flex; gap:6px; align-items:center; margin-left: auto;">
          <button type="button" class="btn-secondary" id="council-refresh-logs-btn" style="padding:4px 10px; font-size:11px;">Refresh</button>
          <button class="close-btn" id="close-logs-submodal">✕</button>
        </div>
      </div>
      <div class="modal-body" style="flex:1; overflow-y:auto; padding: 12px;">
        <pre class="council-logs-pre" id="council-logs-content" style="background: color-mix(in srgb, var(--fg) 4%, var(--bg)); border:1px solid var(--border); padding:12px; border-radius:8px; font-family:var(--font-mono, monospace); font-size:11.5px; white-space:pre-wrap; max-height:480px; overflow-y:auto; line-height:1.5; color:var(--fg); margin:0;">${uiModule.esc(logText)}</pre>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  overlay.querySelector('#close-logs-submodal').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

  overlay.querySelector('#council-refresh-logs-btn')?.addEventListener('click', async () => {
    const pre = overlay.querySelector('#council-logs-content');
    if (pre) pre.textContent = 'Refreshing logs...';
    try {
      const res = await fetch(`${API_BASE}/api/council/logs?lines=250`);
      if (res.ok) {
        const data = await res.json();
        if (pre) pre.textContent = data.logs || 'No logs recorded yet.';
      }
    } catch (e) {
      if (pre) pre.textContent = `Error: ${e.message}`;
    }
  });
}
