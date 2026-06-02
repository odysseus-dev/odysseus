"""Pin the calendar's text-contrast helper (#1141). Multi-day bars and the
week all-day strip paint the title directly on the solid event colour; the
event palette is deliberately pale, so a fixed `#fff`/`var(--fg)` left titles
unreadable. `_calTextColor` must pick a text colour that clears WCAG AA
(>= 4.5:1) against any solid event colour.

Driven through `node --input-type=module` so we exercise the real JS, mirroring
tests/test_compare_js.py. Skips if `node` isn't installed.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out_lines:
        raise AssertionError("node produced no stdout")
    return json.loads(out_lines[-1])


# The full set of solid colours an event title can be painted on: the pale
# CAL_COLORS swatches plus the CAL_PALETTE hex entries.
_EVENT_COLORS = [
    "#f0b5ba", "#e8ccb2", "#f2dfbd", "#cce0bc", "#b0d7f7",
    "#e2bcee", "#abdbe0", "#f0b5cc",                          # CAL_COLORS (pale)
    "#5b8abf", "#bf6b5b", "#5bbf7a", "#bf9a5b", "#9a5bbf",
    "#5bbfb8", "#bf8a5b", "#7070c0", "#bf5b8a",               # CAL_PALETTE
]


def test_text_color_clears_wcag_aa_on_every_event_color(node_available):
    """The chosen text colour must reach >= 4.5:1 contrast on each swatch."""
    colors_json = json.dumps(_EVENT_COLORS)
    script = textwrap.dedent(f"""
        const {{ _calTextColor }} = await import('./static/js/calendar/utils.js');
        const lin = (v) => {{ v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); }};
        const lum = (hex) => {{
          let h = hex.slice(1);
          if (h.length === 3) h = h.split('').map((c) => c + c).join('');
          return 0.2126*lin(parseInt(h.slice(0,2),16)) + 0.7152*lin(parseInt(h.slice(2,4),16)) + 0.0722*lin(parseInt(h.slice(4,6),16));
        }};
        const ratio = (a, b) => {{ const la = lum(a), lb = lum(b); const hi = Math.max(la, lb), lo = Math.min(la, lb); return (hi + 0.05) / (lo + 0.05); }};
        const out = {{}};
        for (const c of {colors_json}) {{
          const txt = _calTextColor(c);
          out[c] = {{ txt, ratio: ratio(txt, c) }};
        }}
        console.log(JSON.stringify(out));
    """)
    out = _run_node(script)
    for color, r in out.items():
        assert r["txt"] in ("#000", "#fff"), f"{color} -> {r['txt']}"
        assert r["ratio"] >= 4.5, f"{color}: contrast {r['ratio']:.2f} < 4.5 (text {r['txt']})"


def test_pale_swatches_get_dark_text(node_available):
    """Sanity check on the reported case: pale tints must take dark text."""
    script = textwrap.dedent("""
        const { _calTextColor } = await import('./static/js/calendar/utils.js');
        console.log(JSON.stringify(['#f2dfbd', '#abdbe0', '#f0b5cc'].map(_calTextColor)));
    """)
    assert _run_node(script) == ["#000", "#000", "#000"]


def test_non_hex_colors_defer_to_css(node_available):
    """CSS vars, bg-image sentinels and junk return '' so the stylesheet wins."""
    script = textwrap.dedent("""
        const { _calTextColor } = await import('./static/js/calendar/utils.js');
        const cases = ['var(--accent)', 'bg:http://x/y.png', '', 'tomato', null, undefined];
        console.log(JSON.stringify(cases.map(_calTextColor)));
    """)
    assert _run_node(script) == ["", "", "", "", "", ""]


def test_shorthand_and_alpha_hex_supported(node_available):
    """3-digit shorthand and 8-digit (alpha) hex still resolve to a colour."""
    script = textwrap.dedent("""
        const { _calTextColor } = await import('./static/js/calendar/utils.js');
        console.log(JSON.stringify({ white3: _calTextColor('#fff'), black3: _calTextColor('#000'), alpha: _calTextColor('#0b0b0bff') }));
    """)
    out = _run_node(script)
    assert out == {"white3": "#000", "black3": "#fff", "alpha": "#fff"}
