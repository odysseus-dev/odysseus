// tourHints.js — secret continuation of /tour. The first time the user opens
// a tool modal (after the welcome experience), surface a single "pro tip"
// hint pointing out that modals can be snapped to the screen edge or
// fullscreened by dragging the title bar. Shown once globally — once the
// user has dismissed it (or it auto-hides), it never returns.

const HINT_SEEN_KEY = 'odysseus-hint-drag-to-snap-seen';

// Allow-list of modals where the snap/fullscreen hint makes sense.
// These are the full-window "tool" modals where users commonly want to
// reposition or fullscreen the pane (email, calendar, cookbook, gallery,
// library, brain memories, tasks, theme, compare). Transient modals
// like settings, prompts, rename dialogs, custom-preset picker, etc.
// are excluded — opening those is task-focused and the snap tip would
// be noise.
const SHOW_MODALS = new Set([
  'email-lib-modal',
  'calendar-modal',
  'compare-modal',     // not currently a real id, defensive
  'cookbook-modal',
  'gallery-modal',
  'doclib-modal',
  'library-modal',     // chat-history library (sessions.js)
  'memory-modal',      // brain / memories
  'tasks-modal',
  'theme-modal',
]);

// Some modals have dynamic per-instance IDs (e.g. one window per opened
// email). Match by prefix so any window from the same family qualifies.
const SHOW_MODAL_PREFIXES = ['email-window-'];

function _modalShouldShowHint(id) {
  if (!id) return false;
  if (SHOW_MODALS.has(id)) return true;
  return SHOW_MODAL_PREFIXES.some(p => id.startsWith(p));
}

let _shown = false;
let _initialized = false;

function _hasSeen() { return localStorage.getItem(HINT_SEEN_KEY) === '1'; }
function _markSeen() { try { localStorage.setItem(HINT_SEEN_KEY, '1'); } catch {} }

function _isVisible(el) {
  if (!el || el.classList.contains('hidden')) return false;
  // Some modals set inline display:none rather than .hidden
  if (el.style.display === 'none') return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function _onModalOpened(modal) {
  if (_shown || _hasSeen()) return;
  const id = modal.id;
  if (!_modalShouldShowHint(id)) return;
  // Don't interrupt the welcome / tour itself
  if (document.body.classList.contains('tour-active')) return;
  if (document.getElementById('tour-tooltip')) return;
  // Mobile: skip — snapping isn't a desktop-only feature there
  if (window.innerWidth <= 768) return;

  _shown = true;
  // Give the modal a moment to settle (some open with their own animation).
  setTimeout(() => _show(modal), 380);
}

function _show(modal) {
  if (_hasSeen()) return;
  const content = modal.querySelector('.modal-content') || modal;
  const r = content.getBoundingClientRect();

  const pop = document.createElement('div');
  pop.className = 'tour-hint';
  pop.innerHTML = `
    <div class="tour-hint-visual" aria-hidden="true">
      <svg viewBox="0 0 100 60" width="160" height="96">
        <!-- ambient frame -->
        <rect x="0.5" y="0.5" width="99" height="59" rx="3" fill="none" stroke="currentColor" stroke-opacity="0.18" />
        <!-- snap-zone preview (right half) -->
        <rect class="th-zone" x="51" y="2" width="47" height="56" rx="2" fill="currentColor" opacity="0" />
        <!-- the modal being dragged -->
        <g class="th-modal-group">
          <rect x="22" y="20" width="34" height="22" rx="2.5" fill="var(--bg)" stroke="currentColor" stroke-width="1.2" />
          <rect x="22" y="20" width="34" height="5"  rx="2.5" fill="currentColor" opacity="0.35" />
        </g>
        <!-- cursor -->
        <path class="th-cursor" d="M0 0 L0 9 L2.5 7 L4.5 10 L6 9 L4 6 L7 6 Z" fill="currentColor" />
      </svg>
    </div>
    <div class="tour-hint-text"><b>Pro tip:</b> drag any window's title bar to a screen edge to snap it. Drag to the top for fullscreen.</div>
    <button class="tour-hint-dismiss" type="button">Got it</button>
  `;
  document.body.appendChild(pop);

  // Prefer placing to the right of the modal; fall back to left, then below.
  pop.style.opacity = '0';
  requestAnimationFrame(() => {
    const pw = pop.offsetWidth || 260;
    const ph = pop.offsetHeight || 200;
    let left = r.right + 14;
    let top  = r.top;
    if (left + pw > window.innerWidth - 8) {
      left = r.left - pw - 14;
      if (left < 8) {
        left = Math.max(8, r.left + (r.width - pw) / 2);
        top  = r.bottom + 14;
        if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 14);
      }
    }
    pop.style.left = left + 'px';
    pop.style.top  = top  + 'px';
    pop.style.opacity = '';
    pop.classList.add('tour-hint-in');
  });

  const _escHandler = (e) => {
    if (e.key === 'Escape' && pop.isConnected) {
      e.preventDefault();
      e.stopImmediatePropagation();
      _dismiss();
    }
  };
  document.addEventListener('keydown', _escHandler, true);

  const _dismiss = () => {
    document.removeEventListener('keydown', _escHandler, true);
    pop.classList.add('tour-hint-out');
    setTimeout(() => pop.remove(), 280);
    _markSeen();
  };
  pop.querySelector('.tour-hint-dismiss').addEventListener('click', _dismiss);
  // Auto-dismiss after 14s so it doesn't linger forever.
  setTimeout(() => { if (pop.isConnected) _dismiss(); }, 14000);
}

function _watchModals() {
  const observeModal = (modal) => {
    if (!modal || modal.dataset.tourHintObserved === '1') return;
    modal.dataset.tourHintObserved = '1';
    observer.observe(modal, {
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ['class', 'style'],
    });
    if (_isVisible(modal)) _onModalOpened(modal);
  };
  const observer = new MutationObserver((muts) => {
    if (_hasSeen() || _shown) return;
    for (const m of muts) {
      if (m.attributeName !== 'class' && m.attributeName !== 'style') continue;
      const el = m.target;
      if (!(el instanceof HTMLElement)) continue;
      if (!el.classList.contains('modal')) continue;
      const wasHidden = !m.oldValue || /\bhidden\b/.test(m.oldValue) || /display:\s*none/.test(m.oldValue);
      if (wasHidden && _isVisible(el)) _onModalOpened(el);
    }
  });
  document.querySelectorAll('.modal').forEach(observeModal);
  const addObserver = new MutationObserver((muts) => {
    if (_hasSeen() || _shown) return;
    for (const m of muts) {
      m.addedNodes.forEach(node => {
        if (!(node instanceof HTMLElement)) return;
        if (node.classList.contains('modal')) observeModal(node);
        node.querySelectorAll?.('.modal').forEach(observeModal);
      });
    }
  });
  addObserver.observe(document.body, { childList: true, subtree: true });
}

export function init() {
  if (_initialized) return;
  _initialized = true;
  if (_hasSeen()) return; // nothing to do
  // Defer one tick so the rest of the app has a chance to mount its modals.
  setTimeout(_watchModals, 50);
}

if (typeof window !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}

export default { init };
