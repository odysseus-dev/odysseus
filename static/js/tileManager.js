/**
 * tileManager.js — desktop window tiling for tool modals.
 *
 * Hooks into any modal whose `.modal-header` is dragged (each tool wires its
 * own drag; we just watch pointer moves). Shows a translucent ghost preview
 * when the cursor is near a snap zone. On release, snaps the modal-content
 * to fill that zone with a springy animation.
 *
 * Snap zones (9):
 *   - top edge (10% strip)        → maximize
 *   - top-left corner             → top-left quarter
 *   - top-right corner            → top-right quarter
 *   - left edge                   → left half
 *   - right edge                  → right half
 *   - bottom-left corner          → bottom-left quarter
 *   - bottom-right corner         → bottom-right quarter
 *   - bottom edge                 → bottom half
 *   - sidebar edge (if present)   → snap next to the sidebar
 *
 * Mobile (≤768px) is excluded — the swipe-dismiss UX takes precedence.
 *
 * Each modal-content remembers its pre-snap geometry so dragging away restores
 * the original size.
 */

const EDGE_THRESHOLD_PX = 24;     // how close to an edge counts as "near"
const CORNER_THRESHOLD_PX = 64;   // corner box size
const TOP_FULL_STRIP_PX = 8;      // top strip → maximize

let _ghost = null;
let _activeZone = null;
let _tracking = null; // { content, startRect }

function _hasFinePointer() {
  if (typeof window.matchMedia !== 'function') return true;
  return window.matchMedia('(pointer: fine)').matches;
}

function _isTouchInput() {
  if (typeof window.matchMedia !== 'function') {
    return typeof navigator !== 'undefined' && navigator.maxTouchPoints > 0;
  }
  return window.matchMedia('(pointer: coarse)').matches
    || window.matchMedia('(hover: none)').matches
    || navigator.maxTouchPoints > 0;
}

function _isTouchLandscape() {
  if (typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(orientation: landscape)').matches && _isTouchInput();
}

function _isTouchPortrait() {
  if (typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(orientation: portrait)').matches && _isTouchInput();
}

function _isElementVisible(el) {
  if (!el) return false;
  const cs = window.getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden') return false;
  const opacity = parseFloat(cs.opacity || '1');
  return !Number.isFinite(opacity) || opacity > 0.05;
}

function _isDesktop() {
  // This is desktop tiling. Coarse-touch Android landscape uses edge docking
  // only; otherwise the same swipe can also leave a right-half tile snap behind.
  return window.innerWidth > 768 && _hasFinePointer();
}

function _dockClassForSide(side) {
  return side === 'left' ? 'modal-left-docked' : 'modal-right-docked';
}

function _hasOtherDockedWindow(side, owner) {
  const cls = _dockClassForSide(side);
  return Array.from(document.querySelectorAll(`.${cls}`)).some((el) => {
    if (!el || el === owner) return false;
    if (owner && el.contains && el.contains(owner)) return false;
    if (owner && owner.contains && owner.contains(el)) return false;
    return true;
  });
}

function _clearDockSide(side, owner = null) {
  if (side !== 'left' && side !== 'right') return;
  if (_hasOtherDockedWindow(side, owner)) return;
  document.body.classList.remove(side === 'left' ? 'left-dock-active' : 'right-dock-active');
  document.documentElement.style.removeProperty(side === 'left' ? '--left-dock-w' : '--right-dock-w');
  document.documentElement.style.removeProperty(side === 'left' ? '--left-dock-reserve-w' : '--right-dock-reserve-w');
  if (side === 'left') {
    try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
  }
}

function _clearEmailSplitGeometry() {
  document.body.classList.remove('email-doc-split-active', 'email-front');
  document.documentElement.style.removeProperty('--email-doc-split-left-x');
  document.documentElement.style.removeProperty('--email-doc-split-email-w');
  document.documentElement.style.removeProperty('--email-doc-split-right-x');
  const docPane = document.getElementById('doc-editor-pane');
  if (docPane) {
    [
      'position', 'left', 'right', 'top', 'bottom', 'width', 'max-width',
      'height', 'z-index', 'transform',
    ].forEach((prop) => docPane.style.removeProperty(prop));
  }
  const divider = document.getElementById('doc-divider');
  if (divider) divider.style.display = '';
}

function _ensureGhost() {
  if (_ghost) return _ghost;
  _ghost = document.createElement('div');
  _ghost.id = 'tile-ghost';
  document.body.appendChild(_ghost);
  return _ghost;
}

function _hideGhost() {
  if (_ghost) _ghost.classList.remove('visible');
}

function _showGhost(rect) {
  const g = _ensureGhost();
  g.style.left = rect.left + 'px';
  g.style.top  = rect.top  + 'px';
  g.style.width  = rect.width  + 'px';
  g.style.height = rect.height + 'px';
  g.classList.add('visible');
}

function _viewportWorkspaceRect(inset = 4) {
  // Account for visible nav rails on either side of the viewport. Android
  // landscape chooses the rail side dynamically based on the camera cutout,
  // so read the actual side instead of assuming a fixed edge.
  const sidebar = document.getElementById('sidebar');
  const rail = document.querySelector('.icon-rail') || document.querySelector('#icon-rail');
  let leftEdge = 0;
  let rightEdge = window.innerWidth;
  const prefersRightNav = document.body.classList.contains('hamburger-right');
  const isRightNav = (el, span) => !!(el?.classList?.contains('right-side')
    || prefersRightNav
    || span?.right >= window.innerWidth - 1
    || span?.left >= window.innerWidth / 2);
  const visibleSpan = (el) => {
    if (!el || !_isElementVisible(el)) return null;
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 1 || r.height <= 1) return null;
    const left = Math.max(0, r.left);
    const right = Math.min(window.innerWidth, r.right);
    if (right - left <= 1) return null;
    return { rect: r, left, right };
  };
  const sbSpan = !sidebar?.classList?.contains('hidden') ? visibleSpan(sidebar) : null;
  if (sbSpan) {
    const sb = sbSpan.rect;
    if (isRightNav(sidebar, sbSpan)) {
      if (sbSpan.left <= 1 && sbSpan.right < window.innerWidth / 2) {
        // Right-side nav can briefly report its old left-edge rect while the
        // Android dock/sidebar transition is settling. Ignore that stale span
        // so fullscreen content is not offset by a phantom left menu.
      } else {
        rightEdge = Math.min(rightEdge, sbSpan.left);
      }
    } else if (sbSpan.left <= 1 || sb.left <= 1) {
      leftEdge = Math.max(leftEdge, sbSpan.right);
    }
  }
  const railSpan = visibleSpan(rail);
  if (railSpan) {
    const rr = railSpan.rect;
    if (isRightNav(rail, railSpan)) {
      if (railSpan.left <= 1 && railSpan.right < window.innerWidth / 2) {
        // Same transitional guard as the sidebar above.
      } else {
        rightEdge = Math.min(rightEdge, railSpan.left);
      }
    } else if (railSpan.left <= 1 || rr.left <= 1) {
      leftEdge = Math.max(leftEdge, railSpan.right);
    }
  }
  return {
    left: leftEdge + inset,
    top: inset,
    right: rightEdge - inset,
    bottom: window.innerHeight - inset,
  };
}

function _viewportSafeRect() {
  return _viewportWorkspaceRect(4);
}

function _fullscreenRect() {
  if (_isTouchLandscape()) {
    const safe = _viewportWorkspaceRect(0);
    const width = Math.max(0, safe.right - safe.left);
    const height = Math.max(0, safe.bottom - safe.top);
    return { left: safe.left, top: safe.top, width, height };
  }
  return { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
}

export function fullscreenWorkspaceRect() {
  return _fullscreenRect();
}

function _zoneForPointer(x, y) {
  const safe = _viewportSafeRect();
  const W = safe.right - safe.left;
  const H = safe.bottom - safe.top;

  // Dragged OVER the top edge (cursor at/past the very top) -> desktop fullscreen.
  if (y <= 0) {
    return { name: 'fullscreen', rect: _fullscreenRect() };
  }
  // Near the top edge (but not over it) → "maximize": fill the safe area,
  // which sits NEXT TO the sidebar/rail rather than covering it.
  if (y <= safe.top + TOP_FULL_STRIP_PX) {
    return { name: 'maximize', rect: { left: safe.left, top: safe.top, width: W, height: H } };
  }

  // Corner quarter-snaps DISABLED (user request) — only the top strip
  // (maximize) and the right/bottom half-snaps remain. The LEFT-half snap
  // is also disabled (the sidebar lives there; docking over it is awkward).
  if (x >= safe.right - EDGE_THRESHOLD_PX)
    return { name: 'right-half', rect: { left: safe.left + W / 2, top: safe.top, width: W / 2, height: H } };
  if (y >= safe.bottom - EDGE_THRESHOLD_PX)
    return { name: 'bottom-half', rect: { left: safe.left, top: safe.top + H / 2, width: W, height: H / 2 } };

  return null;
}

function _zoneForContent(content, x, y) {
  const modal = content && content.closest && content.closest('.modal, .research-overlay');
  const zone = _zoneForPointer(x, y);
  if (!zone) return null;
  // Settings has a dense two-column layout; the full-height sidebar-style dock
  // crushes it. Let it tile only into the normal right half, where the nav can
  // flip to top tabs via CSS when the window gets narrow.
  if (modal && modal.id === 'settings-modal' && zone.name !== 'right-half') return null;
  if (modal && (modal.id === 'cookbook-modal'
      || modal.id === 'theme-modal'
      || modal.id === 'memory-modal')
      && zone.name !== 'fullscreen') return null;
  return zone;
}

function _clearEdgeDockResidue(modal, content) {
  const hadDockState = !!(
    (modal && (modal.classList.contains('modal-left-docked') || modal.classList.contains('modal-right-docked')))
    || (content && (content._preDockSnapshot || content._dockSide || content._dockSuspended))
  );
  if (modal) {
    const hadLeft = modal.classList.contains('modal-left-docked');
    const hadRight = modal.classList.contains('modal-right-docked');
    const hadEmailSnapLeft = modal.classList.contains('email-snap-left');
    modal.classList.remove('modal-left-docked', 'modal-right-docked', 'email-snap-left');
    if (hadLeft) _clearDockSide('left', modal);
    if (hadRight) _clearDockSide('right', modal);
    if (hadEmailSnapLeft) {
      _clearDockSide('left', modal);
      _clearEmailSplitGeometry();
    }
    if (modal._dockCloseWatcher) {
      try { modal._dockCloseWatcher.obs && modal._dockCloseWatcher.obs.disconnect(); } catch (_) {}
      try { modal._dockCloseWatcher.parentObs && modal._dockCloseWatcher.parentObs.disconnect(); } catch (_) {}
      delete modal._dockCloseWatcher;
    }
  }
  if (!content) return;
  if (content._leftDockNavObs) {
    try { content._leftDockNavObs.navObs.disconnect(); } catch (_) {}
    try { window.removeEventListener('resize', content._leftDockNavObs.reanchor); } catch (_) {}
    delete content._leftDockNavObs;
  }
  delete content._preDockSnapshot;
  delete content._dockSide;
  delete content._dockSuspended;
  if (hadDockState) {
    ['right', 'bottom', 'max-width', 'border-radius']
      .forEach(p => content.style.removeProperty(p));
  }
}

function _applySnap(content, rect, zoneName) {
  // A tile-snap supersedes any edge-dock on this same modal. The two
  // systems (windowDrag→modalSnap edge-dock, and this tile manager) both
  // fire on a left/right-edge drag-release. If we leave modalSnap's
  // `left-dock-active` body class + `--left-dock-w` padding in place, it
  // reserves a strip on the left AND this manager's safe-rect already
  // accounts for the sidebar's (now padding-shifted) position — the two
  // double-count and jam the window to the right behind a massive empty
  // zone, which gets worse each time the sidebar is toggled. Clear the
  // orphaned edge-dock state so only the tile-snap positions the window.
  const _modal = content.closest && content.closest('.modal, .research-overlay');
  const _fromRect = content.getBoundingClientRect();
  _clearEdgeDockResidue(_modal, content);
  const snapRect = zoneName === 'fullscreen' ? _fullscreenRect() : rect;

  // Stash pre-snap geometry once; if we re-snap, keep the original. Capture a
  // CONCRETE fixed position (from the rendered rect when the inline value is
  // empty) and the position itself — otherwise un-snap restored empty left/top
  // + no position, and the .modal flex parent re-centered the window.
  if (!content.dataset._tilePreSnap) {
    content.dataset._tilePreSnap = JSON.stringify({
      position: 'fixed',
      left:   content.style.left || (Math.round(_fromRect.left) + 'px'),
      top:    content.style.top  || (Math.round(_fromRect.top)  + 'px'),
      width:  content.style.width,
      height: content.style.height,
      maxHeight: content.style.maxHeight,
      transform: content.style.transform,
    });
  }
  content.style.transition = 'left 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), top 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), width 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), height 0.22s cubic-bezier(0.34, 1.56, 0.64, 1)';
  // Use !important — some modals (e.g. cookbook) carry inline width/height
  // and CSS that otherwise re-center the .modal-content, which made the snap
  // "jump back to the middle" on release.
  content.style.setProperty('position', 'fixed', 'important');
  content.style.setProperty('left',   snapRect.left   + 'px', 'important');
  content.style.setProperty('top',    snapRect.top    + 'px', 'important');
  content.style.setProperty('width',  snapRect.width  + 'px', 'important');
  content.style.setProperty('max-width', 'none', 'important');
  content.style.setProperty('height', snapRect.height + 'px', 'important');
  content.style.setProperty('max-height', snapRect.height + 'px', 'important');
  content.style.setProperty('margin', '0', 'important');
  content.style.setProperty('transform', 'none', 'important');
  content.dataset._tileZone = zoneName;
  setTimeout(() => { content.style.transition = ''; }, 250);
  _scheduleFullscreenSettle(content, zoneName);
}

function _scheduleFullscreenSettle(content, zoneName) {
  if (zoneName !== 'fullscreen' || !_isTouchLandscape() || !content) return;
  const run = () => {
    if (!content.isConnected || content.dataset._tileZone !== 'fullscreen') return;
    _reclampAllThrottled(false);
  };
  requestAnimationFrame(run);
  setTimeout(run, 80);
  setTimeout(run, 220);
  setTimeout(run, 520);
}

function _unsnap(content) {
  const pre = content.dataset._tilePreSnap;
  if (!pre) return;
  if (_isTouchPortrait()) {
    [
      'position', 'left', 'top', 'right', 'bottom', 'width', 'height',
      'max-width', 'max-height', 'margin', 'transform',
    ].forEach(p => content.style.removeProperty(p));
    delete content.dataset._tilePreSnap;
    delete content.dataset._tileZone;
    return;
  }
  // Clear the !important snap props first — Object.assign can't override them.
  ['position', 'left', 'top', 'width', 'max-width', 'height', 'max-height', 'margin', 'transform']
    .forEach(p => content.style.removeProperty(p));
  try {
    const r = JSON.parse(pre);
    Object.assign(content.style, r);
  } catch {}
  // Keep it a fixed floating window so the restored left/top actually take
  // effect — without position:fixed the .modal flex parent re-centers it.
  if (!content.style.position) content.style.position = 'fixed';
  delete content.dataset._tilePreSnap;
  delete content.dataset._tileZone;
}

function _findDragTarget(e) {
  const header = e.target.closest('.modal-header');
  if (!header) return null;
  // Skip clicks on header buttons (close, minimize, etc.)
  if (e.target.closest('button')) return null;
  const modal = header.closest('.modal, .research-overlay');
  if (!modal) return null;
  const content = modal.querySelector('.modal-content, .research-pane');
  return content || null;
}

document.addEventListener('pointerdown', (e) => {
  if (!_isDesktop()) return;
  const content = _findDragTarget(e);
  if (!content) return;

  // If we're already snapped, dragging away should unsnap immediately so the
  // user can move freely.
  if (content.dataset._tileZone) {
    // Defer slightly so pointermove threshold is met before unsnap kicks in
    _tracking = { content, startX: e.clientX, startY: e.clientY, willUnsnap: true };
  } else {
    _tracking = { content, startX: e.clientX, startY: e.clientY, willUnsnap: false };
  }
});

document.addEventListener('pointermove', (e) => {
  if (!_tracking) return;
  if (!_isDesktop()) return;
  const dx = e.clientX - _tracking.startX;
  const dy = e.clientY - _tracking.startY;
  if (Math.hypot(dx, dy) < 6) return;

  // Unsnap on first significant move
  if (_tracking.willUnsnap) {
    _unsnap(_tracking.content);
    _tracking.willUnsnap = false;
  }

  // Detect snap zone under cursor
  const zone = _zoneForContent(_tracking.content, e.clientX, e.clientY);
  if (zone) {
    _showGhost(zone.rect);
    _activeZone = zone;
  } else {
    _hideGhost();
    _activeZone = null;
  }
});

document.addEventListener('pointerup', () => {
  if (!_tracking) return;
  const t = _tracking;
  _tracking = null;
  _hideGhost();
  if (_activeZone && _isDesktop()) {
    _applySnap(t.content, _activeZone.rect, _activeZone.name);
  }
  _activeZone = null;
});

// Re-clamp every currently-snapped window so it keeps filling its zone after
// the safe-rect changes (viewport resize, sidebar toggle, etc.).
function _reclampAll(animate = false) {
  document.querySelectorAll('.modal-content[data-_tile-zone], .research-pane[data-_tile-zone]').forEach(c => {
    const name = c.dataset._tileZone;
    if (!name) return;
    const safe = _viewportSafeRect();
    const W = safe.right - safe.left, H = safe.bottom - safe.top;
    let r;
    switch (name) {
      case 'fullscreen':     r = _fullscreenRect(); break;
      case 'maximize':       r = { left: safe.left, top: safe.top, width: W, height: H }; break;
      case 'left-half':      r = { left: safe.left, top: safe.top, width: W/2, height: H }; break;
      case 'right-half':     r = { left: safe.left + W/2, top: safe.top, width: W/2, height: H }; break;
      case 'bottom-half':    r = { left: safe.left, top: safe.top + H/2, width: W, height: H/2 }; break;
      case 'top-left':       r = { left: safe.left, top: safe.top, width: W/2, height: H/2 }; break;
      case 'top-right':      r = { left: safe.left + W/2, top: safe.top, width: W/2, height: H/2 }; break;
      case 'bottom-left':    r = { left: safe.left, top: safe.top + H/2, width: W/2, height: H/2 }; break;
      case 'bottom-right':   r = { left: safe.left + W/2, top: safe.top + H/2, width: W/2, height: H/2 }; break;
      default: return;
    }
    if (animate) {
      c.style.transition = 'left 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), top 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), width 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), height 0.22s cubic-bezier(0.34, 1.56, 0.64, 1)';
      setTimeout(() => { c.style.transition = ''; }, 250);
    }
    c.style.setProperty('left', r.left + 'px', 'important');
    c.style.setProperty('top',  r.top  + 'px', 'important');
    c.style.setProperty('width', r.width + 'px', 'important');
    c.style.setProperty('max-width', 'none', 'important');
    c.style.setProperty('height', r.height + 'px', 'important');
    c.style.setProperty('max-height', r.height + 'px', 'important');
  });
}

let _reclampPending = false;
function _reclampAllThrottled(animate) {
  if (_reclampPending) return;
  _reclampPending = true;
  requestAnimationFrame(() => {
    try { _reclampAll(animate); } finally { _reclampPending = false; }
  });
}

window.addEventListener('resize', () => _reclampAllThrottled(false));

// Watch nav edge changes so toggling hidden/right-side, or Android landscape
// cutout handling moving the icon rail, re-tiles any snapped modal that was
// anchored to the old safe-rect.
function _watchNavEdges() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  if (!sidebar && !rail) {
    // Sidebar may not be in the DOM yet during early init.
    requestAnimationFrame(_watchNavEdges);
    return;
  }
  const mo = new MutationObserver(() => _reclampAllThrottled(true));
  if (sidebar) mo.observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
  if (rail) mo.observe(rail, { attributes: true, attributeFilter: ['class', 'style'] });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _watchNavEdges);
} else {
  _watchNavEdges();
}
window.addEventListener('orientationchange', () => _reclampAllThrottled(true));
window.addEventListener('odysseus:cutoutchange', () => _reclampAllThrottled(true));
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', () => _reclampAllThrottled(false));
  window.visualViewport.addEventListener('scroll', () => _reclampAllThrottled(false));
}
if (screen.orientation && screen.orientation.addEventListener) {
  screen.orientation.addEventListener('change', () => _reclampAllThrottled(true));
}

// ── Public API for other drag sources (e.g. dragging a minimized dock chip
// to a screen edge) to reuse the same snap zones + ghost preview + apply. ──

// Show the snap-zone ghost for a point and return the zone (or null).
export function previewZoneAt(x, y, target = null) {
  if (!_isDesktop()) { _hideGhost(); _activeZone = null; return null; }
  const content = target && target.querySelector
    ? (target.querySelector('.modal-content, .research-pane') || target)
    : null;
  const zone = content ? _zoneForContent(content, x, y) : _zoneForPointer(x, y);
  if (zone) { _showGhost(zone.rect); _activeZone = zone; }
  else { _hideGhost(); _activeZone = null; }
  return zone;
}

export function clearPreview() {
  _hideGhost();
  _activeZone = null;
}

// Snap a modal (its .modal-content) into a previously-detected zone.
export function snapModalToZone(modal, zone) {
  if (!modal || !zone) return;
  const content = modal.querySelector ? (modal.querySelector('.modal-content, .research-pane') || modal) : modal;
  if (!content) return;
  if (modal.id === 'settings-modal' && zone.name !== 'right-half' && !zone.force) return;
  const rect = zone.name === 'fullscreen' ? _fullscreenRect() : zone.rect;
  _applySnap(content, rect, zone.name);
}

export function restoreModalSnap(modal) {
  if (!modal) return false;
  const content = modal.querySelector ? (modal.querySelector('.modal-content, .research-pane') || modal) : modal;
  if (!content || !content.dataset._tilePreSnap) return false;
  _unsnap(content);
  return true;
}

export {};
