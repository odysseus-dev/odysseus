import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_JS = ROOT / "static" / "js" / "calendar.js"
STYLE_CSS = ROOT / "static" / "style.css"
UTILS_JS = ROOT / "static" / "js" / "calendar" / "utils.js"

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_calendar_readable_text_color_prefers_dark_ink_for_pastels():
    values = _node_eval(
        """
        import { _calReadableTextColor } from './static/js/calendar/utils.js';
        console.log(JSON.stringify({
          blue: _calReadableTextColor('#b0d7f7'),
          yellow: _calReadableTextColor('#f2dfbd'),
          shortHex: _calReadableTextColor('#abc')
        }));
        """
    )

    assert values == {
        "blue": "#111820",
        "yellow": "#111820",
        "shortHex": "#111820",
    }


def test_calendar_readable_text_color_keeps_light_text_for_dark_colors():
    values = _node_eval(
        """
        import { _calReadableTextColor } from './static/js/calendar/utils.js';
        console.log(JSON.stringify({
          navy: _calReadableTextColor('#1f3552'),
          red: _calReadableTextColor('#78252d'),
          variable: _calReadableTextColor('var(--accent)')
        }));
        """
    )

    assert values == {
        "navy": "#ffffff",
        "red": "#ffffff",
        "variable": "var(--fg)",
    }


def test_calendar_event_surfaces_use_computed_foreground_variable():
    calendar_js = CALENDAR_JS.read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")
    utils_js = UTILS_JS.read_text(encoding="utf-8")

    assert "_calReadableTextColor" in utils_js
    assert "function _calEventFg(ev)" in calendar_js
    assert "--cal-event-fg:${_calEventFg(md)}" in calendar_js
    assert "--cal-event-fg:${_calEventFg(ev)}" in calendar_js
    assert "color: var(--cal-event-fg, #fff);" in style_css
    assert "color: var(--cal-event-fg, var(--fg));" in style_css
