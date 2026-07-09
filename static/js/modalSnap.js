// Four-edge snap docking for draggable modals.
//
// Dragging a modal to the left, right, top, or bottom edge reserves that
// portion of the workspace and reflows the chat into the remaining area.
// Horizontal docks use a viewport-fraction width; vertical docks default to
// a 50/50 split. While docked:
//   - the modal-content is fixed to the selected workspace edge
//   - body gets a side-specific class + size variable for the chat reserve
//   - if the remaining chat width would drop under 380px, the wide
//     sidebar auto-collapses to the icon rail (mirrors notes-view UX)
//
// Drag-away from the owning edge un-docks back to a centered window —
// the same restore values the snap-to-top exit path uses.

// Wider snap zone than the top-snap fullscreen (6px) — the right edge
// is harder to hit precisely since most users drag broadly toward the
// side rather than aiming at a 1px line. 60px feels generous without
// false-positive triggers from casual repositioning.
const SNAP_PX = 60;
const UNSNAP_PX = 80;
const MIN_CHAT_WIDTH = 380;
const MIN_CHAT_HEIGHT = 260;
const EMAIL_DOC_SPLIT_WIDTH_KEY = 'odysseus-email-doc-split-width';
const EDGE_DOCK_WIDTH_KEY_PREFIX = 'odysseus-edge-dock-width';
const EDGE_DOCK_HEIGHT_KEY_PREFIX = 'odysseus-edge-dock-height';
const MIN_EDGE_DOCK_WIDTH = 320;
const MIN_EDGE_DOCK_HEIGHT = 220;
const MOBILE_DOCK_BREAKPOINT = 768;
const EDGE_DOCK_RESIZE_HANDLE_PX = 10;
const DOCK_SIDES = ['left', 'right', 'top', 'bottom'];

let _edgeDockHandlePositioner = null;

// Root-level accessibility scaling uses CSS `zoom`. Pointer and measured
// coordinates are visual pixels, while fixed-position styles are authored in
// unzoomed layout pixels. Keep dock geometry in one coordinate system so the
// preview, committed panel, reserve, and resize seam remain identical.
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

function _viewportWidth() { return window.innerWidth / _uiScaleFactor(); }
function _viewportHeight() { return window.innerHeight / _uiScaleFactor(); }
function _layoutCoordinate(value) { return value / _uiScaleFactor(); }

function _layoutRect(rect) {
  if (!rect) return rect;
  const scale = _uiScaleFactor();
  return {
    left: rect.left / scale,
    top: rect.top / scale,
    right: rect.right / scale,
    bottom: rect.bottom / scale,
    width: rect.width / scale,
    height: rect.height / scale,
  };
}

function _positionEdgeDockResizeHandles() {
  try { _edgeDockHandlePositioner && _edgeDockHandlePositioner(); } catch (_) {}
}

function _dockClassForSide(side) {
  return `modal-${side}-docked`;
}

function _bodyDockClassForSide(side) {
  return `${side}-dock-active`;
}

function _dockSizeProperty(side) {
  return (side === 'left' || side === 'right')
    ? `--${side}-dock-w`
    : `--${side}-dock-h`;
}

function _dockReserveProperty(side) {
  return (side === 'left' || side === 'right')
    ? `--${side}-dock-reserve-w`
    : `--${side}-dock-reserve-h`;
}

function _isActiveDockOwner(owner) {
  if (!owner || owner.isConnected === false) return false;
  if (owner.classList?.contains('hidden') || owner.classList?.contains('modal-minimized')) return false;
  if (owner.style?.display === 'none') return false;
  const content = _resolveDockNodes(owner)?.content;
  if (!content || content.isConnected === false || content._dockSuspended) return false;
  if (content.classList?.contains('hidden') || content.style?.display === 'none') return false;
  return true;
}

function _otherActiveDockOwners(side, owner) {
  const cls = _dockClassForSide(side);
  return Array.from(document.querySelectorAll(`.${cls}`)).filter((el) => {
    if (!el || el === owner) return false;
    if (owner && el.contains && el.contains(owner)) return false;
    if (owner && owner.contains && owner.contains(el)) return false;
    return _isActiveDockOwner(el);
  });
}

function _hasOtherDockedWindow(side, owner) {
  return _otherActiveDockOwners(side, owner).length > 0;
}

function _hasOtherSidebarDock(owner) {
  return ['left', 'right'].some((side) => _hasOtherDockedWindow(side, owner));
}

export function clearDockSide(side, owner = null) {
  if (!DOCK_SIDES.includes(side)) return;
  const survivors = _otherActiveDockOwners(side, owner);
  if (survivors.length) {
    const survivor = survivors[survivors.length - 1];
    const content = _resolveDockNodes(survivor)?.content;
    const rect = content?.getBoundingClientRect ? _layoutRect(content.getBoundingClientRect()) : null;
    const horizontal = side === 'left' || side === 'right';
    const inlineSize = parseFloat(horizontal ? content?.style?.width : content?.style?.height);
    const measuredSize = horizontal ? rect?.width : rect?.height;
    const size = Number.isFinite(inlineSize) && inlineSize > 0 ? inlineSize : measuredSize;
    if (Number.isFinite(size) && size > 0) {
      _setDockVars(side, side === 'left' && document.body.classList.contains('email-doc-split-active') ? 0 : size);
    }
    return;
  }
  document.body.classList.remove(_bodyDockClassForSide(side));
  document.documentElement.style.removeProperty(_dockSizeProperty(side));
  document.documentElement.style.removeProperty(_dockReserveProperty(side));
  if (side === 'left') {
    try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
  }
  _positionEdgeDockResizeHandles();
}

// Default dock width: ~38% of viewport, clamped to a reasonable band.
function _defaultDockWidth() {
  return Math.min(640, Math.max(420, Math.round(_viewportWidth() * 0.38)));
}

function _dockWidthStorageKey(modal, content, side) {
  const id = modal?.id || content?.id || content?.dataset?.modalId || '';
  return id ? `${EDGE_DOCK_WIDTH_KEY_PREFIX}:${side}:${id}` : null;
}

function _storedDockWidth(modal, content, side) {
  const key = _dockWidthStorageKey(modal, content, side);
  if (!key) return null;
  try {
    const n = parseFloat(localStorage.getItem(key) || '');
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch (_) {
    return null;
  }
}

function _saveDockWidth(modal, content, side, width) {
  const key = _dockWidthStorageKey(modal, content, side);
  if (!key) return;
  try { localStorage.setItem(key, String(Math.round(width))); } catch (_) {}
}

function _dockHeightStorageKey(side) {
  return (side === 'top' || side === 'bottom')
    ? `${EDGE_DOCK_HEIGHT_KEY_PREFIX}:${side}:shared`
    : null;
}

function _storedDockHeight(side) {
  const key = _dockHeightStorageKey(side);
  if (!key) return null;
  try {
    const n = parseFloat(localStorage.getItem(key) || '');
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch (_) {
    return null;
  }
}

function _saveDockHeight(side, height) {
  const key = _dockHeightStorageKey(side);
  if (!key) return;
  try { localStorage.setItem(key, String(Math.round(height))); } catch (_) {}
}

function _minEdgeDockWidth() {
  return _viewportWidth() < 900 ? 280 : MIN_EDGE_DOCK_WIDTH;
}

function _activeDockWidth(side) {
  if (side !== 'left' && side !== 'right') return 0;
  const cls = side === 'left' ? 'left-dock-active' : 'right-dock-active';
  if (!document.body.classList.contains(cls)) return 0;
  const prop = side === 'left' ? '--left-dock-w' : '--right-dock-w';
  const raw = getComputedStyle(document.documentElement).getPropertyValue(prop);
  const n = parseFloat(raw || '');
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function _activeDockHeight(side) {
  if (side !== 'top' && side !== 'bottom') return 0;
  if (!document.body.classList.contains(_bodyDockClassForSide(side))) return 0;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(_dockSizeProperty(side));
  const n = parseFloat(raw || '');
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function _oppositeDockWidth(side, owner = null) {
  const opposite = side === 'left' ? 'right' : 'left';
  if (owner?.classList?.contains(_dockClassForSide(opposite))
      && !_hasOtherDockedWindow(opposite, owner)) return 0;
  return _activeDockWidth(opposite);
}

function _oppositeDockHeight(side, owner = null) {
  const opposite = side === 'top' ? 'bottom' : 'top';
  if (owner?.classList?.contains(_dockClassForSide(opposite))
      && !_hasOtherDockedWindow(opposite, owner)) return 0;
  return _activeDockHeight(opposite);
}

function _clampDockWidthToSpace(width, min, max) {
  const floor = Math.min(min, Math.max(220, Math.round(max)));
  const ceiling = Math.max(floor, Math.round(max));
  return Math.min(ceiling, Math.max(floor, Math.round(width)));
}

function _clampRightDockWidth(width) {
  const min = _minEdgeDockWidth();
  const space = _dockWorkspaceEdges();
  const leftDockW = _activeDockWidth('left');
  const maxByChat = space.width - leftDockW - MIN_CHAT_WIDTH;
  const max = Math.min(Math.round(_viewportWidth() * 0.82), maxByChat);
  return _clampDockWidthToSpace(width, min, max);
}

function _clampLeftDockWidth(width, left = _leftNavRight()) {
  const min = _minEdgeDockWidth();
  const rightDockW = _activeDockWidth('right');
  const right = Math.max(left, _viewportWidth() - _rightNavWidth());
  const available = Math.max(0, right - left - rightDockW);
  const max = Math.min(Math.round(available * 0.82), available - MIN_CHAT_WIDTH);
  return _clampDockWidthToSpace(width, min, max);
}

function _resolveRightDockWidth(modal, content) {
  return _clampRightDockWidth(content?._userDockWidth || _storedDockWidth(modal, content, 'right') || _defaultDockWidth());
}

function _resolveLeftDockWidth(content, left = _leftNavRight()) {
  return _clampLeftDockWidth(content?._userDockWidth || _storedDockWidth(content?._dockOwner, content, 'left') || _resolveEmailDocSplitWidth(content, left), left);
}

function _verticalDockBounds(side, owner = null) {
  const available = Math.max(0, _viewportHeight() - _oppositeDockHeight(side, owner));
  if (available < MIN_EDGE_DOCK_HEIGHT + MIN_CHAT_HEIGHT) {
    const isActiveOwner = owner?.classList?.contains(_dockClassForSide(side));
    if (isActiveOwner && available > 0) {
      const compactChat = available > MIN_CHAT_HEIGHT
        ? MIN_CHAT_HEIGHT
        : Math.floor(available / 2);
      const maxDock = Math.max(1, available - compactChat);
      const minDock = Math.min(MIN_EDGE_DOCK_HEIGHT, maxDock);
      return { min: minDock, max: maxDock, available };
    }
    return { min: 0, max: 0, available };
  }
  return {
    min: MIN_EDGE_DOCK_HEIGHT,
    max: available - MIN_CHAT_HEIGHT,
    available,
  };
}

function _clampVerticalDockHeight(height, side, owner = null) {
  const bounds = _verticalDockBounds(side, owner);
  if (bounds.max <= 0) return 0;
  const requested = Number.isFinite(height) && height > 0
    ? height
    : Math.round(bounds.available / 2);
  return _clampDockWidthToSpace(requested, bounds.min, bounds.max);
}

function _resolveVerticalDockHeight(content, side, owner = null) {
  return _clampVerticalDockHeight(
    content?._userDockHeight || _storedDockHeight(side) || Math.round(_viewportHeight() / 2),
    side,
    owner,
  );
}

function _setDockVars(side, size) {
  document.documentElement.style.setProperty(_dockSizeProperty(side), size + 'px');
  document.documentElement.style.setProperty(_dockReserveProperty(side), size + 'px');
}

function _hasFinePointer() {
  if (typeof window.matchMedia !== 'function') return window.innerWidth > MOBILE_DOCK_BREAKPOINT;
  return window.matchMedia('(pointer: fine)').matches
    || window.matchMedia('(any-pointer: fine)').matches;
}

function _canUseDockSide(side) {
  return (side === 'left' || side === 'right')
    || (_hasFinePointer() && window.innerWidth > MOBILE_DOCK_BREAKPOINT);
}

function _isEmailDockOwner(owner) {
  const id = owner?.id || '';
  return id === 'email-lib-modal' || id.startsWith('email-reader-') || owner?.classList?.contains('email-window-modal');
}

function _clampDockWidthInWorkspace(width, available, allowCompact = false) {
  const min = _minEdgeDockWidth();
  if (available < min + MIN_CHAT_WIDTH) {
    if (!allowCompact || available <= 0) return 0;
    const compactChat = available > MIN_CHAT_WIDTH
      ? MIN_CHAT_WIDTH
      : Math.floor(available / 2);
    const maxDock = Math.max(1, available - compactChat);
    const compactMin = Math.min(min, maxDock);
    return _clampDockWidthToSpace(
      width,
      compactMin,
      maxDock,
    );
  }
  const max = Math.max(min, Math.min(Math.round(available * 0.82), available - MIN_CHAT_WIDTH));
  return _clampDockWidthToSpace(width, min, max);
}

export function edgeDockPreviewRect(modal, side = 'right') {
  if (!DOCK_SIDES.includes(side) || !_canUseDockSide(side)) return null;
  const content = _resolveDockNodes(modal)?.content || null;
  let space = _dockWorkspaceEdges();

  if (side === 'left') {
    const isActiveOwner = !!modal?.classList?.contains(_dockClassForSide(side));
    space = isActiveOwner ? _dockWorkspaceEdges() : _railWorkspaceEdges();
    const requested = content?._userDockWidth
      || _storedDockWidth(modal, content, side)
      || _resolveEmailDocSplitWidth(content, space.left);
    const width = _clampDockWidthInWorkspace(
      requested,
      Math.max(0, space.width - _oppositeDockWidth(side, modal)),
      !!modal?.classList?.contains(_dockClassForSide(side)),
    );
    if (width <= 0) return null;
    return { left: space.left, top: 0, width, height: _viewportHeight() };
  }

  if (side === 'right') {
    const isActiveOwner = !!modal?.classList?.contains(_dockClassForSide(side));
    const requested = content?._userDockWidth
      || _storedDockWidth(modal, content, side)
      || _defaultDockWidth();
    if (!isActiveOwner && _shouldAutoCollapseSidebar(Math.max(requested, _minEdgeDockWidth()))) {
      space = _railWorkspaceEdges();
    }
    const width = _clampDockWidthInWorkspace(
      requested,
      Math.max(0, space.width - _oppositeDockWidth(side, modal)),
      !!modal?.classList?.contains(_dockClassForSide(side)),
    );
    if (width <= 0) return null;
    return { left: space.right - width, top: 0, width, height: _viewportHeight() };
  }

  const height = _resolveVerticalDockHeight(content, side, modal);
  if (height <= 0) return null;
  return {
    left: space.left,
    top: side === 'top' ? 0 : _viewportHeight() - height,
    width: space.width,
    height,
  };
}

function _showSnapHint(on, side = 'right', modal = null) {
  const cls = `modal-snap-hint-${side}`;
  let hint = document.querySelector('.' + cls);
  if (!on) {
    if (hint) hint.remove();
    return;
  }
  document.querySelectorAll('.modal-snap-hint').forEach((el) => {
    if (el !== hint) el.remove();
  });
  document.getElementById('tile-ghost')?.classList.remove('visible');
  const rect = edgeDockPreviewRect(modal, side);
  if (!rect) return;
  if (!hint) {
    hint = document.createElement('div');
    hint.className = 'modal-snap-hint ' + cls;
    document.body.appendChild(hint);
  }
  hint.style.cssText = [
    'position:fixed',
    `left:${Math.round(rect.left)}px`,
    `top:${Math.round(rect.top)}px`,
    `width:${Math.round(rect.width)}px`,
    `height:${Math.round(rect.height)}px`,
    'box-sizing:border-box',
    'background:color-mix(in srgb, var(--accent, var(--red)) 16%, transparent)',
    'border:2px solid color-mix(in srgb, var(--accent, var(--red)) 64%, transparent)',
    'border-radius:0',
    'box-shadow:inset 0 0 28px color-mix(in srgb, var(--accent, var(--red)) 8%, transparent), 0 0 18px color-mix(in srgb, var(--accent, var(--red)) 18%, transparent)',
    'z-index:9998',
    'pointer-events:none',
    'transition:left 0.12s ease, top 0.12s ease, width 0.12s ease, height 0.12s ease, opacity 0.12s ease',
  ].join(';');
}

// Check if the body's current chat area would be narrower than the
// MIN_CHAT_WIDTH floor after reserving dockW pixels on the right. Returns
// true if the wide sidebar should be collapsed to the rail.
function _shouldAutoCollapseSidebar(dockW) {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  if (!sidebar) return false;
  const sidebarHidden = sidebar.classList.contains('hidden');
  if (sidebarHidden) return false;
  const sb = _layoutRect(sidebar.getBoundingClientRect()).width || 0;
  const rl = (rail && window.getComputedStyle(rail).display !== 'none')
    ? _layoutRect(rail.getBoundingClientRect()).width
    : 0;
  const remaining = _viewportWidth() - sb - rl - _activeDockWidth('left') - dockW;
  return remaining < MIN_CHAT_WIDTH;
}

function _isElementVisible(el) {
  if (!el || !el.isConnected) return false;
  const style = window.getComputedStyle(el);
  return style.display !== 'none' && style.visibility !== 'hidden';
}

function _isRightAnchoredRect(rect) {
  return !!rect && rect.width > 0 && rect.right >= _viewportWidth() - 1;
}

// Right edge (px) of whatever left navigation is currently showing — the
// expanded sidebar if visible, otherwise the icon rail. Used to anchor the
// left dock so it always sits flush to the right of the nav.
function _leftNavRight() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  let x = 0;
  if (sidebar && !sidebar.classList.contains('hidden') && _isElementVisible(sidebar)) {
    const r = _layoutRect(sidebar.getBoundingClientRect());
    if (r.width && !sidebar.classList.contains('right-side') && !_isRightAnchoredRect(r)) {
      x = Math.max(x, r.right);
    }
  }
  if (rail && _isElementVisible(rail)) {
    const r = _layoutRect(rail.getBoundingClientRect());
    if (r.width && !rail.classList.contains('right-side') && !_isRightAnchoredRect(r)) {
      x = Math.max(x, r.right);
    }
  }
  return x;
}

function _rightNavWidth() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  let width = 0;
  if (sidebar && !sidebar.classList.contains('hidden') && _isElementVisible(sidebar)) {
    const r = _layoutRect(sidebar.getBoundingClientRect());
    if (sidebar.classList.contains('right-side') || _isRightAnchoredRect(r)) {
      width = Math.max(width, r.width);
    }
  }
  if (rail && _isElementVisible(rail)) {
    const r = _layoutRect(rail.getBoundingClientRect());
    if (rail.classList.contains('right-side') || _isRightAnchoredRect(r)) {
      width = Math.max(width, r.width);
    }
  }
  return Math.round(width);
}

function _dockWorkspaceEdges() {
  const left = _leftNavRight();
  const right = Math.max(left, _viewportWidth() - _rightNavWidth());
  return { left, right, width: Math.max(0, right - left) };
}

// Side docks collapse the expanded navigation before anchoring. Predict that
// post-collapse rail geometry so the preview is exactly the committed area.
function _railWorkspaceEdges() {
  const rail = document.getElementById('icon-rail');
  const measured = rail && _layoutRect(rail.getBoundingClientRect());
  let width = measured?.width || 0;
  if (!width && rail) {
    const cssWidth = parseFloat(window.getComputedStyle(rail).width || '');
    if (Number.isFinite(cssWidth)) width = cssWidth;
  }
  if (!rail || width <= 0) return _dockWorkspaceEdges();
  const onRight = rail.classList.contains('right-side')
    || document.body.classList.contains('hamburger-right');
  const left = onRight ? 0 : width;
  const right = _viewportWidth() - (onRight ? width : 0);
  return { left, right, width: Math.max(0, right - left) };
}

function _clampEmailDocSplitWidth(width, left = _leftNavRight()) {
  const available = Math.max(0, _viewportWidth() - left);
  if (!available) return 0;
  const compact = available < 760;
  const minEmail = compact ? 260 : 340;
  const minDoc = compact ? 260 : 360;
  const maxEmail = Math.max(minEmail, available - minDoc);
  return Math.min(maxEmail, Math.max(minEmail, Math.round(width)));
}

function _storedEmailDocSplitWidth() {
  try {
    const raw = localStorage.getItem(EMAIL_DOC_SPLIT_WIDTH_KEY);
    const n = parseFloat(raw || '');
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch (_) {
    return null;
  }
}

function _saveEmailDocSplitWidth(width) {
  try { localStorage.setItem(EMAIL_DOC_SPLIT_WIDTH_KEY, String(Math.round(width))); } catch (_) {}
}

function _disconnectLeftDockObservers(content) {
  if (!content?._leftDockNavObs) return;
  const obs = content._leftDockNavObs;
  try { obs.navObs && obs.navObs.disconnect(); } catch (_) {}
  try { obs.bodyObs && obs.bodyObs.disconnect(); } catch (_) {}
  try { obs.disconnectDocObs && obs.disconnectDocObs(); } catch (_) {}
  try { window.removeEventListener('resize', obs.reanchor); } catch (_) {}
  delete content._leftDockNavObs;
}

function _applyEmailDocSplitGeometry(left, emailWidth) {
  const x = left + emailWidth;
  document.documentElement.style.setProperty('--email-doc-split-left-x', `${left}px`);
  document.documentElement.style.setProperty('--email-doc-split-email-w', `${emailWidth}px`);
  document.documentElement.style.setProperty('--email-doc-split-right-x', `${x}px`);

  // emailLibrary.js pins the document pane with inline !important styles
  // after opening a document beside a snapped email. Update that inline
  // geometry too, otherwise the email resizes but the document stays put.
  const docPane = document.getElementById('doc-editor-pane');
  if (!docPane || window.innerWidth <= 768) return;
  docPane.style.setProperty('position', 'fixed', 'important');
  docPane.style.setProperty('left', `${x}px`, 'important');
  docPane.style.setProperty('right', 'var(--right-dock-reserve-w, var(--right-dock-w, 0px))', 'important');
  docPane.style.setProperty('top', 'var(--top-dock-reserve-h, var(--top-dock-h, 0px))', 'important');
  docPane.style.setProperty('bottom', 'var(--bottom-dock-reserve-h, var(--bottom-dock-h, 0px))', 'important');
  docPane.style.setProperty('width', 'auto', 'important');
  docPane.style.setProperty('max-width', 'none', 'important');
  docPane.style.setProperty('height', 'auto', 'important');
  docPane.style.setProperty('z-index', '260', 'important');
  docPane.style.setProperty('transform', 'none', 'important');
}

function _clearEmailDocSplitGeometry() {
  document.body.classList.remove('email-doc-split-active');
  document.documentElement.style.removeProperty('--email-doc-split-left-x');
  document.documentElement.style.removeProperty('--email-doc-split-email-w');
  document.documentElement.style.removeProperty('--email-doc-split-right-x');
  const docPane = document.getElementById('doc-editor-pane');
  if (!docPane) return;
  [
    'position', 'left', 'right', 'top', 'bottom', 'width', 'max-width',
    'height', 'z-index', 'transform',
  ].forEach(prop => docPane.style.removeProperty(prop));
}

function _resolveEmailDocSplitWidth(content, left) {
  const available = Math.max(0, _viewportWidth() - left);
  const fallback = Math.max(440, available * 0.55);
  const requested = content?._emailDocSplitUserW || _storedEmailDocSplitWidth() || fallback;
  return _clampEmailDocSplitWidth(requested, left);
}

// Position a left-docked window flush against the current left nav, covering
// the chat area. Re-run whenever the sidebar is toggled so the window slides
// to follow the nav instead of being covered by it.
//
// Also: if the document editor pane is rendered to the right of the chat
// area, cap the email's right edge to stop just before it so the two share
// the row instead of overlapping. Pure geometry read — no CSS class changes
// (the previous attempt that flipped body classes here caused layout thrash
// and broke the whole tab).
function _anchorLeftDock(content) {
  if (!content || content._dockSide !== 'left') return false;
  const docView = document.body.classList.contains('doc-view');
  const preview = docView ? null : edgeDockPreviewRect(content._dockOwner, 'left');
  if (!docView && !preview) return false;
  const left = preview?.left ?? _leftNavRight();
  const w = docView
    ? _resolveEmailDocSplitWidth(content, left)
    : preview.width;
  content.style.left = left + 'px';
  content.style.width = w + 'px';
  content.style.maxWidth = w + 'px';
  // If a document is also open, drive the existing email/doc-split CSS rule
  // (style.css `body.email-doc-split-active.doc-view .doc-editor-pane`) so
  // the doc-pane becomes position:fixed starting at the email's right edge.
  // No flex/max-width fighting; the doc just owns the right side from the
  // email's right edge to the viewport edge — they touch flush, no gap.
  const docOpen = document.body.classList.contains('doc-view') && _isEmailDockOwner(content._dockOwner);
  if (docOpen) {
    if (!document.body.classList.contains('email-doc-split-active')) {
      document.body.classList.add('email-doc-split-active');
    }
    _setDockVars('left', 0);
    _applyEmailDocSplitGeometry(left, w);
  } else if (document.body.classList.contains('email-doc-split-active')) {
    _clearEmailDocSplitGeometry();
  } else {
    _setDockVars('left', w);
  }
  return true;
}

export function collapseSidebarToRail() { return _collapseSidebarToRail(); }
function _collapseSidebarToRail() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  if (!sidebar || !rail) return;
  // Mark the collapse as route/dock-driven so the paired restore in
  // app.js (window._restoreSidebarIfRouteCollapsed) knows it owns the
  // un-collapse. Same marker the /email and /notes openers use — they
  // can't both be active at once so no conflict.
  if (!sidebar.classList.contains('hidden')) {
    document.body.dataset.routeCollapsedSidebar = '1';
  }
  sidebar.classList.add('hidden');
  rail.classList.remove('rail-hidden');
  try { window.syncRailSide && window.syncRailSide(); } catch (_) {}
}

// Resolve the dock target. For .modal containers, the inner .modal-content
// is what we position; for standalone panes (research, compare, etc.) the
// passed element itself is both the container and the content. Returns
// {modal, content} or null when nothing usable was passed in.
function _resolveDockNodes(target) {
  if (!target) return null;
  const content = target.querySelector
    ? (target.querySelector('.modal-content') || target)
    : target;
  return { modal: target, content };
}

// Apply edge dock state to a modal/pane on any workspace edge.
export function applyEdgeDock(modal, side = 'right', dockClass) {
  if (!DOCK_SIDES.includes(side) || !_canUseDockSide(side)) return 0;
  if (!dockClass) dockClass = _dockClassForSide(side);
  return _applyDockInternal(modal, side, dockClass);
}

// Backwards-compat: existing callers use applyRightDock for right snaps.
export function applyRightDock(modal, dockClass = 'modal-right-docked') {
  return _applyDockInternal(modal, 'right', dockClass);
}

function _applyDockInternal(modal, side, dockClass) {
  if (!DOCK_SIDES.includes(side) || !_canUseDockSide(side)) return 0;
  const nodes = _resolveDockNodes(modal);
  if (!nodes) return 0;
  const content = nodes.content;
  if (!content) return 0;
  // Resolve the exact target before mutating any dock classes. A second dock
  // is rejected when it cannot coexist with the opposite dock while keeping
  // both the modal and chat above their minimum usable dimensions.
  const targetRect = edgeDockPreviewRect(modal, side);
  if (!targetRect) return 0;
  // If the modal is currently docked on the OTHER side (e.g. the user
  // manually docked it right, then a reply re-docks it left), clear that
  // side's class + body push first. Otherwise both sides' state coexist —
  // the old dock keeps pushing/overlapping and the reply doc opens beneath
  // the still-docked window. We keep _preDockSnapshot (the guard below skips
  // re-capturing) so un-dock still restores the original floating geometry.
  // Guarded on the other-side class so a normal first dock still snapshots
  // the floating window's real left/right inline styles below.
  let switchedSide = false;
  for (const otherSide of DOCK_SIDES) {
    if (otherSide === side) continue;
    const otherClass = _dockClassForSide(otherSide);
    if (!modal.classList.contains(otherClass)) continue;
    modal.classList.remove(otherClass);
    clearDockSide(otherSide, modal);
    switchedSide = true;
  }
  if (switchedSide) {
    content.style.left = '';
    content.style.top = '';
    content.style.right = '';
    content.style.bottom = '';
  }
  // Snapshot the actual rendered rect + inline styles so un-dock can
  // restore the exact same floating window the user had before. Without
  // this, a window the user had carefully resized would snap back to
  // some 720×85vh default — feels like the dock ate their layout.
  if (!content._preDockSnapshot) {
    const r = _layoutRect(content.getBoundingClientRect());
    content._preDockSnapshot = {
      rect: { left: r.left, top: r.top, width: r.width, height: r.height },
      style: {
        position: content.style.position,
        left: content.style.left,
        top: content.style.top,
        right: content.style.right,
        bottom: content.style.bottom,
        width: content.style.width,
        maxWidth: content.style.maxWidth,
        height: content.style.height,
        maxHeight: content.style.maxHeight,
        minHeight: content.style.minHeight,
        minHeightPriority: content.style.getPropertyPriority?.('min-height') || '',
        borderRadius: content.style.borderRadius,
        transform: content.style.transform,
        margin: content.style.margin,
      },
      // Track whether we collapsed the wide sidebar — only restore it
      // on un-dock if the dock was responsible for the collapse.
      collapsedSidebar: false,
    };
  }
  modal.classList.add(dockClass);
  content.style.position = 'fixed';
  content.style.borderRadius = '0';
  content.style.transform = 'none';
  content.style.margin = '0';
  let dockSize = 0;
  if (side === 'left') {
    // Left dock: collapse the sidebar to the icon rail, then pin the window
    // beside the rail. Normal left docks reserve their width so chat shrinks;
    // the email+document split keeps its existing overlay geometry.
    _collapseSidebarToRail();
    content._preDockSnapshot.collapsedSidebar = true;
    content.style.right = 'auto';
    content._dockSide = 'left';
    content._dockOwner = modal;
    _anchorLeftDock(content);
    dockSize = parseFloat(content.style.width) || 0;
    content.style.top = '0';
    content.style.bottom = '0';
    content.style.height = _viewportHeight() + 'px';
    content.style.maxHeight = _viewportHeight() + 'px';
    document.body.classList.add('left-dock-active');
    _setDockVars('left', document.body.classList.contains('email-doc-split-active') ? 0 : dockSize);
    // Re-anchor the email when the sidebar is toggled (expanded/collapsed) so
    // the nav slides the window over instead of growing on top of it. Also
    // re-anchor when the document editor pane appears/disappears (signaled by
    // body.doc-view) AND when the user drags the doc divider to resize it
    // (ResizeObserver) so the email shrinks/grows inversely to keep the two
    // sharing the row cleanly.
    if (!content._leftDockNavObs && typeof MutationObserver !== 'undefined') {
      const sidebar = document.getElementById('sidebar');
      const _doAnchor = () => {
        if (modal.classList.contains(dockClass)) _anchorLeftDock(content);
      };
      const reanchor = () => {
        if (!modal.classList.contains(dockClass)) return;
        _doAnchor();
        // Multi-stage settle: the dock-flip + sidebar collapse + doc mount
        // each have their own transition timing (160ms / ~240ms / variable).
        // Re-measure at each plausible settle point so the email lands flush
        // against the doc's FINAL position, not a mid-transition snapshot.
        requestAnimationFrame(_doAnchor);
        setTimeout(_doAnchor, 80);
        setTimeout(_doAnchor, 250);
        setTimeout(_doAnchor, 500);
      };
      const navObs = new MutationObserver(reanchor);
      if (sidebar) navObs.observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
      // Only react to doc-view toggling — NOT to every body attribute mutation.
      // Listening broadly caused thrashing last time and crashed the tab.
      let _lastDocView = document.body.classList.contains('doc-view');
      const bodyObs = new MutationObserver(() => {
        const cur = document.body.classList.contains('doc-view');
        if (cur !== _lastDocView) {
          _lastDocView = cur;
          reanchor();
          // Rebind the resize observer — the doc pane gets created/destroyed
          // when doc-view flips, so the previous target may be stale.
          _bindDocResizeObs();
        }
      });
      bodyObs.observe(document.body, { attributes: true, attributeFilter: ['class'] });

      // ResizeObserver on the current .doc-editor-pane so dragging its
      // divider live-reflows the email's right edge. Also observe
      // #chat-container — its width changes when the sidebar collapses,
      // when right-dock padding drains, or when doc content paint reflows
      // the row, all of which shift the doc pane's left edge without
      // necessarily resizing the doc pane itself.
      let docResizeObs = null;
      let chatResizeObs = null;
      const _bindDocResizeObs = () => {
        if (docResizeObs) { try { docResizeObs.disconnect(); } catch (_) {} docResizeObs = null; }
        if (chatResizeObs) { try { chatResizeObs.disconnect(); } catch (_) {} chatResizeObs = null; }
        if (typeof ResizeObserver === 'undefined') return;
        const docPane = document.querySelector('.doc-editor-pane');
        if (docPane) {
          docResizeObs = new ResizeObserver(reanchor);
          docResizeObs.observe(docPane);
        }
        const chatPane = document.getElementById('chat-container');
        if (chatPane) {
          chatResizeObs = new ResizeObserver(reanchor);
          chatResizeObs.observe(chatPane);
        }
      };
      _bindDocResizeObs();

      window.addEventListener('resize', reanchor);
      content._leftDockNavObs = {
        navObs,
        bodyObs,
        reanchor,
        disconnectDocObs: () => {
          try { docResizeObs && docResizeObs.disconnect(); } catch (_) {}
          try { chatResizeObs && chatResizeObs.disconnect(); } catch (_) {}
        },
      };
    }
  } else if (side === 'right') {
    dockSize = targetRect.width;
    if (_shouldAutoCollapseSidebar(dockSize)) {
      _collapseSidebarToRail();
      content._preDockSnapshot.collapsedSidebar = true;
    }
    content.style.left = 'auto';
    content.style.right = _rightNavWidth() + 'px';
    content.style.top = '0';
    content.style.bottom = '0';
    content.style.width = dockSize + 'px';
    content.style.maxWidth = dockSize + 'px';
    content.style.height = _viewportHeight() + 'px';
    content.style.maxHeight = _viewportHeight() + 'px';
    document.body.classList.add('right-dock-active');
    _setDockVars('right', dockSize);
  } else {
    const rect = targetRect;
    dockSize = rect.height;
    content.style.setProperty('min-height', '0', 'important');
    content.style.left = rect.left + 'px';
    content.style.right = (_viewportWidth() - rect.left - rect.width) + 'px';
    content.style.top = side === 'top' ? '0' : 'auto';
    content.style.bottom = side === 'bottom' ? '0' : 'auto';
    content.style.width = rect.width + 'px';
    content.style.maxWidth = rect.width + 'px';
    content.style.height = dockSize + 'px';
    content.style.maxHeight = dockSize + 'px';
    document.body.classList.add(_bodyDockClassForSide(side));
    _setDockVars(side, dockSize);
  }
  content._dockSide = side;
  content._dockOwner = modal;
  _positionEdgeDockResizeHandles();
  // Watch for the docked modal disappearing (removed from DOM or hidden
  // via .hidden class) and clean up the body padding + sidebar in that
  // case. Without this, closing a docked window leaves a phantom strip
  // of empty space on the right because nothing tells the body to drop
  // its padding-right.
  if (!modal._dockCloseWatcher && typeof MutationObserver !== 'undefined') {
    const onGone = () => _onDockedModalGone(modal, dockClass);
    // Watch the modal for: the `.hidden` class flip, an inline
    // `display:none` (how the draggable modals — calendar, plan, workspace,
    // etc. — actually close), and parent removal. Without the `style` filter
    // a display:none close left the body's dock padding on, so the chat
    // stayed shifted after the docked modal was closed.
    const _isGone = () => !modal.isConnected
      || modal.classList.contains('hidden')
      || modal.style.display === 'none';
    const obs = new MutationObserver(() => { if (_isGone()) onGone(); });
    obs.observe(modal, { attributes: true, attributeFilter: ['class', 'style'] });
    // A second observer catches DOM removal — childList on the parent
    // is the reliable signal for `.remove()` / `.removeChild()` calls.
    if (modal.parentNode) {
      const parentObs = new MutationObserver(() => {
        if (!modal.isConnected) onGone();
      });
      parentObs.observe(modal.parentNode, { childList: true });
      modal._dockCloseWatcher = { obs, parentObs };
    } else {
      modal._dockCloseWatcher = { obs };
    }
  }
  return dockSize;
}

// Internal: tear down dock state when a docked modal vanishes (close
// button, X, escape, or programmatic removal). Idempotent — bails out
// if the dock is already cleared so multiple observers can fire safely.
function _onDockedModalGone(modal, dockClass) {
  if (!modal) return;
  const watcher = modal._dockCloseWatcher;
  if (watcher) {
    try { watcher.obs && watcher.obs.disconnect(); } catch (_) {}
    try { watcher.parentObs && watcher.parentObs.disconnect(); } catch (_) {}
    delete modal._dockCloseWatcher;
  }
  const _c = _resolveDockNodes(modal)?.content || null;
  _disconnectLeftDockObservers(_c);
  const dockedSides = DOCK_SIDES.filter((side) =>
    modal.classList.contains(_dockClassForSide(side)));
  const hadLeft = dockedSides.includes('left');
  // Clear body-level dock state only for the side this modal owned, and only
  // when another docked window is not still using that side.
  dockedSides.forEach((side) => clearDockSide(side, modal));
  // Tear down the email/doc split CSS vars we set in _anchorLeftDock so the
  // doc-pane returns to its natural flex layout when the email is closed.
  if (hadLeft && !_hasOtherDockedWindow('left', modal)) {
    _clearEmailDocSplitGeometry();
  }
  if (_c?._preDockSnapshot?.collapsedSidebar && !_hasOtherSidebarDock(modal)) {
    _expandSidebarFromRail();
  }
  modal.classList.remove(...DOCK_SIDES.map(_dockClassForSide));
  // Clear the content's docked inline geometry. Singleton modals (plan,
  // workspace, calendar, …) reuse the same element across open/close, so if we
  // only drop the body push the element stays positioned (position:fixed;
  // right:0; fixed width) on the next open — floating over the chat with no
  // push. We deliberately do NOT restore the pre-dock snapshot here: that
  // snapshot is the drag position from when the user pulled the window to the
  // edge (near the side), so restoring it would reopen the modal off to the
  // side, still overlapping. Clearing the inline styles lets the modal reopen
  // at its CSS default (centered). Drag-to-undock still uses clearRightDock,
  // which DOES restore the snapshot for the peel-off feel.
  if (_c) {
    for (const prop of ['position', 'inset', 'left', 'top', 'right', 'bottom',
                        'width', 'maxWidth', 'height', 'maxHeight',
                        'minHeight', 'borderRadius', 'transform', 'margin']) {
      _c.style[prop] = '';
    }
    delete _c._preDockSnapshot;
    delete _c._dockSide;
    delete _c._dockOwner;
  }
  _positionEdgeDockResizeHandles();
}

function _expandSidebarFromRail() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  sidebar.classList.remove('hidden');
  try { window.syncRailSide && window.syncRailSide(); } catch (_) {}
}

// Un-dock a previously docked modal. Restores the exact rendered size +
// position the modal had before being docked. (cx, cy) re-anchors the
// drag near the cursor so the panel feels like it peeled off the edge.
export function clearRightDock(modal, cx, cy, dockClass) {
  const nodes = _resolveDockNodes(modal);
  if (!nodes) return;
  const content = nodes.content;
  if (!content) return;
  // Figure out which side was docked — fall back to right for legacy callers.
  const side = content._dockSide
    || DOCK_SIDES.find((candidate) => modal.classList.contains(_dockClassForSide(candidate)))
    || 'right';
  if (!dockClass) dockClass = _dockClassForSide(side);
  if (!modal.classList.contains(dockClass)) return;
  modal.classList.remove(dockClass);
  clearDockSide(side, modal);
  if (side === 'left' && !_hasOtherDockedWindow('left', modal)) {
    _clearEmailDocSplitGeometry();
  }
  delete content._dockSide;
  delete content._dockOwner;
  _disconnectLeftDockObservers(content);
  const snap = content._preDockSnapshot;
  // Re-expand the wide sidebar if we collapsed it — but only if the
  // user didn't manually toggle it during the dock (we don't want to
  // override their explicit choice).
  if (snap && snap.collapsedSidebar && !_hasOtherSidebarDock(modal)) _expandSidebarFromRail();
  // Restore the exact inline style values the modal had before docking
  // (width: min(720px, 92vw), max-height: 85vh, etc. — whatever the
  // mount path set). Setting an empty string here removes the property
  // from the inline style attribute, letting CSS rules take back over.
  const r = snap && snap.rect;
  const sty = (snap && snap.style) || {};
  content.style.position = sty.position || 'fixed';
  content.style.right = sty.right || '';
  content.style.bottom = sty.bottom || '';
  // Inline width/height may have been empty on the original (CSS-driven)
  // modal — but we're now forcing position:fixed, which kills the
  // CSS-flex-centered layout that produced the original size. Without a
  // fallback, position:fixed + width:auto collapses the window to its
  // content's min-width and the user sees a tiny pane after undock.
  // Use the captured rendered rect as a backup so the floating window
  // returns at roughly the same dimensions it had before docking.
  content.style.width = sty.width || (r && r.width ? r.width + 'px' : '');
  content.style.maxWidth = sty.maxWidth || '';
  content.style.height = sty.height || (r && r.height ? r.height + 'px' : '');
  content.style.maxHeight = sty.maxHeight || '';
  content.style.removeProperty('min-height');
  if (sty.minHeight) {
    content.style.setProperty('min-height', sty.minHeight, sty.minHeightPriority || '');
  }
  content.style.borderRadius = sty.borderRadius || '';
  content.style.transform = sty.transform || '';
  content.style.margin = sty.margin || '';
  // Re-anchor near the cursor so the panel feels peeled-off the edge.
  // Use the captured rect width as the centering reference (CSS may not
  // have resolved the inline width yet on this microtask). Fall back to
  // the original captured left/top when no cursor coords are passed.
  const refW = (r && r.width) || (content.offsetWidth / _uiScaleFactor()) || 720;
  const refH = (r && r.height) || (content.offsetHeight / _uiScaleFactor()) || (_viewportHeight() * 0.7);
  const targetLeft = (typeof cx === 'number')
    ? Math.max(8, _layoutCoordinate(cx) - refW / 2)
    : (sty.left || (r ? r.left + 'px' : Math.max(8, (_viewportWidth() - refW) / 2) + 'px'));
  const targetTop = (typeof cy === 'number')
    ? Math.max(8, _layoutCoordinate(cy) - 20)
    : (sty.top || (r ? r.top + 'px' : Math.max(8, (_viewportHeight() - refH) / 3) + 'px'));
  content.style.left = (typeof targetLeft === 'number') ? targetLeft + 'px' : targetLeft;
  content.style.top = (typeof targetTop === 'number') ? targetTop + 'px' : targetTop;
  delete content._preDockSnapshot;
  delete content._dockSuspended;
  _positionEdgeDockResizeHandles();
}

// Temporarily release a docked modal's body push (chat returns to full
// width) WITHOUT un-docking the window — used when a docked modal is
// MINIMIZED. The modal keeps its docked geometry + class + snapshot so
// resumeDock() can snap it right back when the chip is reopened. Returns the
// docked side, or null if the modal wasn't docked.
export function suspendDock(modal) {
  const nodes = _resolveDockNodes(modal);
  if (!nodes || !nodes.content) return null;
  const content = nodes.content;
  const hadEmailSnapLeft = modal.classList.contains('email-snap-left');
  const side = content._dockSide
    || DOCK_SIDES.find((candidate) => modal.classList.contains(_dockClassForSide(candidate)))
    || (modal.classList.contains('email-snap-left') ? 'left' : null);
  if (!side) return null;
  // Stop the close-watcher from tearing the dock fully down when `.hidden`
  // is added by minimize — we want to keep the dock, just release the push.
  if (modal._dockCloseWatcher) {
    try { modal._dockCloseWatcher.obs && modal._dockCloseWatcher.obs.disconnect(); } catch (_) {}
    try { modal._dockCloseWatcher.parentObs && modal._dockCloseWatcher.parentObs.disconnect(); } catch (_) {}
    delete modal._dockCloseWatcher;
  }
  // Release the body push + restore the sidebar so the chat fills the width.
  clearDockSide(side, modal);
  if (side === 'left') {
    _disconnectLeftDockObservers(content);
  }
  if (hadEmailSnapLeft) {
    modal.classList.remove('email-snap-left');
    _clearEmailDocSplitGeometry();
    delete content._dockSide;
    delete content._dockOwner;
    delete content._dockSuspended;
    return null;
  }
  if (side === 'left' && !_hasOtherDockedWindow('left', modal)) {
    _clearEmailDocSplitGeometry();
  }
  if (content._preDockSnapshot?.collapsedSidebar && !_hasOtherSidebarDock(modal)) {
    _expandSidebarFromRail();
  }
  content._dockSuspended = side;
  _positionEdgeDockResizeHandles();
  return side;
}

// Re-apply the body push (+ sidebar collapse + width var + close-watcher)
// for a modal that was suspendDock()'d, so RESTORING a minimized docked
// window nudges the chat back in. Idempotent via applyEdgeDock's guarded
// snapshot. Returns true if a suspended dock was resumed.
export function resumeDock(modal) {
  const nodes = _resolveDockNodes(modal);
  if (!nodes || !nodes.content) return false;
  const content = nodes.content;
  const side = content._dockSuspended;
  if (!side) return false;
  delete content._dockSuspended;
  try { applyEdgeDock(modal, side); } catch (_) {}
  return true;
}

// Wire right-edge snap detection into a drag session. Call this once per
// modal that should support docking. Returns an object the caller's drag
// handler can poll: { hovering(): boolean, commit(): void, release(): void }.
// The drag handler is responsible for calling onMove(clientX, clientY)
// during mousemove and commit() at mouseup if hovering().
export function makeRightDockController(modal, dockClass = 'modal-right-docked') {
  return makeEdgeDockController(modal, 'right', dockClass);
}

// Read the current visible left-nav edge for snap detection. Use measured
// geometry instead of CSS vars because the sidebar can auto-collapse during a
// dock operation while --sidebar-w is still settling.
function _leftNavWidth() {
  return _leftNavRight();
}

// Generic edge-snap controller for left/right/top/bottom. Same pattern
// as the original right-only controller: caller drives onMove during
// mousemove, then calls commit()/release() at mouseup based on hovering().
export function makeEdgeDockController(modal, side = 'right', dockClass) {
  if (!DOCK_SIDES.includes(side)) side = 'right';
  if (!dockClass) dockClass = _dockClassForSide(side);
  let _hoveringSnap = false;
  const _distFromEdge = (cx, cy) => {
    const x = _layoutCoordinate(cx);
    const y = _layoutCoordinate(cy);
    if (side === 'left') return x - _leftNavWidth();
    if (side === 'right') return _viewportWidth() - _rightNavWidth() - x;
    if (side === 'top') return y;
    return _viewportHeight() - y;
  };
  return {
    onMove(cx, cy) {
      if (!_canUseDockSide(side)) {
        _showSnapHint(false, side, modal);
        _hoveringSnap = false;
        return false;
      }
      if (modal.classList.contains(dockClass)) {
        if (_distFromEdge(cx, cy) > UNSNAP_PX) {
          clearRightDock(modal, cx, cy, dockClass);
          return true;
        }
        return false;
      }
      const nearEdge = _distFromEdge(cx, cy) <= SNAP_PX
        && !!edgeDockPreviewRect(modal, side);
      if (nearEdge !== _hoveringSnap) {
        _hoveringSnap = nearEdge;
        _showSnapHint(nearEdge, side, modal);
      }
      return false;
    },
    distance(cx, cy) { return _distFromEdge(cx, cy); },
    near(cx, cy) {
      return _canUseDockSide(side)
        && _distFromEdge(cx, cy) <= SNAP_PX
        && !!edgeDockPreviewRect(modal, side);
    },
    hovering() { return _hoveringSnap; },
    side() { return side; },
    commit() {
      _showSnapHint(false, side, modal);
      _hoveringSnap = false;
      if (!_canUseDockSide(side)) return 0;
      return _applyDockInternal(modal, side, dockClass);
    },
    release() {
      _showSnapHint(false, side, modal);
      _hoveringSnap = false;
    },
  };
}

function _reanchorActiveDocks() {
  // Two passes let opposite docks settle against each other's newly clamped
  // size after a viewport or UI-scale change instead of preserving one stale
  // pre-resize reserve for the rest of the calculation.
  for (let pass = 0; pass < 2; pass += 1) {
    for (const side of DOCK_SIDES) {
      document.querySelectorAll(`.${_dockClassForSide(side)}`).forEach((owner) => {
        if (!owner?.isConnected || owner.classList.contains('hidden') || owner.style.display === 'none') return;
        const content = _resolveDockNodes(owner)?.content;
        if (!content || content._dockSuspended) return;
        content._dockSide = side;
        content._dockOwner = owner;
        if (side === 'left') {
          if (!_anchorLeftDock(content)) {
            clearRightDock(owner, undefined, undefined, _dockClassForSide(side));
            return;
          }
          content.style.top = '0';
          content.style.bottom = '0';
          content.style.height = _viewportHeight() + 'px';
          content.style.maxHeight = _viewportHeight() + 'px';
          const width = parseFloat(content.style.width) || 0;
          _setDockVars('left', document.body.classList.contains('email-doc-split-active') ? 0 : width);
          return;
        }
        const rect = edgeDockPreviewRect(owner, side);
        if (!rect) {
          clearRightDock(owner, undefined, undefined, _dockClassForSide(side));
          return;
        }
        content.style.left = side === 'right' ? 'auto' : rect.left + 'px';
        content.style.right = (_viewportWidth() - rect.left - rect.width) + 'px';
        content.style.top = side === 'bottom' ? 'auto' : '0';
        content.style.bottom = side === 'bottom' ? '0' : (side === 'right' ? '0' : 'auto');
        content.style.width = rect.width + 'px';
        content.style.maxWidth = rect.width + 'px';
        content.style.height = rect.height + 'px';
        content.style.maxHeight = rect.height + 'px';
        _setDockVars(side, side === 'right' ? rect.width : rect.height);
      });
    }
  }
  _positionEdgeDockResizeHandles();
}

(function _initEdgeDockResizeHandles() {
  if (typeof document === 'undefined') return;
  if (!document.body) {
    document.addEventListener('DOMContentLoaded', _initEdgeDockResizeHandles, { once: true });
    return;
  }

  const handles = {
    left: document.createElement('div'),
    right: document.createElement('div'),
    top: document.createElement('div'),
    bottom: document.createElement('div'),
  };
  const _setStyle = (el, prop, value) => {
    if (el.style[prop] !== value) el.style[prop] = value;
  };
  const _hideHandle = (handle) => _setStyle(handle, 'display', 'none');

  for (const side of DOCK_SIDES) {
    const handle = handles[side];
    const verticalSeam = side === 'top' || side === 'bottom';
    handle.className = `edge-dock-resize-handle edge-dock-resize-handle-${side}`;
    handle.style.position = 'fixed';
    handle.style.top = '0';
    handle.style.bottom = '0';
    handle.style.width = verticalSeam ? '0' : EDGE_DOCK_RESIZE_HANDLE_PX + 'px';
    handle.style.height = verticalSeam ? EDGE_DOCK_RESIZE_HANDLE_PX + 'px' : 'auto';
    handle.style.cursor = verticalSeam ? 'row-resize' : 'col-resize';
    // Invisible at rest, accent stripe fades in on hover (see
    // .edge-dock-resize-handle CSS rule).
    handle.style.background = 'transparent';
    handle.style.transition = 'background 0.18s ease';
    handle.style.pointerEvents = 'auto';
    handle.style.touchAction = 'none';
    handle.style.display = 'none';
    handle.title = 'Drag to resize docked window';
    document.body.appendChild(handle);
  }

  const _isUsableDockOwner = (owner) => {
    if (!owner || !owner.isConnected) return false;
    if (owner.classList?.contains('hidden')) return false;
    if (owner.style?.display === 'none') return false;
    const nodes = _resolveDockNodes(owner);
    const content = nodes?.content;
    if (!content || !content.isConnected) return false;
    if (content.classList?.contains('hidden')) return false;
    if (content.style?.display === 'none') return false;
    const r = content.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const _activeDockOwner = (side) => {
    const cls = _dockClassForSide(side);
    const all = Array.from(document.querySelectorAll(`.${cls}`));
    for (const owner of all.reverse()) {
      if (_isUsableDockOwner(owner)) return owner;
    }
    return null;
  };

  const _zIndexFor = (el, fallback = 250) => {
    const raw = el ? window.getComputedStyle(el).zIndex : '';
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : fallback;
  };

  const _hasVisibleFloatingModal = (owner) => {
    const all = Array.from(document.querySelectorAll('.modal:not(.hidden):not(.modal-minimized)'));
    return all.some((modal) => {
      if (!modal || modal === owner) return false;
      if (owner?.contains?.(modal) || modal.contains?.(owner)) return false;
      if (DOCK_SIDES.some((side) => modal.classList.contains(_dockClassForSide(side)))
          || modal.classList.contains('email-snap-left')) return false;
      if (modal.style.display === 'none') return false;
      const content = _resolveDockNodes(modal)?.content;
      const r = content?.getBoundingClientRect?.();
      return !!r && r.width > 0 && r.height > 0;
    });
  };

  const _setSize = (owner, side, clientX, clientY) => {
    const nodes = _resolveDockNodes(owner);
    const content = nodes?.content;
    if (!content) return 0;
    const pointerX = _layoutCoordinate(clientX);
    const pointerY = _layoutCoordinate(clientY);
    let size = 0;
    if (side === 'top' || side === 'bottom') {
      size = _clampVerticalDockHeight(
        side === 'top' ? pointerY : _viewportHeight() - pointerY,
        side,
        owner,
      );
      if (size <= 0) return 0;
      content._userDockHeight = size;
      content.style.top = side === 'top' ? '0' : 'auto';
      content.style.bottom = side === 'bottom' ? '0' : 'auto';
      content.style.height = size + 'px';
      content.style.maxHeight = size + 'px';
      document.body.classList.add(_bodyDockClassForSide(side));
      _setDockVars(side, size);
    } else if (side === 'right') {
      const requested = _viewportWidth() - _rightNavWidth() - pointerX;
      if (_shouldAutoCollapseSidebar(requested)) {
        _collapseSidebarToRail();
        if (content._preDockSnapshot) content._preDockSnapshot.collapsedSidebar = true;
      }
      size = _clampRightDockWidth(requested);
      content._userDockWidth = size;
      content.style.left = 'auto';
      content.style.right = _rightNavWidth() + 'px';
      content.style.width = size + 'px';
      content.style.maxWidth = size + 'px';
      document.body.classList.add('right-dock-active');
      _setDockVars('right', size);
    } else {
      const left = _leftNavRight();
      size = _clampLeftDockWidth(pointerX - left, left);
      content._userDockWidth = size;
      content._emailDocSplitUserW = size;
      content.style.left = left + 'px';
      content.style.right = 'auto';
      content.style.width = size + 'px';
      content.style.maxWidth = size + 'px';
      document.body.classList.add('left-dock-active');
      _setDockVars('left', document.body.classList.contains('email-doc-split-active') ? 0 : size);
    }
    _positionEdgeDockResizeHandles();
    return size;
  };

  _edgeDockHandlePositioner = () => {
    const splitOwnsLeftSeam = document.body.classList.contains('email-doc-split-active')
      && document.body.classList.contains('doc-view')
      && window.innerWidth > 768;
    for (const side of DOCK_SIDES) {
      const handle = handles[side];
      const verticalSeam = side === 'top' || side === 'bottom';
      if (window.innerWidth <= MOBILE_DOCK_BREAKPOINT
          || !_canUseDockSide(side)
          || (side === 'left' && splitOwnsLeftSeam)) {
        _hideHandle(handle);
        continue;
      }
      const owner = _activeDockOwner(side);
      const content = owner && _resolveDockNodes(owner)?.content;
      if (!content) {
        _hideHandle(handle);
        continue;
      }
      if (_hasVisibleFloatingModal(owner)) {
        _hideHandle(handle);
        continue;
      }
      const r = _layoutRect(content.getBoundingClientRect());
      _setStyle(handle, 'display', 'block');
      if (verticalSeam) {
        const y = side === 'top' ? r.bottom : r.top;
        const left = Math.max(0, r.left);
        const width = Math.min(_viewportWidth(), r.right) - left;
        if (!Number.isFinite(y) || y <= 0 || y >= _viewportHeight() || width <= 0) {
          _hideHandle(handle);
          continue;
        }
        _setStyle(handle, 'left', left + 'px');
        _setStyle(handle, 'top', (y - EDGE_DOCK_RESIZE_HANDLE_PX / 2) + 'px');
        _setStyle(handle, 'bottom', 'auto');
        _setStyle(handle, 'width', width + 'px');
        _setStyle(handle, 'height', EDGE_DOCK_RESIZE_HANDLE_PX + 'px');
        _setStyle(handle, 'cursor', 'row-resize');
        _setStyle(handle, 'background', 'transparent');
      } else {
        const x = side === 'right' ? r.left : r.right;
        const top = Math.max(0, r.top);
        const height = Math.min(_viewportHeight(), r.bottom) - top;
        if (!Number.isFinite(x) || x <= 0 || x >= _viewportWidth() || height <= 0) {
          _hideHandle(handle);
          continue;
        }
        _setStyle(handle, 'left', (x - EDGE_DOCK_RESIZE_HANDLE_PX / 2) + 'px');
        _setStyle(handle, 'top', top + 'px');
        _setStyle(handle, 'bottom', 'auto');
        _setStyle(handle, 'width', EDGE_DOCK_RESIZE_HANDLE_PX + 'px');
        _setStyle(handle, 'height', height + 'px');
        _setStyle(handle, 'cursor', 'col-resize');
        _setStyle(handle, 'background', 'transparent');
      }
      _setStyle(handle, 'zIndex', String(_zIndexFor(owner) + 1));
    }
  };

  for (const side of DOCK_SIDES) {
    const handle = handles[side];
    handle.addEventListener('pointerdown', (e) => {
      if (handle.style.display === 'none') return;
      const owner = _activeDockOwner(side);
      if (!owner) return;
      e.preventDefault();
      e.stopPropagation();
      handle.setPointerCapture?.(e.pointerId);
      const nodes = _resolveDockNodes(owner);
      const content = nodes?.content;
      const prevCursor = document.body.style.cursor;
      const prevUserSelect = document.body.style.userSelect;
      const verticalSeam = side === 'top' || side === 'bottom';
      document.body.style.cursor = verticalSeam ? 'row-resize' : 'col-resize';
      document.body.style.userSelect = 'none';
      document.body.classList.add('edge-dock-resizing');
      const onMove = (ev) => {
        ev.preventDefault();
        _setSize(owner, side, ev.clientX, ev.clientY);
      };
      const onUp = (ev) => {
        try { handle.releasePointerCapture?.(e.pointerId); } catch (_) {}
        document.removeEventListener('pointermove', onMove, true);
        document.removeEventListener('pointerup', onUp, true);
        document.removeEventListener('pointercancel', onUp, true);
        document.body.classList.remove('edge-dock-resizing');
        document.body.style.cursor = prevCursor;
        document.body.style.userSelect = prevUserSelect;
        const measured = _layoutRect(content?.getBoundingClientRect?.());
        const finalSize = verticalSeam ? measured?.height || 0 : measured?.width || 0;
        if (finalSize) {
          if (verticalSeam) _saveDockHeight(side, finalSize);
          else _saveDockWidth(owner, content, side, finalSize);
        }
        ev.preventDefault();
      };
      document.addEventListener('pointermove', onMove, true);
      document.addEventListener('pointerup', onUp, true);
      document.addEventListener('pointercancel', onUp, true);
    });
  }

  new MutationObserver(_positionEdgeDockResizeHandles).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  new MutationObserver(_positionEdgeDockResizeHandles).observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
  // Accessibility scaling is applied by toggling a class on the root element;
  // no window resize event is fired. Recompute fixed dock geometry immediately
  // so inline sizes and reserve variables stay in the new logical coordinate
  // system instead of rendering oversized until the next physical resize.
  const reanchorAfterRootClassChange = () => {
    _reanchorActiveDocks();
    // Scaling classes can change the modal's measured box over more than one
    // layout frame. Re-anchor again after layout settles so the invisible hit
    // target cannot remain across the modal header at a stale Y coordinate.
    requestAnimationFrame(() => {
      _reanchorActiveDocks();
      requestAnimationFrame(_reanchorActiveDocks);
    });
  };
  new MutationObserver(reanchorAfterRootClassChange).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
  });
  const navObserver = new MutationObserver(_reanchorActiveDocks);
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  if (sidebar) navObserver.observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
  if (rail) navObserver.observe(rail, { attributes: true, attributeFilter: ['class', 'style'] });
  let raf = 0;
  const schedulePosition = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      _positionEdgeDockResizeHandles();
    });
  };
  new MutationObserver(schedulePosition).observe(document.body, { childList: true });
  window.addEventListener('resize', _reanchorActiveDocks);
  window.addEventListener('odysseus:modal-opened', _reanchorActiveDocks);
  window.addEventListener('odysseus:ui-scale-change', _reanchorActiveDocks);
  _positionEdgeDockResizeHandles();
})();

(function _initSplitSeamIndicator() {
  if (typeof document === 'undefined') return;
  const stripe = document.createElement('div');
  stripe.id = 'email-doc-split-seam';
  stripe.style.position = 'fixed';
  stripe.style.top = '0';
  stripe.style.bottom = '0';
  stripe.style.width = '10px';
  stripe.style.cursor = 'col-resize';
  stripe.style.zIndex = '261';
  stripe.style.background = 'linear-gradient(to right, transparent 0 3px, color-mix(in srgb, var(--accent, var(--red)) 35%, transparent) 3px 7px, transparent 7px 10px)';
  stripe.style.pointerEvents = 'auto';
  stripe.style.touchAction = 'none';
  stripe.style.display = 'none';
  stripe.title = 'Drag to resize email and draft';

  const _activeLeftDockContent = () => {
    const modal = document.querySelector(
      '#email-lib-modal.modal-left-docked:not(.hidden), ' +
      '#email-lib-modal.email-snap-left:not(.hidden), ' +
      '.modal[id^="email-reader-"].modal-left-docked:not(.hidden), ' +
      '.modal[id^="email-reader-"].email-snap-left:not(.hidden)'
    );
    return modal?.querySelector?.('.modal-content') || null;
  };

  const _position = () => {
    const splitActive = document.body.classList.contains('email-doc-split-active')
      && document.body.classList.contains('doc-view')
      && window.innerWidth > 768;
    if (!splitActive) { stripe.style.display = 'none'; return; }
    const x = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--email-doc-split-right-x')) || 0;
    if (!x) { stripe.style.display = 'none'; return; }
    stripe.style.display = 'block';
    stripe.style.left = (x - 5) + 'px';
  };

  const _dragTo = (clientX) => {
    const content = _activeLeftDockContent();
    if (!content) return;
    const left = _leftNavRight();
    const w = _clampEmailDocSplitWidth(_layoutCoordinate(clientX) - left, left);
    content._emailDocSplitUserW = w;
    content.style.left = left + 'px';
    content.style.width = w + 'px';
    content.style.maxWidth = w + 'px';
    _applyEmailDocSplitGeometry(left, w);
    _position();
  };

  stripe.addEventListener('pointerdown', (e) => {
    if (stripe.style.display === 'none') return;
    e.preventDefault();
    stripe.setPointerCapture?.(e.pointerId);
    const prevCursor = document.body.style.cursor;
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.body.classList.add('email-doc-split-resizing');
    _dragTo(e.clientX);
    const onMove = (ev) => {
      ev.preventDefault();
      _dragTo(ev.clientX);
    };
    const onUp = (ev) => {
      try { stripe.releasePointerCapture?.(e.pointerId); } catch (_) {}
      document.removeEventListener('pointermove', onMove, true);
      document.removeEventListener('pointerup', onUp, true);
      document.removeEventListener('pointercancel', onUp, true);
      document.body.classList.remove('email-doc-split-resizing');
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevUserSelect;
      const rightX = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--email-doc-split-right-x')) || 0;
      const left = _leftNavRight();
      if (rightX > left) _saveEmailDocSplitWidth(rightX - left);
      ev.preventDefault();
    };
    document.addEventListener('pointermove', onMove, true);
    document.addEventListener('pointerup', onUp, true);
    document.addEventListener('pointercancel', onUp, true);
  });

  document.body.appendChild(stripe);
  new MutationObserver(_position).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  new MutationObserver(_position).observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
  window.addEventListener('resize', _position);
  _position();
})();
