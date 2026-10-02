"""Accent-painted surfaces must derive their foreground from their own background.

Regression cover for a theme whose accent (`--red`) is dark while the send
button is independently overridden to a light colour: a single global
foreground derived from `--red` leaves white text on a light button.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_THEME_JS = _REPO / "static" / "js" / "theme.js"
_STYLE_CSS = _REPO / "static" / "style.css"


def _extract_fn(source: str, name: str) -> str:
    """Pull one top-level `function name(...) { ... }` out of the module."""
    start = source.index(f"function {name}(")
    depth = 0
    i = source.index("{", start)
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name}")


def _run_node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_accent_foregrounds_follow_their_own_backgrounds():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    src = _THEME_JS.read_text(encoding="utf-8")
    helpers = "\n".join(
        _extract_fn(src, name)
        for name in ("hexToRgb", "_relativeLuminance", "_contrastRatio", "_readableOn", "_mixSrgb")
    ) if "function hexToRgb(" in src else "\n".join(
        [
            # hexToRgb is imported from ./color/hex.js in the module; inline an
            # equivalent so the helpers can be exercised standalone.
            "function hexToRgb(h){h=String(h).replace('#','');"
            "if(h.length===3)h=h.split('').map(x=>x+x).join('');"
            "return {r:parseInt(h.slice(0,2),16),g:parseInt(h.slice(2,4),16),b:parseInt(h.slice(4,6),16)};}",
            _extract_fn(src, "_relativeLuminance"),
            "const _AA_NORMAL_TEXT = 4.5;",
            _extract_fn(src, "_contrastRatio"),
            _extract_fn(src, "_readableOn"),
            _extract_fn(src, "_mixSrgb"),
        ]
    )

    script = f"""
      {helpers}
      const contrast = (a, b) => {{
        const l = [_relativeLuminance(a), _relativeLuminance(b)].sort((x, y) => y - x);
        return (l[0] + 0.05) / (l[1] + 0.05);
      }};
      // Genuinely dark accent, deliberately light custom send button.
      // (#e06c75, the stock accent, is NOT dark enough for white under AA --
      // it yields 3.20:1 -- so it is asserted separately below.)
      const red = '#7a2530';
      const accentPrimary = '#f2c14e';
      const sendBg = '#f2c14e';
      const sendHover = '#f7d488';
      const panel = '#111111';

      const onAccent = _readableOn(red);
      const onAccentPrimary = _readableOn(accentPrimary);
      const accentPrimaryHover = _mixSrgb(accentPrimary, '#ffffff', 0.85);
      const onAccentPrimaryHover = _readableOn(accentPrimaryHover);
      const onSend = _readableOn(sendBg);
      const onSendHover = _readableOn(sendHover);
      const newchatBg = _mixSrgb(sendHover, panel, 0.85);
      const onNewchat = _readableOn(newchatBg);

      console.log(JSON.stringify({{
        onAccent, onAccentPrimary, accentPrimaryHover, onAccentPrimaryHover,
        onSend, onSendHover, onNewchat, newchatBg,
        accentContrast: contrast(red, onAccent),
        accentPrimaryContrast: contrast(accentPrimary, onAccentPrimary),
        accentPrimaryHoverContrast: contrast(accentPrimaryHover, onAccentPrimaryHover),
        sendContrast: contrast(sendBg, onSend),
        sendHoverContrast: contrast(sendHover, onSendHover),
        newchatContrast: contrast(newchatBg, onNewchat),
        naiveSendContrast: contrast(sendBg, onAccent),
        stockAccentFg: _readableOn('#e06c75'),
        stockAccentOnWhite: contrast('#e06c75', '#fff'),
        stockAccentChosen: contrast('#e06c75', _readableOn('#e06c75')),
      }}));
    """
    out = _run_node(script)

    # The accent and the send button disagree: that disagreement is the bug.
    assert out["onAccent"] == "#fff"
    assert out["onAccentPrimary"] == "#171717"
    assert out["onAccentPrimaryHover"] == "#171717"
    assert out["onSend"] == "#171717"
    assert out["onSendHover"] == "#171717"

    # Reusing the accent's foreground on the send button is what used to happen.
    assert out["naiveSendContrast"] < 3, out

    # The stock accent is the reason the 3:1 floor was not enough: white on
    # #e06c75 is 3.20:1, which cleared the old floor and fails AA. The pair
    # now flips to dark text instead of shipping sub-AA white.
    assert 3 <= out["stockAccentOnWhite"] < 4.5, out
    assert out["stockAccentFg"] == "#171717", out
    assert out["stockAccentChosen"] >= 4.5, out

    # These tokens land on button labels around 9-15px, which WCAG treats as
    # normal-size text: 4.5:1, not the 3:1 large-text/UI-component floor.
    for key in (
        "accentContrast",
        "accentPrimaryContrast",
        "accentPrimaryHoverContrast",
        "sendContrast",
        "sendHoverContrast",
        "newchatContrast",
    ):
        assert out[key] >= 4.5, (key, out[key])


def test_no_rule_pairs_on_accent_with_an_independent_background():
    """An independently configurable background must not use --on-accent."""
    css = _STYLE_CSS.read_text(encoding="utf-8")
    offenders = []
    for match in re.finditer(r"([^\n{}]+)\{([^{}]*)\}", css):
        body = match.group(2)
        if "var(--on-accent)" in body and (
            "--send-btn-bg" in body
            or "--send-btn-hover" in body
            or "--accent-primary" in body
        ):
            offenders.append(match.group(1).strip())
    assert not offenders, offenders


def test_primary_hover_states_use_the_derived_hover_pair():
    """Hover fills must not rely on a filter while retaining base text."""
    css = _STYLE_CSS.read_text(encoding="utf-8")
    assert ".confirm-btn-primary:hover { filter:brightness(1.15); }" not in css
    thumb_hover = re.search(r"\.thumb\.thumb-image button:hover\s*\{([^}]*)\}", css)
    assert thumb_hover, "thumbnail hover rule disappeared"
    assert "filter" not in thumb_hover.group(1)
    assert "background:var(--accent-primary-hover); color:var(--on-accent-primary-hover)" in css
    assert "background: var(--accent-primary-hover);" in css
    assert "color: var(--on-accent-primary-hover);" in css


def test_readable_on_clears_aa_for_every_background():
    """No background may be left without an AA-passing foreground.

    `#fff` clears 4.5:1 only up to luminance ~0.1833 and `#171717` only from
    ~0.2136, so backgrounds in between clear AA against neither. Pure black
    closes that band: the better of `#fff`/`#000` is never worse than 4.58:1.
    """
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    src = _THEME_JS.read_text(encoding="utf-8")
    helpers = "\n".join(
        [
            "function hexToRgb(h){h=String(h).replace('#','');"
            "if(h.length===3)h=h.split('').map(x=>x+x).join('');"
            "return {r:parseInt(h.slice(0,2),16),g:parseInt(h.slice(2,4),16),b:parseInt(h.slice(4,6),16)};}",
            _extract_fn(src, "_relativeLuminance"),
            "const _AA_NORMAL_TEXT = 4.5;",
            _extract_fn(src, "_contrastRatio"),
            _extract_fn(src, "_readableOn"),
        ]
    )
    script = f"""
      {helpers}
      const hx = (n) => n.toString(16).padStart(2, '0');
      let worst = Infinity, worstBg = null, failures = 0, scanned = 0;
      for (let r = 0; r < 256; r += 5)
        for (let g = 0; g < 256; g += 5)
          for (let b = 0; b < 256; b += 5) {{
            const bg = '#' + hx(r) + hx(g) + hx(b);
            const ratio = _contrastRatio(_readableOn(bg), bg);
            scanned++;
            if (ratio < 4.5) failures++;
            if (ratio < worst) {{ worst = ratio; worstBg = bg; }}
          }}
      // The band where a #fff/#171717-only choice has no AA-passing answer.
      const band = ['#767676', '#7a7a7a', '#808080', '#428d6f'].map((bg) => ({{
        bg,
        chosen: _readableOn(bg),
        ratio: _contrastRatio(_readableOn(bg), bg),
        bestOfWhiteAndSoftBlack: Math.max(
          _contrastRatio('#fff', bg), _contrastRatio('#171717', bg)
        ),
      }}));
      console.log(JSON.stringify({{ scanned, failures, worst, worstBg, band }}));
    """
    out = _run_node(script)

    assert out["failures"] == 0, out
    assert out["worst"] >= 4.5, out

    # At least one sampled colour must actually exercise the gap, otherwise
    # this test would keep passing if the fallback were removed again.
    exercised = [c for c in out["band"] if c["bestOfWhiteAndSoftBlack"] < 4.5]
    assert exercised, out["band"]
    for case in exercised:
        assert case["ratio"] >= 4.5, case
