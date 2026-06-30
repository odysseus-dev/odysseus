// static/js/contextRefs.js
// Sticky library context chips: state, persistence, preflight, and rendering.

import { getJSON, setJSON } from './storage.js';

const STORAGE_KEY = 'odysseus.contextRefs.v1';
const MAX_REFS = 4;

let API_BASE = '';
let _state = null;

function _loadState() {
  if (_state === null) {
    _state = getJSON(STORAGE_KEY, {});
    if (typeof _state !== 'object' || _state === null) _state = {};
  }
  return _state;
}

function _persist() {
  setJSON(STORAGE_KEY, _state);
}

function _getSessionRefs(sessionId) {
  const s = _loadState();
  return Array.isArray(s[sessionId]) ? s[sessionId].slice() : [];
}

function _key(ref) {
  return `${ref.type}:${ref.id}`;
}

export function init(apiBase) {
  API_BASE = apiBase || '';
}

export function getRefs(sessionId) {
  return _getSessionRefs(sessionId);
}

export function setRefs(sessionId, refs) {
  const s = _loadState();
  s[sessionId] = refs.slice(0, MAX_REFS);
  _persist();
  renderContextStrip(sessionId);
}

export function addRef(ref, sessionId) {
  const refs = _getSessionRefs(sessionId);
  const k = _key(ref);
  if (refs.some(r => _key(r) === k)) return refs;
  if (refs.length >= MAX_REFS) {
    refs.shift();
  }
  refs.push({ type: ref.type, id: ref.id, title: ref.title || 'Untitled' });
  setRefs(sessionId, refs);
  return refs;
}

export function removeRef(id, sessionId) {
  const refs = _getSessionRefs(sessionId).filter(r => r.id !== id);
  setRefs(sessionId, refs);
  return refs;
}

export function clearRefs(sessionId) {
  const s = _loadState();
  delete s[sessionId];
  _persist();
  renderContextStrip(sessionId);
}

function _typeIconSvg(type) {
  if (type === 'document') {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>';
  }
  if (type === 'research') {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>';
  }
  return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
}

function _typeLabel(type) {
  if (type === 'document') return 'Doc';
  if (type === 'research') return 'Research';
  return 'Chat';
}

export function renderContextStrip(sessionId) {
  const strip = document.getElementById('context-strip');
  if (!strip) return;
  const refs = _getSessionRefs(sessionId);
  if (!refs.length) {
    strip.innerHTML = '';
    strip.style.display = 'none';
    return;
  }
  strip.style.display = 'flex';
  strip.innerHTML = refs.map(ref => `
    <div class="thumb context-chip" data-type="${escapeHtml(ref.type)}" data-id="${escapeHtml(ref.id)}" title="${escapeHtml(ref.title)}">
      <span class="context-chip-icon">${_typeIconSvg(ref.type)}</span>
      <span class="context-chip-type">${_typeLabel(ref.type)}</span>
      <span class="context-chip-title">${escapeHtml(ref.title)}</span>
      <button type="button" class="context-chip-remove" aria-label="Remove ${escapeHtml(ref.title)}">×</button>
    </div>
  `).join('');

  strip.querySelectorAll('.context-chip-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const chip = btn.closest('.context-chip');
      if (chip) removeRef(chip.dataset.id, sessionId);
    });
  });
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

export async function preflightAdd(ref, sessionId) {
  if (!API_BASE) throw new Error('contextRefs not initialized');
  const refs = _getSessionRefs(sessionId);
  const payload = {
    session_id: sessionId,
    refs: refs.map(r => ({ type: r.type, id: r.id, title: r.title })),
    candidate: { type: ref.type, id: ref.id, title: ref.title || 'Untitled' },
  };
  const res = await fetch(`${API_BASE}/api/context_refs/preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  });
  let data = {};
  try {
    data = await res.json();
  } catch {}
  if (!res.ok || !data.ok) {
    const ui = await import('./ui.js').catch(() => ({}));
    const message = data.message || 'Not enough context room to attach this source.';
    if (ui.styledConfirm) {
      await ui.styledConfirm(message, {
        title: 'Not enough context room',
        confirmText: 'OK',
        singleButton: true,
      });
    } else if (ui.showError) {
      ui.showError(message);
    } else {
      window.alert(message);
    }
    return false;
  }
  return true;
}

export async function addRefWithPreflight(ref, sessionId) {
  if (!sessionId) {
    const ui = await import('./ui.js').catch(() => ({}));
    (ui.showError || window.alert)('Select a chat session first.');
    return false;
  }
  const refs = _getSessionRefs(sessionId);
  if (refs.some(r => _key(r) === _key(ref))) {
    const ui = await import('./ui.js').catch(() => ({}));
    (ui.showToast || console.log)('Already attached');
    return true;
  }
  if (refs.length >= MAX_REFS) {
    const ui = await import('./ui.js').catch(() => ({}));
    (ui.showError || window.alert)(`At most ${MAX_REFS} context sources can be attached.`);
    return false;
  }
  const ok = await preflightAdd(ref, sessionId);
  if (!ok) return false;
  addRef(ref, sessionId);
  const ui = await import('./ui.js').catch(() => ({}));
  (ui.showToast || console.log)(`Attached ${ref.title || ref.id}`);
  return true;
}

export default {
  init,
  getRefs,
  setRefs,
  addRef,
  removeRef,
  clearRefs,
  renderContextStrip,
  preflightAdd,
  addRefWithPreflight,
};
