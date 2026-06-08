// Odysseus UI — Initialization Scripts
// ES6 module — extracted from index.html inline scripts

import Storage from './storage.js';

function clearFreshComposerRestore() {
  const msgInput = document.getElementById('message');
  if (!msgInput) return;
  const hasSessionTarget = !!(window.location.hash || Storage.get('lastSessionId'));
  if (hasSessionTarget) return;
  if (msgInput.value) {
    msgInput.value = '';
    msgInput.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

clearFreshComposerRestore();
window.addEventListener('pageshow', clearFreshComposerRestore);

// SECURITY: defense-in-depth state wipe on user switch. If the authenticated
// user is different from the one whose state is cached in this browser,
// wipe localStorage + sessionStorage so the new account doesn't inherit
// the previous user's last session id, last-used model, draft chat input,
// or cached lists. The settings-tab Logout button already wipes on
// explicit logout; this catches the cases where a different user signs
// in without the previous one logging out cleanly.
(async () => {
  try {
    const res = await fetch('/api/auth/status', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json().catch(() => ({}));
    const liveUser = (data && data.username) || '';
    if (!liveUser) return;
    const KEY = 'odysseus-auth-user';
    const cachedUser = localStorage.getItem(KEY);
    if (cachedUser && cachedUser !== liveUser) {
      const _keepKeys = new Set(['odysseus-last-user', KEY]);
      const toRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && !_keepKeys.has(k)) toRemove.push(k);
      }
      toRemove.forEach(k => localStorage.removeItem(k));
      sessionStorage.clear();
      clearFreshComposerRestore();
    }
    localStorage.setItem(KEY, liveUser);
    // Apply per-user privilege gates to the UI. The backend enforces these
    // independently — this is purely cosmetic / "don't dangle controls the
    // user can't actually use." Privileges come from /api/auth/status; admins
    // always get the full set so this is a no-op for them.
    try {
      const privs = (data && data.privileges) || {};
      const hideOn = (selector, allowed) => {
        if (allowed === undefined || allowed === true) return;
        document.querySelectorAll(selector).forEach(el => {
          el.style.display = 'none';
        });
      };
      // Document editor — overflow menu button + the docs panel rail/tool button.
      hideOn('#overflow-doc-btn, #tool-doc-btn', privs.can_use_documents);
      // Research — sidebar tool + the in-input deep-research toggle.
      hideOn('#tool-research-btn, #research-toggle-btn', privs.can_use_research);
      // Memory & skills (rail/tool button only — UI/API entry).
      hideOn('#tool-memory-btn', privs.can_manage_memory);
      // Agent mode toggle — force chat mode by hiding the Agent toggle button.
      if (privs.can_use_agent === false) {
        const _agent = document.getElementById('mode-agent-btn');
        const _chat = document.getElementById('mode-chat-btn');
        if (_agent) _agent.style.display = 'none';
        if (_chat) { _chat.classList.add('active'); _chat.click?.(); }
      }
    } catch (_) { /* DOM not ready or unexpected shape — UI gates are non-fatal */ }
  } catch (_) { /* anonymous / loopback mode — nothing to do */ }
})();

/* Sidebar section default-collapsed setup. The click-to-toggle handlers
   themselves live in js/section-management.js — attaching them in BOTH
   places caused two toggles per click, which read as "clicks aren't doing
   anything" (even-count parity). Keep only the initial-state-apply here. */
{
  const KEY = Storage.KEYS.SIDEBAR_COLLAPSED;
  const saved = Storage.getJSON(KEY, {});
  const _defaultCollapsed = { 'sessions-section': true };
  document.querySelectorAll('.sidebar .section').forEach((section) => {
    const id = section.id;
    if (!id) return;
    const shouldCollapse = (id in saved) ? saved[id] : !!_defaultCollapsed[id];
    if (shouldCollapse) section.classList.add('collapsed');
  });
  // Sessions-section notification dot: clear when the section becomes
  // expanded. Watch the class with MutationObserver so we don't need a
  // click handler (which would race the section-management one).
  const sessionsSection = document.getElementById('sessions-section');
  if (sessionsSection) {
    new MutationObserver(() => {
      if (!sessionsSection.classList.contains('collapsed')) {
        const dot = document.getElementById('chats-notif-dot');
        if (dot) dot.style.display = 'none';
      }
    }).observe(sessionsSection, { attributes: true, attributeFilter: ['class'] });
  }
}

/* Publish the icon rail's + wide sidebar's current widths as CSS vars so
   fullscreen panels can reserve space on the left for whichever is
   currently visible (the two are mutually exclusive — see
   sidebar-layout.js:57). Updates live as either resizes; toggles to 0
   when hidden so the fullscreen view reclaims the space. */
{
  const rail = document.getElementById('icon-rail');
  const sidebar = document.getElementById('sidebar');
  const root = document.documentElement;
  const _measure = (el) => {
    if (!el) return null;
    const cs = window.getComputedStyle(el);
    const hidden = cs.display === 'none' || cs.visibility === 'hidden';
    if (hidden) return 0;
    return Math.round(el.getBoundingClientRect().width);
  };
  const _sync = () => {
    // Icon rail width
    const rw = _measure(rail);
    if (rw === null) {
      root.style.removeProperty('--icon-rail-w');
    } else if (rw > 0) {
      root.style.setProperty('--icon-rail-w', rw + 'px');
    } else {
      // 0 from a visible-but-not-yet-laid-out rail: don't shadow the
      // CSS fallback; re-sync on the next frame instead.
      const cs = rail && window.getComputedStyle(rail);
      const hidden = !cs || cs.display === 'none' || cs.visibility === 'hidden';
      if (hidden) {
        root.style.setProperty('--icon-rail-w', '0px');
      } else {
        root.style.removeProperty('--icon-rail-w');
        requestAnimationFrame(_sync);
        return;
      }
    }
    // Sidebar width — `.sidebar.hidden` collapses to width: 0 so the
    // measurement is naturally 0 in the hidden state.
    const sw = _measure(sidebar);
    if (sw === null) {
      root.style.removeProperty('--sidebar-w');
    } else {
      root.style.setProperty('--sidebar-w', sw + 'px');
    }
  };
  _sync();
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(_sync);
    if (rail) ro.observe(rail);
    if (sidebar) ro.observe(sidebar);
  }
  // Class flips (sidebar.hidden ↔ visible) don't trigger ResizeObserver
  // until layout settles a frame later; also watch the class attribute
  // so we re-sync immediately when the user toggles the hamburger.
  if (sidebar && typeof MutationObserver !== 'undefined') {
    new MutationObserver(_sync).observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
  }
  if (rail && typeof MutationObserver !== 'undefined') {
    new MutationObserver(_sync).observe(rail, { attributes: true, attributeFilter: ['class', 'style'] });
  }
  window.addEventListener('resize', _sync);
}

/* Keep minimized tool chips above the composer. Both the current modalManager
   dock and the legacy fallback dock consume this root-level clearance. */
{
  const root = document.documentElement;
  const chatBar = document.querySelector('.chat-input-bar');
  const attachStrip = document.getElementById('attach-strip');
  const chatContainer = document.getElementById('chat-container');
  const _syncComposerClearance = () => {
    let top = window.innerHeight;
    for (const el of [attachStrip, chatBar]) {
      if (!el) continue;
      const rect = el.getBoundingClientRect();
      if (rect.height > 0) top = Math.min(top, rect.top);
    }
    const clearance = Math.max(12, Math.ceil(window.innerHeight - top + 8));
    root.style.setProperty('--composer-clearance', clearance + 'px');
  };
  requestAnimationFrame(_syncComposerClearance);
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(_syncComposerClearance);
    if (chatBar) ro.observe(chatBar);
    if (attachStrip) ro.observe(attachStrip);
  }
  if (chatContainer && typeof MutationObserver !== 'undefined') {
    new MutationObserver(_syncComposerClearance).observe(chatContainer, {
      attributes: true,
      attributeFilter: ['class'],
    });
  }
  if (chatBar) chatBar.addEventListener('transitionend', _syncComposerClearance);
  window.addEventListener('resize', _syncComposerClearance);
}

/* ---- Resizable sidebar — drag edge to resize, collapse if small, drag rail edge to expand ---- */
{
  const sidebar = document.getElementById('sidebar');
  const handle = document.getElementById('sidebar-resize-handle');
  const railHandle = document.getElementById('rail-resize-handle');
  const iconRail = document.getElementById('icon-rail');
  if (sidebar && handle) {

  const STORAGE_KEY = Storage.KEYS.SIDEBAR_WIDTH;
  const MIN_WIDTH = 200;
  const MAX_WIDTH = 700;
  const COLLAPSE_THRESHOLD = 150;

  function getSavedWidth() {
    const w = parseInt(Storage.get(STORAGE_KEY, '340'), 10);
    return (w >= MIN_WIDTH && w <= MAX_WIDTH) ? w : 340;
  }

  // Restore saved width
  const savedWidth = Storage.get(STORAGE_KEY);
  if (savedWidth) {
    const w = parseInt(savedWidth, 10);
    if (w >= MIN_WIDTH && w <= MAX_WIDTH) sidebar.style.width = w + 'px';
  }

  let startX, startWidth, isRight, collapsed, expanding;

  // --- Drag from sidebar edge to resize / collapse ---
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    expanding = false;
    isRight = sidebar.classList.contains('right-side');
    startX = e.clientX;
    startWidth = sidebar.getBoundingClientRect().width;
    collapsed = false;
    sidebar.classList.add('resizing');
    handle.classList.add('dragging');
    document.addEventListener('mousemove', onDrag);
    document.addEventListener('mouseup', stopDrag);
  });

  // --- Drag from icon rail edge to expand sidebar ---
  if (railHandle) {
    railHandle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      expanding = true;
      isRight = sidebar.classList.contains('right-side') ||
                iconRail.classList.contains('right-side');
      startX = e.clientX;
      collapsed = false;

      // Unhide sidebar at 0 width so we can grow it
      sidebar.classList.remove('hidden');
      sidebar.classList.add('resizing');
      sidebar.style.width = '0px';
      sidebar.style.opacity = '0.3';
      railHandle.classList.add('dragging');

      document.addEventListener('mousemove', onExpandDrag);
      document.addEventListener('mouseup', stopExpandDrag);
    });
  }

  function onDrag(e) {
    const delta = isRight ? (startX - e.clientX) : (e.clientX - startX);
    const rawWidth = startWidth + delta;

    if (rawWidth < COLLAPSE_THRESHOLD) {
      sidebar.style.width = Math.max(0, rawWidth) + 'px';
      sidebar.style.opacity = Math.max(0.2, rawWidth / COLLAPSE_THRESHOLD);
      collapsed = true;
    } else {
      const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, rawWidth));
      sidebar.style.width = newWidth + 'px';
      sidebar.style.opacity = '';
      collapsed = false;
    }
  }

  function stopDrag() {
    sidebar.classList.remove('resizing');
    handle.classList.remove('dragging');
    sidebar.style.opacity = '';
    document.removeEventListener('mousemove', onDrag);
    document.removeEventListener('mouseup', stopDrag);

    if (collapsed) {
      sidebar.style.width = '';
      sidebar.classList.add('hidden');
      if (typeof syncRailSide === 'function') syncRailSide();
    } else {
      const finalWidth = parseInt(sidebar.style.width, 10);
      if (finalWidth >= MIN_WIDTH) {
        Storage.set(STORAGE_KEY, String(finalWidth));
      }
    }
  }

  function onExpandDrag(e) {
    const delta = isRight ? (startX - e.clientX) : (e.clientX - startX);
    const rawWidth = Math.max(0, delta);

    if (rawWidth < COLLAPSE_THRESHOLD) {
      sidebar.style.width = rawWidth + 'px';
      sidebar.style.opacity = Math.max(0.3, rawWidth / COLLAPSE_THRESHOLD);
      collapsed = true;
    } else {
      const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, rawWidth));
      sidebar.style.width = newWidth + 'px';
      sidebar.style.opacity = '';
      collapsed = false;
    }
  }

  function stopExpandDrag() {
    sidebar.classList.remove('resizing');
    sidebar.style.opacity = '';
    if (railHandle) railHandle.classList.remove('dragging');
    document.removeEventListener('mousemove', onExpandDrag);
    document.removeEventListener('mouseup', stopExpandDrag);

    if (collapsed) {
      // Didn't drag far enough — snap back to icon rail
      sidebar.style.width = '';
      sidebar.classList.add('hidden');
      if (typeof syncRailSide === 'function') syncRailSide();
    } else {
      // Expanded — save width and sync
      const finalWidth = parseInt(sidebar.style.width, 10);
      if (finalWidth >= MIN_WIDTH) {
        Storage.set(STORAGE_KEY, String(finalWidth));
      }
      if (typeof syncRailSide === 'function') syncRailSide();
    }
  }

  } // end if (sidebar && handle)
}

/* ---- Mobile viewport fix — keep chat visible when virtual keyboard opens ---- */
{
  if (window.visualViewport) {
    let _lastVVHeight = window.visualViewport.height;
    window.visualViewport.addEventListener('resize', function() {
      const vv = window.visualViewport;
      const keyboardOpened = vv.height < _lastVVHeight - 50;
      _lastVVHeight = vv.height;
      if (keyboardOpened) {
        var chatHistory = document.getElementById('chat-history');
        if (chatHistory) {
          requestAnimationFrame(function() {
            chatHistory.scrollTop = chatHistory.scrollHeight;
          });
        }
      }
    });
  }

  // Fade welcome screen when mobile keyboard opens (input focus/blur)
  if ('ontouchstart' in window) {
    document.addEventListener('DOMContentLoaded', function() {
      var _msgInput = document.getElementById('message');
      if (!_msgInput) return;
      _msgInput.addEventListener('focus', function() {
        var welcome = document.getElementById('welcome-screen');
        if (welcome && !welcome.classList.contains('hidden')) {
          welcome.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
          welcome.style.opacity = '0';
          welcome.style.transform = 'translate(-50%, -50%) scale(0.92)';
        }
      });
      _msgInput.addEventListener('blur', function() {
        var welcome = document.getElementById('welcome-screen');
        if (welcome && !welcome.classList.contains('hidden')) {
          welcome.style.transition = 'opacity 0.3s ease, transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)';
          welcome.style.opacity = '';
          welcome.style.transform = '';
        }
      });
    });
  }
}

/* ── Release welcome-screen entrance animations once the page is settled ──
   The splash's entrance animations (#welcome-screen / .welcome-name) are held
   by CSS (`body:not(.welcome-ready)`) until this runs, so they no longer play
   while fonts are loading and the layout is still shifting on first paint
   (which made the splash "go haywire"). We flip the flag after fonts are ready
   plus a couple of frames, with load + timeout fallbacks so the splash is never
   left hidden. Lives here (a network-first module) rather than inline in
   index.html so it updates in lockstep with the gating CSS. */
(function () {
  let fired = false;
  function release() {
    if (fired) return;
    fired = true;
    requestAnimationFrame(() =>
      requestAnimationFrame(() => document.body.classList.add('welcome-ready'))
    );
  }
  try { if (document.fonts && document.fonts.ready) document.fonts.ready.then(release); } catch (_) {}
  if (document.readyState === 'complete') release();
  else window.addEventListener('load', release);
  setTimeout(release, 1200);  // hard fallback — never leave the splash hidden
})();
