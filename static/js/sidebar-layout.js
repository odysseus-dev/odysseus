// ============================================
// Sidebar Layout — icon rail, hamburger cycling, mobile backdrop & swipe
// ============================================

let _syncRailSideFn = null;

/**
 * Get the current syncRailSide function reference.
 * Needed because it gets patched after initial setup.
 */
export function syncRailSide() {
  if (_syncRailSideFn) _syncRailSideFn();
}

/**
 * Initialize sidebar layout: icon rail, hamburger cycling, mobile backdrop, swipe gestures.
 * @param {Object} Storage - Storage module
 * @param {Object} opts
 * @param {Object} opts.documentModule - Document module (for swapSide)
 * @param {Function} opts._closeCompareIfActive
 * @param {Function} opts._deactivateIncognito
 * @param {Object} opts.presetsModule
 * @param {Object} opts.sessionModule
 * @param {Function} opts.el - Element lookup helper
 * @param {*} opts._defaultChat - Default chat config
 * @param {Function} opts._syncResearchIndicator
 */
export function initSidebarLayout(Storage, opts) {
  const {
    documentModule, _closeCompareIfActive, _deactivateIncognito,
    presetsModule, sessionModule, el, _defaultChat, _syncResearchIndicator
  } = opts;

  // ── Icon rail + sidebar toggle ──
  const iconRail = document.getElementById('icon-rail');
  const hamburgerBtn = document.getElementById('hamburger-btn');

  function _syncRailSideCore() {
    const sidebar = document.getElementById('sidebar');
    if (!iconRail) return;
    const isRight = sidebar.classList.contains('right-side');
    const sidebarHidden = sidebar.classList.contains('hidden');
    const railHidden = iconRail.classList.contains('rail-hidden');
    const isMobileMini = iconRail.classList.contains('mobile-mini');
    iconRail.classList.toggle('right-side', isRight);
    // On mobile mini mode, JS already set inline styles — don't touch
    if (isMobileMini) {
      // Just update side positioning
      if (isRight) {
        iconRail.style.left = 'auto';
        iconRail.style.right = '0';
      } else {
        iconRail.style.left = '0';
        iconRail.style.right = 'auto';
      }
    } else {
      iconRail.style.display = (sidebarHidden && !railHidden) ? '' : 'none';
    }
    // Hamburger is always visible — just update body classes for CSS layout adjustments
    if (hamburgerBtn) {
      document.body.classList.toggle('hamburger-right', isRight);
      document.body.classList.toggle('hamburger-left', !isRight);
      document.body.classList.toggle('hamburger-only', sidebarHidden && railHidden);
      document.body.classList.toggle('sidebar-collapsed', sidebarHidden);
    }
    // Keep incognito button clear of hamburger
    const incogBtn = document.getElementById('incognito-btn');
    if (incogBtn) {
      if (isRight && sidebarHidden) {
        incogBtn.style.right = '48px';
      } else {
        incogBtn.style.right = '';
      }
    }
  }

  // Set initial reference and expose globally
  _syncRailSideFn = _syncRailSideCore;
  window.syncRailSide = syncRailSide;

  // Restore sidebar side preference
  if (Storage.get(Storage.KEYS.SIDEBAR_SIDE) === 'right') {
    document.getElementById('sidebar').classList.add('right-side');
  }
  syncRailSide();

  // In-sidebar toggle button — same behavior as hamburger
  const sidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
  if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener('click', (e) => {
      if (hamburgerBtn) hamburgerBtn.click();
    });
  }

  // New chat buttons — same as clicking brand
  const chatNewBtn = document.getElementById('chat-new-btn');
  const sidebarNewChat = document.getElementById('sidebar-new-chat-btn');
  [chatNewBtn, sidebarNewChat].forEach(btn => {
    if (btn) btn.addEventListener('click', () => {
      const brandBtn = document.getElementById('sidebar-brand-btn');
      if (brandBtn) brandBtn.click();
    });
  });

  // Hamburger cycles: full sidebar → mini → off → full
  let _userToggledSidebar = false;
  let _wasAutoCollapsed = false;

  // Deliberate "open the sidebar" used by the mobile swipe gesture (wired at
  // module scope). It MUST set _userToggledSidebar so the auto-collapse
  // MutationObserver doesn't immediately re-hide it (the swipe was opening it,
  // then checkSidebarAutoCollapse re-added .hidden because this flag was unset
  // — looked like nothing happened). Mirrors the hamburger's mobile-open path.
  window._odyOpenSidebar = function(side) {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    // On mobile, never open the sidebar while Compare is running — the panes
    // own the screen and stray gestures (swipe, dragging a dock chip to the X)
    // were popping it open. Blocking the open helper covers every path.
    const cc = document.getElementById('chat-container');
    if (window.innerWidth < 768 && cc && cc.classList.contains('compare-active')) return;
    _userToggledSidebar = true;
    // Optionally place the sidebar on a specific edge (the swipe gesture passes
    // the direction). Persist it + re-anchor the doc panel.
    if (side === 'left' || side === 'right') {
      const wantRight = side === 'right';
      if (sidebar.classList.contains('right-side') !== wantRight) {
        sidebar.classList.toggle('right-side', wantRight);
        try { Storage.set(Storage.KEYS.SIDEBAR_SIDE, side); } catch (_) {}
        if (documentModule && documentModule.swapSide) { try { documentModule.swapSide(); } catch (_) {} }
      }
    }
    const backdrop = document.getElementById('sidebar-backdrop');
    if (window.innerWidth < 768 && iconRail) { iconRail.classList.remove('mobile-mini'); iconRail.style.cssText = ''; }
    sidebar.classList.remove('hidden');
    if (backdrop && window.innerWidth < 768) backdrop.classList.add('visible');
    syncRailSide();
  };

  if (hamburgerBtn) {
    hamburgerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sidebar = document.getElementById('sidebar');

      _userToggledSidebar = true;
      const isSidebarVisible = !sidebar.classList.contains('hidden');

      if (window.innerWidth < 768) {
        // Mobile: full sidebar ↔ hidden — simple toggle, no mini rail
        const backdrop = document.getElementById('sidebar-backdrop');
        if (iconRail) { iconRail.classList.remove('mobile-mini'); iconRail.style.cssText = ''; }

        if (isSidebarVisible) {
          // Closing sidebar
          sidebar.classList.add('hidden');
          if (backdrop) backdrop.classList.remove('visible');
        } else {
          // Mobile: the hamburger always opens the sidebar from the RIGHT.
          // (Not persisted — keeps the desktop side preference untouched.)
          if (!sidebar.classList.contains('right-side')) {
            sidebar.classList.add('right-side');
            if (documentModule && documentModule.swapSide) { try { documentModule.swapSide(); } catch (_) {} }
          }
          // Opening sidebar — blur keyboard first, then open after layout settles
          if (document.activeElement && document.activeElement !== document.body
              && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) {
            document.activeElement.blur();
            // Wait for keyboard dismiss to settle, then open
            setTimeout(() => {
              sidebar.classList.remove('hidden');
              if (backdrop) backdrop.classList.add('visible');
              syncRailSide();
            }, 250);
          } else {
            sidebar.classList.remove('hidden');
            if (backdrop) backdrop.classList.add('visible');
          }
        }
        syncRailSide();
        return;
      }

      // Desktop: full sidebar ↔ mini (icon rail) — simple toggle
      if (isSidebarVisible) {
        sidebar.classList.add('hidden');
      } else {
        _wasAutoCollapsed = false;
        iconRail.classList.remove('rail-hidden');
        sidebar.classList.remove('hidden');
      }
      syncRailSide();
    });
  }

  // Icon rail section clicks — open sidebar and scroll to section
  if (iconRail) {
    iconRail.addEventListener('click', (e) => {
      const btn = e.target.closest('.icon-rail-btn');
      if (!btn || btn.id === 'rail-new-session' || btn.id === 'rail-delete-session' || btn.id === 'rail-search-btn' || btn.id === 'rail-settings' || btn.id === 'rail-admin') return;
      const sectionId = btn.dataset.section;
      if (!sectionId) return;
      const sidebar = document.getElementById('sidebar');
      sidebar.classList.remove('hidden');
      syncRailSide();
      const section = document.getElementById(sectionId);
      if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        section.classList.remove('collapsed');
      }
    });
  }

  // Auto-collapse sidebar when window gets small or chat area is squeezed
  const AUTO_COLLAPSE_WIDTH = 700;
  const MIN_CHAT_WIDTH = 380; // collapse sidebar if chat gets narrower than this

  function checkSidebarAutoCollapse() {
    if (_userToggledSidebar) return;
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    const isHidden = sidebar.classList.contains('hidden');

    // Check if chat area is too narrow (e.g. sidebar + doc panel both open).
    // BUT — if a tile-snapped modal exists, IT is what's making chat narrow,
    // and that's the user's explicit choice. Don't auto-collapse the sidebar
    // in response, or we get a reactive loop: snap → narrow chat → hide
    // sidebar → safe-rect changes → reclamp modal → new chat width → ...
    const chatContainer = document.querySelector('.chat-container');
    const hasTileSnapped = document.querySelector('.modal-content[data-_tile-zone], .research-pane[data-_tile-zone]');
    const chatTooNarrow = chatContainer && chatContainer.offsetWidth < MIN_CHAT_WIDTH && !isHidden && !hasTileSnapped;

    if ((window.innerWidth < AUTO_COLLAPSE_WIDTH || chatTooNarrow) && !isHidden) {
      sidebar.classList.add('hidden');
      _wasAutoCollapsed = true;
      syncRailSide();
    } else if (window.innerWidth >= AUTO_COLLAPSE_WIDTH && isHidden && _wasAutoCollapsed) {
      // Only restore if chat won't be too narrow
      sidebar.classList.remove('hidden');
      void document.body.offsetWidth; // reflow
      if (chatContainer && chatContainer.offsetWidth < MIN_CHAT_WIDTH) {
        sidebar.classList.add('hidden');
      } else {
        _wasAutoCollapsed = false;
      }
      syncRailSide();
    }
  }

  window.addEventListener('resize', () => {
    _userToggledSidebar = false; // allow auto-collapse on actual resize
    requestAnimationFrame(checkSidebarAutoCollapse);
  });
  // Also re-check when doc panel toggles
  new MutationObserver(() => requestAnimationFrame(checkSidebarAutoCollapse))
    .observe(document.body, { attributes: true, attributeFilter: ['class'] });

  // Auto-collapse on initial load if window is small
  if (window.innerWidth < AUTO_COLLAPSE_WIDTH) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar && !sidebar.classList.contains('hidden')) {
      sidebar.classList.add('hidden');
      _wasAutoCollapsed = true;
      syncRailSide();
    }
  }

  // ── Mobile sidebar backdrop + swipe-to-close ──
  // Backdrop overlay: tapping it closes the sidebar
  const mobileBackdrop = document.createElement('div');
  mobileBackdrop.id = 'sidebar-backdrop';
  document.body.appendChild(mobileBackdrop);

  function updateMobileBackdrop() {
    if (window.innerWidth >= 768) { mobileBackdrop.classList.remove('visible'); return; }
    const sb = document.getElementById('sidebar');
    const rail = document.getElementById('icon-rail');
    const sidebarOpen = sb && !sb.classList.contains('hidden');
    const miniOpen = rail && rail.classList.contains('mobile-mini');
    mobileBackdrop.classList.toggle('visible', sidebarOpen || miniOpen);
  }

  // Suppress sidebar close briefly after dropdown actions
  window._suppressSidebarClose = false;
  mobileBackdrop.addEventListener('click', (e) => {
    if (window._suppressSidebarClose) return;
    // Don't close while a session is being renamed inline — the rename input
    // lives inside the sidebar, and a backdrop tap (e.g. to dismiss the
    // keyboard) would otherwise kick the user out mid-rename.
    if (document.querySelector('.session-rename-input')) return;
    // Don't close if a dropdown or submenu is visible
    const openDD = document.querySelector('.session-dropdown-menu[style*="display: block"], .session-dropdown-menu[style*="display:block"]');
    const openSub = document.querySelector('.session-folder-submenu[style*="display: block"], .session-folder-submenu[style*="display:block"]');
    if (openDD || openSub) {
      if (openSub) openSub.style.display = 'none';
      if (openDD) openDD.style.display = 'none';
      return;
    }
    const sb = document.getElementById('sidebar');
    if (sb && !sb.classList.contains('hidden')) {
      sb.classList.add('hidden');
    }
    mobileBackdrop.classList.remove('visible');
    syncRailSide();
  });

  // Patch syncRailSide to also update backdrop
  const _origSyncRailSideCore = _syncRailSideCore;
  _syncRailSideFn = function() { _origSyncRailSideCore(); updateMobileBackdrop(); };
  window.syncRailSide = syncRailSide;

  // Swipe sidebar toward edge to close
  const sidebar = document.getElementById('sidebar');
  if (sidebar && 'ontouchstart' in window) {
    let _swStartX = 0, _swStartY = 0, _swSwiping = false;
    sidebar.addEventListener('touchstart', (e) => {
      if (e.target.closest('.list-item')) { _swSwiping = false; return; }
      _swStartX = e.touches[0].clientX;
      _swStartY = e.touches[0].clientY;
      _swSwiping = true;
    }, { passive: true });
    sidebar.addEventListener('touchmove', (e) => {
      if (!_swSwiping) return;
      const dx = e.touches[0].clientX - _swStartX;
      const dy = Math.abs(e.touches[0].clientY - _swStartY);
      if (dy > 40) { _swSwiping = false; return; }
      const isRight = sidebar.classList.contains('right-side');
      if ((!isRight && dx < -60) || (isRight && dx > 60)) {
        _swSwiping = false;
        const _backdrop = document.getElementById('sidebar-backdrop');
        if (_backdrop) _backdrop.classList.remove('visible');
        sidebar.classList.add('hidden');
        syncRailSide();
      }
    }, { passive: true });
    sidebar.addEventListener('touchend', () => { _swSwiping = false; }, { passive: true });
  }

  // ── Click outside sidebar / icon rail to close (mobile only) ──
  document.addEventListener('click', (e) => {
    if (window.innerWidth >= 700) return; // desktop keeps sidebar open
    const sb = document.getElementById('sidebar');
    const rail = document.getElementById('icon-rail');
    // Ignore clicks on elements removed from DOM (e.g. session list re-render during folder toggle)
    if (!e.target.isConnected) return;
    // Ignore clicks on the sidebar, icon rail, or hamburger button itself
    if (e.target.closest('#sidebar') || e.target.closest('#icon-rail') || e.target.closest('#hamburger-btn')) return;
    // Ignore clicks inside modals or the chat input area
    if (e.target.closest('.modal') || e.target.closest('.input-bar') || e.target.closest('#message')) return;
    // Ignore clicks on session/folder dropdowns and the styled prompt
    // overlay — they're body-level elements logically tied to a sidebar
    // action (e.g. "Move to folder → New Folder…"), so closing the
    // sidebar when the user clicks one yanks the action mid-flight.
    if (e.target.closest('.session-dropdown, .folder-submenu, #styled-prompt-overlay, #styled-confirm-overlay')) return;
    // Close full sidebar if open (with animation)
    if (sb && !sb.classList.contains('hidden')) {
      const backdrop = document.getElementById('sidebar-backdrop');
      if (backdrop) backdrop.classList.remove('visible');
      sb.classList.add('hidden');
      syncRailSide();
      return;
    }
    // Close mobile-mini icon rail overlay if open
    if (rail && rail.classList.contains('mobile-mini')) {
      rail.classList.remove('mobile-mini');
      rail.style.cssText = '';
      const backdrop = document.getElementById('sidebar-backdrop');
      if (backdrop) backdrop.classList.remove('visible');
      syncRailSide();
    }
  });

  // ── Mobile: close sidebar/rail when a tool button is tapped ──
  // The user expects the sidebar to get out of the way the moment a tool
  // window opens — otherwise the modal lands behind the sidebar on phones.
  // We remember whether the sidebar was open at the moment the tool was
  // tapped so we can re-open it when the tool's modal is dismissed; that
  // way clicking around the app doesn't leave the sidebar permanently
  // shut.
  let _sidebarWasOpenBeforeTool = false;
  let _railWasOpenBeforeTool = false;
  document.addEventListener('click', (e) => {
    if (window.innerWidth >= 700) return;
    const btn = e.target.closest('[id^="tool-"], [id^="rail-"]');
    if (!btn) return;
    setTimeout(() => {
      const sb = document.getElementById('sidebar');
      const rail = document.getElementById('icon-rail');
      const backdrop = document.getElementById('sidebar-backdrop');
      let changed = false;
      if (sb && !sb.classList.contains('hidden')) {
        _sidebarWasOpenBeforeTool = true;
        sb.classList.add('hidden');
        changed = true;
      }
      if (rail && rail.classList.contains('mobile-mini')) {
        _railWasOpenBeforeTool = true;
        rail.classList.remove('mobile-mini');
        rail.style.cssText = '';
        changed = true;
      }
      if (changed) {
        if (backdrop) backdrop.classList.remove('visible');
        syncRailSide();
      }
    }, 0);
  });

  // When a tool is dismissed by swiping it down (ui.js fires `modal-dismissed`),
  // don't bounce the sidebar back open — the swipe should just dismiss the tool.
  // Button-close still restores the prior sidebar state (no event fired there).
  window.addEventListener('modal-dismissed', () => {
    _sidebarWasOpenBeforeTool = false;
    _railWasOpenBeforeTool = false;
  });

  // ── Mobile: when a tool modal closes, restore the sidebar/rail to
  // whatever state it was in before the tool was opened. ──
  // We watch every .modal for the .hidden class going on, and if our
  // remembered "sidebar-was-open" flag is set, undo the auto-close.
  if (window.innerWidth < 700) {
    const _restoreSidebar = () => {
      const sb = document.getElementById('sidebar');
      const rail = document.getElementById('icon-rail');
      const backdrop = document.getElementById('sidebar-backdrop');
      // Skip if any modal is still visible (.modal without .hidden) — we only
      // restore once the user is back to bare chat. A tool swiped DOWN to a
      // dock chip is minimized (display:none via .modal-minimized), not closed
      // — it's still "around", so don't bounce the sidebar open behind it. Only
      // a full close (no minimized modal, no dock chips) should restore.
      const anyOpen = [...document.querySelectorAll('.modal')]
        .some(m => (!m.classList.contains('hidden') && getComputedStyle(m).display !== 'none')
                   || m.classList.contains('modal-minimized'));
      const anyDocked = document.querySelectorAll('.minimized-dock-chip').length > 0;
      if (anyOpen || anyDocked) {
        // A tool is still minimized/docked. The user has left the "launched
        // from the sidebar" context — drop the restore intent so that later
        // FULLY closing the tool (e.g. dragging its chip to the trash) doesn't
        // bounce the sidebar open. (The modal-dismissed listener that normally
        // clears these gets blocked by modalManager's stopImmediatePropagation.)
        _sidebarWasOpenBeforeTool = false;
        _railWasOpenBeforeTool = false;
        return;
      }
      if (_sidebarWasOpenBeforeTool && sb && sb.classList.contains('hidden')) {
        sb.classList.remove('hidden');
        if (backdrop) backdrop.classList.add('visible');
      }
      if (_railWasOpenBeforeTool && rail && !rail.classList.contains('mobile-mini')) {
        rail.classList.add('mobile-mini');
      }
      _sidebarWasOpenBeforeTool = false;
      _railWasOpenBeforeTool = false;
      if (_sidebarWasOpenBeforeTool || _railWasOpenBeforeTool) syncRailSide();
    };
    const _modalObs = new MutationObserver((muts) => {
      let triggered = false;
      for (const m of muts) {
        if (m.type !== 'attributes' || m.attributeName !== 'class') continue;
        const t = m.target;
        if (!(t instanceof HTMLElement) || !t.classList) continue;
        if (t.classList.contains('modal')) { triggered = true; break; }
      }
      if (triggered) setTimeout(_restoreSidebar, 50);
    });
    _modalObs.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });
  }

  // (Mobile swipe-to-open-sidebar is wired at MODULE scope — see
  // _initChatSwipeToOpenSidebar() at the bottom of this file — so it attaches
  // independently of this init function completing.)
}

// ── Mobile: swipe horizontally on the splash/chat to open the sidebar ──
// Wired at MODULE scope (not inside initSidebarLayout) so a throw anywhere in
// that init can't drop this listener. Bound on `document` so it catches the
// touch regardless of which child element is under the finger. touchmove is
// NON-passive and calls preventDefault() once the gesture is locked
// horizontal — without that, Firefox (and others) treat the horizontal swipe
// as their own scroll/navigation gesture and our handler never gets to act.
function _initChatSwipeToOpenSidebar() {
  if (window.__odySwipeWired) return;
  window.__odySwipeWired = true;

  // Areas where a horizontal drag means something else (their own scroll/drag).
  const EXCLUDE = [
    '#sidebar', '#icon-rail', '.modal', '.input-bar', '#message',
    '#minimized-dock', '.minimized-dock-chip', '#dock-trash-zone',
    'pre', 'table', '.agent-tool-output', '.agent-thread-cmd',
    'input', 'textarea', 'select',
  ].join(', ');

  let sx = 0, sy = 0, track = false, decided = false;

  const reset = () => { track = false; decided = false; };

  document.addEventListener('touchstart', (e) => {
    reset();
    if (window.innerWidth >= 768) return;
    if (!e.touches || e.touches.length !== 1) return;
    if (window._chipDragging) return;
    const sb = document.getElementById('sidebar');
    if (sb && !sb.classList.contains('hidden')) return; // already open
    // Only in the chat / empty-chat view. Not when a document or PDF is open
    // (body.doc-view), notes is open (body.notes-view), or a tool modal is up.
    if (document.body.classList.contains('doc-view') ||
        document.body.classList.contains('notes-view')) return;
    // Not while Compare is running — it takes over #chat-container with its own
    // panes/scroll, and the swipe-to-open-sidebar gesture gets in the way there.
    const cc = document.getElementById('chat-container');
    if (cc && cc.classList.contains('compare-active')) return;
    const anyModalOpen = [...document.querySelectorAll('.modal')].some(
      m => !m.classList.contains('hidden') && getComputedStyle(m).display !== 'none');
    if (anyModalOpen) return;
    const t = e.target;
    if (t && t.closest && t.closest(EXCLUDE)) return;
    // The gesture must start within the chat area itself.
    if (!(t && t.closest && t.closest('#chat-container'))) return;
    sx = e.touches[0].clientX;
    sy = e.touches[0].clientY;
    track = true;
  }, { passive: true, capture: true });

  document.addEventListener('touchmove', (e) => {
    if (!track) return;
    if (window._chipDragging) { track = false; return; }
    if (!e.touches || !e.touches.length) return;
    const dx = e.touches[0].clientX - sx;
    const dy = e.touches[0].clientY - sy;
    const adx = Math.abs(dx), ady = Math.abs(dy);
    if (!decided) {
      if (adx < 10 && ady < 10) return;          // not enough travel to judge
      if (ady > adx) { track = false; return; }   // vertical-dominant → let it scroll
      decided = true;                             // locked into a horizontal swipe
    }
    // Claim the gesture from the browser so it doesn't scroll/navigate instead.
    if (e.cancelable) e.preventDefault();
    if (adx >= 40) {
      track = false;
      // Direction picks the side (per user preference): swipe LEFT → sidebar
      // on the left, swipe RIGHT → sidebar on the right. dx<0 is a leftward
      // finger motion; mapping it to 'right' (and dx>0 to 'left') is what makes
      // it feel correct in practice.
      const side = dx < 0 ? 'right' : 'left';
      // Use the deliberate-open helper (sets _userToggledSidebar so the
      // auto-collapse observer doesn't instantly re-hide it). Fall back to a
      // plain unhide if the helper isn't wired yet.
      if (typeof window._odyOpenSidebar === 'function') {
        window._odyOpenSidebar(side);
      } else {
        const sb = document.getElementById('sidebar');
        if (sb) { sb.classList.remove('hidden'); try { syncRailSide(); } catch (_) {} }
      }
    }
  }, { passive: false, capture: true });

  document.addEventListener('touchend', reset, { passive: true, capture: true });
  document.addEventListener('touchcancel', reset, { passive: true, capture: true });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initChatSwipeToOpenSidebar);
} else {
  _initChatSwipeToOpenSidebar();
}
