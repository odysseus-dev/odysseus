/**
 * OdysseusPkg — Package Runtime API
 *
 * Packages use this to inject UI into named slots and interact with the app.
 * Available as window.OdysseusPkg (set at load time) and as an ES6 default export.
 *
 * Usage inside a widget script:
 *   const api = window.OdysseusPkg;
 *   api.addWidget('chatInput', myButton);
 *   api.openPanel('my-modal', 'My Panel', { width: '680px', onInit: (body) => { ... } });
 *   const text = api.getChatInput();
 *   await api.callLLM('You are...', 'Improve this: ' + text);
 */
import { makeWindowDraggable } from './windowDrag.js';
import * as _Modals from './modalManager.js';

// Named injection slot registry.
// Each entry is a function returning the live DOM container (resolved lazily
// so the API can be imported before the DOM is fully parsed).
const _slotFn = {
  sidebar:   () => document.getElementById('package-widgets-sidebar'),
  chatInput: () => document.getElementById('pkg-slot-chat-input'),
  toolbar:   () => document.getElementById('package-widgets-toolbar'),
};

/**
 * Append an element into a named UI slot.
 * @param {'sidebar'|'chatInput'|'toolbar'} slot
 * @param {HTMLElement} element
 */
function addWidget(slot, element) {
  const fn = _slotFn[slot];
  if (!fn) { console.warn('[OdysseusPkg] Unknown slot:', slot); return; }
  const container = fn();
  if (!container) { console.warn('[OdysseusPkg] Slot not in DOM yet:', slot); return; }
  container.appendChild(element);
}

/** Return the current text in the chat textarea. */
function getChatInput() {
  return document.getElementById('message')?.value || '';
}

/**
 * Set the chat textarea content and trigger a React-style input event
 * so the app's auto-resize and ghost-text logic stays in sync.
 */
function setChatInput(text) {
  const ta = document.getElementById('message');
  if (!ta) return;
  // Use the native setter so React/framework state handlers notice the change
  const nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value'
  )?.set;
  if (nativeSetter) {
    nativeSetter.call(ta, text);
  } else {
    ta.value = text;
  }
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.dispatchEvent(new Event('change', { bubbles: true }));
  ta.focus();
}

/**
 * Make a non-streaming LLM call through the server.
 * @param {string} systemPrompt  System instruction for the model.
 * @param {string} userMessage   The user turn.
 * @param {string} [model='']    Optional model spec (defaults to user's default model).
 * @returns {Promise<string>}    The model's text response.
 */
async function callLLM(systemPrompt, userMessage, model = '') {
  const res = await fetch('/api/pkg/llm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system: systemPrompt, message: userMessage, model }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'LLM call failed');
  }
  const data = await res.json();
  return data.content;
}

/**
 * Create or restore a draggable modal panel (like the built-in tool panels).
 *
 * If the panel with `id` is already open, restores it from minimized state.
 * If it doesn't exist yet, creates it, registers it with the modal manager,
 * and calls opts.onInit(bodyEl) so the caller can populate the body.
 *
 * @param {string} id - Unique modal ID (also used as DOM id)
 * @param {string} title - Header text
 * @param {object} opts
 * @param {string}   [opts.width='680px']        - Modal content width
 * @param {string}   [opts.sidebarBtnId]         - Sidebar button to highlight when open
 * @param {Function} [opts.onInit]               - Called with bodyEl when modal is first created
 * @param {Function} [opts.onClose]              - Called when the modal is closed/removed
 * @returns {HTMLElement|null} The body container element
 */
function openPanel(id, title, opts = {}) {
  if (_Modals.isRegistered(id)) {
    if (_Modals.isMinimized(id)) _Modals.restore(id);
    return document.getElementById(`${id}-body`);
  }

  const width = opts.width || '680px';
  const modal = document.createElement('div');
  modal.id = id;
  modal.className = 'modal';
  modal.innerHTML = `
    <div class="modal-content" role="dialog" aria-label="${title.replace(/"/g, '')}"
         style="width:min(${width},95vw);max-height:85vh;overflow-y:auto;padding:10px;background:var(--bg)">
      <div class="modal-header" id="${id}-header">
        <h4>${title}</h4>
        <button class="close-btn" id="${id}-close-btn" aria-label="Close">&#x2716;</button>
      </div>
      <div id="${id}-body" class="pkg-modal-body"></div>
    </div>`;
  document.body.appendChild(modal);

  makeWindowDraggable(modal, {
    content: modal.querySelector('.modal-content'),
    header:  modal.querySelector(`#${id}-header`),
  });
  modal.querySelector(`#${id}-close-btn`).addEventListener('click', () => {
    _Modals.close(id);
  });

  _Modals.register(id, {
    sidebarBtnId: opts.sidebarBtnId,
    restoreFn: () => { modal.classList.remove('hidden', 'modal-minimized'); },
    closeFn: () => {
      if (opts.onClose) opts.onClose();
      modal.remove();
    },
  });

  modal.classList.remove('hidden', 'modal-minimized');

  const bodyEl = document.getElementById(`${id}-body`);
  if (opts.onInit) opts.onInit(bodyEl);
  return bodyEl;
}

// ── Cross-package event bus ────────────────────────────────────────────────
const _evtHandlers = {};

/**
 * Lightweight pub-sub for cross-package and app→package communication.
 *
 * Well-known events emitted by the app shell:
 *   'workspace:selected'  { wsId: string|null }
 *   'workspace:exec-done' { wsId, command, exitCode }
 *   'chat:send'           { message: string }
 *
 * @example
 *   const unsub = OdysseusPkg.events.on('workspace:selected', ({ wsId }) => { ... });
 *   unsub(); // unsubscribe
 *   OdysseusPkg.events.emit('my-pkg:data-ready', { items: [...] });
 */
const events = {
  on(event, handler) {
    (_evtHandlers[event] ??= []).push(handler);
    return () => {
      _evtHandlers[event] = (_evtHandlers[event] || []).filter(h => h !== handler);
    };
  },
  off(event, handler) {
    if (_evtHandlers[event])
      _evtHandlers[event] = _evtHandlers[event].filter(h => h !== handler);
  },
  emit(event, data) {
    for (const h of (_evtHandlers[event] || [])) {
      try { h(data); } catch (e) { console.error(`[OdysseusPkg.events] '${event}' handler threw:`, e); }
    }
  },
};

// Bridge native DOM events into the pkg event bus so widgets don't need window listeners
window.addEventListener('workspace-changed', () => {
  const wsId = window.OdysseusShellWS?.getSelected?.() ?? null;
  events.emit('workspace:selected', { wsId });
});

// ── Per-package storage ────────────────────────────────────────────────────

/**
 * Return a simple key-value storage interface scoped to pkgId.
 * Backed by the server-side config store (GET/PUT /api/packages/{id}/config).
 *
 * @param {string} pkgId
 * @returns {{ get, set, delete, getAll, clear }}
 *
 * @example
 *   const store = OdysseusPkg.storage('my-package');
 *   const val = await store.get('api_key', '');
 *   await store.set('api_key', 'sk-...');
 *   await store.delete('api_key');
 */
function storage(pkgId) {
  const _url = () => `/api/packages/${encodeURIComponent(pkgId)}/config`;

  async function _load() {
    const r = await fetch(_url());
    if (!r.ok) return {};
    return (await r.json()).config || {};
  }

  async function _save(cfg) {
    const r = await fetch(_url(), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: cfg }),
    });
    if (!r.ok) throw new Error(`storage save failed (${r.status})`);
    return (await r.json()).config || cfg;
  }

  return {
    async get(key, defaultValue = null) {
      const cfg = await _load();
      return key in cfg ? cfg[key] : defaultValue;
    },
    async set(key, value) {
      const cfg = await _load();
      return _save({ ...cfg, [key]: value });
    },
    async delete(key) {
      const cfg = await _load();
      const updated = { ...cfg };
      delete updated[key];
      return _save(updated);
    },
    async getAll() {
      return _load();
    },
    async clear() {
      return _save({});
    },
  };
}

/**
 * Fetch this package's stored config for the current user.
 * @param {string} pkgId  The package ID from manifest.json
 * @returns {Promise<object>}
 */
async function getConfig(pkgId) {
  const res = await fetch(`/api/packages/${encodeURIComponent(pkgId)}/config`);
  if (!res.ok) return {};
  const data = await res.json();
  return data.config || {};
}

/**
 * Persist config updates for this package (shallow merge with existing values).
 * @param {string} pkgId
 * @param {object} updates  Key/value pairs to store
 * @returns {Promise<object>}  Full saved config
 */
async function setConfig(pkgId, updates) {
  const current = await getConfig(pkgId);
  const merged = { ...current, ...updates };
  const res = await fetch(`/api/packages/${encodeURIComponent(pkgId)}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config: merged }),
  });
  if (!res.ok) throw new Error('setConfig failed');
  const data = await res.json();
  return data.config || merged;
}

/**
 * Register a settings tab for this package inside the Settings modal.
 * Creates a nav button and a panel container wired to the settings nav system.
 *
 * @param {string}   pkgId    Package ID (used for the tab's data attribute)
 * @param {string}   label    Display name for the tab button
 * @param {Function} initFn   Called once with the panel <div> when tab is first opened
 * @param {string}   [iconSvg] Optional SVG string for the nav icon
 */
function addSettingsTab(pkgId, label, initFn, iconSvg) {
  const tabKey = `pkg-${pkgId}`;

  const navSlot = document.getElementById('pkg-slot-settings-tabs');
  const panelSlot = document.getElementById('pkg-slot-settings-panels');
  if (!navSlot || !panelSlot) {
    console.warn('[OdysseusPkg] Settings tab slots not found in DOM:', pkgId);
    return;
  }

  // Nav button
  const btn = document.createElement('button');
  btn.className = 'settings-nav-item';
  btn.dataset.settingsTab = tabKey;
  btn.innerHTML = (iconSvg || '') + `<span>${label}</span>`;
  navSlot.appendChild(btn);

  // Panel container
  const panel = document.createElement('div');
  panel.dataset.settingsPanel = tabKey;
  panel.className = 'hidden';
  panelSlot.appendChild(panel);

  // Wire click: hide all panels/deactivate all buttons, then show this one
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-settings-panel]').forEach(p => p.classList.add('hidden'));
    document.querySelectorAll('.settings-nav-item').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    panel.classList.remove('hidden');
    if (!panel._pkgInitialized) {
      panel._pkgInitialized = true;
      if (initFn) initFn(panel);
    }
  });
}

const OdysseusPkg = { addWidget, getChatInput, setChatInput, callLLM, openPanel, getConfig, setConfig, addSettingsTab, events, storage };

// Expose globally so widget scripts that can't do ES6 imports can access it
window.OdysseusPkg = OdysseusPkg;

export default OdysseusPkg;
