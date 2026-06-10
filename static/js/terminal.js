// Terminal panel — a real interactive PTY terminal backed by /ws/pty.
//
// Self-contained: injects its own modal + launch button, lazy-loads xterm.js
// from /static/lib/ (offline-first), and speaks the JSON WebSocket protocol
// {type:input|resize|ping} -> server, {type:output|exit|error|pong} <- server.
//
// This is the REAL terminal (ConPTY on Windows) so tqdm/progress bars and
// ANSI/curses apps render correctly — unlike the one-shot exec + log tail.

let _term = null;        // xterm Terminal instance
let _fit = null;         // FitAddon instance
let _ws = null;          // WebSocket
let _resizeObs = null;   // ResizeObserver
let _xtermReady = null;  // lazy-load promise
let _injected = false;

const LIB = {
  js: '/static/lib/xterm.js',
  css: '/static/lib/xterm.css',
  fit: '/static/lib/xterm-addon-fit.js',
};

function ensureXTerm() {
  if (_xtermReady) return _xtermReady;
  if (window.Terminal && window.FitAddon) return (_xtermReady = Promise.resolve());
  _xtermReady = new Promise((resolve, reject) => {
    // CSS
    if (!document.querySelector('link[data-xterm-css]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = LIB.css;
      link.setAttribute('data-xterm-css', '1');
      document.head.appendChild(link);
    }
    const loadScript = (src) => new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = res;
      s.onerror = () => rej(new Error('Failed to load ' + src));
      document.head.appendChild(s);
    });
    // xterm core first, then the fit addon (depends on window.Terminal).
    loadScript(LIB.js)
      .then(() => loadScript(LIB.fit))
      .then(resolve)
      .catch(reject);
  });
  return _xtermReady;
}

function injectMarkup() {
  if (_injected) return;
  _injected = true;

  // Launch button — fixed, bottom-right, unobtrusive.
  const btn = document.createElement('button');
  btn.id = 'terminal-launch-btn';
  btn.title = 'Open terminal (real PTY)';
  btn.textContent = 'Terminal';
  btn.style.cssText =
    'position:fixed;bottom:14px;right:14px;z-index:240;padding:6px 12px;' +
    'font-size:0.8rem;border-radius:6px;cursor:pointer;' +
    'background:var(--panel,#1e1e1e);color:var(--fg,#eee);' +
    'border:1px solid var(--border,#444);opacity:0.82;';
  btn.addEventListener('mouseenter', () => (btn.style.opacity = '1'));
  btn.addEventListener('mouseleave', () => (btn.style.opacity = '0.82'));
  btn.addEventListener('click', () => toggle());
  document.body.appendChild(btn);

  // Modal
  const modal = document.createElement('div');
  modal.id = 'terminal-modal';
  modal.className = 'modal hidden';
  modal.innerHTML =
    '<div class="modal-content" style="width:min(900px,94vw);height:min(560px,82vh);' +
    'display:flex;flex-direction:column;background:var(--bg,#111);">' +
      '<div class="modal-header" style="cursor:move;">' +
        '<h4 style="margin:0;">Terminal</h4>' +
        '<button class="close-btn" id="close-terminal-modal" aria-label="Close terminal">✖</button>' +
      '</div>' +
      '<div id="terminal-body" style="flex:1;overflow:hidden;padding:6px 8px;' +
      'background:var(--bg,#000);display:flex;flex-direction:column;">' +
        '<div id="terminal-xterm" style="flex:1;width:100%;height:100%;"></div>' +
      '</div>' +
      '<div id="terminal-status" style="font-size:0.7rem;opacity:0.6;padding:2px 10px;' +
      'border-top:1px solid var(--border,#333);">disconnected</div>' +
    '</div>';
  document.body.appendChild(modal);

  modal.querySelector('#close-terminal-modal').addEventListener('click', () => close());
}

function setStatus(text) {
  const el = document.getElementById('terminal-status');
  if (el) el.textContent = text;
}

function safeFit(sendResize = true) {
  if (!_fit || !_term) return;
  try {
    _fit.fit();
    if (sendResize && _ws && _ws.readyState === WebSocket.OPEN &&
        _term.cols > 0 && _term.rows > 0) {
      _ws.send(JSON.stringify({ type: 'resize', cols: _term.cols, rows: _term.rows }));
    }
  } catch (_) { /* container not laid out yet */ }
}

async function mount() {
  await ensureXTerm();
  const container = document.getElementById('terminal-xterm');
  if (!container) return;

  if (_term) {
    // Re-fit an existing terminal (re-open case).
    safeFit();
    return;
  }

  _term = new window.Terminal({
    scrollback: 5000,
    fontSize: 13,
    fontFamily: 'Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace',
    cursorBlink: true,
    theme: { background: '#000000' },
  });
  const FitAddonCtor = (window.FitAddon && (window.FitAddon.FitAddon || window.FitAddon));
  _fit = new FitAddonCtor();
  _term.loadAddon(_fit);
  _term.open(container);

  // 3-phase resize: immediate, post-paint, post-layout-settle.
  safeFit(false);
  setTimeout(() => safeFit(false), 250);
  setTimeout(() => safeFit(false), 500);

  _resizeObs = new ResizeObserver(() => safeFit());
  _resizeObs.observe(container);

  connect();

  _term.onData((d) => {
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: 'input', data: d }));
    }
  });
}

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const cols = (_term && _term.cols) || 80;
  const rows = (_term && _term.rows) || 24;
  const url = `${proto}://${window.location.host}/ws/pty?cols=${cols}&rows=${rows}`;
  setStatus('connecting…');
  _ws = new WebSocket(url);

  _ws.onopen = () => {
    setStatus('connected');
    // Send an authoritative size once the socket is live.
    safeFit();
  };
  _ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg.type === 'output') {
      _term.write(msg.data);
    } else if (msg.type === 'exit') {
      _term.write(`\r\n\x1b[33m[process exited: code ${msg.code}]\x1b[0m\r\n`);
      setStatus(`exited (code ${msg.code}) — reopen to start a new session`);
    } else if (msg.type === 'error') {
      _term.write(`\r\n\x1b[31m${msg.data}\x1b[0m\r\n`);
      setStatus('error');
    }
  };
  _ws.onclose = (ev) => {
    if (ev.code === 4403) {
      _term.write('\r\n\x1b[31m[access denied: admin login required]\x1b[0m\r\n');
      setStatus('access denied (admin only)');
    } else {
      setStatus('disconnected');
    }
  };
  _ws.onerror = () => setStatus('connection error');
}

function disconnect() {
  if (_ws) {
    try { _ws.close(); } catch (_) {}
    _ws = null;
  }
}

export function open() {
  injectMarkup();
  const modal = document.getElementById('terminal-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  // Mount after the modal is visible so the container has real dimensions.
  setTimeout(() => { mount(); }, 0);
}

export function close() {
  const modal = document.getElementById('terminal-modal');
  if (modal) modal.classList.add('hidden');
  disconnect();
  if (_resizeObs) { try { _resizeObs.disconnect(); } catch (_) {} _resizeObs = null; }
  // Dispose the terminal so a fresh session starts next open.
  if (_term) { try { _term.dispose(); } catch (_) {} _term = null; _fit = null; }
}

export function isVisible() {
  const modal = document.getElementById('terminal-modal');
  return !!modal && !modal.classList.contains('hidden');
}

export function toggle() {
  if (isVisible()) close(); else open();
}

// Self-register: inject the launch button as soon as the module loads.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectMarkup);
} else {
  injectMarkup();
}

const terminalModule = { open, close, isVisible, toggle };
export default terminalModule;
if (typeof window !== 'undefined') window.terminalModule = terminalModule;
