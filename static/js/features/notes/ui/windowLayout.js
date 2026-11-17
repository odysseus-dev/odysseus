/** Shared-window integration: safe fullscreen bounds, docking and stacking. */
export function createWindowLayout({
  makeWindowDraggable,
  snapModalToZone,
  clearDockSide,
  applyEdgeDock,
  topToolWindowZ
}) {
  function _notesFullscreenSafeRect() {
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    let left = 0;
    let right = vw;

    const sidebar = document.getElementById('sidebar');
    const rail = document.getElementById('icon-rail');
    const hamburgerRight = document.body.classList.contains('hamburger-right')
      || sidebar?.classList.contains('right-side')
      || rail?.classList.contains('right-side');

    const reserve = (el) => {
      if (!el || getComputedStyle(el).display === 'none') return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      if (hamburgerRight) right = Math.min(right, rect.left);
      else left = Math.max(left, rect.right);
    };

    if (sidebar && !sidebar.classList.contains('hidden')) reserve(sidebar);
    reserve(rail);

    // The fixed hamburger can remain visible even when the rail/sidebar is
    // collapsed. Reserve its strip too so fullscreen Notes does not sit beneath it.
    const hamburger = document.getElementById('hamburger-btn');
    if (hamburger && getComputedStyle(hamburger).display !== 'none') {
      const rect = hamburger.getBoundingClientRect();
      const pad = 8;
      if (hamburgerRight) right = Math.min(right, rect.left - pad);
      else left = Math.max(left, rect.right + pad);
    }

    left = Math.max(0, Math.min(left, vw - 80));
    right = Math.max(left + 80, Math.min(right, vw));
    return { left, top: 0, width: right - left, height: vh };
  }

  function _wireNotesWindow(pane) {
    if (!pane || pane.dataset.windowDragWired === '1') return;
    const header = pane.querySelector('.notes-pane-header');
    if (!header) return;
    pane.dataset.windowDragWired = '1';
    makeWindowDraggable(pane, {
      content: pane,
      header,
      fsClass: 'notes-window-fullscreen',
      skipSelector: 'button, input, select, textarea, label, .notes-mobile-grabber',
      enableDock: true,
      enableLeftDock: true,
      onEnterFullscreen: () => {
        pane.classList.add('notes-window-fullscreen');
        snapModalToZone(pane, {
          name: 'fullscreen',
          rect: _notesFullscreenSafeRect(),
        });
      },
      onExitFullscreen: () => {
        _restoreNotesSidebarDock(pane);
      },
    });
  }

  function _clearNotesSnapStyles(pane) {
    if (!pane) return;
    const hadLeft = pane.classList.contains('modal-left-docked');
    const hadRight = pane.classList.contains('modal-right-docked');
    pane.classList.remove('notes-window-fullscreen', 'modal-left-docked', 'modal-right-docked');
    if (hadLeft) clearDockSide('left', pane);
    if (hadRight) clearDockSide('right', pane);
    ['position', 'left', 'top', 'right', 'bottom', 'width', 'max-width', 'height',
      'max-height', 'margin', 'transform', 'border-radius']
      .forEach((prop) => pane.style.removeProperty(prop));
    delete pane.dataset._tilePreSnap;
    delete pane.dataset._tileZone;
    delete pane._preDockSnapshot;
    delete pane._dockSide;
    delete pane._dockSuspended;
  }

  function _restoreNotesSidebarDock(pane) {
    if (!pane || window.innerWidth <= 768) return;
    _clearNotesSnapStyles(pane);
    if (!pane.isConnected) return;
    applyEdgeDock(pane, 'right');
  }

  function _topToolWindowZ(exclude = null) {
    return topToolWindowZ({ exclude });
  }

  function _bringNotesToFront(pane = document.getElementById('notes-pane')) {
    if (!pane) return;
    const backdrop = document.getElementById('notes-pane-backdrop') || pane.parentElement;
    const z = _topToolWindowZ(backdrop) + 1;
    if (backdrop) backdrop.style.setProperty('z-index', String(z), 'important');
    try {
      window.dispatchEvent(new CustomEvent('odysseus:modal-opened', {
        detail: { id: 'notes-panel', modal: pane },
      }));
    } catch (_) {}
  }


  return { _wireNotesWindow, _restoreNotesSidebarDock, _bringNotesToFront };
}
