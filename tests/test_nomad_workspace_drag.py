from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_workspace_drag_module_is_loaded():
    html = _read("static/index.html")
    assert '/static/js/workspaceDrag.js' in html


def test_docked_nomad_windows_do_not_keep_modal_backdrop():
    css = _read("static/orbital.css")
    assert '.modal.modal-left-docked' in css
    assert '.modal.modal-right-docked' in css
    assert 'backdrop-filter: none !important' in css


def test_nomad_chat_reserves_both_dock_widths():
    css = _read("static/orbital.css")
    assert 'margin-left: calc(var(--left-dock-w, 0px) + 10px) !important' in css
    assert 'margin-right: calc(var(--right-dock-w, 0px) + 10px) !important' in css


def test_sidebar_components_offer_left_center_and_right_drop_targets():
    js = _read("static/js/workspaceDrag.js")
    assert "import { applyEdgeDock, clearRightDock } from './modalSnap.js'" in js
    assert 'data-side="left"' in js
    assert 'data-side="center"' in js
    assert 'data-side="right"' in js
    assert "applyEdgeDock(target, side)" in js


def test_touch_capable_windows_does_not_disable_mouse_drag():
    js = _read("static/js/workspaceDrag.js")
    css = _read("static/orbital.css")
    assert "matchMedia('(pointer: coarse)')" not in js
    assert '@media (max-width: 760px), (pointer: coarse)' not in css


def test_nomad_user_messages_use_uplink_panel_language():
    css = _read("static/orbital.css")
    assert ".msg-user .role::after" in css
    assert "content: 'UPLINK'" in css
    assert "clip-path: polygon(" in css
    assert "border-right-color: rgba(255,69,58,.62)" in css
