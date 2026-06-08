// Right-edge snap docking for draggable modals.
//
// Adds a "drag-to-right" gesture that docks a modal as a right-side panel
// (mirrors the snap-to-top fullscreen pattern used by _makeDraggable in
// emailLibrary.js / documentLibrary.js / galleryEditor.js). While docked:
//   - the modal-content lives at `right: 0; top: 0; bottom: 0` with a
//     viewport-fraction width
//   - body gets `right-dock-active` + `--right-dock-w` so the workspace
//     underneath reserves room for the fixed side panel
//   - if the remaining chat width would drop under 380px, the wide
//     sidebar auto-collapses to the icon rail (mirrors notes-view UX)
//
// Drag-away from the right edge un-docks back to a centered window —
// the same restore values the snap-to-top exit path uses.

// Wider snap zone than the top-snap fullscreen (6px) — the right edge
// is harder to hit precisely since most users drag broadly toward the
// side rather than aiming at a 1px line. 60px feels generous without
// false-positive triggers from casual repositioning.
const SNAP_PX = 60;
const UNSNAP_PX = 80;
const MIN_CHAT_WIDTH = 380;
const EMAIL_DOC_SPLIT_WIDTH_KEY = 'odysseus-email-doc-split-width';
const EDGE_DOCK_WIDTH_KEY_PREFIX = 'odysseus-edge-dock-width';
const MIN_EDGE_DOCK_WIDTH = 320;

let _edgeDockHandlePositioner = null;

function _positionEdgeDockResizeHandles() {
  try { _edgeDockHandlePositioner && _edgeDockHandlePositioner(); } catch (_) {}
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

function _hasAnyOtherDockedWindow(owner) {
  return _hasOtherDockedWindow('left', owner) || _hasOtherDockedWindow('right', owner);
}

export function clearDockSide(side, owner = null) {
  if (side !== 'left' && side !== 'right') return;
  if (_hasOtherDockedWindow(side, owner)) return;
  document.body.classList.remove(side === 'left' ? 'left-dock-active' : 'right-dock-active');
  document.documentElement.style.removeProperty(side === 'left' ? '--left-dock-w' : '--right-dock-w');
  if (side === 'left') {
    try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}
  }
  _positionEdgeDockResizeHandles();
}

// Default dock width: ~38% of viewport, clamped to a reasonable band.
function _defaultDockWidth() {
  return Math.min(640, Math.max(420, Math.round(window.innerWidth * 0.38)));
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

function _minEdgeDockWidth() {
  return window.innerWidth < 900 ? 280 : MIN_EDGE_DOCK_WIDTH;
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

function _clampDockWidthToSpace(width, min, max) {
  const floor = Math.min(min, Math.max(220, Math.round(max)));
  const ceiling = Math.max(floor, Math.round(max));
  return Math.min(ceiling, Math.max(floor, Math.round(width)));
}

function _clampRightDockWidth(width) {
  const min = _minEdgeDockWidth();
  const navRight = _leftNavRight();
  const leftDockW = _activeDockWidth('left');
  const maxByChat = window.innerWidth - navRight - leftDockW - MIN_CHAT_WIDTH;
  const max = Math.min(Math.round(window.innerWidth * 0.82), maxByChat);
  return _clampDockWidthToSpace(width, min, max);
}

function _clampLeftDockWidth(width, left = _leftNavRight()) {
  const min = _minEdgeDockWidth();
  const rightDockW = _activeDockWidth('right');
  const available = Math.max(0, window.innerWidth - left - rightDockW);
  const max = Math.min(Math.round(available * 0.82), available - MIN_CHAT_WIDTH);
  return _clampDockWidthToSpace(width, min, max);
}

function _resolveRightDockWidth(modal, content) {
  return _clampRightDockWidth(content?._userDockWidth || _storedDockWidth(modal, content, 'right') || _defaultDockWidth());
}

function _resolveLeftDockWidth(content, left = _leftNavRight()) {
  return _clampLeftDockWidth(content?._userDockWidth || _storedDockWidth(content?._dockOwner, content, 'left') || _resolveEmailDocSplitWidth(content, left), left);
}

function _isEmailDockOwner(owner) {
  const id = owner?.id || '';
  return id === 'email-lib-modal' || id.startsWith('email-reader-') || owner?.classList?.contains('email-window-modal');
}

function _showSnapHint(on, side = 'right') {
  const cls = side === 'left' ? 'modal-snap-hint-left' : 'modal-snap-hint-right';
  let hint = document.querySelector('.' + cls);
  if (!on) {
    if (hint) hint.remove();
    return;
  }
  if (hint) return;
  hint = document.createElement('div');
  hint.className = 'modal-snap-hint ' + cls;
  const w = _defaultDockWidth();
  const edge = side === 'left' ? 'left:0' : 'right:0';
  const borderSide = side === 'left' ? 'border-right' : 'border-left';
  hint.style.cssText = `position:fixed;${edge};top:0;bottom:0;width:${w}px;background:color-mix(in srgb, var(--accent-primary, #60a5fa) 12%, transparent);${borderSide}:2px dashed color-mix(in srgb, var(--accent-primary, #60a5fa) 60%, transparent);z-index:9998;pointer-events:none;transition:opacity 0.12s;`;
  document.body.appendChild(hint);
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
  const sb = sidebar.getBoundingClientRect().width || 0;
  const rl = (rail && window.getComputedStyle(rail).display !== 'none')
    ? rail.getBoundingClientRect().width
    : 0;
  const remaining = window.innerWidth - sb - rl - _activeDockWidth('left') - dockW;
  return remaining < MIN_CHAT_WIDTH;
}

// Right edge (px) of whatever left navigation is currently showing — the
// expanded sidebar if visible, otherwise the icon rail. Used to anchor the
// left dock so it always sits flush to the right of the nav.
function _leftNavRight() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  let x = 0;
  if (sidebar && !sidebar.classList.contains('hidden')) {
    const r = sidebar.getBoundingClientRect();
    if (r.width) x = Math.max(x, r.right);
  }
  if (rail && window.getComputedStyle(rail).display !== 'none') {
    const r = rail.getBoundingClientRect();
    if (r.width) x = Math.max(x, r.right);
  }
  return x;
}

function _clampEmailDocSplitWidth(width, left = _leftNavRight()) {
  const available = Math.max(0, window.innerWidth - left);
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
  docPane.style.setProperty('right', 'var(--right-dock-w, 0px)', 'important');
  docPane.style.setProperty('top', '0px', 'important');
  docPane.style.setProperty('bottom', '0px', 'important');
  docPane.style.setProperty('width', 'auto', 'important');
  docPane.style.setProperty('max-width', 'none', 'important');
  docPane.style.setProperty('height', '100vh', 'important');
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
  const available = Math.max(0, window.innerWidth - left);
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
  if (!content || content._dockSide !== 'left') return;
  const left = _leftNavRight();
  const w = document.body.classList.contains('doc-view')
    ? _resolveEmailDocSplitWidth(content, left)
    : _resolveLeftDockWidth(content, left);
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
    document.documentElement.style.setProperty('--left-dock-w', '0px');
    _applyEmailDocSplitGeometry(left, w);
  } else if (document.body.classList.contains('email-doc-split-active')) {
    _clearEmailDocSplitGeometry();
  } else {
    document.documentElement.style.setProperty('--left-dock-w', w + 'px');
  }
}

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

// Apply edge dock state to a modal/pane. `side` is 'right' (default) or 'left'.
export function applyEdgeDock(modal, side = 'right', dockClass) {
  if (!dockClass) dockClass = side === 'left' ? 'modal-left-docked' : 'modal-right-docked';
  return _applyDockInternal(modal, side, dockClass);
}

// Backwards-compat: existing callers use applyRightDock for right snaps.
export function applyRightDock(modal, dockClass = 'modal-right-docked') {
  return _applyDockInternal(modal, 'right', dockClass);
}

function _applyDockInternal(modal, side, dockClass) {
  const nodes = _resolveDockNodes(modal);
  if (!nodes) return 0;
  const content = nodes.content;
  if (!content) return 0;
  // If the modal is currently docked on the OTHER side (e.g. the user
  // manually docked it right, then a reply re-docks it left), clear that
  // side's class + body push first. Otherwise both sides' state coexist —
  // the old dock keeps pushing/overlapping and the reply doc opens beneath
  // the still-docked window. We keep _preDockSnapshot (the guard below skips
  // re-capturing) so un-dock still restores the original floating geometry.
  // Guarded on the other-side class so a normal first dock still snapshots
  // the floating window's real left/right inline styles below.
  const otherSide = side === 'left' ? 'right' : 'left';
  const otherClass = _dockClassForSide(otherSide);
  if (modal.classList.contains(otherClass)) {
    modal.classList.remove(otherClass);
    clearDockSide(otherSide, modal);
    // Reset the edge anchors so the new side positions from a clean slate
    // (the right dock pins right:0; the left dock pins left:<nav>).
    content.style.left = '';
    content.style.right = '';
  }
  // Snapshot the actual rendered rect + inline styles so un-dock can
  // restore the exact same floating window the user had before. Without
  // this, a window the user had carefully resized would snap back to
  // some 720×85vh default — feels like the dock ate their layout.
  if (!content._preDockSnapshot) {
    const r = content.getBoundingClientRect();
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
  content.style.top = '0';
  content.style.bottom = '0';
  content.style.height = '100vh';
  content.style.maxHeight = '100vh';
  content.style.borderRadius = '0';
  content.style.transform = 'none';
  content.style.margin = '0';
  let w;
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
    w = parseFloat(content.style.width) || 0;
    document.body.classList.add('left-dock-active');
    document.documentElement.style.setProperty(
      '--left-dock-w',
      document.body.classList.contains('email-doc-split-active') ? '0px' : w + 'px',
    );
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
  } else {
    w = _resolveRightDockWidth(modal, content);
    content.style.left = 'auto';
    content.style.right = '0';
    content.style.width = w + 'px';
    content.style.maxWidth = w + 'px';
    document.body.classList.add('right-dock-active');
    document.documentElement.style.setProperty('--right-dock-w', w + 'px');
    if (_shouldAutoCollapseSidebar(w)) {
      _collapseSidebarToRail();
      content._preDockSnapshot.collapsedSidebar = true;
    }
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
  return w;
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
  const _c = modal.querySelector ? modal.querySelector('.modal-content') : null;
  _disconnectLeftDockObservers(_c);
  const hadRight = modal.classList.contains('modal-right-docked');
  const hadLeft = modal.classList.contains('modal-left-docked');
  // Clear body-level dock state only for the side this modal owned, and only
  // when another docked window is not still using that side.
  if (hadRight) clearDockSide('right', modal);
  if (hadLeft) clearDockSide('left', modal);
  // Tear down the email/doc split CSS vars we set in _anchorLeftDock so the
  // doc-pane returns to its natural flex layout when the email is closed.
  if (hadLeft && !_hasOtherDockedWindow('left', modal)) {
    _clearEmailDocSplitGeometry();
  }
  if (_c?._preDockSnapshot?.collapsedSidebar && !_hasAnyOtherDockedWindow(modal)) {
    _expandSidebarFromRail();
  }
  modal.classList.remove('modal-right-docked');
  modal.classList.remove('modal-left-docked');
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
                        'borderRadius', 'transform', 'margin']) {
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
  const side = content._dockSide || (modal.classList.contains('modal-left-docked') ? 'left' : 'right');
  if (!dockClass) dockClass = side === 'left' ? 'modal-left-docked' : 'modal-right-docked';
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
  if (snap && snap.collapsedSidebar && !_hasAnyOtherDockedWindow(modal)) _expandSidebarFromRail();
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
  content.style.borderRadius = sty.borderRadius || '';
  content.style.transform = sty.transform || '';
  content.style.margin = sty.margin || '';
  // Re-anchor near the cursor so the panel feels peeled-off the edge.
  // Use the captured rect width as the centering reference (CSS may not
  // have resolved the inline width yet on this microtask). Fall back to
  // the original captured left/top when no cursor coords are passed.
  const refW = (r && r.width) || content.offsetWidth || 720;
  const refH = (r && r.height) || content.offsetHeight || (window.innerHeight * 0.7);
  const targetLeft = (typeof cx === 'number')
    ? Math.max(8, cx - refW / 2)
    : (sty.left || (r ? r.left + 'px' : Math.max(8, (window.innerWidth - refW) / 2) + 'px'));
  const targetTop = (typeof cy === 'number')
    ? Math.max(8, cy - 20)
    : (sty.top || (r ? r.top + 'px' : Math.max(8, (window.innerHeight - refH) / 3) + 'px'));
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
    || (modal.classList.contains('modal-left-docked') ? 'left'
        : modal.classList.contains('email-snap-left') ? 'left'
        : modal.classList.contains('modal-right-docked') ? 'right' : null);
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
  if (content._preDockSnapshot?.collapsedSidebar && !_hasAnyOtherDockedWindow(modal)) {
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

// Generic edge-snap controller. `side` is 'left' or 'right'. Same pattern
// as the original right-only controller: caller drives onMove during
// mousemove, then calls commit()/release() at mouseup based on hovering().
export function makeEdgeDockController(modal, side = 'right', dockClass) {
  if (!dockClass) dockClass = side === 'left' ? 'modal-left-docked' : 'modal-right-docked';
  let _hoveringSnap = false;
  const _distFromEdge = (cx) => {
    if (side === 'left') return cx - _leftNavWidth();
    return window.innerWidth - cx;
  };
  return {
    onMove(cx, cy) {
      if (modal.classList.contains(dockClass)) {
        if (_distFromEdge(cx) > UNSNAP_PX) {
          clearRightDock(modal, cx, cy, dockClass);
          return true;
        }
        return false;
      }
      const nearEdge = _distFromEdge(cx) <= SNAP_PX;
      if (nearEdge !== _hoveringSnap) {
        _hoveringSnap = nearEdge;
        _showSnapHint(nearEdge, side);
      }
      return false;
    },
    hovering() { return _hoveringSnap; },
    side() { return side; },
    commit() {
      _showSnapHint(false, side);
      _hoveringSnap = false;
      _applyDockInternal(modal, side, dockClass);
    },
    release() {
      _showSnapHint(false, side);
      _hoveringSnap = false;
    },
  };
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
  };
  const _setStyle = (el, prop, value) => {
    if (el.style[prop] !== value) el.style[prop] = value;
  };
  const _hideHandle = (handle) => _setStyle(handle, 'display', 'none');

  for (const side of ['left', 'right']) {
    const handle = handles[side];
    handle.className = `edge-dock-resize-handle edge-dock-resize-handle-${side}`;
    handle.style.position = 'fixed';
    handle.style.top = '0';
    handle.style.bottom = '0';
    handle.style.width = '10px';
    handle.style.cursor = 'col-resize';
    handle.style.background = 'linear-gradient(to right, transparent 0 3px, color-mix(in srgb, var(--accent, var(--red)) 35%, transparent) 3px 7px, transparent 7px 10px)';
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
      if (modal.classList.contains('modal-left-docked')
          || modal.classList.contains('modal-right-docked')
          || modal.classList.contains('email-snap-left')) return false;
      if (modal.style.display === 'none') return false;
      const content = _resolveDockNodes(modal)?.content;
      const r = content?.getBoundingClientRect?.();
      return !!r && r.width > 0 && r.height > 0;
    });
  };

  const _setWidth = (owner, side, clientX) => {
    const nodes = _resolveDockNodes(owner);
    const content = nodes?.content;
    if (!content) return 0;
    let w = 0;
    if (side === 'right') {
      w = _clampRightDockWidth(window.innerWidth - clientX);
      content._userDockWidth = w;
      content.style.left = 'auto';
      content.style.right = '0';
      content.style.width = w + 'px';
      content.style.maxWidth = w + 'px';
      document.body.classList.add('right-dock-active');
      document.documentElement.style.setProperty('--right-dock-w', w + 'px');
      if (_shouldAutoCollapseSidebar(w)) {
        _collapseSidebarToRail();
        if (content._preDockSnapshot) content._preDockSnapshot.collapsedSidebar = true;
      }
    } else {
      const left = _leftNavRight();
      w = _clampLeftDockWidth(clientX - left, left);
      content._userDockWidth = w;
      content._emailDocSplitUserW = w;
      content.style.left = left + 'px';
      content.style.right = 'auto';
      content.style.width = w + 'px';
      content.style.maxWidth = w + 'px';
      document.body.classList.add('left-dock-active');
      document.documentElement.style.setProperty(
        '--left-dock-w',
        document.body.classList.contains('email-doc-split-active') ? '0px' : w + 'px',
      );
    }
    _positionEdgeDockResizeHandles();
    return w;
  };

  _edgeDockHandlePositioner = () => {
    const splitOwnsLeftSeam = document.body.classList.contains('email-doc-split-active')
      && document.body.classList.contains('doc-view')
      && window.innerWidth > 768;
    for (const side of ['left', 'right']) {
      const handle = handles[side];
      if (window.innerWidth <= 768 || (side === 'left' && splitOwnsLeftSeam)) {
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
      const r = content.getBoundingClientRect();
      const x = side === 'right' ? r.left : r.right;
      if (!Number.isFinite(x) || x <= 0 || x >= window.innerWidth) {
        _hideHandle(handle);
        continue;
      }
      _setStyle(handle, 'display', 'block');
      _setStyle(handle, 'left', (x - 5) + 'px');
      _setStyle(handle, 'zIndex', String(_zIndexFor(owner) + 1));
    }
  };

  for (const side of ['left', 'right']) {
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
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.body.classList.add('edge-dock-resizing');
      _setWidth(owner, side, e.clientX);
      const onMove = (ev) => {
        ev.preventDefault();
        _setWidth(owner, side, ev.clientX);
      };
      const onUp = (ev) => {
        try { handle.releasePointerCapture?.(e.pointerId); } catch (_) {}
        document.removeEventListener('pointermove', onMove, true);
        document.removeEventListener('pointerup', onUp, true);
        document.removeEventListener('pointercancel', onUp, true);
        document.body.classList.remove('edge-dock-resizing');
        document.body.style.cursor = prevCursor;
        document.body.style.userSelect = prevUserSelect;
        const finalW = side === 'right'
          ? parseFloat(document.documentElement.style.getPropertyValue('--right-dock-w')) || content?.getBoundingClientRect?.().width || 0
          : content?.getBoundingClientRect?.().width || 0;
        if (finalW) _saveDockWidth(owner, content, side, finalW);
        ev.preventDefault();
      };
      document.addEventListener('pointermove', onMove, true);
      document.addEventListener('pointerup', onUp, true);
      document.addEventListener('pointercancel', onUp, true);
    });
  }

  new MutationObserver(_positionEdgeDockResizeHandles).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  new MutationObserver(_positionEdgeDockResizeHandles).observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
  let raf = 0;
  const schedulePosition = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      _positionEdgeDockResizeHandles();
    });
  };
  new MutationObserver(schedulePosition).observe(document.body, { childList: true });
  window.addEventListener('resize', _positionEdgeDockResizeHandles);
  window.addEventListener('odysseus:modal-opened', _positionEdgeDockResizeHandles);
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
  stripe.style.zIndex = '9999';
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
    const w = _clampEmailDocSplitWidth(clientX - left, left);
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
