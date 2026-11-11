from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


STATE = read("static/js/editor/state.js")
POPUP = read("static/js/editor/build/transform-popup.js")
SESSION = read("static/js/editor/tools/transform-session.js")
MULTI = read("static/js/editor/multi-transform.js")
WARP = read("static/js/editor/perspective-warp.js")
SW = read("static/sw.js")


def test_perspective_module_shape():
    assert "export function keystoneCorners(" in WARP
    assert "export function warpRgba(" in WARP
    assert "export function perspectiveWarpCanvas(" in WARP
    assert "export function isPerspectiveActive(" in WARP


def test_state_has_perspective_fields():
    assert "transformPendingPerspX: 0" in STATE
    assert "transformPendingPerspY: 0" in STATE


def test_popup_exposes_perspective_fields():
    assert 'id="ge-transform-persp-x"' in POPUP
    assert 'id="ge-transform-persp-y"' in POPUP
    assert 'data-spin-for="ge-transform-persp-x"' in POPUP
    assert 'data-spin-for="ge-transform-persp-y"' in POPUP


def test_session_threads_perspective_through_transform():
    assert "from '../perspective-warp.js'" in SESSION
    assert "isPerspectiveActive" in SESSION
    assert "keystoneCorners" in SESSION
    assert "perspectiveWarpCanvas" in SESSION
    assert "'#ge-transform-persp-x': state.transformPendingPerspX" in SESSION
    assert "'#ge-transform-persp-y': state.transformPendingPerspY" in SESSION
    assert "perspectiveX: state.transformPendingPerspX" in SESSION
    assert "state.transformPendingPerspX = target.perspectiveX" in SESSION
    assert "const perspectiveX = target" in MULTI
    assert "perspectiveX," in MULTI
    assert "target?.perspectiveX ?? 0" in MULTI


def test_perspective_module_is_precached():
    assert "/static/js/editor/perspective-warp.js" in SW
