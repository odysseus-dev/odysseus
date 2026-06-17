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
         style="width:min(${width},95vw);max-height:85vh;overflow-y:auto;padding:0">
      <div class="modal-header" id="${id}-header">
        <h4>${title}</h4>
        <button class="close-btn" id="${id}-close-btn" aria-label="Close">&#x2716;</button>
      </div>
      <div id="${id}-body" style="padding:16px"></div>
    </div>`;
  document.body.appendChild(modal);

  makeWindowDraggable(
    modal.querySelector('.modal-content'),
    modal.querySelector(`#${id}-header`)
  );
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

const OdysseusPkg = { addWidget, getChatInput, setChatInput, callLLM, openPanel };

// Expose globally so widget scripts that can't do ES6 imports can access it
window.OdysseusPkg = OdysseusPkg;

export default OdysseusPkg;
