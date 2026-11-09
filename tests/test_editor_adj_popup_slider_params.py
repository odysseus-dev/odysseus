"""Every adjustment popup that renders slider rows must store their values.

``buildAdjBody`` renders ``sliderRow`` controls per adjustment type, and
``onAdjSliderInput`` maps a moved slider onto ``layer._stagedAdj.params``. A
type with rows but no input branch shows a working slider whose value is
silently dropped on OK (Vibrance regressed exactly this way). The browser
behavior is covered by tests/e2e/photo-editor/adjustment-layers.spec.js.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POPUP = (ROOT / "static/js/editor/fx/adj-popup.js").read_text(encoding="utf-8")


def _function_body(name: str, next_name: str) -> str:
    start = POPUP.index(f"function {name}(")
    return POPUP[start:POPUP.index(f"function {next_name}(", start)]


def _types_with_slider_rows() -> set[str]:
    builder = _function_body("buildAdjBody", "onAdjSliderInput")
    branches = re.split(r"\}\s*else if \(|if \(type === ", builder)
    types = set()
    for branch in branches:
        names = re.findall(r"type === '([a-z-]+)'", "type === " + branch.split(")", 1)[0])
        if names and "sliderRow(" in branch:
            types.update(names)
    return types


def test_every_slider_adjustment_type_writes_its_params():
    rendered = _types_with_slider_rows()
    assert {"exposure", "white-balance", "vibrance", "black-white"} <= rendered, rendered
    handler = _function_body("onAdjSliderInput", "wireCurveEditor")
    handled = set(re.findall(r"type === '([a-z-]+)'", handler))
    missing = sorted(rendered - handled)
    assert not missing, f"slider rows whose input is never stored: {missing}"


def test_vibrance_slider_stores_the_raw_value():
    handler = _function_body("onAdjSliderInput", "wireCurveEditor")
    assert re.search(r"else if \(type === 'vibrance'\) \{\s*p\[key\] = raw;", handler)
