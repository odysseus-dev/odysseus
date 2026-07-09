from pathlib import Path


CSS = Path("static/style.css").read_text(encoding="utf-8")
INIT_JS = Path("static/js/init.js").read_text(encoding="utf-8")
INDEX_HTML = Path("static/index.html").read_text(encoding="utf-8")
MODAL_MANAGER_JS = Path("static/js/modalManager.js").read_text(encoding="utf-8")
MODAL_SNAP_JS = Path("static/js/modalSnap.js").read_text(encoding="utf-8")
EMAIL_LIBRARY_JS = Path("static/js/emailLibrary.js").read_text(encoding="utf-8")
NOTES_JS = Path("static/js/notes.js").read_text(encoding="utf-8")
SETTINGS_JS = Path("static/js/settings.js").read_text(encoding="utf-8")


def test_both_minimized_window_docks_clear_the_composer():
    assert "#minimized-dock {" in CSS
    assert "bottom: var(--composer-clearance, 12px);" in CSS
    assert "#modal-dock {" in CSS
    assert "bottom:var(--composer-clearance, 0px);" in CSS


def test_composer_clearance_tracks_input_and_attachment_height():
    assert "const chatBar = document.querySelector('.chat-input-bar');" in INIT_JS
    assert "const attachStrip = document.getElementById('attach-strip');" in INIT_JS
    assert "const COMPOSER_DOCK_GAP = 4;" in INIT_JS
    assert "const _uiScaleFactor = () => {" in INIT_JS
    assert "Math.ceil((window.innerHeight - top + COMPOSER_DOCK_GAP) / _uiScaleFactor())" in INIT_JS
    assert "root.style.setProperty('--composer-clearance', clearance + 'px');" in INIT_JS


def test_composer_clearance_and_scroll_button_follow_vertical_chat_resizes():
    assert "if (chatContainer) ro.observe(chatContainer);" in INIT_JS
    assert "chatContainer.addEventListener('transitionend', _queueComposerClearanceSync);" in INIT_JS
    assert "layoutObserver.observe(chatBar);" in INDEX_HTML
    assert "if (chatShell) layoutObserver.observe(chatShell);" in INDEX_HTML


def test_desktop_minimized_chips_track_the_scaled_chatbar_row():
    assert "function _desktopChatbarDockRect()" in MODAL_MANAGER_JS
    assert "left: Math.round(rect.left / scale)" in MODAL_MANAGER_JS
    assert "width: Math.floor(rect.width / scale)" in MODAL_MANAGER_JS
    assert "dock.classList.toggle('dock-chatbar-row', !!chatbarDock);" in MODAL_MANAGER_JS
    assert "document.querySelector('.chat-container')" in MODAL_MANAGER_JS
    assert "e.propertyName === 'margin-top'" in MODAL_MANAGER_JS
    assert "e.propertyName === 'margin-bottom'" in MODAL_MANAGER_JS
    assert "#minimized-dock.dock-chatbar-row" in CSS
    assert "const chatbarDock = _dockPos ? null : _desktopChatbarDockRect();" in MODAL_MANAGER_JS
    assert "scrollbar-width: thin;\n      pointer-events: none;" in CSS
    assert ":root.ui-scale-125 { --ui-scale-factor: 1.25;" in CSS


def test_top_and_bottom_docks_reserve_chat_and_document_rows():
    assert "body.top-dock-active .chat-container," in CSS
    assert "margin-top: var(--top-dock-reserve-h, var(--top-dock-h, 0px));" in CSS
    assert "body.bottom-dock-active .chat-container," in CSS
    assert "margin-bottom: var(--bottom-dock-reserve-h, var(--bottom-dock-h, 0px));" in CSS
    assert "body:is(.top-dock-active, .bottom-dock-active) .doc-editor-pane" in CSS
    assert "- var(--top-dock-reserve-h, var(--top-dock-h, 0px))" in CSS
    assert "- var(--bottom-dock-reserve-h, var(--bottom-dock-h, 0px))" in CSS
    assert "body:is(.top-dock-active, .bottom-dock-active) .chat-container.welcome-active .chat-input-bar" in CSS
    assert "margin-bottom: 16px;" in CSS
    assert ".modal.modal-top-docked .modal-content" in CSS
    assert ".modal.modal-bottom-docked .modal-content" in CSS


def test_email_document_split_honors_vertical_and_side_dock_reserves():
    top = "var(--top-dock-reserve-h, var(--top-dock-h, 0px))"
    bottom = "var(--bottom-dock-reserve-h, var(--bottom-dock-h, 0px))"
    right = "var(--right-dock-reserve-w, var(--right-dock-w, 0px))"
    for source in (MODAL_SNAP_JS, EMAIL_LIBRARY_JS, CSS):
        assert top in source
        assert bottom in source
        assert right in source
    assert "docPane.style.setProperty('height', 'auto', 'important');" in MODAL_SNAP_JS
    assert "docPane.style.setProperty('height', 'auto', 'important');" in EMAIL_LIBRARY_JS


def test_modal_lifecycle_recognizes_all_four_reserved_edges():
    assert "const _EDGE_DOCK_SIDES = ['left', 'right', 'top', 'bottom'];" in MODAL_MANAGER_JS
    assert "return _EDGE_DOCK_SIDES.includes(side) ? side : null;" in MODAL_MANAGER_JS
    assert "if (_isEdgeDocked(modal))" in MODAL_MANAGER_JS
    assert "const suspendedDockSide = contentBeforeClose?._dockSuspended\n    || _edgeDockSide(modalBeforeClose);" in MODAL_MANAGER_JS
    assert "new MutationObserver(reanchorAfterRootClassChange).observe(document.documentElement" in MODAL_SNAP_JS
    assert "attributeFilter: ['class']" in MODAL_SNAP_JS


def test_edge_resize_handles_settle_after_scale_and_only_show_on_hover():
    assert "requestAnimationFrame(_reanchorActiveDocks);" in MODAL_SNAP_JS
    assert "_setStyle(handle, 'background', 'transparent');" in MODAL_SNAP_JS
    assert ".edge-dock-resize-handle-top:hover" in CSS
    assert ".edge-dock-resize-handle-bottom:hover" in CSS


def test_constrained_settings_notes_and_calendar_remain_scrollable():
    assert "const dockSides = ['left', 'right', 'top', 'bottom'];" in SETTINGS_JS
    assert "activeDockSides.forEach((side) => clearDockSide(side, modalEl));" in SETTINGS_JS
    assert "const dockSides = ['left', 'right', 'top', 'bottom'];" in NOTES_JS
    assert "activeDockSides.forEach((side) => clearDockSide(side, pane));" in NOTES_JS
    assert "#settings-modal.modal-top-docked .settings-sidebar" in CSS
    assert "#settings-modal.modal-bottom-docked .settings-sidebar" in CSS
    assert "#calendar-modal:is(.modal-left-docked, .modal-right-docked, .modal-top-docked, .modal-bottom-docked) #cal-body" in CSS
