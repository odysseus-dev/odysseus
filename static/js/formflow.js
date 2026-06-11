// static/js/formflow.js — FormFlow modal panel (notes-style pane)
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { applyEdgeDock, clearDockSide } from './modalSnap.js';
import { wireSwipeDismiss, collapseSidebarForMobileSheet, isMobileSheet } from './panelSheet.js';

// ─── Module state ──────────────────────────────────────────────────────────
let _open        = false;
let _questions   = [];
let _answers     = {};
let _currentIdx  = 0;
let _activeTab   = 'paste';
let _pendingFile = null;

// Live DOM refs — set each time the pane opens, cleared on close
let _pane        = null;
let _backdrop    = null;

// ─── Styles (injected once) ────────────────────────────────────────────────
function _ensureStyles() {
  if (document.getElementById('ff-modal-styles')) return;
  const s = document.createElement('style');
  s.id = 'ff-modal-styles';
  s.textContent = `
.ff-pane-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px 16px 80px;
  display: flex;
  flex-direction: column;
}
.ff-progress {
  position: absolute;
  top: 0; left: 0;
  height: 3px;
  background: var(--red);
  width: 0%;
  transition: width 0.35s ease;
  border-radius: 0;
  pointer-events: none;
}
.ff-screen { display: none; flex-direction: column; flex: 1; }
.ff-screen.active { display: flex; animation: ffFade 0.16s ease; }
@keyframes ffFade {
  from { opacity:0; transform:translateY(5px); }
  to   { opacity:1; transform:translateY(0); }
}
/* ── Input ── */
.ff-title {
  font-size: 1.25rem; font-weight: 700; margin-bottom: 4px;
  background: linear-gradient(135deg, var(--brand-color,var(--red)), color-mix(in srgb,var(--brand-color,var(--red)) 55%,var(--fg)));
  -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}
.ff-sub { font-size:0.82rem; color:color-mix(in srgb,var(--fg) 48%,transparent); margin-bottom:18px; }
.ff-tabs { display:flex; gap:2px; border-bottom:1px solid var(--border); margin-bottom:14px; }
.ff-tab {
  padding:6px 14px; font-size:0.82rem; color:color-mix(in srgb,var(--fg) 45%,transparent);
  cursor:pointer; border:none; border-bottom:2px solid transparent; margin-bottom:-1px;
  background:none; font-family:inherit; transition:color .15s,border-color .15s;
}
.ff-tab.active { color:var(--fg); border-bottom-color:var(--red); }
.ff-tab:hover:not(.active) { color:var(--fg); }
.ff-tab-pane { display:none; }
.ff-tab-pane.active { display:block; }
.ff-paste {
  width:100%; min-height:140px; background:var(--panel); border:1px solid var(--border);
  border-radius:8px; color:var(--fg); font-size:0.9rem; padding:12px; resize:vertical;
  font-family:inherit; outline:none; transition:border-color .2s; line-height:1.5;
}
.ff-paste:focus { border-color:var(--red); }
.ff-paste::placeholder { color:color-mix(in srgb,var(--fg) 28%,transparent); }
.ff-drop-zone {
  border:1.5px dashed var(--border); border-radius:8px; padding:36px 16px;
  text-align:center; cursor:pointer; transition:border-color .2s,background .2s;
  color:color-mix(in srgb,var(--fg) 48%,transparent); user-select:none; font-size:0.88rem;
}
.ff-drop-zone:hover,.ff-drop-zone.drag-over {
  border-color:var(--red); background:color-mix(in srgb,var(--red) 6%,var(--panel));
}
.ff-drop-icon { font-size:1.8rem; display:block; margin-bottom:8px; }
.ff-drop-hint { font-size:0.74rem; margin-top:6px; color:color-mix(in srgb,var(--fg) 30%,transparent); }
.ff-file-chosen {
  display:none; align-items:center; gap:10px; background:var(--panel);
  border:1px solid var(--border); border-radius:8px; padding:10px 14px; font-size:0.86rem;
}
.ff-file-chosen.visible { display:flex; }
.ff-file-name { flex:1; color:var(--fg); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ff-file-clear {
  color:color-mix(in srgb,var(--fg) 40%,transparent); cursor:pointer;
  background:none; border:none; font-size:0.95rem; padding:0 2px; line-height:1;
  transition:color .15s;
}
.ff-file-clear:hover { color:var(--red); }
.ff-btn-primary {
  display:block; width:100%; margin-top:14px; padding:11px;
  background:var(--red); color:#fff; border:none; border-radius:8px;
  font-size:0.95rem; font-weight:600; cursor:pointer; font-family:inherit; transition:opacity .18s;
}
.ff-btn-primary:hover { opacity:0.85; }
.ff-btn-primary:disabled { opacity:0.35; cursor:not-allowed; }
.ff-error { margin-top:10px; color:var(--red); font-size:0.82rem; min-height:18px; }
/* ── Loading ── */
.ff-loading-title { font-size:1rem; font-weight:600; margin-bottom:6px; text-align:center; }
.ff-loading-badge { font-size:0.72rem; color:color-mix(in srgb,var(--fg) 38%,transparent); margin-bottom:16px; text-align:center; min-height:16px; }
.ff-token-stream {
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  padding:12px; font-family:'JetBrains Mono','Fira Code','Courier New',monospace;
  font-size:0.7rem; color:color-mix(in srgb,var(--fg) 42%,transparent);
  max-height:130px; overflow:hidden; white-space:pre-wrap; word-break:break-all;
}
.ff-pulse {
  display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--red);
  animation:ffPulse 1s ease-in-out infinite; vertical-align:middle; margin-left:5px; margin-bottom:1px;
}
@keyframes ffPulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.3;transform:scale(.75)} }
/* ── Form ── */
.ff-q-number { font-size:0.72rem; letter-spacing:.08em; text-transform:uppercase; color:color-mix(in srgb,var(--fg) 38%,transparent); margin-bottom:10px; }
.ff-q-label { font-size:1.3rem; font-weight:700; line-height:1.25; color:var(--fg); margin-bottom:4px; }
.ff-q-required { color:var(--red); margin-left:3px; }
.ff-q-input-wrap { margin-top:18px; }
.ff-input {
  width:100%; background:var(--panel); border:1px solid var(--border); border-radius:8px;
  color:var(--fg); font-size:0.95rem; padding:10px 12px; font-family:inherit; outline:none;
  transition:border-color .2s;
}
.ff-input:focus { border-color:var(--red); }
.ff-input::placeholder { color:color-mix(in srgb,var(--fg) 28%,transparent); }
textarea.ff-input { min-height:110px; resize:vertical; line-height:1.5; }
.ff-limit-counter {
  text-align:right; font-size:0.74rem; font-family:'JetBrains Mono','Fira Code',monospace;
  margin-top:6px; color:color-mix(in srgb,var(--fg) 40%,transparent); transition:color .2s; min-height:16px;
}
.ff-limit-counter.warn { color:var(--warn,#f0ad4e); }
.ff-limit-counter.over { color:var(--red); font-weight:700; }
.ff-choice-list { display:flex; flex-direction:column; gap:7px; }
.ff-choice-opt {
  display:flex; align-items:center; gap:10px; padding:9px 12px;
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  cursor:pointer; transition:border-color .15s,background .15s; font-size:0.88rem; user-select:none;
}
.ff-choice-opt:hover { border-color:var(--red); }
.ff-choice-opt.selected { border-color:var(--red); background:color-mix(in srgb,var(--red) 10%,var(--panel)); }
.ff-choice-dot { width:13px; height:13px; border-radius:50%; border:2px solid var(--border); flex-shrink:0; transition:border-color .15s,background .15s; }
.ff-choice-opt.selected .ff-choice-dot { border-color:var(--red); background:var(--red); }
.ff-check-box {
  width:13px; height:13px; border-radius:3px; border:2px solid var(--border); flex-shrink:0;
  display:flex; align-items:center; justify-content:center; transition:border-color .15s,background .15s;
  font-size:0.6rem; color:#fff;
}
.ff-choice-opt.selected .ff-check-box { border-color:var(--red); background:var(--red); }
.ff-yesno { display:flex; gap:10px; }
.ff-yesno-btn {
  flex:1; padding:13px; background:var(--panel); border:1px solid var(--border); border-radius:8px;
  color:var(--fg); font-size:0.95rem; font-weight:500; cursor:pointer; font-family:inherit;
  transition:border-color .15s,background .15s;
}
.ff-yesno-btn:hover { border-color:var(--red); }
.ff-yesno-btn.selected { border-color:var(--red); background:color-mix(in srgb,var(--red) 10%,var(--panel)); }
.ff-scale { display:flex; gap:6px; flex-wrap:wrap; }
.ff-scale-btn {
  width:44px; height:44px; background:var(--panel); border:1px solid var(--border); border-radius:8px;
  color:var(--fg); font-size:0.9rem; cursor:pointer; font-family:inherit;
  transition:border-color .15s,background .15s;
}
.ff-scale-btn:hover { border-color:var(--red); }
.ff-scale-btn.selected { border-color:var(--red); background:color-mix(in srgb,var(--red) 10%,var(--panel)); }
/* ── Nav bar (form screen) ── */
.ff-nav {
  display:none; position:absolute; bottom:0; left:0; right:0;
  justify-content:space-between; padding:12px 16px;
  background:color-mix(in srgb,var(--panel) 92%,transparent);
  backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
  border-top:1px solid var(--border);
}
.ff-nav.visible { display:flex; }
.ff-nav-btn {
  padding:8px 22px; border-radius:8px; font-size:0.88rem; font-weight:500; cursor:pointer;
  border:1px solid var(--border); background:var(--panel); color:var(--fg);
  font-family:inherit; transition:border-color .15s,opacity .15s;
}
.ff-nav-btn:hover:not(:disabled) { border-color:var(--red); }
.ff-nav-btn.primary { background:var(--red); border-color:var(--red); color:#fff; }
.ff-nav-btn.primary:hover:not(:disabled) { opacity:0.85; }
.ff-nav-btn:disabled { opacity:0.3; cursor:not-allowed; }
.ff-nav-btn.invisible { visibility:hidden; }
/* ── Review ── */
.ff-review-title { font-size:1.2rem; font-weight:700; margin-bottom:5px; }
.ff-review-sub { font-size:0.82rem; color:color-mix(in srgb,var(--fg) 45%,transparent); margin-bottom:18px; }
.ff-review-actions { display:flex; gap:8px; margin-bottom:20px; }
.ff-review-btn {
  padding:8px 16px; border-radius:8px; font-size:0.82rem; font-weight:500; cursor:pointer;
  border:1px solid var(--border); background:var(--panel); color:var(--fg);
  font-family:inherit; transition:border-color .15s;
}
.ff-review-btn:hover { border-color:var(--red); }
.ff-review-btn.primary { background:var(--red); border-color:var(--red); color:#fff; }
.ff-review-btn.primary:hover { opacity:0.85; }
.ff-review-btn:disabled { opacity:0.5; cursor:default; }
.ff-review-item { margin-bottom:18px; padding-bottom:18px; border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent); }
.ff-review-item:last-child { border-bottom:none; }
.ff-review-q-num { font-size:0.7rem; letter-spacing:.08em; text-transform:uppercase; color:color-mix(in srgb,var(--fg) 35%,transparent); margin-bottom:2px; }
.ff-review-q-label { font-size:0.92rem; font-weight:600; margin-bottom:7px; }
.ff-review-answer { font-size:0.88rem; white-space:pre-wrap; background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:9px 12px; line-height:1.5; }
.ff-review-answer.empty { color:color-mix(in srgb,var(--fg) 35%,transparent); font-style:italic; }
.ff-restart-row { margin-top:20px; text-align:center; }
.ff-restart-link { background:none; border:none; font-family:inherit; font-size:0.82rem; color:color-mix(in srgb,var(--fg) 38%,transparent); cursor:pointer; text-decoration:underline; transition:color .15s; }
.ff-restart-link:hover { color:var(--fg); }
`;
  document.head.appendChild(s);
}

function _ensureFormFlowChipRegistered() {
  if (Modals.isRegistered('ff-panel')) return;
  Modals.register('ff-panel', {
    railBtnId: 'rail-formflow',
    sidebarBtnId: 'tool-formflow-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _forceClose(); },
  });
}

// ─── Panel open / close ────────────────────────────────────────────────────
export function openPanel() {
  if (_open) return;
  _open = true;
  _ensureStyles();

  document.body.classList.add('formflow-view');
  collapseSidebarForMobileSheet();

  const btn = document.getElementById('tool-formflow-btn');
  if (btn) btn.classList.add('active');

  // Backdrop + pane (reuse notes-pane CSS for shell + animations + docking)
  _backdrop = document.createElement('div');
  _backdrop.className = 'notes-pane-backdrop';
  _backdrop.id = 'ff-backdrop';

  _pane = document.createElement('div');
  _pane.className = 'notes-pane';
  _pane.id = 'ff-pane';
  _pane.style.position = 'relative'; // needed for absolute-positioned nav bar

  _pane.innerHTML = `
    <div class="ff-progress" id="ff-progress"></div>
    <div class="notes-mobile-grabber" aria-hidden="true"></div>
    <div class="notes-pane-header">
      <h4 style="font-size:0.88rem;font-weight:600;display:flex;align-items:center;gap:7px;margin:0;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:.7;flex-shrink:0;">
          <rect x="3" y="3" width="18" height="18" rx="2"/>
          <path d="M3 9h18"/><path d="M9 21V9"/><path d="M7 6h.01"/><path d="M12 6h5"/>
        </svg>
        FormFlow
      </h4>
      <span style="flex:1"></span>
      <button id="ff-minimize-btn" class="modal-minimize-btn" title="Minimize" aria-label="Minimize FormFlow" style="position:relative;left:2px;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" aria-hidden="true"><line x1="6" y1="18" x2="18" y2="18"/></svg>
      </button>
    </div>

    <div class="ff-pane-body">
      <!-- Input screen -->
      <div id="ff-screen-input" class="ff-screen active">
        <div class="ff-title">FormFlow</div>
        <div class="ff-sub">Paste a form or upload a file — answer one question at a time.</div>
        <div class="ff-tabs">
          <button class="ff-tab active" data-tab="paste">Paste text</button>
          <button class="ff-tab" data-tab="upload">Upload file</button>
        </div>
        <div id="ff-pane-paste" class="ff-tab-pane active">
          <textarea id="ff-paste" class="ff-paste" placeholder="Paste your form, questionnaire, or application here…" rows="7"></textarea>
        </div>
        <div id="ff-pane-upload" class="ff-tab-pane">
          <div id="ff-drop-zone" class="ff-drop-zone">
            <span class="ff-drop-icon">📄</span>
            <div>Drop a file here or <strong>click to browse</strong></div>
            <div class="ff-drop-hint">.pdf · .txt · .md · .html · .png · .jpg · .jpeg</div>
          </div>
          <div id="ff-file-chosen" class="ff-file-chosen">
            <span>📎</span>
            <span id="ff-file-name" class="ff-file-name"></span>
            <button id="ff-file-clear" class="ff-file-clear" aria-label="Remove file">✕</button>
          </div>
          <input id="ff-file-input" type="file" accept=".pdf,.txt,.md,.html,.png,.jpg,.jpeg" style="display:none" />
        </div>
        <div class="ff-error" id="ff-input-error"></div>
        <button class="ff-btn-primary" id="ff-parse-btn">Parse Form →</button>
      </div>

      <!-- Loading screen -->
      <div id="ff-screen-loading" class="ff-screen" style="align-items:center;padding-top:12px;">
        <div class="ff-loading-title">Parsing your form<span class="ff-pulse"></span></div>
        <div class="ff-loading-badge" id="ff-loading-badge"></div>
        <div id="ff-token-stream" class="ff-token-stream" style="width:100%;"></div>
      </div>

      <!-- Form screen -->
      <div id="ff-screen-form" class="ff-screen">
        <div class="ff-q-number" id="ff-q-number"></div>
        <div class="ff-q-label" id="ff-q-label"></div>
        <div class="ff-q-input-wrap" id="ff-q-input-wrap"></div>
        <div class="ff-limit-counter" id="ff-limit-counter"></div>
      </div>

      <!-- Review screen -->
      <div id="ff-screen-review" class="ff-screen">
        <div class="ff-review-title">Review your answers</div>
        <div class="ff-review-sub" id="ff-review-sub"></div>
        <div class="ff-review-actions">
          <button class="ff-review-btn primary" id="ff-copy-btn">Copy all</button>
          <button class="ff-review-btn" id="ff-download-btn">Download .txt</button>
        </div>
        <div id="ff-review-list"></div>
        <div class="ff-restart-row">
          <button class="ff-restart-link" id="ff-restart-btn">← Parse another form</button>
        </div>
      </div>
    </div>

    <!-- Fixed nav (form screen only) -->
    <div class="ff-nav" id="ff-nav">
      <button class="ff-nav-btn" id="ff-nav-back">Back</button>
      <button class="ff-nav-btn primary" id="ff-nav-next">Next →</button>
    </div>
  `;

  if (isMobileSheet()) {
    _pane.style.position = 'fixed';
    _pane.style.inset = '0';
    _pane.style.width = '100%';
    _pane.style.maxWidth = '100%';
    _pane.style.zIndex = '170';
    _pane.style.borderRadius = '14px 14px 0 0';
    _pane.style.animation = 'sheet-enter 0.25s cubic-bezier(0.2, 0.8, 0.2, 1) both';
    _pane.style.transformOrigin = 'bottom center';
  }

  _backdrop.addEventListener('click', (e) => { if (e.target === _backdrop) closePanel('down'); });
  _backdrop.appendChild(_pane);
  document.body.appendChild(_backdrop);

  wireSwipeDismiss(_pane.querySelector('.notes-mobile-grabber'), _pane, () => closePanel('down'));
  wireSwipeDismiss(_pane.querySelector('.notes-pane-header'), _pane, () => closePanel('down'));

  _wireEvents();

  if (!isMobileSheet()) {
    makeWindowDraggable(_pane, {
      content: _pane,
      header: _pane.querySelector('.notes-pane-header'),
      skipSelector: 'button, input, select, textarea',
    });
    applyEdgeDock(_pane, 'right');
  }

  Modals.register('ff-panel', {
    railBtnId: 'rail-formflow',
    sidebarBtnId: 'tool-formflow-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _forceClose(); },
  });

  // Restore prior state if pane was re-opened mid-session
  _syncScreen(_currentScreen());
}

export function closePanel(direction) {
  if (!_open) return;
  _open = false;

  const minimize = direction === 'down';
  if (minimize) {
    _ensureFormFlowChipRegistered();
  } else if (Modals.isRegistered('ff-panel')) {
    try { Modals.unregister('ff-panel'); } catch {}
  }

  document.body.classList.remove('formflow-view');
  try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}

  const btn = document.getElementById('tool-formflow-btn');
  if (btn) btn.classList.remove('active');

  if (_pane) {
    _pane.classList.add('notes-pane-leaving');
    const cleanup = () => {
      try { _pane?.remove(); } catch {}
      try { _backdrop?.remove(); } catch {}
      _pane = null;
      _backdrop = null;
    };
    _pane.addEventListener('animationend', cleanup, { once: true });
    setTimeout(cleanup, 220);
  } else if (_backdrop) {
    _backdrop.remove();
    _backdrop = null;
  }

  if (minimize) {
    try { Modals.minimize('ff-panel'); } catch {}
  }
}

function _forceClose() {
  _open = false;
  document.body.classList.remove('formflow-view');
  try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
  try { Modals.unregister('ff-panel'); } catch {}
  try { _pane?.remove(); } catch {}
  try { _backdrop?.remove(); } catch {}
  _pane = null;
  _backdrop = null;
  const btn = document.getElementById('tool-formflow-btn');
  if (btn) btn.classList.remove('active');
}

export function togglePanel() {
  if (_open) closePanel();
  else openPanel();
}

// ─── Track current screen across re-opens ─────────────────────────────────
let _screen = 'input'; // 'input' | 'loading' | 'form' | 'review'

function _currentScreen() { return _screen; }

function _showScreen(name) {
  _screen = name;
  const screens = ['input', 'loading', 'form', 'review'];
  screens.forEach(s => {
    const el = document.getElementById('ff-screen-' + s);
    if (el) el.classList.toggle('active', s === name);
  });
  const nav = document.getElementById('ff-nav');
  if (nav) nav.classList.toggle('visible', name === 'form');
  _updateProgress();
}

function _syncScreen(name) {
  if (name === 'form' && _questions.length) {
    _renderQuestion(_currentIdx);
    _showScreen('form');
  } else if (name === 'review' && _questions.length) {
    _renderReview();
    _showScreen('review');
  } else {
    _showScreen('input');
  }
}

function _updateProgress() {
  const bar = document.getElementById('ff-progress');
  if (!bar) return;
  let pct = 0;
  if (_screen === 'loading') pct = 15;
  else if (_screen === 'form') {
    const n = _questions.length;
    pct = n ? Math.round(((_currentIdx + 1) / n) * 80) + 15 : 15;
  } else if (_screen === 'review') pct = 100;
  bar.style.width = pct + '%';
}

// ─── Wire all event listeners (called once per open) ──────────────────────
function _wireEvents() {
  // Minimize
  document.getElementById('ff-minimize-btn')?.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    closePanel('down');
  });

  // Tabs
  _pane.querySelectorAll('.ff-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      _activeTab = tab.dataset.tab;
      _pane.querySelectorAll('.ff-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === _activeTab));
      _pane.querySelectorAll('.ff-tab-pane').forEach(p => p.classList.toggle('active', p.id === 'ff-pane-' + _activeTab));
      document.getElementById('ff-input-error').textContent = '';
    });
  });

  // File drop
  const dropZ  = document.getElementById('ff-drop-zone');
  const fileIn = document.getElementById('ff-file-input');
  const fileChosen = document.getElementById('ff-file-chosen');
  const fileClear  = document.getElementById('ff-file-clear');
  dropZ.addEventListener('click', () => fileIn.click());
  dropZ.addEventListener('dragover',  e => { e.preventDefault(); dropZ.classList.add('drag-over'); });
  dropZ.addEventListener('dragleave', () => dropZ.classList.remove('drag-over'));
  dropZ.addEventListener('drop', e => {
    e.preventDefault(); dropZ.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f) _setFile(f);
  });
  fileIn.addEventListener('change', () => { if (fileIn.files[0]) _setFile(fileIn.files[0]); });
  fileClear.addEventListener('click', () => {
    _pendingFile = null; fileIn.value = '';
    fileChosen.classList.remove('visible');
    dropZ.style.display = '';
    document.getElementById('ff-input-error').textContent = '';
  });

  // Parse button
  document.getElementById('ff-parse-btn').addEventListener('click', _handleSubmit);

  // Nav
  document.getElementById('ff-nav-next').addEventListener('click', _advanceForm);
  document.getElementById('ff-nav-back').addEventListener('click', () => {
    if (_currentIdx > 0) _renderQuestion(_currentIdx - 1);
  });

  // Keyboard Enter to advance (global on pane)
  _pane.addEventListener('keydown', e => {
    if (_screen !== 'form') return;
    if (e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'Enter') _tryAdvance();
  });

  // Review actions
  document.getElementById('ff-copy-btn').addEventListener('click', _copyAnswers);
  document.getElementById('ff-download-btn').addEventListener('click', _downloadAnswers);
  document.getElementById('ff-restart-btn').addEventListener('click', _restart);
}

// ─── File helpers ──────────────────────────────────────────────────────────
function _setFile(f) {
  _pendingFile = f;
  document.getElementById('ff-file-name').textContent = f.name;
  document.getElementById('ff-file-chosen').classList.add('visible');
  document.getElementById('ff-drop-zone').style.display = 'none';
  document.getElementById('ff-input-error').textContent = '';
}

function _fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = e => {
      const result = e.target.result;
      const idx = result.indexOf(',');
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    r.onerror = () => reject(new Error('Could not read image file.'));
    r.readAsDataURL(file);
  });
}

async function _extractPdf(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/formflow/extract-pdf', { method: 'POST', body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'PDF extraction failed (status ' + res.status + ').');
  }
  return (await res.json()).text;
}

// ─── Submit / parse ────────────────────────────────────────────────────────
async function _handleSubmit() {
  const errEl  = document.getElementById('ff-input-error');
  const parseBtn = document.getElementById('ff-parse-btn');
  errEl.textContent = '';
  parseBtn.disabled = true;

  try {
    if (_activeTab === 'paste') {
      const txt = document.getElementById('ff-paste').value.trim();
      if (!txt) { errEl.textContent = 'Please paste some form text first.'; return; }
      await _startParse({ text: txt });
    } else {
      const file = _pendingFile;
      if (!file) { errEl.textContent = 'Please choose a file first.'; return; }
      const ext = file.name.split('.').pop().toLowerCase();
      if (['png', 'jpg', 'jpeg'].includes(ext)) {
        const b64 = await _fileToBase64(file);
        await _startParse({ image_base64: b64, media_type: file.type || 'image/jpeg' });
      } else if (ext === 'pdf') {
        errEl.textContent = 'Extracting PDF…';
        const pdfText = await _extractPdf(file);
        errEl.textContent = '';
        await _startParse({ text: pdfText });
      } else {
        const raw = await file.text();
        if (!raw.trim()) { errEl.textContent = 'File appears to be empty.'; return; }
        await _startParse({ text: raw });
      }
    }
  } catch (err) {
    errEl.textContent = err.message || 'Something went wrong.';
  } finally {
    if (document.getElementById('ff-parse-btn')) {
      document.getElementById('ff-parse-btn').disabled = false;
    }
  }
}

async function _startParse(payload) {
  _showScreen('loading');
  const tokenEl = document.getElementById('ff-token-stream');
  const badgeEl = document.getElementById('ff-loading-badge');
  if (tokenEl) tokenEl.textContent = '';
  if (badgeEl) badgeEl.textContent = '';

  let accumulated = '';
  let errMsg = '';

  try {
    const res = await fetch('/api/formflow/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Parse request failed (' + res.status + ').');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let lineBuf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      lineBuf += decoder.decode(value, { stream: true });
      const lines = lineBuf.split('\n');
      lineBuf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') break;
        try {
          const evt = JSON.parse(raw);
          if (evt.model && badgeEl) {
            badgeEl.textContent = evt.model;
          } else if (evt.error) {
            errMsg = evt.error;
          } else if (evt.delta) {
            accumulated += evt.delta;
            if (tokenEl) {
              tokenEl.textContent = accumulated.length > 400
                ? '…' + accumulated.slice(-400)
                : accumulated;
            }
          }
        } catch {}
      }
    }
  } catch (err) {
    _showScreen('input');
    const errEl = document.getElementById('ff-input-error');
    if (errEl) errEl.textContent = err.message || 'Connection error during parse.';
    return;
  }

  if (errMsg) {
    _showScreen('input');
    const errEl = document.getElementById('ff-input-error');
    if (errEl) errEl.textContent = errMsg;
    return;
  }

  let questions;
  try {
    const cleaned = accumulated.trim().replace(/^```json\s*/i, '').replace(/```\s*$/, '');
    questions = JSON.parse(cleaned);
    if (!Array.isArray(questions) || !questions.length) throw new Error('empty');
  } catch {
    _showScreen('input');
    const errEl = document.getElementById('ff-input-error');
    if (errEl) errEl.textContent = 'Could not parse the model\'s response. Try again or switch models.';
    return;
  }

  _questions = questions.map((q, i) => ({
    id:          q.id || ('q' + (i + 1)),
    type:        q.type || 'textarea',
    label:       q.label || ('Question ' + (i + 1)),
    required:    q.required !== false,
    options:     Array.isArray(q.options) ? q.options : [],
    scaleMin:    q.scaleMin != null ? Number(q.scaleMin) : 1,
    scaleMax:    q.scaleMax != null ? Number(q.scaleMax) : 5,
    wordLimit:   q.wordLimit  || null,
    charLimit:   q.charLimit  || null,
    placeholder: q.placeholder || '',
  }));
  _answers    = {};
  _currentIdx = 0;

  _renderQuestion(0);
  _showScreen('form');
}

// ─── Form rendering ────────────────────────────────────────────────────────
function _renderQuestion(index) {
  _currentIdx = index;
  const q = _questions[index];
  const n = _questions.length;

  document.getElementById('ff-q-number').textContent = 'Question ' + (index + 1) + ' of ' + n;
  document.getElementById('ff-q-label').innerHTML = _esc(q.label) + (q.required ? '<span class="ff-q-required">*</span>' : '');
  document.getElementById('ff-limit-counter').textContent = '';
  document.getElementById('ff-limit-counter').className = 'ff-limit-counter';
  document.getElementById('ff-q-input-wrap').innerHTML = '';

  const existing = _answers[q.id];
  const wrap = document.getElementById('ff-q-input-wrap');

  switch (q.type) {
    case 'text': case 'email': case 'number': wrap.appendChild(_buildText(q, existing)); break;
    case 'textarea':   wrap.appendChild(_buildTextarea(q, existing)); break;
    case 'choice':     wrap.appendChild(_buildChoice(q, existing)); break;
    case 'multi':      wrap.appendChild(_buildMulti(q, existing)); break;
    case 'yesno':      wrap.appendChild(_buildYesNo(q, existing)); break;
    case 'scale':      wrap.appendChild(_buildScale(q, existing)); break;
    default:           wrap.appendChild(_buildTextarea(q, existing));
  }

  _updateNav(q);
  _updateProgress();
  const first = wrap.querySelector('input, textarea');
  if (first) setTimeout(() => first.focus(), 40);
}

function _buildText(q, existing) {
  const inp = document.createElement('input');
  inp.type = q.type === 'email' ? 'email' : q.type === 'number' ? 'number' : 'text';
  inp.className = 'ff-input';
  inp.placeholder = q.placeholder || '';
  inp.value = existing != null ? String(existing) : '';
  inp.addEventListener('input', () => { _answers[q.id] = inp.value; _updateLimit(q, inp.value); _updateNav(q); });
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); _tryAdvance(); } });
  _updateLimit(q, inp.value);
  return inp;
}

function _buildTextarea(q, existing) {
  const ta = document.createElement('textarea');
  ta.className = 'ff-input';
  ta.placeholder = q.placeholder || '';
  ta.value = existing != null ? String(existing) : '';
  ta.addEventListener('input', () => { _answers[q.id] = ta.value; _updateLimit(q, ta.value); _updateNav(q); });
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _tryAdvance(); } });
  _updateLimit(q, ta.value);
  return ta;
}

function _buildChoice(q, existing) {
  const wrap = document.createElement('div');
  wrap.className = 'ff-choice-list';
  q.options.forEach(opt => {
    const item = document.createElement('div');
    item.className = 'ff-choice-opt' + (existing === opt ? ' selected' : '');
    item.innerHTML = '<div class="ff-choice-dot"></div>' + _esc(opt);
    item.addEventListener('click', () => {
      wrap.querySelectorAll('.ff-choice-opt').forEach(el => el.classList.remove('selected'));
      item.classList.add('selected');
      _answers[q.id] = opt;
      _updateNav(q);
    });
    wrap.appendChild(item);
  });
  return wrap;
}

function _buildMulti(q, existing) {
  const selected = Array.isArray(existing) ? existing.slice() : [];
  const wrap = document.createElement('div');
  wrap.className = 'ff-choice-list';
  q.options.forEach(opt => {
    const item = document.createElement('div');
    item.className = 'ff-choice-opt' + (selected.includes(opt) ? ' selected' : '');
    item.innerHTML = '<div class="ff-check-box">' + (selected.includes(opt) ? '✓' : '') + '</div>' + _esc(opt);
    item.addEventListener('click', () => {
      const box = item.querySelector('.ff-check-box');
      const idx = selected.indexOf(opt);
      if (idx >= 0) { selected.splice(idx, 1); item.classList.remove('selected'); box.textContent = ''; }
      else          { selected.push(opt);       item.classList.add('selected');    box.textContent = '✓'; }
      _answers[q.id] = selected.slice();
      _updateNav(q);
    });
    wrap.appendChild(item);
  });
  return wrap;
}

function _buildYesNo(q, existing) {
  const wrap = document.createElement('div');
  wrap.className = 'ff-yesno';
  ['Yes', 'No'].forEach(label => {
    const btn = document.createElement('button');
    btn.className = 'ff-yesno-btn' + (existing === label ? ' selected' : '');
    btn.textContent = label;
    btn.addEventListener('click', () => {
      wrap.querySelectorAll('.ff-yesno-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      _answers[q.id] = label;
      _updateNav(q);
    });
    wrap.appendChild(btn);
  });
  return wrap;
}

function _buildScale(q, existing) {
  const wrap = document.createElement('div');
  wrap.className = 'ff-scale';
  for (let n = q.scaleMin; n <= q.scaleMax; n++) {
    const val = n;
    const btn = document.createElement('button');
    btn.className = 'ff-scale-btn' + (existing === val ? ' selected' : '');
    btn.textContent = String(val);
    btn.addEventListener('click', () => {
      wrap.querySelectorAll('.ff-scale-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      _answers[q.id] = val;
      _updateNav(q);
    });
    wrap.appendChild(btn);
  }
  return wrap;
}

function _updateLimit(q, value) {
  const el = document.getElementById('ff-limit-counter');
  if (!el) return;
  if (!q.wordLimit && !q.charLimit) { el.textContent = ''; return; }
  const text = value || '';
  if (q.wordLimit) {
    const words = text.trim() === '' ? 0 : text.trim().split(/\s+/).length;
    const pct = words / q.wordLimit;
    el.textContent = words + ' / ' + q.wordLimit + ' words';
    el.className = 'ff-limit-counter' + (pct >= 1 ? ' over' : pct >= 0.85 ? ' warn' : '');
  } else {
    const pct = text.length / q.charLimit;
    el.textContent = text.length + ' / ' + q.charLimit + ' chars';
    el.className = 'ff-limit-counter' + (pct >= 1 ? ' over' : pct >= 0.85 ? ' warn' : '');
  }
}

function _isOverLimit(q) {
  const val = _answers[q.id];
  const text = typeof val === 'string' ? val : '';
  if (q.wordLimit) return text.trim() !== '' && text.trim().split(/\s+/).length > q.wordLimit;
  if (q.charLimit) return text.length > q.charLimit;
  return false;
}

function _hasAnswer(q) {
  const a = _answers[q.id];
  if (a == null) return false;
  if (typeof a === 'string') return a.trim() !== '';
  if (Array.isArray(a)) return a.length > 0;
  return true;
}

function _updateNav(q) {
  const next = document.getElementById('ff-nav-next');
  const back = document.getElementById('ff-nav-back');
  if (!next || !back) return;
  next.disabled = _isOverLimit(q) || (q.required && !_hasAnswer(q));
  back.classList.toggle('invisible', _currentIdx === 0);
  next.textContent = _currentIdx === _questions.length - 1 ? 'Review →' : 'Next →';
}

function _tryAdvance() {
  const next = document.getElementById('ff-nav-next');
  if (next && !next.disabled) _advanceForm();
}

function _advanceForm() {
  if (_currentIdx === _questions.length - 1) {
    _renderReview();
    _showScreen('review');
  } else {
    _renderQuestion(_currentIdx + 1);
  }
}

// ─── Review ────────────────────────────────────────────────────────────────
function _answerDisplay(q) {
  const a = _answers[q.id];
  if (a == null || a === '' || (Array.isArray(a) && !a.length)) return null;
  return Array.isArray(a) ? a.join(', ') : String(a);
}

function _renderReview() {
  const total    = _questions.length;
  const answered = _questions.filter(q => _hasAnswer(q)).length;
  const subEl = document.getElementById('ff-review-sub');
  if (subEl) subEl.textContent = answered + ' of ' + total + ' question' + (total !== 1 ? 's' : '') + ' answered';

  const list = document.getElementById('ff-review-list');
  if (!list) return;
  list.innerHTML = '';
  _questions.forEach((q, i) => {
    const display = _answerDisplay(q);
    const item = document.createElement('div');
    item.className = 'ff-review-item';
    item.innerHTML = `
      <div class="ff-review-q-num">Q${i + 1}</div>
      <div class="ff-review-q-label">${_esc(q.label)}</div>
      <div class="ff-review-answer${display ? '' : ' empty'}">${_esc(display || '(not answered)')}</div>
    `;
    list.appendChild(item);
  });
  _updateProgress();
}

function _buildExport() {
  return _questions.map((q, i) =>
    'Q' + (i + 1) + '. ' + q.label + '\n' + (_answerDisplay(q) || '(not answered)')
  ).join('\n\n');
}

function _copyAnswers() {
  const text = _buildExport();
  const btn  = document.getElementById('ff-copy-btn');
  navigator.clipboard.writeText(text).then(() => {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'Copied!'; btn.disabled = true;
    setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1800);
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy');
    document.body.removeChild(ta);
  });
}

function _downloadAnswers() {
  const blob = new Blob([_buildExport()], { type: 'text/plain' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = 'formflow-answers.txt'; a.click();
  URL.revokeObjectURL(url);
}

function _restart() {
  _questions = []; _answers = {}; _currentIdx = 0; _pendingFile = null;
  const paste = document.getElementById('ff-paste');
  const fileIn = document.getElementById('ff-file-input');
  const fileChosen = document.getElementById('ff-file-chosen');
  const dropZ = document.getElementById('ff-drop-zone');
  if (paste) paste.value = '';
  if (fileIn) fileIn.value = '';
  if (fileChosen) fileChosen.classList.remove('visible');
  if (dropZ) dropZ.style.display = '';
  const errEl = document.getElementById('ff-input-error');
  if (errEl) errEl.textContent = '';
  _showScreen('input');
}

// ─── Utility ───────────────────────────────────────────────────────────────
function _esc(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const formflowModule = { openPanel, closePanel, togglePanel };
export default formflowModule;
window.formflowModule = formflowModule;
