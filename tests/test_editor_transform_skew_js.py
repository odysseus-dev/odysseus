from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


STATE = read("static/js/editor/state.js")
POPUP = read("static/js/editor/build/transform-popup.js")
SESSION = read("static/js/editor/tools/transform-session.js")
MULTI = read("static/js/editor/multi-transform.js")


def test_state_has_skew_fields():
    assert "transformPendingSkewX: 0" in STATE
    assert "transformPendingSkewY: 0" in STATE


def test_popup_exposes_skew_fields():
    assert 'id="ge-transform-skew-x"' in POPUP
    assert 'id="ge-transform-skew-y"' in POPUP
    assert 'data-spin-for="ge-transform-skew-x"' in POPUP
    assert 'data-spin-for="ge-transform-skew-y"' in POPUP


def test_session_threads_skew_through_transform():
    assert "state.transformPendingSkewX" in SESSION
    assert "state.transformPendingSkewY" in SESSION
    assert "'#ge-transform-skew-x': state.transformPendingSkewX" in SESSION
    assert "skewX: state.transformPendingSkewX" in SESSION
    assert "state.transformPendingSkewX = target.skewX" in SESSION
    assert "Math.tan((geometry.skewX" in SESSION
    assert "Math.tan((geometry.skewY" in SESSION


def test_multitransform_geometry_carries_skew():
    assert "target.skewX || 0" in MULTI
    assert "target.skewY || 0" in MULTI
    assert "skewPadX" in MULTI
    assert "skewPadY" in MULTI
    assert "skewX," in MULTI
    assert "skewY," in MULTI
