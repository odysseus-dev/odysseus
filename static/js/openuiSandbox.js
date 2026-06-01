const PARENT_SOURCE = 'odysseus-openui-parent';
const SANDBOX_SOURCE = 'odysseus-openui-sandbox';
const SANDBOX_URL = '/static/openui-sandbox.html';

export const OPENUI_IFRAME_SANDBOX = 'allow-scripts';

let _nextId = 1;
let _messageBound = false;
const _controllers = new Map();

function _jsonClone(value, maxChars = 20000) {
  try {
    const text = JSON.stringify(value ?? null);
    if (text.length > maxChars) return null;
    return JSON.parse(text);
  } catch (_e) {
    return null;
  }
}

export function sanitizeSandboxAction(action) {
  const clean = _jsonClone(action);
  if (!clean || typeof clean !== 'object') return null;
  if (clean.type !== 'continue_conversation') return null;
  return {
    type: 'continue_conversation',
    humanFriendlyMessage: String(clean.humanFriendlyMessage || '').slice(0, 1000),
    formName: clean.formName ? String(clean.formName).slice(0, 120) : undefined,
    formState: _jsonClone(clean.formState, 20000),
  };
}

function _themeSnapshot() {
  const root = document.documentElement;
  const cs = getComputedStyle(root);
  const read = (name, fallback = '') => (cs.getPropertyValue(name) || fallback).trim();
  return {
    bg: read('--bg', '#ffffff'),
    fg: read('--fg', '#111111'),
    accent: read('--accent', '#2aa198'),
    border: read('--border', 'rgba(0,0,0,0.18)'),
    fontFamily: read('--font-family', cs.fontFamily || 'system-ui, sans-serif'),
  };
}

function _bindMessages() {
  if (_messageBound) return;
  _messageBound = true;
  window.addEventListener('message', (event) => {
    const msg = event.data || {};
    if (!msg || msg.source !== SANDBOX_SOURCE || !msg.id) return;
    const ctrl = _controllers.get(msg.id);
    if (!ctrl || event.source !== ctrl.iframe.contentWindow) return;

    if (msg.type === 'height') {
      const height = Math.max(120, Math.min(1200, Number(msg.height) || 0));
      ctrl.iframe.style.height = `${height}px`;
      return;
    }
    if (msg.type === 'state') {
      const state = _jsonClone(msg.state);
      if (typeof ctrl.options.onState === 'function') ctrl.options.onState(state);
      return;
    }
    if (msg.type === 'action') {
      const action = sanitizeSandboxAction(msg.action);
      if (!action || !ctrl.options.forwardActions) return;
      if (typeof ctrl.options.onAction === 'function') ctrl.options.onAction(action);
      window.dispatchEvent(new CustomEvent('odysseus-openui-action', { detail: action }));
      return;
    }
    if (msg.type === 'error') {
      ctrl.mount.innerHTML = '<div class="inline-openui-error">OpenUI render failed.</div>';
    }
  });
}

function _post(ctrl, payload) {
  if (!ctrl.iframe.contentWindow) return;
  ctrl.iframe.contentWindow.postMessage({
    source: PARENT_SOURCE,
    id: ctrl.id,
    ...payload,
  }, '*');
}

function _ensureController(mount, options = {}) {
  _bindMessages();
  mount.classList.add('openui-sandbox-host');
  if (mount._openuiSandboxController) {
    mount._openuiSandboxController.options = options;
    return mount._openuiSandboxController;
  }

  mount.innerHTML = '';
  const iframe = document.createElement('iframe');
  const id = `openui-${_nextId++}`;
  iframe.className = 'openui-sandbox-frame';
  iframe.title = options.title || 'OpenUI preview';
  iframe.referrerPolicy = 'no-referrer';
  iframe.setAttribute('sandbox', OPENUI_IFRAME_SANDBOX);
  iframe.src = SANDBOX_URL;

  const ctrl = {
    id,
    iframe,
    mount,
    loaded: false,
    pending: null,
    options,
  };
  _controllers.set(id, ctrl);
  mount._openuiSandboxController = ctrl;

  iframe.addEventListener('load', () => {
    ctrl.loaded = true;
    if (ctrl.pending) _post(ctrl, ctrl.pending);
  });
  mount.appendChild(iframe);
  return ctrl;
}

export function renderSandboxedOpenUI(mount, response, options = {}) {
  if (!mount) return;
  const ctrl = _ensureController(mount, options);
  const payload = {
    type: 'render',
    response: String(response || ''),
    isStreaming: !!options.isStreaming,
    initialState: options.initialState || undefined,
    theme: _themeSnapshot(),
  };
  ctrl.pending = payload;
  if (ctrl.loaded) _post(ctrl, payload);
}

export function unmountSandboxedOpenUI(mount) {
  const ctrl = mount && mount._openuiSandboxController;
  if (!ctrl) return;
  try {
    _post(ctrl, { type: 'unmount' });
  } catch (_e) {}
  _controllers.delete(ctrl.id);
  delete mount._openuiSandboxController;
  mount.classList.remove('openui-sandbox-host');
  mount.innerHTML = '';
}
