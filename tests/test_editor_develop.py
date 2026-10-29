import json
import re
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVELOP_MODULE = (ROOT / "static/js/editor/fx/develop.js").as_uri()
PIXEL_PASS = ROOT / "static/js/editor/fx/pixel-pass.js"
LAYER_HELPERS = ROOT / "static/js/editor/layer-helpers.js"
ADJUSTMENT_LAYER = ROOT / "static/js/editor/adjustment-layer.js"
ADJ_POPUP = ROOT / "static/js/editor/fx/adj-popup.js"
STUDIO_PANELS = ROOT / "static/js/editor/panels/studio-panels.js"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
WIRE_TOPBAR = ROOT / "static/js/editor/wire-topbar-menus.js"
SW = ROOT / "static/sw.js"
CSS = ROOT / "static/css/13-image-editor.css"


def run_node(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _develop(pixels, params, width=1, height=1):
    source = textwrap.dedent(
        f"""
        import {{developImageData}} from {json.dumps(DEVELOP_MODULE)};
        const data=new Uint8ClampedArray({json.dumps(pixels)});
        developImageData(data, {width}, {height}, {json.dumps(params)});
        console.log(JSON.stringify(Array.from(data)));
        """
    )
    return run_node(source)


def _grey(value):
    return [value, value, value, 255]


def test_no_op_params_return_the_source_unchanged():
    pixels = [120, 60, 200, 255]
    assert _develop(pixels, {}) == pixels
    assert _develop(pixels, {
        "exposure": 0, "contrast": 0, "highlights": 0, "shadows": 0,
        "whites": 0, "blacks": 0, "temperature": 0, "tint": 0,
        "clarity": 0, "vibrance": 0, "saturation": 0,
    }) == pixels


def test_exposure_and_contrast_move_tone_in_the_expected_direction():
    brighter = _develop(_grey(120), {"exposure": 1})
    assert brighter[0] > 120
    # Contrast pushes a value below mid-grey darker and one above brighter.
    dark = _develop(_grey(90), {"contrast": 60})
    bright = _develop(_grey(200), {"contrast": 60})
    assert dark[0] < 90
    assert bright[0] > 200


def test_white_balance_warms_and_cools():
    warm = _develop([128, 128, 128, 255], {"temperature": 80})
    cool = _develop([128, 128, 128, 255], {"temperature": -80})
    assert warm[0] > warm[2]  # more red than blue
    assert cool[2] > cool[0]  # more blue than red


def test_shadows_and_highlights_and_blacks_and_whites_shape_endpoints():
    lifted = _develop(_grey(40), {"shadows": 80})
    recovered = _develop(_grey(220), {"highlights": -80})
    crushed = _develop(_grey(30), {"blacks": 80})
    bright_white = _develop(_grey(230), {"whites": 80})
    assert lifted[0] > 40
    assert recovered[0] < 220
    assert crushed[0] < 30
    assert bright_white[0] > 230


def test_clarity_is_a_no_op_on_a_flat_field():
    flat = [128, 128, 128, 255] * 16
    assert _develop(flat, {"clarity": 60}, width=4, height=4) == flat


def test_vibrance_boosts_muted_more_than_saturated():
    muted = _develop([130, 120, 110, 255], {"vibrance": 60})
    saturated = _develop([220, 40, 40, 255], {"vibrance": 60})
    muted_spread = max(muted[:3]) - min(muted[:3])
    saturated_spread = max(saturated[:3]) - min(saturated[:3])
    # The muted patch gains a larger relative spread than the near-primary patch.
    assert muted_spread - (130 - 110) > 0
    assert saturated_spread - (220 - 40) < (muted_spread - (130 - 110)) * 4


def test_develop_is_registered_as_an_adjustment_type_everywhere():
    pixel = PIXEL_PASS.read_text()
    assert "import { developImageData } from './develop.js';" in pixel
    assert "if (adj.type === 'develop') {" in pixel
    assert "developImageData(d, w, h, p);" in pixel

    helpers = LAYER_HELPERS.read_text()
    assert "case 'develop':" in helpers
    assert "'develop': 'Develop'," in helpers
    assert re.search(r"'develop': '<svg", helpers)

    assert "'develop'," in ADJUSTMENT_LAYER.read_text()

    popup = ADJ_POPUP.read_text()
    assert "} else if (type === 'develop') {" in popup
    for key in ["temperature", "tint", "exposure", "contrast", "highlights",
                "shadows", "whites", "blacks", "clarity", "vibrance", "saturation"]:
        assert f"sliderRow('{key}'" in popup, key
    assert "p.exposure = raw / 100;" in popup

    panels = STUDIO_PANELS.read_text()
    assert "['develop', 'Develop']," in panels
    assert re.search(r"develop:\s*'<path", panels)

    assert 'data-image-action="adj-develop"' in TOPBAR.read_text()
    assert "adjustments.apply', { type: 'develop' }" in WIRE_TOPBAR.read_text()

    sw = SW.read_text()
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", sw)
    assert "'/static/js/editor/fx/develop.js'," in sw


def test_develop_panel_polish_groups_and_reset_affordance():
    popup = ADJ_POPUP.read_text()
    # Slider rows can carry a default so the reset arrow only shows when changed.
    assert 'data-default="${defaultValue}"' in popup
    assert 'row.classList.toggle(\'is-modified\'' in popup
    # The Develop body groups its sliders under Basic / Presence.
    assert 'ge-adj-develop' in popup
    assert '>Basic</div>' in popup
    assert '>Presence</div>' in popup
    for key in ["temperature", "tint", "exposure", "contrast", "highlights",
                "shadows", "whites", "blacks", "clarity", "vibrance", "saturation"]:
        assert f"sliderRow('{key}'" in popup and "0)" in popup

    css = CSS.read_text()
    assert ".ge-adj-develop .ge-adj-value {" in css
    assert "font-size: 12px;" in css
    assert ".ge-adj-group-label {" in css
    assert "min-width: 24px; min-height: 24px;" in css
    assert "margin-left: 6px;" in css
    assert ".ge-adj-develop .ge-adj-row.is-modified .ge-adj-revert" in css
