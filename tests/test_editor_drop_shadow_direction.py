from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")


def test_drop_shadow_dialog_selects_a_direction_and_distance():
    # Drop shadows are stored as x/y offsets, but the dialog has to expose a
    # direction: convert at the boundary instead of asking for H/V pixels.
    assert "function _shadowAngleDistance(params = {}) {" in EDITOR
    assert "function _shadowOffsets(values = {}) {" in EDITOR
    assert "angle: Math.round((Math.atan2(-y, x) * 180) / Math.PI)," in EDITOR
    assert "x: Math.round(Math.cos(radians) * distance)," in EDITOR
    assert "y: Math.round(-Math.sin(radians) * distance)," in EDITOR
    assert "{ key: 'distance', label: 'Distance', min: 0, max: 200, step: 1, value: _shadowAngleDistance" in EDITOR
    assert "{ key: 'angle', label: 'Direction', min: -180, max: 180, step: 1, value: _shadowAngleDistance" in EDITOR
    assert "{ key: 'x', label: 'Horizontal'" not in EDITOR
    assert "{ key: 'y', label: 'Vertical'" not in EDITOR
    # Both the add and the edit dialogs turn the values back into offsets.
    assert EDITOR.count("..._shadowOffsets(values)") == 2
    assert EDITOR.count("..._shadowOffsets(result)") == 2
    # Soft-shadow presets kept their metre; the preset loop migrates them.
    assert "{ ...preset.params, ..._shadowAngleDistance(preset.params) }" in EDITOR
