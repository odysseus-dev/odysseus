from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"
PIXEL_PASS = ROOT / "static/js/editor/fx/pixel-pass.js"


def test_desaturate_is_a_deterministic_local_op():
    src = EDITOR.read_text(encoding="utf-8")
    # Desaturate/Cmd+Shift+U is handled on the client, not sent to the model.
    assert r"\bde-?satur\w*" in src
    assert r"\b(?:remove|strip|drop|take\s+out)\s+(?:all\s+|the\s+)?colou?rs?\b(?!\s*cast)" in src
    assert "await _runQuickControlOp('adjustments.apply', { type: 'hue-saturation', params: { saturation: 0 } });" in src
    assert "'Desaturated the photo.'" in src


def test_auto_tone_levels_colour_use_the_autotone_op():
    src = EDITOR.read_text(encoding="utf-8")
    assert r"\bauto[-\s]?tone\b|\bauto[-\s]?levels?\b|\bauto[-\s]?colou?rs?\b" in src
    assert "await _runQuickControlOp('photo.autoTone', {});" in src
    # The op is registered in the editor control registry.
    assert "'photo.autoTone':" in src
    assert "auto tone/colour" in src


def test_auto_contrast_uses_the_levels_endpoint_stretch():
    src = EDITOR.read_text(encoding="utf-8")
    assert r"\bauto[-\s]?contrast\b" in src
    assert "await _runQuickControlOp('adjustments.apply', { type: 'levels', params: { inBlack: 12, inWhite: 243 } });" in src
    assert "'Added auto contrast.'" in src


def test_branches_run_before_the_generative_blur_branch():
    src = EDITOR.read_text(encoding="utf-8")
    # Guard rail: deterministic adjustments must be matched before the blur /
    # generative fall-throughs, otherwise "remove the colour" would leak to the
    # image model.
    desat = src.index("params: { saturation: 0 }")
    blur = src.index("depth\\s*of\\s*field")
    assert desat < blur


def test_hue_saturation_saturation_is_a_multiplier():
    # The desaturate branch passes saturation: 0, which is only correct because
    # the pixel pass treats saturation as a multiplier (1 = neutral).
    src = PIXEL_PASS.read_text(encoding="utf-8")
    assert "const saturationScale = Math.max(0, finiteOr(p.saturation, 1));" in src
