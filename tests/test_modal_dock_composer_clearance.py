from pathlib import Path


CSS = Path("static/style.css").read_text(encoding="utf-8")
INIT_JS = Path("static/js/init.js").read_text(encoding="utf-8")
MODAL_MANAGER_JS = Path("static/js/modalManager.js").read_text(encoding="utf-8")
TILE_MANAGER_JS = Path("static/js/tileManager.js").read_text(encoding="utf-8")
EMAIL_LIBRARY_JS = Path("static/js/emailLibrary.js").read_text(encoding="utf-8")
EMAIL_INBOX_JS = Path("static/js/emailInbox.js").read_text(encoding="utf-8")
WINDOW_DRAG_JS = Path("static/js/windowDrag.js").read_text(encoding="utf-8")
UI_JS = Path("static/js/ui.js").read_text(encoding="utf-8")
MODAL_SNAP_JS = Path("static/js/modalSnap.js").read_text(encoding="utf-8")
CALENDAR_JS = Path("static/js/calendar.js").read_text(encoding="utf-8")
NOTES_JS = Path("static/js/notes.js").read_text(encoding="utf-8")
SIDEBAR_LAYOUT_JS = Path("static/js/sidebar-layout.js").read_text(encoding="utf-8")
GALLERY_EDITOR_JS = Path("static/js/galleryEditor.js").read_text(encoding="utf-8")
RIGHT_PANEL_JS = Path("static/js/editor/build/right-panel.js").read_text(encoding="utf-8")
TOPBAR_JS = Path("static/js/editor/wire-topbar.js").read_text(encoding="utf-8")
TOPBAR_MENUS_JS = Path("static/js/editor/wire-topbar-menus.js").read_text(encoding="utf-8")


def test_both_minimized_window_docks_clear_the_composer():
    assert "#minimized-dock {" in CSS
    assert "bottom: var(--composer-clearance, 12px);" in CSS
    assert "#modal-dock {" in CSS
    assert "bottom:var(--composer-clearance, 0px);" in CSS


def test_chat_history_reserves_space_for_collapsed_overlays():
    assert "padding-bottom: var(--collapsed-overlay-space, 0px);" in CSS
    assert "scroll-padding-bottom: calc(var(--collapsed-overlay-space, 0px) + 2px);" in CSS


def test_composer_clearance_tracks_input_and_attachment_height():
    assert "const chatBar = document.querySelector('.chat-input-bar');" in INIT_JS
    assert "const attachStrip = document.getElementById('attach-strip');" in INIT_JS
    assert "const COMPOSER_DOCK_GAP = 4;" in INIT_JS
    assert "const COLLAPSED_OVERLAY_HISTORY_GAP = 4;" in INIT_JS
    assert "const TOUCH_DOCK_HISTORY_RATIO = 0.75;" in INIT_JS
    assert "const _setRootPxIfChanged = (name, value) => {" in INIT_JS
    assert "if (root.style.getPropertyValue(name) !== next)" in INIT_JS
    assert "const _queueComposerClearanceSync = () => {" in INIT_JS
    assert "const _isTouchDockViewport = () => window.innerWidth <= 768" in INIT_JS
    assert "const touchDock = dock.id === 'minimized-dock' && _isTouchDockViewport();" in INIT_JS
    assert "const reserveHeight = touchDock ? rect.height * TOUCH_DOCK_HISTORY_RATIO : rect.height;" in INIT_JS
    assert "_setRootPxIfChanged('--composer-clearance', clearance);" in INIT_JS
    assert "_setRootPxIfChanged('--collapsed-overlay-space', collapsedOverlaySpace);" in INIT_JS
    assert "_toggleBodyClassIfChanged('has-collapsed-overlays', collapsedOverlaySpace > 0);" in INIT_JS
    assert "new MutationObserver(_queueComposerClearanceSync).observe(dock, {\n        childList: true,\n      });" in INIT_JS
    assert "subtree: true,\n        attributeFilter: ['class', 'style']," not in INIT_JS


def test_minimized_dock_saved_position_is_orientation_scoped():
    assert "dockPosByLayout: _dockPosByLayout," in MODAL_MANAGER_JS
    assert "_dockPosByLayout[_dockLayout] = _dockPos;" in MODAL_MANAGER_JS
    assert "_dockPos = _clampStoredDockPos(_dockPosByLayout[_dockLayout]);" in MODAL_MANAGER_JS
    assert "Legacy unscoped" in MODAL_MANAGER_JS


def test_minimized_dock_can_reset_to_default_home_position():
    assert "const DEFAULT_DOCK_RESET_RADIUS = 72;" in MODAL_MANAGER_JS
    assert "const DEFAULT_DOCK_HOME_BAND_HEIGHT = 132;" in MODAL_MANAGER_JS
    assert "const TOP_LEFT_FALLBACK_SLOP = 16;" in MODAL_MANAGER_JS
    assert "function _defaultDockPosition(dock, width, height)" in MODAL_MANAGER_JS
    assert "function _isNearDefaultDockPosition(dock, left, top, width, height)" in MODAL_MANAGER_JS
    assert "function _isInDefaultDockHomeBand(dock, left, top, width, height)" in MODAL_MANAGER_JS
    assert "function _isDefaultDockDrop(dock, left, top, width, height)" in MODAL_MANAGER_JS
    assert "function _isTopLeftFallbackPosition(pos)" in MODAL_MANAGER_JS
    assert "function _pruneFallbackChipPositions()" in MODAL_MANAGER_JS
    assert "function _resetDockToDefault(dock)" in MODAL_MANAGER_JS
    assert "const clearDragSurface = () => {" in MODAL_MANAGER_JS
    assert "const finishDragSurface = () => {" in MODAL_MANAGER_JS
    assert "const clearFreeChipStyles = () => {" in MODAL_MANAGER_JS
    assert "finishDragSurface();" in MODAL_MANAGER_JS
    assert "if (_pruneFallbackChipPositions()) _saveDockState();" in MODAL_MANAGER_JS
    assert "_chipPositions.clear();" in MODAL_MANAGER_JS
    assert "delete _dockPosByLayout[_dockLayout];" in MODAL_MANAGER_JS


def test_desktop_minimized_dock_uses_chatbar_width_and_side_scroll():
    assert "function _desktopChatbarDockRect(bounds = _dockWorkspaceBounds())" in MODAL_MANAGER_JS
    assert "if (_isTouchInput()) return null;" in MODAL_MANAGER_JS
    assert "const rect = _visibleRect(document.querySelector('.chat-input-bar'));" in MODAL_MANAGER_JS
    assert "return { left: Math.round(left), width };" in MODAL_MANAGER_JS
    assert "const desktopChatbarDock = !!_desktopChatbarDockRect(bounds);" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-chatbar-row', desktopChatbarDock);" in MODAL_MANAGER_JS
    assert "const chatbarDock = _desktopChatbarDockRect(bounds);" in MODAL_MANAGER_JS
    assert "if (chatbarDock) {\n    if (_dockPos) {" in MODAL_MANAGER_JS
    assert "_dockPos = null;\n      delete _dockPosByLayout[_dockLayout];\n      _saveDockState();" in MODAL_MANAGER_JS
    assert "dock.style.left = `${chatbarDock.left}px`;" in MODAL_MANAGER_JS
    assert "dock.style.width = `${chatbarDock.width}px`;" in MODAL_MANAGER_JS
    assert "dock.style.maxWidth = `${chatbarDock.width}px`;" in MODAL_MANAGER_JS
    assert "dock.style.removeProperty('width');" in MODAL_MANAGER_JS
    assert "dock.addEventListener('wheel', (e) => {" in MODAL_MANAGER_JS
    assert "if (!dock.classList.contains('dock-chatbar-row')) return;" in MODAL_MANAGER_JS
    assert "if (dock.scrollWidth <= dock.clientWidth + 1) return;" in MODAL_MANAGER_JS
    assert "dock.scrollLeft += delta;" in MODAL_MANAGER_JS
    assert "}, { passive: false });" in MODAL_MANAGER_JS
    marker = "#minimized-dock.dock-chatbar-row"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 1800]
    assert "flex-wrap: nowrap;" in block
    assert "justify-content: flex-start;" in block
    assert "overflow-x: auto;" in block
    assert "overflow-y: hidden;" in block
    assert "scrollbar-width: thin;" in block
    assert "touch-action: pan-x;" in block
    assert "box-sizing: border-box;" in block
    assert "mask-image: linear-gradient(90deg" in block
    assert "#minimized-dock.dock-chatbar-row .minimized-dock-chip" in block
    assert "flex: 0 0 auto;" in block


def test_touch_landscape_does_not_get_mobile_overlay_sheet_rules():
    marker = "Mobile bottom sheet modals"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 5600]
    assert "@media (max-width: 768px) {" in block
    assert "(orientation: landscape) and (hover: none)" not in block
    assert "(orientation: landscape) and (pointer: coarse)" not in block
    assert "#cookbook-modal .modal-content," in block
    assert "#calendar-modal .modal-content," in block
    assert "#research-overlay .modal-content," in block
    assert "max-height: 100dvh !important;" in block
    assert "height: 100dvh !important;" in block


def test_touch_landscape_gallery_editor_splits_tools_and_canvas():
    marker = "Touch landscape editor"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index("/* ── Group Chat", CSS.index(marker))]
    editor_marker = '#gallery-modal #gallery-editor-container[style*="flex"] .ge-editor-body'
    topbar_marker = '#gallery-modal #gallery-editor-container[style*="flex"] .ge-topbar'
    panel_marker = '#gallery-modal #gallery-editor-container[style*="flex"] .ge-right-panel'
    canvas_marker = '#gallery-modal #gallery-editor-container[style*="flex"] .ge-canvas-area'
    docked_canvas_marker = '#gallery-modal:is(.modal-left-docked, .modal-right-docked):not(.modal-full-expanded) #gallery-editor-container[style*="flex"] .ge-canvas-area'
    docked_panel_marker = '#gallery-modal:is(.modal-left-docked, .modal-right-docked):not(.modal-full-expanded) #gallery-editor-container[style*="flex"] .ge-right-panel'
    assert "@media (orientation: landscape) and (hover: none)" in block
    assert "(orientation: landscape) and (pointer: coarse)" in block
    assert editor_marker in block
    assert "flex-direction: row !important;" in block[block.index(editor_marker): block.index(panel_marker)]
    topbar_block = block[block.index(topbar_marker): block.index('#gallery-modal #gallery-editor-container[style*="flex"] .ge-toolbar')]
    assert "order: 0;" in topbar_block
    assert "overflow-x: auto;" in topbar_block
    assert "overflow-y: hidden;" in topbar_block
    assert "flex-wrap: nowrap;" in topbar_block
    assert "scrollbar-width: none;" in topbar_block
    assert ".ge-topbar::-webkit-scrollbar" in topbar_block
    assert ".ge-topbar-left" in topbar_block
    assert "flex: 0 0 auto;" in topbar_block
    panel_block = block[block.index(panel_marker): block.index('#gallery-modal #gallery-editor-container[style*="flex"] .ge-right-panel.expanded')]
    assert "position: static !important;" in panel_block
    assert "flex: 0 0 calc(50% - 26px) !important;" in panel_block
    assert "max-width: calc(50% - 26px);" in panel_block
    canvas_block = block[block.index(canvas_marker): block.index('#gallery-modal #gallery-editor-container[style*="flex"] .ge-panel-resize')]
    assert "display: flex !important;" in canvas_block
    assert "flex: 1 1 50% !important;" in canvas_block
    assert "display: none" not in canvas_block
    docked_canvas_block = block[block.index(docked_canvas_marker): block.index(docked_panel_marker)]
    assert "display: none !important;" in docked_canvas_block
    assert "flex: 0 0 0 !important;" in docked_canvas_block
    assert "width: 0 !important;" in docked_canvas_block
    docked_panel_block = block[block.index(docked_panel_marker):]
    assert "flex: 1 1 0 !important;" in docked_panel_block
    assert "display: flex !important;" in docked_panel_block
    assert "overscroll-behavior: contain;" in docked_panel_block
    assert "max-width: none !important;" in docked_panel_block
    assert ".ge-topbar" in docked_panel_block
    assert "overflow-x: auto;" in docked_panel_block
    assert "scrollbar-width: none;" in docked_panel_block
    assert ".ge-topbar-left" in docked_panel_block
    assert "flex: 0 0 auto;" in docked_panel_block
    assert ".ge-controls" in docked_panel_block
    assert "padding-bottom: calc(14px + env(safe-area-inset-bottom, 0px)) !important;" in docked_panel_block
    assert ".ge-layers-list" in docked_panel_block
    assert "padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px)) !important;" in docked_panel_block
    assert ".ge-control-row" in docked_panel_block
    assert "flex-wrap: wrap;" in docked_panel_block
    assert ".ge-image-menu" in docked_panel_block
    assert ".ge-save-menu" in docked_panel_block
    assert "max-height: min(72vh, 420px);" in docked_panel_block
    assert "if (_isLandscapeEditorSplit()) return false;" in GALLERY_EDITOR_JS
    assert "if (_isTouchLandscape() && _isDockedGalleryEditor()) return null;" in GALLERY_EDITOR_JS
    assert "function _resetDockedOptionScroll()" in GALLERY_EDITOR_JS
    assert "panel.scrollTop = 0;" in GALLERY_EDITOR_JS
    assert "if (isTouchLandscape()) return false;" in RIGHT_PANEL_JS
    assert "function isTouchLandscapeGalleryEditor()" in TOPBAR_MENUS_JS
    assert "function positionLandscapeMenu(btn, menu, minWidth = 180)" in TOPBAR_MENUS_JS
    assert "function editorMenuBounds(pad = 8)" in TOPBAR_MENUS_JS
    assert "const bounds = editorMenuBounds(8);" in TOPBAR_MENUS_JS
    assert "menu.style.position = 'fixed';" in TOPBAR_MENUS_JS
    assert "menu.style.maxHeight =" in TOPBAR_MENUS_JS
    assert "function topbarMenuBounds(pad = 8)" in TOPBAR_JS
    assert "const bounds = topbarMenuBounds(8);" in TOPBAR_JS
    assert "saveMenu.style.maxHeight =" in TOPBAR_JS


def test_android_sidebar_explicit_open_survives_viewport_noise():
    assert "let _mobileSidebarExplicitOpen = false;" in SIDEBAR_LAYOUT_JS
    assert "const isMobileSidebarViewport = () => window.innerWidth < 768 || isTouchLandscape();" in SIDEBAR_LAYOUT_JS
    assert "const markMobileSidebarOpen = () => {" in SIDEBAR_LAYOUT_JS
    assert "if (_mobileSidebarExplicitOpen && !isHidden && isMobileSidebarViewport()) return;" in SIDEBAR_LAYOUT_JS
    assert "const isVisualViewportEvent = e && window.visualViewport && e.currentTarget === window.visualViewport;" in SIDEBAR_LAYOUT_JS
    assert "if (!isVisualViewportEvent) {" in SIDEBAR_LAYOUT_JS
    assert "if (!isMobileSidebarViewport() || e?.type === 'orientationchange')" in SIDEBAR_LAYOUT_JS
    assert "let restored = false;" in SIDEBAR_LAYOUT_JS
    assert "if (restored) syncRailSide();" in SIDEBAR_LAYOUT_JS


def test_touch_layouts_keep_modal_window_controls_visible():
    marker = "Keep window controls available on Android sheets"
    block = CSS[CSS.index(marker) - 300: CSS.index(marker) + 900]
    assert ".modal-content .close-btn," in block
    assert ".modal-minimize-btn," in block
    assert ".modal-expand-btn," in block
    assert "[data-full-expand]" in block
    assert "display: inline-flex !important;" in block
    assert ".minimize-btn { display: none !important; }" not in CSS


def test_shared_modal_controls_include_full_expand_button():
    assert "function _injectExpandButton" in MODAL_MANAGER_JS
    assert "btn.className = 'modal-expand-btn';" in MODAL_MANAGER_JS
    assert "closeBtn.parentNode.insertBefore(btn, closeBtn);" in MODAL_MANAGER_JS
    assert "toggleFullExpand(modalId);" in MODAL_MANAGER_JS
    assert "force: true," in MODAL_MANAGER_JS
    assert "restoreModalSnap(modal)" in MODAL_MANAGER_JS
    assert "export function restoreModalSnap(modal)" in TILE_MANAGER_JS
    assert ".modal-expand-btn" in EMAIL_LIBRARY_JS
    assert "function _isTouchLandscape()" in MODAL_MANAGER_JS
    assert "function _isTouchPortrait()" in MODAL_MANAGER_JS
    assert "const hidden = _isTouchPortrait();" in MODAL_MANAGER_JS
    assert "Fullscreen hidden in portrait" in MODAL_MANAGER_JS
    assert "function _syncAllExpandButtons()" in MODAL_MANAGER_JS
    assert "window.addEventListener('orientationchange', _syncAllExpandButtons);" in MODAL_MANAGER_JS
    assert "if (_isTouchLandscape() && !wasExpanded) {" not in MODAL_MANAGER_JS
    assert "Fullscreen disabled in landscape" not in MODAL_MANAGER_JS


def test_full_expand_restores_previous_dock_or_window_state():
    assert "applyEdgeDock," in MODAL_MANAGER_JS
    assert "clearDockSide," in MODAL_MANAGER_JS
    assert "const _FULL_EXPAND_TOGGLE_LOCK_MS = 280;" in MODAL_MANAGER_JS
    assert "function _isFullExpandToggleLocked(modal)" in MODAL_MANAGER_JS
    assert "function _lockFullExpandToggle(modal)" in MODAL_MANAGER_JS
    assert "if (_isFullExpandToggleLocked(modal)) return true;" in MODAL_MANAGER_JS
    assert "_lockFullExpandToggle(modal);" in MODAL_MANAGER_JS
    assert "function _releaseWindowDockState(modal, content)" in MODAL_MANAGER_JS
    assert "function _captureFullExpandReturnState(modal, content)" in MODAL_MANAGER_JS
    assert "function _restoreFullExpandReturnState(modal, content)" in MODAL_MANAGER_JS
    assert "function _fitFullExpandedContentToModalFrame(modal)" in MODAL_MANAGER_JS
    assert "const rect = _isTouchLandscape() ? fullscreenWorkspaceRect() : modal.getBoundingClientRect();" in MODAL_MANAGER_JS
    assert "content.style.setProperty('left', `${Math.round(rect.left)}px`, 'important');" in MODAL_MANAGER_JS
    assert "content.style.setProperty('width', `${Math.round(rect.width)}px`, 'important');" in MODAL_MANAGER_JS
    assert "content.style.setProperty('max-width', 'none', 'important');" in MODAL_MANAGER_JS
    assert "content.dataset._tileZone = 'fullscreen';" in MODAL_MANAGER_JS
    assert "function _scheduleFullExpandGeometrySettle(modal)" in MODAL_MANAGER_JS
    assert "if (!modal) return;" in MODAL_MANAGER_JS
    assert "if (!modal.isConnected || modal.classList.contains('hidden') || !_isFullExpanded(modal)) return;" in MODAL_MANAGER_JS
    assert "_fitFullExpandedContentToModalFrame(modal);" in MODAL_MANAGER_JS
    assert "setTimeout(run, 560);" in MODAL_MANAGER_JS
    assert "setTimeout(run, 900);" in MODAL_MANAGER_JS
    assert "content._fullExpandReturnState = returnState;" in MODAL_MANAGER_JS
    assert "const restoredDock = _restoreFullExpandReturnState(modal, content);" in MODAL_MANAGER_JS
    assert "const dockWidth = applyEdgeDock(modal, state.side);" in MODAL_MANAGER_JS
    assert "return dockWidth > 0;" in MODAL_MANAGER_JS
    assert "clearRightDock(modal)" in MODAL_MANAGER_JS
    assert "modal.classList.remove('email-snap-left');" in MODAL_MANAGER_JS
    assert "_clearEmailSplitGeometry();" in MODAL_MANAGER_JS
    assert "_releaseWindowDockState(modal, content);" in MODAL_MANAGER_JS
    assert "_scheduleFullExpandGeometrySettle(modal);" in MODAL_MANAGER_JS
    assert "modal.classList.remove('modal-left-docked', 'modal-right-docked', 'email-snap-left');" in TILE_MANAGER_JS


def test_fullscreen_safe_rect_ignores_hidden_or_offscreen_nav_geometry():
    assert "function _isElementVisible(el)" in TILE_MANAGER_JS
    assert "parseFloat(cs.opacity || '1')" in TILE_MANAGER_JS
    assert "const visibleSpan = (el) => {" in TILE_MANAGER_JS
    assert "const prefersRightNav = document.body.classList.contains('hamburger-right');" in TILE_MANAGER_JS
    assert "const isRightNav = (el, span) =>" in TILE_MANAGER_JS
    assert "const left = Math.max(0, r.left);" in TILE_MANAGER_JS
    assert "const right = Math.min(window.innerWidth, r.right);" in TILE_MANAGER_JS
    assert "if (right - left <= 1) return null;" in TILE_MANAGER_JS
    assert "const sbSpan = !sidebar?.classList?.contains('hidden') ? visibleSpan(sidebar) : null;" in TILE_MANAGER_JS
    assert "Right-side nav can briefly report its old left-edge rect" in TILE_MANAGER_JS
    assert "rightEdge = Math.min(rightEdge, sbSpan.left);" in TILE_MANAGER_JS
    assert "leftEdge = Math.max(leftEdge, sbSpan.right);" in TILE_MANAGER_JS
    assert "const railSpan = visibleSpan(rail);" in TILE_MANAGER_JS
    assert "rightEdge = Math.min(rightEdge, railSpan.left);" in TILE_MANAGER_JS
    assert "export function fullscreenWorkspaceRect()" in TILE_MANAGER_JS
    assert "const snapRect = zoneName === 'fullscreen' ? _fullscreenRect() : rect;" in TILE_MANAGER_JS
    assert "content.style.setProperty('left',   snapRect.left   + 'px', 'important');" in TILE_MANAGER_JS
    assert "content.style.setProperty('max-width', 'none', 'important');" in TILE_MANAGER_JS
    assert "case 'fullscreen':     r = _fullscreenRect(); break;" in TILE_MANAGER_JS
    assert "fullscreenWorkspaceRect," in MODAL_MANAGER_JS
    assert "const rect = _isTouchLandscape() ? fullscreenWorkspaceRect() : modal.getBoundingClientRect();" in MODAL_MANAGER_JS
    assert "function _scheduleFullscreenSettle(content, zoneName)" in TILE_MANAGER_JS
    assert "if (zoneName !== 'fullscreen' || !_isTouchLandscape() || !content) return;" in TILE_MANAGER_JS
    assert "_reclampAllThrottled(false);" in TILE_MANAGER_JS
    assert "setTimeout(run, 520);" in TILE_MANAGER_JS
    marker = "body.hamburger-right:not(.email-doc-split-active) #email-lib-modal.email-lib-fullscreen:not(.modal-right-docked) .modal-content"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 260]
    assert "left: 0 !important;" in block
    assert "right: calc(var(--icon-rail-w, 48px) + var(--sidebar-w, 0px)) !important;" in block


def test_edge_docking_clears_the_opposite_side_first():
    assert "function _clearOppositeDockedWindows(side, owner)" in MODAL_SNAP_JS
    assert "const oppositeSide = side === 'left' ? 'right' : 'left';" in MODAL_SNAP_JS
    assert "if (existingSide !== oppositeSide) return;" in MODAL_SNAP_JS
    assert "detail: { side: existingSide, modal: existing, replacement: owner }" in MODAL_SNAP_JS
    assert "clearRightDock(existing, undefined, undefined, dockClass);" in MODAL_SNAP_JS
    assert "suspendDock(existing);" in MODAL_SNAP_JS
    assert "_clearOppositeDockedWindows(side, modal);" in MODAL_SNAP_JS
    assert "_requestDockReplacement(side, modal);" in MODAL_SNAP_JS


def test_right_edge_tile_snap_delegates_to_reserved_edge_dock():
    assert "import { applyEdgeDock } from './modalSnap.js';" in TILE_MANAGER_JS
    assert "reserved right dock" in TILE_MANAGER_JS
    assert "function _rightDockPreviewRect(safe)" in TILE_MANAGER_JS
    assert "const MAX_DESKTOP_EDGE_DOCK_WIDTH = 720;" in TILE_MANAGER_JS
    assert "const MAX_DESKTOP_EDGE_DOCK_RATIO = 0.44;" in TILE_MANAGER_JS
    assert "return { name: 'right-half', rect: _rightDockPreviewRect(safe) };" in TILE_MANAGER_JS
    assert "if (zoneName === 'right-half' && _modal) {" in TILE_MANAGER_JS
    assert "const dockW = applyEdgeDock(_modal, 'right');" in TILE_MANAGER_JS
    assert "if (dockW) {" in TILE_MANAGER_JS
    assert "return;" in TILE_MANAGER_JS
    assert "case 'right-half':     r = _rightDockPreviewRect(safe); break;" in TILE_MANAGER_JS


def test_android_tool_opens_auto_dock_and_replace_existing_dock():
    assert "canUseEdgeDock," in MODAL_MANAGER_JS
    assert "preferredEdgeDockSide," in MODAL_MANAGER_JS
    assert "function _isOdysseusAndroidApp()" in MODAL_MANAGER_JS
    assert "function _androidAutoDockEnabled()" in MODAL_MANAGER_JS
    assert "return _isOdysseusAndroidApp() && _isTouchLandscape() && canUseEdgeDock();" in MODAL_MANAGER_JS
    assert "function _scheduleAndroidDefaultDock(id, modal)" in MODAL_MANAGER_JS
    assert "return !_modalWindowContent(modal)?._dockSuspended;" in MODAL_MANAGER_JS
    assert "applyEdgeDock(modal, preferredEdgeDockSide(modal));" in MODAL_MANAGER_JS
    assert "function _handleModalShown(id, modal)" in MODAL_MANAGER_JS
    assert "if (!_scheduleAndroidDefaultDock(id, modal)) _applyRememberedDock(id);" in MODAL_MANAGER_JS
    assert "_handleModalShown(id, _modalEl);" in MODAL_MANAGER_JS
    assert "if (_isModalVisible(modal)) _scheduleAndroidDefaultDock(id, modal);" in MODAL_MANAGER_JS
    assert "export function preferredEdgeDockSide(owner = null)" in MODAL_SNAP_JS
    assert "if (_activeDockedWindows('left', owner).length) return 'left';" in MODAL_SNAP_JS
    assert "if (_activeDockedWindows('right', owner).length) return 'right';" in MODAL_SNAP_JS
    assert "return navRight ? 'left' : 'right';" in MODAL_SNAP_JS


def test_android_landscape_notes_open_as_docked_page_not_mobile_sheet():
    assert "preferredEdgeDockSide" in NOTES_JS
    assert "function _isNotesAndroidDockMode()" in NOTES_JS
    assert "return base && !_isNotesAndroidDockMode();" in NOTES_JS
    assert "applyEdgeDock(pane, preferredEdgeDockSide(pane));" in NOTES_JS


def test_touch_landscape_minimized_dock_compacts_and_pages_when_menu_open():
    assert "function _syncDockRowMode(dock, bounds = _dockWorkspaceBounds())" in MODAL_MANAGER_JS
    assert "function _usesCompactTouchChips()" in MODAL_MANAGER_JS
    assert "function _isTouchMenuOpen()" in MODAL_MANAGER_JS
    assert "const touchLandscape = _isTouchLandscape();" in MODAL_MANAGER_JS
    assert "const menuOpen = _isTouchMenuOpen();" in MODAL_MANAGER_JS
    assert "const pagedTouchDock = touchPortrait || (touchLandscape && menuOpen);" in MODAL_MANAGER_JS
    assert "const stackedTouchPages = touchPortrait && menuOpen;" in MODAL_MANAGER_JS
    assert "dock.style.maxWidth = `${Math.round(maxWidth)}px`;" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-single-row', touchLandscape && !pagedTouchDock);" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-paged-row', pagedTouchDock);" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-compact-chips', compactTouchChips);" in MODAL_MANAGER_JS
    assert "if (isTouchChipDock) chip.classList.add('chip-compact-touch');" in MODAL_MANAGER_JS
    assert "_syncDockRowMode(dock, bounds);" in MODAL_MANAGER_JS
    assert "const hasPagedStructure = !!current.querySelector('.minimized-dock-page');" in MODAL_MANAGER_JS
    assert "const wantsPagedStructure = current.classList.contains('dock-paged-row');" in MODAL_MANAGER_JS
    assert "if (hasChips && hasPagedStructure !== wantsPagedStructure) _renderDock();" in MODAL_MANAGER_JS
    marker = "#minimized-dock.dock-single-row"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 360]
    assert "flex-wrap: nowrap;" in block
    assert "overflow-x: auto;" in block
    assert "overflow-y: hidden;" in block
    assert "-webkit-overflow-scrolling: touch;" in block
    marker = ".minimized-dock-chip.chip-compact-touch"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 900]
    assert "width: 40px;" in block
    assert "height: 40px;" in block
    assert ".minimized-dock-chip.chip-compact-touch .minimized-dock-label" in block
    assert "display: none !important;" in block
    assert ".minimized-dock-chip.chip-compact-touch.chip-active" in block


def test_touch_portrait_minimized_dock_pages_in_four_icon_blocks():
    assert "const TOUCH_DOCK_PAGE_CHIPS = 4;" in MODAL_MANAGER_JS
    assert "const TOUCH_DOCK_ROW_PAGE_WIDTH = (TOUCH_DOCK_PAGE_CHIPS * 40) + ((TOUCH_DOCK_PAGE_CHIPS - 1) * 8) + 12;" in MODAL_MANAGER_JS
    assert "const TOUCH_DOCK_STACK_PAGE_WIDTH = 52;" in MODAL_MANAGER_JS
    assert "const TOUCH_DOCK_STACK_PAGE_HEIGHT = TOUCH_DOCK_ROW_PAGE_WIDTH;" in MODAL_MANAGER_JS
    assert "function _isTouchMenuOpen()" in MODAL_MANAGER_JS
    assert "!sidebar.classList.contains('hidden')" in MODAL_MANAGER_JS
    assert "const touchPortrait = _isTouchPortrait();" in MODAL_MANAGER_JS
    assert "const pagedTouchDock = touchPortrait || (touchLandscape && menuOpen);" in MODAL_MANAGER_JS
    assert "const stackedTouchPages = touchPortrait && menuOpen;" in MODAL_MANAGER_JS
    assert "dock.style.height = `${Math.round(TOUCH_DOCK_STACK_PAGE_HEIGHT)}px`;" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-paged-row', pagedTouchDock);" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-stacked-pages', stackedTouchPages);" in MODAL_MANAGER_JS
    assert "function _pageTouchDock(dock, direction, { wrap = false } = {})" in MODAL_MANAGER_JS
    assert "if (next == null) next = wrap ? pageOffsets[pageOffsets.length - 1] : 0;" in MODAL_MANAGER_JS
    assert "const current = stacked ? (dock.scrollTop || 0) : (dock.scrollLeft || 0);" in MODAL_MANAGER_JS
    assert "page.offsetTop - dock.clientTop" in MODAL_MANAGER_JS
    assert "if (stacked) dock.scrollTo({ top: next, behavior: 'smooth' });" in MODAL_MANAGER_JS
    assert "_settleTouchDockPage(dock, next, stacked);" in MODAL_MANAGER_JS
    assert "function _settleTouchDockPage(dock, next, stacked)" in MODAL_MANAGER_JS
    assert "if (!stacked || !dock) return;" in MODAL_MANAGER_JS
    assert "dock._touchDockSettleTimers.forEach(clearTimeout);" in MODAL_MANAGER_JS
    assert "dock.style.scrollSnapType = 'none';" in MODAL_MANAGER_JS
    assert "dock.scrollTop = next;" in MODAL_MANAGER_JS
    assert "const rawPageOffsets = [...dock.querySelectorAll('.minimized-dock-page')]" in MODAL_MANAGER_JS
    assert "const offsetOrigin = rawPageOffsets[0] || 0;" in MODAL_MANAGER_JS
    assert "dock.scrollTo({ left: next, behavior: 'smooth' });" in MODAL_MANAGER_JS
    assert "touchPage.className = 'minimized-dock-page';" in MODAL_MANAGER_JS
    assert "touchPageChipCount % TOUCH_DOCK_PAGE_CHIPS === 0" in MODAL_MANAGER_JS
    assert "dock.contains(chip)" in MODAL_MANAGER_JS
    assert "let pagedSwipeHandled = false;" in MODAL_MANAGER_JS
    assert "dock.classList.contains('dock-paged-row')" in MODAL_MANAGER_JS
    assert "const stackedDock = dock.classList.contains('dock-stacked-pages');" in MODAL_MANAGER_JS
    assert "const horizontalSwipe = !stackedDock && Math.abs(dx) > Math.abs(dy) * 1.25;" in MODAL_MANAGER_JS
    assert "const verticalStackSwipe = stackedDock && Math.abs(dy) > Math.abs(dx) * 1.05;" in MODAL_MANAGER_JS
    assert "const direction = verticalStackSwipe ? (dy < 0 ? 1 : -1) : (dx < 0 ? 1 : -1);" in MODAL_MANAGER_JS
    assert "pagedSwipeHandled = _pageTouchDock(dock, direction, { wrap: stackedDock });" in MODAL_MANAGER_JS
    marker = "#minimized-dock.dock-paged-row"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 3000]
    assert "flex-wrap: nowrap;" in block
    assert "overflow-x: auto;" in block
    assert "scroll-snap-type: x mandatory;" in block
    assert "overscroll-behavior-x: contain;" in block
    assert "gap: 0;" in block
    assert "padding: 0;" in block
    assert "pointer-events: auto;" in block
    assert "#minimized-dock.dock-paged-row .minimized-dock-page" in block
    assert "flex-direction: row;" in block
    assert "flex: 0 0 100%;" in block
    assert "min-width: 100%;" in block
    assert "box-sizing: border-box;" in block
    assert "padding: 6px;" in block
    assert "scroll-snap-align: start;" in block
    assert "#minimized-dock.dock-paged-row.dock-stacked-pages .minimized-dock-page" in block
    assert "flex-direction: column-reverse;" in block
    assert "min-height: 100%;" in block
    assert "#minimized-dock.dock-paged-row.dock-stacked-pages" in block
    assert "flex-direction: column;" in block
    assert "overflow-y: auto;" in block
    assert "scroll-snap-type: y mandatory;" in block
    assert "scroll-padding-top: 0;" in block
    assert "scroll-padding-bottom: 0;" in block
    assert "linear-gradient(180deg" in block
    assert "inset 0 16px 14px -18px" in block
    assert "inset 0 -16px 14px -18px" in block
    assert "touch-action: pan-y;" in block
    assert "#minimized-dock.dock-paged-row .minimized-dock-chip" in block
    assert "flex: 0 0 40px;" in block
    assert "width: 40px;" in block
    assert "height: 40px;" in block
    assert "#minimized-dock.dock-paged-row .minimized-dock-chip:nth-child(4n+1)" not in block


def test_android_suppresses_email_unread_dock_badge_without_resizing_dock():
    assert "#minimized-dock.dock-paged-row.dock-compact-chips:not(.dock-stacked-pages) .minimized-dock-page" not in CSS
    assert '.minimized-dock-chip.chip-compact-touch[data-modal-id="email-lib-modal"][data-email-unread-label]::after' not in CSS
    assert "function _isOdysseusAndroidApp()" in EMAIL_LIBRARY_JS
    assert "delete chip.dataset.emailUnreadLabel;" in EMAIL_LIBRARY_JS
    assert "if (_isOdysseusAndroidApp()) return;" in EMAIL_LIBRARY_JS


def test_android_touch_docking_is_landscape_only_without_enabling_resize():
    assert "Touch portrait stays sheet-only. Default true." in WINDOW_DRAG_JS
    assert "Touch landscape bypasses the default" in WINDOW_DRAG_JS
    assert "explicit callsite opt-outs still win." in WINDOW_DRAG_JS
    assert "function _isTouchPortraitDockBlocked()" in WINDOW_DRAG_JS
    assert "const hasCustomMobileSkip = typeof options.mobileSkip === 'number';" in WINDOW_DRAG_JS
    assert "const mobileSkip = hasCustomMobileSkip ? options.mobileSkip : 768;" in WINDOW_DRAG_JS
    assert "const enableFullscreen = false;" in WINDOW_DRAG_JS
    assert "dock takes over from fullscreen" not in WINDOW_DRAG_JS
    assert "Dragging a fullscreen window to a SIDE edge" not in WINDOW_DRAG_JS
    assert "DOCK_EDGE_PX" not in WINDOW_DRAG_JS
    assert "const resizeMobileSkip = (typeof options.resizeMobileSkip === 'number')" in WINDOW_DRAG_JS
    assert "mobileSkip: resizeMobileSkip," in WINDOW_DRAG_JS
    assert "const _shouldSkipDrag = () => _isTouchPortraitDockBlocked()" in WINDOW_DRAG_JS
    assert "&& (hasCustomMobileSkip || !_isTouchLandscape()));" in WINDOW_DRAG_JS
    assert "const _dockAllowed = () => enableDock && (!_isTouchInput() || _isTouchLandscape());" in WINDOW_DRAG_JS
    assert "if (_shouldSkipDrag()) return;" in WINDOW_DRAG_JS
    assert "const dockAllowed = _dockAllowed();" in WINDOW_DRAG_JS
    assert "const activeRightDock = dockAllowed ? rightDock : null;" in WINDOW_DRAG_JS
    assert "if (enableTouch) header.style.touchAction = 'none';" in WINDOW_DRAG_JS
    assert "mostly-vertical downward pulls" in WINDOW_DRAG_JS
    assert "try { window._modalWindowDragging = true; } catch (_) {}" in WINDOW_DRAG_JS
    assert "if (ev.cancelable) ev.preventDefault();" in WINDOW_DRAG_JS
    assert "document.addEventListener('touchmove', onMove, { passive: false, capture: true });" in WINDOW_DRAG_JS
    assert "window._modalWindowDragging = false;" in WINDOW_DRAG_JS
    assert "if (window._modalWindowDragging)" in UI_JS
    assert "_swipeTarget = null;" in UI_JS
    assert "export function canUseEdgeDock()" in MODAL_SNAP_JS
    assert "return !_isTouchInput() || _isTouchLandscape();" in MODAL_SNAP_JS
    assert "if (!canUseEdgeDock()) {\n    _clearDocksForDisabledViewport();\n    return 0;\n  }" in MODAL_SNAP_JS
    assert "const DOCKED_CONTENT_INLINE_PROPS = [" in MODAL_SNAP_JS
    assert "content.style.removeProperty(prop);" in MODAL_SNAP_JS
    assert "function _clearTileSnapResidue(content)" in MODAL_SNAP_JS
    assert "delete content.dataset._tileZone;" in MODAL_SNAP_JS
    assert "delete content.dataset._tilePreSnap;" in MODAL_SNAP_JS
    assert "function _clearDocksForDisabledViewport()" in MODAL_SNAP_JS
    assert "This is a viewport-mode change, not a user drag-undock." in MODAL_SNAP_JS
    assert "_clearTileSnapResidue(content);" in MODAL_SNAP_JS
    assert "_onDockedModalGone(owner, _dockClassForSide(side));" in MODAL_SNAP_JS
    assert "clearRightDock(owner, undefined, undefined" not in MODAL_SNAP_JS
    assert "if (!canUseEdgeDock()) {" in MODAL_SNAP_JS
    assert "function _hasFinePointer()" in TILE_MANAGER_JS
    assert "Coarse-touch Android landscape uses edge docking" in TILE_MANAGER_JS
    assert "return window.innerWidth > 768 && _hasFinePointer();" in TILE_MANAGER_JS
    assert "function _touchLandscapeDockWidth()" in MODAL_SNAP_JS
    assert "function _rightNavConsumesWorkspace()" in MODAL_SNAP_JS
    assert "const TOUCH_LANDSCAPE_SPLIT_ADJUST_PX = 96;" in MODAL_SNAP_JS
    assert "const TOUCH_LANDSCAPE_SPLIT_HIT_PX = 18;" in MODAL_SNAP_JS
    assert "function _touchLandscapeDockBounds()" in MODAL_SNAP_JS
    assert "function _clampTouchLandscapeDockWidth(width)" in MODAL_SNAP_JS
    assert "function _rightDockReserveWidth(width)" in MODAL_SNAP_JS
    assert "return width;" in MODAL_SNAP_JS
    assert "if (_isTouchLandscape()) return _clampTouchLandscapeDockWidth(width);" in MODAL_SNAP_JS
    assert "content?._touchLandscapeDockWidth || _touchLandscapeDockWidth()" in MODAL_SNAP_JS
    assert "function _setRightDockVars(width)" in MODAL_SNAP_JS
    assert "--right-dock-reserve-w" in MODAL_SNAP_JS
    assert "--left-dock-reserve-w" in MODAL_SNAP_JS
    assert "const touchSplit = _isTouchLandscape();" in MODAL_SNAP_JS
    assert "const handleW = touchSplit ? TOUCH_LANDSCAPE_SPLIT_HIT_PX : EDGE_DOCK_RESIZE_HANDLE_PX;" in MODAL_SNAP_JS
    assert "handle.title = touchSplit ? 'Drag to adjust split' : 'Drag to resize docked window; click to hide';" in MODAL_SNAP_JS
    assert "if (!_isTouchLandscape() && isTap && _requestDockMinimize(owner, side)) {" in MODAL_SNAP_JS
    assert "margin-left: var(--left-dock-reserve-w, var(--left-dock-w, 0px));" in CSS
    assert "margin-right: var(--right-dock-reserve-w, var(--right-dock-w, 0px));" in CSS
    assert "@media (orientation: portrait) and (hover: none)" in CSS
    assert "@media (orientation: landscape) and (hover: none)" in CSS
    assert "function _fullscreenRect()" in TILE_MANAGER_JS
    assert "function _viewportWorkspaceRect(inset = 4)" in TILE_MANAGER_JS
    assert "return _viewportWorkspaceRect(4);" in TILE_MANAGER_JS
    assert "const safe = _viewportWorkspaceRect(0);" in TILE_MANAGER_JS
    assert "case 'fullscreen':     r = _fullscreenRect(); break;" in TILE_MANAGER_JS
    assert "const rect = zone.name === 'fullscreen' ? _fullscreenRect() : zone.rect;" in TILE_MANAGER_JS
    assert "function _fullscreenOwnerRect(content)" not in TILE_MANAGER_JS
    assert "if (zone.name === 'fullscreen' && _isTouchLandscape()) return;" not in TILE_MANAGER_JS
    assert "function _isTouchPortrait()" in TILE_MANAGER_JS
    assert "if (_isTouchPortrait()) {" in TILE_MANAGER_JS


def test_desktop_modal_autowire_is_event_driven_but_android_keeps_dock_polling():
    assert "function _wireAutoWireScanner()" in MODAL_MANAGER_JS
    assert "if (_isOdysseusAndroidApp()) {\n    return setInterval(_scanAndWire, 1000);\n  }" in MODAL_MANAGER_JS
    assert "const _scanTimer = _wireAutoWireScanner();" in MODAL_MANAGER_JS
    assert "const _scanTimer = setInterval(_scanAndWire, 1000);" not in MODAL_MANAGER_JS
    assert "new MutationObserver((mutations) =>" in MODAL_MANAGER_JS
    assert "observe(document.body, { childList: true, subtree: true });" in MODAL_MANAGER_JS
    assert "window.addEventListener('focus', _queueAutoWireScan);" in MODAL_MANAGER_JS
    assert "document.addEventListener('visibilitychange'" in MODAL_MANAGER_JS


def test_email_unread_refresh_stays_live_for_new_mail():
    assert "_refreshUnreadCount();" in EMAIL_INBOX_JS
    assert "setInterval(_refreshUnreadCount, 60000);" in EMAIL_INBOX_JS


def test_pc_window_budget_minimizes_oldest_background_windows():
    assert "const DESKTOP_VISIBLE_WINDOW_LIMIT = 4;" in MODAL_MANAGER_JS
    assert "function _isDesktopWindowBudgetEnabled()" in MODAL_MANAGER_JS
    assert "return !_isOdysseusAndroidApp() && window.innerWidth > 768 && !_isTouchInput();" in MODAL_MANAGER_JS
    assert "function _enforceDesktopWindowBudget(activeId)" in MODAL_MANAGER_JS
    assert "if (!_isFloatingBudgetWindow(modal) || !_isModalVisible(modal)) continue;" in MODAL_MANAGER_JS
    assert "visible.length - DESKTOP_VISIBLE_WINDOW_LIMIT" in MODAL_MANAGER_JS
    assert "if (victim.id === activeId) continue;" in MODAL_MANAGER_JS
    assert "if (minimize(victim.id)) excess -= 1;" in MODAL_MANAGER_JS
    assert "if (id) setTimeout(() => _enforceDesktopWindowBudget(id), 0);" in MODAL_MANAGER_JS


def test_standalone_email_windows_are_modal_manager_windows():
    assert "Modals.register(winId, {" in EMAIL_LIBRARY_JS
    assert "label: 'Email'," in EMAIL_LIBRARY_JS
    assert "icon: _EMAIL_ICON_PATH," in EMAIL_LIBRARY_JS
    assert "closeFn: closeWindow," in EMAIL_LIBRARY_JS
    assert "Modals.injectMinimizeButton(modal, winId)" in EMAIL_LIBRARY_JS
    assert "Modals.close(winId);" in EMAIL_LIBRARY_JS


def test_mobile_overlay_lists_have_nested_scrollers():
    marker = "#doclib-modal .doclib-modal-content,\n      #email-lib-modal .doclib-modal-content"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 2200]
    assert "display: flex !important;" in block
    assert "#doclib-modal .modal-body," in block
    assert "#email-lib-modal .modal-body {" in block
    assert "flex: 1 1 0 !important;" in block
    assert "#doclib-modal .doclib-grid:not(:has(.doclib-card-expanded))," in block
    assert "#email-lib-modal .doclib-grid:not(:has(.doclib-card-expanded))" in block
    assert "overflow-y: auto !important;" in block
    assert "-webkit-overflow-scrolling: touch;" in block


def test_docked_calendar_splitter_keeps_calendar_visible():
    marker = "#calendar-modal.modal-left-docked .cal-modal-content,"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 5200]
    assert "#calendar-modal.modal-right-docked .cal-modal-content" in block
    assert "#calendar-modal.modal-full-expanded .cal-modal-content" in block
    assert "display: flex;" in block
    assert "flex-direction: column;" in block
    assert "#calendar-modal.modal-left-docked #cal-body," in block
    assert "#calendar-modal.modal-right-docked #cal-body," in block
    assert "#calendar-modal.modal-full-expanded #cal-body" in block
    assert "flex: 1 1 0;" in block
    assert "overflow: hidden;" in block
    assert "#calendar-modal.modal-left-docked #cal-body.cal-form-mode," in block
    assert "#calendar-modal.modal-right-docked #cal-body.cal-form-mode," in block
    assert "#calendar-modal.modal-full-expanded #cal-body.cal-form-mode" in block
    assert "overflow-y: auto;" in block
    assert "-webkit-overflow-scrolling: touch;" in block
    assert "overscroll-behavior: contain;" in block
    assert "#calendar-modal #cal-body.cal-form-mode > .cal-form" in block
    assert "@media (orientation: landscape)" in block
    assert "#calendar-modal.modal-left-docked #cal-body > .cal-grid" in block
    assert "#calendar-modal.modal-left-docked #cal-body > .cal-wk-wrap" in block
    assert "flex-wrap: nowrap;" in block
    assert "overflow-x: auto;" in block


def test_calendar_splitter_height_is_clamped_to_current_layout():
    assert "const CAL_DETAIL_STORAGE_KEY = 'odysseus.cal.detailH';" in CALENDAR_JS
    assert "function _calDetailLayoutKey()" in CALENDAR_JS
    assert "modal?.classList?.contains('modal-full-expanded')" in CALENDAR_JS
    assert "function _calOuterBlockSize(el, { includeBox = true } = {})" in CALENDAR_JS
    assert "function _calDetailBounds(calBody)" in CALENDAR_JS
    assert "function _clampCalDetailHeight(calBody, value)" in CALENDAR_JS
    assert "function _setCalDetailHeight(calBody, value, { persist = false } = {})" in CALENDAR_JS
    assert "function _restoreCalDetailHeight(calBody)" in CALENDAR_JS
    assert "function _resetCalDetailHeight(calBody)" in CALENDAR_JS
    assert "_prepareCalendarBodyForCalendarView(body);" in CALENDAR_JS
    assert "_prepareCalendarBodyForForm(body);" in CALENDAR_JS
    assert "const adjustable = Math.max(0, available - gridMargins - detailMargins);" in CALENDAR_JS
    assert "calBody._calDetailLayoutKey && calBody._calDetailLayoutKey !== key" in CALENDAR_JS
    assert "visibleMax" not in CALENDAR_JS


def test_mobile_email_uses_full_height_sheet():
    marker = "Portrait email should cover the page"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 900]
    assert "#email-lib-modal .modal-content," in block
    assert ".email-reader-tab-modal .modal-content," in block
    assert ".email-window-modal .modal-content" in block
    assert "max-height: 100dvh !important;" in block
    assert "height: 100dvh !important;" in block
    assert "height: 90dvh !important;" not in block
    assert "#email-lib-modal," in block
    assert ".email-reader-tab-modal," in block
    assert ".email-window-modal" in block
    assert "align-items: stretch !important;" in block


def test_opened_email_reader_has_fit_page_toggle():
    assert "const _EMAIL_FIT_ICON" in EMAIL_LIBRARY_JS
    assert "function _fitPageButtonHtml()" in EMAIL_LIBRARY_JS
    assert 'data-act="fit-page"' in EMAIL_LIBRARY_JS
    assert 'aria-label="Fit email to page"' in EMAIL_LIBRARY_JS
    assert "function _wireEmailReaderFitPage(reader)" in EMAIL_LIBRARY_JS
    assert "reader.classList.toggle('email-reader-fit-page')" in EMAIL_LIBRARY_JS
    assert "_wireEmailReaderFitPage(reader);" in EMAIL_LIBRARY_JS
    assert "_wireEmailReaderFitPage(bodyEl);" in EMAIL_LIBRARY_JS
    assert EMAIL_LIBRARY_JS.count("_fitPageButtonHtml()") >= 4
    marker = ".email-card-reader.email-reader-fit-page"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 2500]
    assert "overflow-x: hidden;" in block
    assert ".email-reader-body.html-body" in block
    assert ".email-bubble-body" in block
    assert ".email-thread-turn-body" in block
    assert "max-width: 100% !important;" in block
    assert "min-width: 0 !important;" in block
    assert "inline-size: 100% !important;" in block
    assert "table-layout: fixed !important;" in block
    assert "white-space: pre-wrap !important;" in block


def test_touch_landscape_docked_email_reader_compacts_and_clips_html_width():
    marker = "#email-lib-modal.modal-left-docked .doclib-modal-content,"
    assert marker in CSS
    prelude = CSS[CSS.index(marker) - 120: CSS.index(marker)]
    assert "@media (orientation: landscape) and (hover: none)" in prelude
    block = CSS[CSS.index(marker): CSS.index(marker) + 13000]
    assert ".email-reader-tab-modal.modal-left-docked .modal-content" in block
    assert ".email-window-modal.modal-right-docked .modal-content" in block
    assert "#email-lib-modal.modal-left-docked #email-lib-stats" in block
    assert "display: none !important;" in block
    assert "#email-lib-modal.modal-left-docked .doclib-grid:not(:has(.doclib-card-expanded))" in block
    assert "#email-lib-modal.modal-right-docked .doclib-grid:not(:has(.doclib-card-expanded))" in block
    assert "overflow-y: auto !important;" in block
    assert "touch-action: pan-y;" in block
    assert "overscroll-behavior: contain;" in block
    assert "#email-lib-modal.modal-left-docked .email-card-reader" in block
    assert "#email-lib-modal.modal-left-docked .email-reader-actions" in block
    assert "max-width: min(184px, 48%) !important;" in block
    assert "#email-lib-modal.modal-left-docked .email-reader-actions-row" in block
    assert "display: flex !important;" in block
    assert "overflow-x: auto !important;" in block
    assert "#email-lib-modal.modal-left-docked .email-reader-body" in block
    assert "overflow-x: hidden !important;" in block
    assert ".email-reader-body.html-body :where(img, video, canvas, table" in block
    assert "max-width: 100% !important;" in block
    assert ".email-reader-body.html-body table" in block
    assert "display: block !important;" in block
    assert "table-layout: fixed !important;" in block
    assert "inline-size: 100% !important;" in block
    assert "min-inline-size: 0 !important;" in block
    assert ".email-reader-body.html-body td" in block
    assert "overflow-wrap: anywhere !important;" in block


def test_mobile_calendar_and_research_keep_scroll_height():
    calendar_marker = "Let the calendar pane shrink to nothing on mobile"
    assert calendar_marker in CSS
    calendar_block = CSS[CSS.index(calendar_marker) - 140: CSS.index(calendar_marker) + 700]
    assert "(orientation: landscape) and (hover: none)" not in calendar_block
    assert "(orientation: landscape) and (pointer: coarse)" not in calendar_block
    assert "#cal-body > .cal-grid," in calendar_block
    assert "#cal-body > .cal-wk-wrap" in calendar_block
    assert "min-height: 0;" in calendar_block

    research_marker = "Full-height mobile sheet, matching Cookbook and the other tool overlays."
    assert research_marker in CSS
    research_block = CSS[CSS.index(research_marker) - 140: CSS.index(research_marker) + 900]
    assert "(orientation: landscape) and (hover: none)" not in research_block
    assert "(orientation: landscape) and (pointer: coarse)" not in research_block
    assert "#research-overlay { align-items: stretch !important; justify-content: stretch !important; }" in research_block
    assert "#research-pane .research-pane-body" in research_block
    assert "touch-action: pan-y;" in research_block
