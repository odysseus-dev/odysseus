// Shared window-drag helper. Replaces the duplicated mousedown / mousemove
// / mouseup + snap-to-top fullscreen + left/right edge dock patterns that
// were copy-pasted across calendar.js, tasks.js, gallery.js, emailLibrary.js,
// documentLibrary.js, theme.js. Behavior stays identical to the old per-file
// copies — each callsite provides its own enter/exit-fullscreen callbacks
// since the CSS class + inline styles differ per modal.
//
// API:
//   makeWindowDraggable(modal, { content, header, ...options })
//     modal:           the wrapping .modal element (or a standalone pane)
//     content:         the element being moved (usually .modal-content)
//     header:          the drag handle (usually .modal-header)
//     fsClass:         optional class name representing "fullscreen" state
//     onEnterFullscreen: legacy callback; drag-to-top fullscreen is disabled.
//     onExitFullscreen:  optional (cx, cy) => void — called mid-drag when
//                        the cursor leaves the fullscreen "unsnap" band.
//                        Caller restores windowed
//                        inline styles centered around the cursor.
//     skipSelector:    CSS selector for elements inside `header` whose
//                        clicks should NOT start a drag (close button,
//                        form fields, etc). Default: 'button, input, select'
//     onDragEnd:       optional (state) => void — fires after mouseup
//                        WHEN no snap was committed. state = { rect } so
//                        callers can persist the final position.
//     enableTouch:     bool — also wire touchstart/touchmove/touchend
//                        with the same drag/dock behavior in landscape.
//                        Touch portrait stays sheet-only. Default true.
//     mobileSkip:      drag is disabled below this viewport width.
//                        Default 768. Touch landscape bypasses the default
//                        width gate; explicit callsite opt-outs still win.
//     resizeMobileSkip: resize is disabled below this viewport width.
//                        Default 768. Set to 0 to allow mobile resize.
//     enableDock:      bool — enable left + right edge docks.
//                        Default true.
//     enableFullscreen: legacy option; fullscreen is controlled separately,
//                        not as part of the edge-dock gesture.

import { makeEdgeDockController } from './modalSnap.js';
import { makeWindowResizable } from './windowResize.js';

const SNAP_PX = 6;        // cursor distance from top edge for fullscreen snap
const UNSNAP_PX = 24;     // cursor distance from top before fullscreen exits
function _isTouchInput() {
  return window.matchMedia('(pointer: coarse)').matches ||
    window.matchMedia('(hover: none)').matches ||
    navigator.maxTouchPoints > 0 ||
    'ontouchstart' in window;
}

function _isTouchLandscape() {
  return window.matchMedia('(orientation: landscape)').matches &&
    _isTouchInput();
}

function _isTouchPortraitDockBlocked() {
  return _isTouchInput() && !_isTouchLandscape();
}

export function makeWindowDraggable(modal, options = {}) {
  const content = options.content;
  const header = options.header;
  if (!content || !header) return;
  const fsClass = options.fsClass || null;
  const onEnterFullscreen = options.onEnterFullscreen || null;
  const onExitFullscreen = options.onExitFullscreen || null;
  const enableFullscreen = false;
  const onDragEnd = options.onDragEnd || null;
  const onDragStart = options.onDragStart || null;
  const skipSelector = options.skipSelector || 'button, input, select';
  const hasCustomMobileSkip = typeof options.mobileSkip === 'number';
  const mobileSkip = hasCustomMobileSkip ? options.mobileSkip : 768;
  const resizeMobileSkip = (typeof options.resizeMobileSkip === 'number')
    ? options.resizeMobileSkip
    : ((typeof options.mobileSkip === 'number') ? options.mobileSkip : 768);
  const enableTouch = options.enableTouch !== false;
  const enableDock = options.enableDock !== false && !!modal;
  const _shouldSkipDrag = () => _isTouchPortraitDockBlocked()
    || (mobileSkip > 0
      && window.innerWidth <= mobileSkip
      && (hasCustomMobileSkip || !_isTouchLandscape()));
  const _dockAllowed = () => enableDock && (!_isTouchInput() || _isTouchLandscape());

  header.style.cursor = 'move';
  header.style.userSelect = 'none';
  if (enableTouch) header.style.touchAction = 'none';

  // Edge/corner resize. Every draggable window also becomes resizable — the
  // same gesture a native desktop window uses (grab an edge or corner, drag).
  // Skipped on mobile (windows are full-screen sheets there) and while the
  // window is fullscreen-snapped or docked. Wired here so all ~12 callsites
  // get it without per-file changes.
  if (options.enableResize !== false) {
    const _dockClasses = ['modal-right-docked', 'modal-left-docked'];
    makeWindowResizable(content, {
      modal,
      mobileSkip: resizeMobileSkip,
      minWidth: options.minWidth,
      minHeight: options.minHeight,
      isLocked: () => (fsClass && modal && modal.classList.contains(fsClass))
        || (modal && _dockClasses.some((c) => modal.classList.contains(c))),
      storageKey: options.resizeStorageKey
        || (modal && modal.id ? 'winsize-' + modal.id
          : (content.id ? 'winsize-' + content.id : null)),
    });
  }

  const rightDock = enableDock ? makeEdgeDockController(modal, 'right') : null;
  // Left dock is enabled by default too. modalSnap collapses the wide sidebar
  // and anchors the panel beside the icon rail, so it no longer collides with
  // the navigation. Callers can still pass enableLeftDock:false for a special
  // modal that should only dock right.
  const leftDock = (enableDock && options.enableLeftDock !== false) ? makeEdgeDockController(modal, 'left') : null;

  // Per-drag state, reset on mousedown.
  let dragging = false;
  let startX = 0, startY = 0;
  let startLeft = 0, startTop = 0;
  let snapHint = null;
  // Whether the pointer actually moved beyond a small threshold this drag.
  // Used to suppress the synthetic click the browser fires on mouseup —
  // header click handlers (e.g. "collapse expanded card / back to list")
  // would otherwise fire after a drag and collapse the modal contents.
  let movedDuringDrag = false;
  const MOVE_THRESHOLD = 4;

  const _showSnapHint = (on) => {
    // Top-edge fullscreen hint. Side hints come from the dock controllers.
    if (!on) {
      if (snapHint) { snapHint.remove(); snapHint = null; }
      return;
    }
    if (snapHint) return;
    snapHint = document.createElement('div');
    snapHint.className = 'modal-snap-hint';
    snapHint.style.cssText =
      'position:fixed;left:0;top:0;right:0;bottom:0;' +
      'background:color-mix(in srgb, var(--accent-primary, #60a5fa) 12%, transparent);' +
      'border:2px dashed color-mix(in srgb, var(--accent-primary, #60a5fa) 60%, transparent);' +
      'z-index:9998;pointer-events:none;';
    document.body.appendChild(snapHint);
  };

  const _enterFs = () => {
    if (!onEnterFullscreen) return;
    if (fsClass && modal && modal.classList.contains(fsClass)) return;
    onEnterFullscreen();
  };
  const _exitFs = (cx, cy) => {
    if (!onExitFullscreen) return;
    if (fsClass && modal && !modal.classList.contains(fsClass)) return;
    onExitFullscreen(cx, cy);
    // After exit, re-anchor the drag offsets to the new windowed rect so
    // the drag continues smoothly from the cursor's position.
    const r = content.getBoundingClientRect();
    startX = cx; startY = cy;
    startLeft = r.left; startTop = r.top;
  };

  const _isFullscreen = () => fsClass && modal && modal.classList.contains(fsClass);

  const _startDrag = (cx, cy) => {
    dragging = true;
    if (modal) modal.classList.add('modal-dragging');
    // Cancel any in-flight open animation so we don't pin a mid-animation
    // rect and then jump once the animation settles.
    try {
      content.getAnimations()
        .filter(a => a.playState !== 'finished')
        .forEach(a => a.cancel());
    } catch (_) {}
    const rect = content.getBoundingClientRect();
    if (onDragStart) {
      try { onDragStart({ rect, cx, cy }); } catch (_) {}
    }
    startX = cx; startY = cy;
    startLeft = rect.left; startTop = rect.top;
    // Pin position so the drag follows the cursor instead of fighting a
    // centering transform / margin. Inline styles win unless CSS uses
    // !important (the fullscreen rules do, by design).
    content.style.position = 'fixed';
    content.style.left = startLeft + 'px';
    content.style.top = startTop + 'px';
    content.style.transform = 'none';
    content.style.margin = '0';
  };

  const _onMove = (cx, cy) => {
    if (!dragging) return;
    const dockAllowed = _dockAllowed();
    const activeRightDock = dockAllowed ? rightDock : null;
    const activeLeftDock = dockAllowed ? leftDock : null;
    if (!dockAllowed) {
      if (rightDock) rightDock.release();
      if (leftDock) leftDock.release();
    }
    if (_isFullscreen()) {
      if (activeRightDock) activeRightDock.release();
      if (activeLeftDock) activeLeftDock.release();
      if (cy > UNSNAP_PX) {
        _exitFs(cx, cy);
      }
      return;
    }
    // Right-docked: pulling away from the right edge un-docks. Same for left.
    if (activeRightDock && modal && modal.classList.contains('modal-right-docked')) {
      if (activeRightDock.onMove(cx, cy)) {
        const r = content.getBoundingClientRect();
        startX = cx; startY = cy;
        startLeft = r.left; startTop = r.top;
      }
      return;
    }
    if (activeLeftDock && modal && modal.classList.contains('modal-left-docked')) {
      if (activeLeftDock.onMove(cx, cy)) {
        const r = content.getBoundingClientRect();
        startX = cx; startY = cy;
        startLeft = r.left; startTop = r.top;
      }
      return;
    }
    // Windowed: just follow the cursor.
    if (Math.abs(cx - startX) > MOVE_THRESHOLD || Math.abs(cy - startY) > MOVE_THRESHOLD) {
      movedDuringDrag = true;
    }
    content.style.left = (startLeft + cx - startX) + 'px';
    content.style.top = (startTop + cy - startY) + 'px';
    // Corner guard: in the top fullscreen band the side docks stay OFF, so a
    // top corner only ever snaps to fullscreen — never the corner hybrid.
    const inTopBand = cy <= SNAP_PX;
    _showSnapHint(enableFullscreen && inTopBand);
    if (inTopBand) {
      if (activeRightDock) activeRightDock.release();
      if (activeLeftDock) activeLeftDock.release();
    } else {
      if (activeRightDock) activeRightDock.onMove(cx, cy);
      if (activeLeftDock) activeLeftDock.onMove(cx, cy);
    }
  };

  const _onEnd = (cx, cy) => {
    if (!dragging) return;
    dragging = false;
    const dockAllowed = _dockAllowed();
    const activeRightDock = dockAllowed ? rightDock : null;
    const activeLeftDock = dockAllowed ? leftDock : null;
    if (modal) modal.classList.remove('modal-dragging');
    _showSnapHint(false);
    if (enableFullscreen && typeof cy === 'number' && cy <= SNAP_PX) {
      if (activeRightDock) activeRightDock.release();
      if (activeLeftDock) activeLeftDock.release();
      _enterFs();
      return;
    }
    if (activeRightDock && activeRightDock.hovering()) {
      if (activeLeftDock) activeLeftDock.release();
      activeRightDock.commit();
      return;
    }
    if (activeLeftDock && activeLeftDock.hovering()) {
      if (activeRightDock) activeRightDock.release();
      activeLeftDock.commit();
      return;
    }
    if (rightDock) rightDock.release();
    if (leftDock) leftDock.release();
    if (onDragEnd) {
      const r = content.getBoundingClientRect();
      try { onDragEnd({ rect: r }); } catch (_) {}
    }
  };

  header.addEventListener('mousedown', (e) => {
    if (_shouldSkipDrag()) return;
    if (skipSelector && e.target.closest(skipSelector)) return;
    e.preventDefault();
    movedDuringDrag = false;
    _startDrag(e.clientX, e.clientY);
    const onMove = (ev) => _onMove(ev.clientX, ev.clientY);
    const onUp = (ev) => {
      _onEnd(ev.clientX, ev.clientY);
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      // If the pointer actually moved, swallow the synthetic click the
      // browser fires next — otherwise a header click handler (collapse
      // expanded card / "back to list") runs and undoes the drag intent.
      if (movedDuringDrag) {
        const swallow = (clickEv) => {
          clickEv.stopPropagation();
          clickEv.preventDefault();
        };
        header.addEventListener('click', swallow, { capture: true, once: true });
        // Safety: if no click fires (some browsers), drop the listener.
        setTimeout(() => header.removeEventListener('click', swallow, { capture: true }), 50);
      }
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  if (enableTouch) {
    header.addEventListener('touchstart', (e) => {
      if (_shouldSkipDrag()) return;
      if (skipSelector && e.target.closest(skipSelector)) return;
      const t = e.touches[0];
      if (!t) return;
      const touchStartX = t.clientX;
      const touchStartY = t.clientY;
      let touchDragStarted = false;
      movedDuringDrag = false;
      const onMove = (ev) => {
        const tt = ev.touches[0];
        if (!tt) return;
        const dx = tt.clientX - touchStartX;
        const dy = tt.clientY - touchStartY;
        if (!touchDragStarted) {
          if (Math.hypot(dx, dy) < MOVE_THRESHOLD) return;
          // Preserve the mobile sheet gesture: mostly-vertical downward pulls
          // still minimize/dismiss. Horizontal drags are window drag/dock.
          if (dy > 0 && Math.abs(dy) > Math.abs(dx)) return;
          touchDragStarted = true;
          try { window._modalWindowDragging = true; } catch (_) {}
          _startDrag(touchStartX, touchStartY);
        }
        if (ev.cancelable) ev.preventDefault();
        _onMove(tt.clientX, tt.clientY);
      };
      const onEnd = (ev) => {
        const tt = (ev.changedTouches && ev.changedTouches[0]) || null;
        if (touchDragStarted) _onEnd(tt ? tt.clientX : null, tt ? tt.clientY : null);
        document.removeEventListener('touchmove', onMove, true);
        document.removeEventListener('touchend', onEnd, true);
        document.removeEventListener('touchcancel', onEnd, true);
        setTimeout(() => {
          try { window._modalWindowDragging = false; } catch (_) {}
        }, 0);
      };
      document.addEventListener('touchmove', onMove, { passive: false, capture: true });
      document.addEventListener('touchend', onEnd, { capture: true });
      document.addEventListener('touchcancel', onEnd, { capture: true });
    }, { passive: true });
  }
}
