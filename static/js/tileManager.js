/**
 * tileManager.js — desktop window tiling for tool modals.
 *
 * Hooks into any modal whose `.modal-header` is dragged (each tool wires its
 * own drag; we just watch pointer moves). Shows a translucent ghost preview
 * when the cursor is near a snap zone. On release, snaps the modal-content
 * to fill that zone with a springy animation.
 *
 * Snap zones:
 *   - top edge                    → maximize beside the active sidebar
 *   - left edge                   → left half
 *   - right edge                  → right half
 *
 * Mobile (≤768px) is excluded — the swipe-dismiss UX takes precedence.
 *
 * Each modal-content remembers its pre-snap geometry so dragging away restores
 * the original size.
 */

const EDGE_THRESHOLD_PX = 24;     // how close to an edge counts as "near"

// Floor the workspace may be squeezed to before we refuse to reserve more.
// Mirrors modalSnap's MIN_CHAT_WIDTH so both systems agree on "too narrow".
const MIN_WORKSPACE_PX = 380;

let _ghost = null;
let _activeZone = null;
let _tracking = null; // { content, startRect }

function _isDesktop() { return window.innerWidth > 768; }

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
  if (side === 'left') {
    try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
  }
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

function _viewportSafeRect() {
  // Reserve navigation on whichever edge it actually occupies. Treating a
  // right-side sidebar as left navigation makes leftEdge nearly viewport-wide,
  // yielding negative tile widths and sending dragged windows off-screen.
  const sidebar = document.getElementById('sidebar');
  const rail = document.querySelector('.icon-rail') || document.querySelector('#icon-rail');
  let leftEdge = 4;
  let rightEdge = window.innerWidth - 4;
  const reserve = (element, isRight) => {
    if (!element || window.getComputedStyle(element).display === 'none') return;
    const rect = element.getBoundingClientRect();
    if (!rect.width || rect.bottom <= 0 || rect.top >= window.innerHeight) return;
    if (isRight) rightEdge = Math.min(rightEdge, rect.left - 4);
    else leftEdge = Math.max(leftEdge, rect.right + 4);
  };
  if (sidebar && !sidebar.classList.contains('hidden')) {
    reserve(sidebar, sidebar.classList.contains('right-side'));
  }
  reserve(rail, rail?.classList.contains('right-side'));
  // Keep a valid workspace even during a transient resize frame.
  if (rightEdge <= leftEdge) {
    leftEdge = 4;
    rightEdge = window.innerWidth - 4;
  }
  return {
    left: leftEdge,
    top: 4,
    right: rightEdge,
    bottom: window.innerHeight - 4,
  };
}

// ── Side-zone workspace reservation ───────────────────────────────────────
// A window snapped to `left-half` / `right-half` is pinned to that edge as a
// full-height panel. It is position:fixed, so the workspace underneath keeps
// its full width and half the conversation ends up buried behind the window —
// the window "docks" but nothing else on screen reacts to it.
//
// Reserving the space fixes that: the chat gives up exactly the strip the
// window occupies, so both stay usable side by side.
//
// This deliberately uses its own body class and custom property
// (`tile-left-dock` / `tile-right-dock`, `--tile-*-w`) instead of reusing
// modalSnap's `left-dock-active` / `right-dock-active`. modalSnap owns those
// for true edge docks (email, notes), and both systems can be live at the same
// time on different sides. Separate names mean each one releases only its own
// reservation instead of clearing the other's.

const _sideZoneOf = (name) =>
  (name === 'left-half' ? 'left' : name === 'right-half' ? 'right' : null);

// Where the workspace actually begins, in viewport pixels.
//
// _viewportSafeRect().left is not that: it adds a 4px gutter after the nav and
// falls back to 4 when nothing is visible. Reserving against it leaves the
// chat overlapping the docked window by exactly that gutter, so measure the
// nav itself — the same edges _viewportSafeRect reserves, without the padding.
function _workspaceLeftEdge() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.querySelector('.icon-rail') || document.querySelector('#icon-rail');
  let x = 0;
  const take = (element) => {
    if (!element || window.getComputedStyle(element).display === 'none') return;
    const r = element.getBoundingClientRect();
    if (r.width && r.bottom > 0 && r.top < window.innerHeight) x = Math.max(x, r.right);
  };
  if (sidebar && !sidebar.classList.contains('hidden')) take(sidebar);
  take(rail);
  return x;
}

// Visible rects of every window currently tiled against `side`.
function _sideSnappedRects(side) {
  const out = [];
  document.querySelectorAll('.modal-content[data-_tile-zone], .research-pane[data-_tile-zone]')
    .forEach((c) => {
      if (_sideZoneOf(c.dataset._tileZone) !== side) return;
      if (!c.isConnected || c.classList.contains('hidden')) return;
      if (c.style.display === 'none') return;
      const r = c.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0)) return;
      out.push(r);
    });
  return out;
}

const _lastYield = { left: '', right: '' };
let _yieldObserver = null;
let _yieldRaf = 0;

function _scheduleSideYield() {
  if (_yieldRaf) return;
  _yieldRaf = requestAnimationFrame(() => {
    _yieldRaf = 0;
    try { applySideDockYield(); } catch (_) {}
  });
}

// Watch for side-tiled windows appearing/disappearing/being hidden so the
// reservation is released when the window is closed, minimized or removed.
//
// Stays attached for as long as ANY window carries tile state — not just while
// a reservation is live. A window keeps its zone while it is hidden, and
// singleton modals (extension panel, memory, gallery) are reused: reopening
// one brings the tile geometry back without going through _applySnap. Dropping
// the observer at close time would leave it looking docked with the workspace
// at full width behind it — the exact bug this exists to fix. A subtree
// observer over the whole body is still not something to leave running for a
// page that has never tiled anything, hence the guard on tile state existing
// at all.
function _syncSideYieldObserver(reserving) {
  const hasTileState =
    document.querySelectorAll('.modal-content[data-_tile-zone], .research-pane[data-_tile-zone]').length > 0;

  if (!reserving && !hasTileState) {
    if (_yieldObserver) {
      _yieldObserver.disconnect();
      _yieldObserver = null;
    }
    return;
  }
  if (_yieldObserver || typeof MutationObserver === 'undefined') return;
  _yieldObserver = new MutationObserver(_scheduleSideYield);
  _yieldObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style'],
  });
}

export function applySideDockYield() {
  const docEl = document.documentElement;
  const vw = window.innerWidth;
  const sideAnchor = _workspaceLeftEdge();
  const measured = {};
  let any = false;

  // Space the workspace has to give up: everything between the nav edge and
  // the viewport right, minus the floor we refuse to go below.
  const budget = Math.max(0, vw - sideAnchor - MIN_WORKSPACE_PX);

  // Split the space each side is asking for into what we can shrink and what
  // we cannot. A true edge dock (email, notes) belongs to modalSnap and is
  // applied through its own CSS variable — scaling it here would compute a
  // number nobody writes, and would quietly spend space that is still really
  // occupied. Tile windows are ours to size, so they absorb whatever the fixed
  // docks leave.
  const held = { left: 0, right: 0 };   // fixed: a true edge dock already holds it
  const asked = { left: 0, right: 0 };  // flexible: our own side-tiled windows

  for (const side of ['left', 'right']) {
    const rects = _sideSnappedRects(side);
    const edgeVar = side === 'right' ? '--right-dock-w' : '--left-dock-w';
    const edgePx = parseFloat(getComputedStyle(docEl).getPropertyValue(edgeVar)) || 0;

    let px = 0;
    if (rects.length) {
      px = side === 'right'
        ? Math.max(...rects.map((r) => vw - r.left))
        // Measure from the workspace's natural left edge, NOT from the chat's
        // current rect — re-measuring would compound the margin on every call
        // and creep the workspace off-screen.
        : Math.max(...rects.map((r) => r.right - sideAnchor));
      px = Math.max(0, px);
    }

    held[side] = Number.isFinite(edgePx) ? Math.max(0, edgePx) : 0;
    asked[side] = px;
    measured[`${side}HasTile`] = rects.length > 0;
  }

  // A fixed dock on one side narrows what the other side may take. Both sides
  // are clamped together: checking each on its own lets two side-tiled windows
  // (extension left, memory right) each pass and meet in the middle, leaving a
  // chat a few dozen pixels wide. Scale both by the same factor so neither one
  // is arbitrarily starved.
  const remaining = Math.max(0, budget - held.left - held.right);
  const askedTotal = asked.left + asked.right;
  if (askedTotal > remaining && askedTotal > 0) {
    const scale = remaining / askedTotal;
    asked.left = Math.floor(asked.left * scale);
    asked.right = Math.floor(asked.right * scale);
  }

  for (const side of ['left', 'right']) {
    // Never hand back less than the edge dock on this side is already holding.
    const px = measured[`${side}HasTile`] ? Math.round(Math.max(asked[side], held[side])) : 0;

    if (px > 0) {
      any = true;
      document.body.classList.add(`tile-${side}-dock`);
      const value = `${px}px`;
      // Only write on change — a redundant write mutates the style attribute
      // and would re-trigger our own observer forever.
      if (_lastYield[side] !== value) {
        _lastYield[side] = value;
        docEl.style.setProperty(`--tile-${side}-w`, value);
      }
    } else {
      document.body.classList.remove(`tile-${side}-dock`);
      if (_lastYield[side] !== '') {
        _lastYield[side] = '';
        docEl.style.removeProperty(`--tile-${side}-w`);
      }
    }
  }

  _syncSideYieldObserver(any);
  return any;
}

function _zoneForPointer(x, y) {
  const safe = _viewportSafeRect();
  const W = safe.right - safe.left;
  const H = safe.bottom - safe.top;

  // The whole top-edge target means one sidebar-aware maximize state. The previous nested
  // maximize/top-half bands made nearly identical gestures produce three
  // different heights, including a shallow half-window and a sidebar-covering
  // fullscreen state.
  if (y <= safe.top + EDGE_THRESHOLD_PX)
    return { name: 'maximize', rect: { left: safe.left, top: safe.top, width: W, height: H } };

  // Symmetric side half-snaps. The safe rect starts to the right of
  // the sidebar/rail so left-half does not cover navigation.
  if (x <= safe.left + EDGE_THRESHOLD_PX)
    return { name: 'left-half', rect: { left: safe.left, top: safe.top, width: W / 2, height: H } };
  if (x >= safe.right - EDGE_THRESHOLD_PX)
    return { name: 'right-half', rect: { left: safe.left + W / 2, top: safe.top, width: W / 2, height: H } };
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
      || modal.id === 'theme-modal')
      && zone.name !== 'maximize') return null;
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
    modal.classList.remove('modal-left-docked', 'modal-right-docked');
    if (hadLeft) _clearDockSide('left', modal);
    if (hadRight) _clearDockSide('right', modal);
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
  content.style.setProperty('left',   rect.left   + 'px', 'important');
  content.style.setProperty('top',    rect.top    + 'px', 'important');
  content.style.setProperty('width',  rect.width  + 'px', 'important');
  content.style.setProperty('height', rect.height + 'px', 'important');
  content.style.setProperty('max-height', rect.height + 'px', 'important');
  content.style.setProperty('margin', '0', 'important');
  content.style.setProperty('transform', 'none', 'important');
  content.dataset._tileZone = zoneName;
  // Side zones now push the workspace aside so the chat isn't buried under
  // the docked window. Maximize covers the whole safe rect, so it reserves
  // nothing.
  applySideDockYield();
  setTimeout(() => { content.style.transition = ''; }, 250);
}

function _unsnap(content) {
  const pre = content.dataset._tilePreSnap;
  if (!pre) return;
  // Clear the !important snap props first — Object.assign can't override them.
  ['position', 'left', 'top', 'width', 'height', 'max-height', 'margin', 'transform']
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
  // This window is no longer occupying an edge — hand the space back.
  applySideDockYield();
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
      // Migrate every retired top-edge state to the single sidebar-aware one.
      case 'fullscreen':
      case 'maximize':
      case 'top-half':
        c.dataset._tileZone = 'maximize';
        r = { left: safe.left, top: safe.top, width: W, height: H };
        break;
      case 'left-half':      r = { left: safe.left, top: safe.top, width: W/2, height: H }; break;
      case 'right-half':     r = { left: safe.left + W/2, top: safe.top, width: W/2, height: H }; break;
      case 'bottom-half':
        _unsnap(c);
        return;
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
    c.style.setProperty('height', r.height + 'px', 'important');
    c.style.setProperty('max-height', r.height + 'px', 'important');
  });
  // Zones just changed size/position (resize, sidebar toggle) — the workspace
  // reservation has to follow them or the chat keeps the stale strip.
  applySideDockYield();
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

// Watch the sidebar's class attribute so toggling hidden/right-side re-tiles
// any snapped modal that was anchored to the old safe-rect.
function _watchSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) {
    // Sidebar may not be in the DOM yet during early init.
    requestAnimationFrame(_watchSidebar);
    return;
  }
  const mo = new MutationObserver(() => _reclampAllThrottled(true));
  mo.observe(sidebar, { attributes: true, attributeFilter: ['class'] });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _watchSidebar);
} else {
  _watchSidebar();
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

export function _zoneForPointerForTests(x, y) {
  return _zoneForPointer(x, y);
}

export function _zoneForContentForTests(content, x, y) {
  return _zoneForContent(content, x, y);
}

// Snap a modal (its .modal-content) into a previously-detected zone.
export function snapModalToZone(modal, zone) {
  if (!modal || !zone) return;
  const content = modal.querySelector ? (modal.querySelector('.modal-content, .research-pane') || modal) : modal;
  if (!content) return;
  // Old callers still request `fullscreen`; keep them from recreating the
  // retired viewport-covering or half-height top states.
  if (zone.name === 'fullscreen' || zone.name === 'top-half') {
    const safe = _viewportSafeRect();
    zone = {
      name: 'maximize',
      rect: {
        left: safe.left,
        top: safe.top,
        width: safe.right - safe.left,
        height: safe.bottom - safe.top,
      },
    };
  }
  if (modal.id === 'settings-modal' && zone.name !== 'right-half') return;
  _applySnap(content, zone.rect, zone.name);
}

export {};
