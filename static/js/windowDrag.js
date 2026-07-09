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
//     onEnterFullscreen: optional () => void — called when cursor releases
//                        near the top edge (within SNAP_PX). Caller is
//                        responsible for adding fsClass + applying inline
//                        styles that produce the fullscreen layout.
//     onExitFullscreen:  optional (cx, cy) => void — called mid-drag when
//                        the cursor leaves the fullscreen "unsnap" band
//                        (down > UNSNAP_PX OR near either horizontal edge
//                        in dock-snap range). Caller restores windowed
//                        inline styles centered around the cursor.
//     skipSelector:    CSS selector for elements inside `header` whose
//                        clicks should NOT start a drag (close button,
//                        form fields, etc). Default: 'button, input, select'
//     onDragEnd:       optional (state) => void — fires after mouseup
//                        WHEN no snap was committed. state = { rect } so
//                        callers can persist the final position.
//     enableTouch:     bool — also wire touchstart/touchmove/touchend
//                        with the same drag (no fs/dock on touch). Default
//                        true on desktop, irrelevant on mobile (mobileSkip).
//     mobileSkip:      drag is disabled below this viewport width.
//                        Default 768. Set to 0 to never skip.
//     enableDock:      bool — enable left, right, top, and bottom edge docks.
//                        Default true.
//     enableFullscreen: bool — enable top-edge fullscreen snap.
//                        Default true when onEnterFullscreen is supplied.

import { makeEdgeDockController } from './modalSnap.js';
import { makeWindowResizable } from './windowResize.js';

const SNAP_PX = 6;        // cursor distance from top edge for fullscreen snap
const TOP_TILE_RESERVED_PX = 12; // tileManager owns fullscreen/maximize here
const UNSNAP_PX = 24;     // cursor distance from top before fullscreen exits
const DOCK_EDGE_PX = 60;  // cursor distance from L/R edge to trigger dock
                          // exit while still in fullscreen state

function _hasFinePointer() {
  if (typeof window.matchMedia !== 'function') return window.innerWidth > 768;
  return window.matchMedia('(pointer: fine)').matches
    || window.matchMedia('(any-pointer: fine)').matches;
}

function _uiScaleFactor() {
  try {
    const raw = window.getComputedStyle?.(document.documentElement)
      ?.getPropertyValue?.('--ui-scale-factor') || '';
    const scale = parseFloat(raw);
    return Number.isFinite(scale) && scale > 0 ? scale : 1;
  } catch (_) {
    return 1;
  }
}

function _layoutCoordinate(value) { return value / _uiScaleFactor(); }

function _layoutRect(rect) {
  const scale = _uiScaleFactor();
  return { left: rect.left / scale, top: rect.top / scale };
}

// CSS-var lookup for the rail+sidebar width — used to decide where the
// "left edge" effectively is during a fullscreen drag-out (the cursor
// has to pass the rail to count as "near left").
function _leftNavWidth() {
  const rs = getComputedStyle(document.documentElement);
  const rail = parseInt(rs.getPropertyValue('--icon-rail-w') || '48', 10) || 0;
  const sb = parseInt(rs.getPropertyValue('--sidebar-w') || '0', 10) || 0;
  return rail + sb;
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
  const mobileSkip = (typeof options.mobileSkip === 'number') ? options.mobileSkip : 768;
  const enableTouch = options.enableTouch !== false;
  const enableDock = options.enableDock !== false && !!modal;
  if (enableDock && modal?.dataset) modal.dataset.edgeDockController = '1';

  header.style.cursor = 'move';
  header.style.userSelect = 'none';

  // Edge/corner resize. Every draggable window also becomes resizable — the
  // same gesture a native desktop window uses (grab an edge or corner, drag).
  // Skipped on mobile (windows are full-screen sheets there) and while the
  // window is fullscreen-snapped or docked. Wired here so all ~12 callsites
  // get it without per-file changes.
  if (options.enableResize !== false) {
    const _dockClasses = [
      'modal-right-docked',
      'modal-left-docked',
      'modal-top-docked',
      'modal-bottom-docked',
    ];
    makeWindowResizable(content, {
      modal,
      mobileSkip,
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
  const topDock = (enableDock && options.enableVerticalDock !== false) ? makeEdgeDockController(modal, 'top') : null;
  const bottomDock = (enableDock && options.enableVerticalDock !== false) ? makeEdgeDockController(modal, 'bottom') : null;
  const _dockControllers = () => [topDock, bottomDock, leftDock, rightDock].filter((controller) => {
    if (!controller) return false;
    return (controller.side() === 'top' || controller.side() === 'bottom')
      ? (_hasFinePointer() && window.innerWidth > 768)
      : true;
  });
  const _releaseDockControllers = (except = null) => {
    for (const controller of [topDock, bottomDock, leftDock, rightDock]) {
      if (controller && controller !== except) controller.release();
    }
  };
  const _currentDockController = () => _dockControllers().find((controller) =>
    modal?.classList?.contains(`modal-${controller.side()}-docked`));
  const _dockCandidate = (cx, cy) => {
    const candidates = _dockControllers().filter((controller) => controller.near(cx, cy));
    candidates.sort((a, b) => Math.max(0, a.distance(cx, cy)) - Math.max(0, b.distance(cx, cy)));
    return candidates[0] || null;
  };

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
    const r = _layoutRect(content.getBoundingClientRect());
    startX = _layoutCoordinate(cx); startY = _layoutCoordinate(cy);
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
    const rawRect = content.getBoundingClientRect();
    const rect = _layoutRect(rawRect);
    if (onDragStart) {
      try { onDragStart({ rect: rawRect, cx, cy }); } catch (_) {}
    }
    startX = _layoutCoordinate(cx); startY = _layoutCoordinate(cy);
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
    // Fullscreen state: unsnap on drag-down or drag toward either horizontal
    // edge. Update dock hover immediately after exit so a fast release
    // commits the dock instead of dropping the modal mid-air.
    if (_isFullscreen()) {
      // Corner guard: ignore the side edges while the cursor is still in the
      // top fullscreen band, so dragging across the top corners keeps
      // fullscreen instead of flipping into a corner dock.
      const inTopBand = cy <= TOP_TILE_RESERVED_PX;
      const nearRight = !inTopBand && _layoutCoordinate(window.innerWidth - cx) <= DOCK_EDGE_PX;
      const nearLeft = !inTopBand && (_layoutCoordinate(cx) - _leftNavWidth()) <= DOCK_EDGE_PX;
      // Dragging a fullscreen window to a SIDE edge → keep it fullscreen and
      // just arm the side-dock hint; releasing there docks it (handled in
      // _onEnd, which drops the fullscreen class). Previously this exited
      // fullscreen first, which re-CENTERED the window — so it looked like
      // it "centered instead of docking". Only a downward drag unsnaps to a
      // windowed (centered) modal.
      if (nearRight && rightDock) {
        _releaseDockControllers(rightDock);
        rightDock.onMove(cx, cy);
        return;
      }
      if (nearLeft && leftDock) {
        _releaseDockControllers(leftDock);
        leftDock.onMove(cx, cy);
        return;
      }
      if (cy > UNSNAP_PX) {
        _exitFs(cx, cy);
        const candidate = _dockCandidate(cx, cy);
        _releaseDockControllers(candidate);
        if (candidate) candidate.onMove(cx, cy);
      } else {
        _releaseDockControllers();
      }
      return;
    }
    // Pulling away from any dock edge restores the saved floating geometry.
    const currentDock = _currentDockController();
    if (currentDock) {
      _releaseDockControllers(currentDock);
      if (currentDock.onMove(cx, cy)) {
        const r = _layoutRect(content.getBoundingClientRect());
        startX = _layoutCoordinate(cx); startY = _layoutCoordinate(cy);
        startLeft = r.left; startTop = r.top;
      }
      return;
    }
    // Windowed: just follow the cursor.
    if (Math.abs(_layoutCoordinate(cx) - startX) > MOVE_THRESHOLD
        || Math.abs(_layoutCoordinate(cy) - startY) > MOVE_THRESHOLD) {
      movedDuringDrag = true;
    }
    content.style.left = (startLeft + _layoutCoordinate(cx) - startX) + 'px';
    content.style.top = (startTop + _layoutCoordinate(cy) - startY) + 'px';
    // Corner guard: in the top fullscreen band the side docks stay OFF, so a
    // top corner only ever snaps to fullscreen — never the corner hybrid.
    const inTopBand = cy <= TOP_TILE_RESERVED_PX;
    _showSnapHint(enableFullscreen && inTopBand);
    if (inTopBand) {
      _releaseDockControllers();
    } else {
      const candidate = _dockCandidate(cx, cy);
      _releaseDockControllers(candidate);
      if (candidate) candidate.onMove(cx, cy);
    }
  };

  const _onEnd = (cx, cy) => {
    if (!dragging) return;
    dragging = false;
    if (modal) modal.classList.remove('modal-dragging');
    _showSnapHint(false);
    // Top edge wins over side edges — fullscreen is the more common gesture.
    if (enableFullscreen && typeof cy === 'number' && cy <= SNAP_PX) {
      _releaseDockControllers();
      _enterFs();
      return;
    }
    const hoveredDock = _dockControllers().find((controller) => controller.hovering());
    if (hoveredDock) {
      _releaseDockControllers(hoveredDock);
      if (fsClass && modal) modal.classList.remove(fsClass);
      if (hoveredDock.commit()) return;
    }
    _releaseDockControllers();
    if (onDragEnd) {
      const r = content.getBoundingClientRect();
      try { onDragEnd({ rect: r }); } catch (_) {}
    }
  };

  header.addEventListener('mousedown', (e) => {
    if (mobileSkip > 0 && window.innerWidth <= mobileSkip) return;
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
      if (mobileSkip > 0 && window.innerWidth <= mobileSkip) return;
      if (skipSelector && e.target.closest(skipSelector)) return;
      const t = e.touches[0];
      if (!t) return;
      movedDuringDrag = false;
      _startDrag(t.clientX, t.clientY);
      const onMove = (ev) => {
        const tt = ev.touches[0];
        if (tt) _onMove(tt.clientX, tt.clientY);
      };
      const onEnd = (ev) => {
        const tt = (ev.changedTouches && ev.changedTouches[0]) || null;
        _onEnd(tt ? tt.clientX : null, tt ? tt.clientY : null);
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onEnd);
        document.removeEventListener('touchcancel', onEnd);
      };
      document.addEventListener('touchmove', onMove, { passive: true });
      document.addEventListener('touchend', onEnd);
      document.addEventListener('touchcancel', onEnd);
    }, { passive: true });
  }
}
