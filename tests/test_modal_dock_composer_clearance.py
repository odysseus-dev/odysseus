from pathlib import Path


CSS = Path("static/style.css").read_text(encoding="utf-8")
INIT_JS = Path("static/js/init.js").read_text(encoding="utf-8")
MODAL_MANAGER_JS = Path("static/js/modalManager.js").read_text(encoding="utf-8")
TILE_MANAGER_JS = Path("static/js/tileManager.js").read_text(encoding="utf-8")
EMAIL_LIBRARY_JS = Path("static/js/emailLibrary.js").read_text(encoding="utf-8")
WINDOW_DRAG_JS = Path("static/js/windowDrag.js").read_text(encoding="utf-8")
UI_JS = Path("static/js/ui.js").read_text(encoding="utf-8")
MODAL_SNAP_JS = Path("static/js/modalSnap.js").read_text(encoding="utf-8")


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
    assert "const _isTouchDockViewport = () => window.innerWidth <= 768" in INIT_JS
    assert "const touchDock = dock.id === 'minimized-dock' && _isTouchDockViewport();" in INIT_JS
    assert "const reserveHeight = touchDock ? rect.height * TOUCH_DOCK_HISTORY_RATIO : rect.height;" in INIT_JS
    assert "root.style.setProperty('--composer-clearance', clearance + 'px');" in INIT_JS
    assert "root.style.setProperty('--collapsed-overlay-space', collapsedOverlaySpace + 'px');" in INIT_JS
    assert "document.body.classList.toggle('has-collapsed-overlays', collapsedOverlaySpace > 0);" in INIT_JS


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
    assert "applyEdgeDock, clearDockSide" in MODAL_MANAGER_JS
    assert "function _releaseWindowDockState(modal, content)" in MODAL_MANAGER_JS
    assert "function _captureFullExpandReturnState(modal, content)" in MODAL_MANAGER_JS
    assert "function _restoreFullExpandReturnState(modal, content)" in MODAL_MANAGER_JS
    assert "content._fullExpandReturnState = returnState;" in MODAL_MANAGER_JS
    assert "const restoredDock = _restoreFullExpandReturnState(modal, content);" in MODAL_MANAGER_JS
    assert "try { applyEdgeDock(modal, state.side); }" in MODAL_MANAGER_JS
    assert "clearRightDock(modal)" in MODAL_MANAGER_JS
    assert "modal.classList.remove('email-snap-left');" in MODAL_MANAGER_JS
    assert "_clearEmailSplitGeometry();" in MODAL_MANAGER_JS
    assert "_releaseWindowDockState(modal, content);" in MODAL_MANAGER_JS
    assert "modal.classList.remove('modal-left-docked', 'modal-right-docked', 'email-snap-left');" in TILE_MANAGER_JS


def test_edge_docking_clears_the_opposite_side_first():
    assert "function _clearOppositeDockedWindows(side, owner)" in MODAL_SNAP_JS
    assert "const oppositeSide = side === 'left' ? 'right' : 'left';" in MODAL_SNAP_JS
    assert "if (existingSide !== oppositeSide) return;" in MODAL_SNAP_JS
    assert "detail: { side: existingSide, modal: existing, replacement: owner }" in MODAL_SNAP_JS
    assert "clearRightDock(existing, undefined, undefined, dockClass);" in MODAL_SNAP_JS
    assert "suspendDock(existing);" in MODAL_SNAP_JS
    assert "_clearOppositeDockedWindows(side, modal);" in MODAL_SNAP_JS
    assert "_requestDockReplacement(side, modal);" in MODAL_SNAP_JS


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
    assert "if (zone.name === 'fullscreen' && _isTouchLandscape()) return;" not in TILE_MANAGER_JS
    assert "function _isTouchPortrait()" in TILE_MANAGER_JS
    assert "if (_isTouchPortrait()) {" in TILE_MANAGER_JS


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


def test_docked_calendar_body_gets_page_scroll():
    marker = "#calendar-modal.modal-left-docked .cal-modal-content,"
    assert marker in CSS
    block = CSS[CSS.index(marker): CSS.index(marker) + 1200]
    assert "#calendar-modal.modal-right-docked .cal-modal-content" in block
    assert "#calendar-modal.modal-full-expanded .cal-modal-content" in block
    assert "display: flex;" in block
    assert "flex-direction: column;" in block
    assert "#calendar-modal.modal-left-docked #cal-body," in block
    assert "#calendar-modal.modal-right-docked #cal-body," in block
    assert "#calendar-modal.modal-full-expanded #cal-body" in block
    assert "flex: 1 1 0;" in block
    assert "overflow-y: auto;" in block
    assert "-webkit-overflow-scrolling: touch;" in block
    assert "overscroll-behavior: contain;" in block


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
