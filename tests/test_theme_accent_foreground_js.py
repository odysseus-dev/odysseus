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


def test_send_button_foreground_follows_its_own_background():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    src = _THEME_JS.read_text(encoding="utf-8")
    helpers = "\n".join(
        _extract_fn(src, name)
        for name in ("hexToRgb", "_relativeLuminance", "_readableOn", "_mixSrgb")
    ) if "function hexToRgb(" in src else "\n".join(
        [
            # hexToRgb is imported from ./color/hex.js in the module; inline an
            # equivalent so the helpers can be exercised standalone.
            "function hexToRgb(h){h=String(h).replace('#','');"
            "if(h.length===3)h=h.split('').map(x=>x+x).join('');"
            "return {r:parseInt(h.slice(0,2),16),g:parseInt(h.slice(2,4),16),b:parseInt(h.slice(4,6),16)};}",
            _extract_fn(src, "_relativeLuminance"),
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
      // Dark accent, deliberately light custom send button.
      const red = '#e06c75';
      const sendBg = '#f2c14e';
      const sendHover = '#f7d488';
      const panel = '#111111';

      const onAccent = _readableOn(red);
      const onSend = _readableOn(sendBg);
      const onSendHover = _readableOn(sendHover);
      const newchatBg = _mixSrgb(sendHover, panel, 0.85);
      const onNewchat = _readableOn(newchatBg);

      console.log(JSON.stringify({{
        onAccent, onSend, onSendHover, onNewchat, newchatBg,
        accentContrast: contrast(red, onAccent),
        sendContrast: contrast(sendBg, onSend),
        sendHoverContrast: contrast(sendHover, onSendHover),
        newchatContrast: contrast(newchatBg, onNewchat),
        naiveSendContrast: contrast(sendBg, onAccent),
      }}));
    """
    out = _run_node(script)

    # The accent and the send button disagree: that disagreement is the bug.
    assert out["onAccent"] == "#fff"
    assert out["onSend"] == "#171717"
    assert out["onSendHover"] == "#171717"

    # Reusing the accent's foreground on the send button is what used to happen.
    assert out["naiveSendContrast"] < 3, out

    # Every surface clears the 3:1 floor against the background it renders.
    for key in ("accentContrast", "sendContrast", "sendHoverContrast", "newchatContrast"):
        assert out[key] >= 3, (key, out[key])


def test_no_rule_pairs_on_accent_with_a_send_button_background():
    """A send-button background must never be paired with --on-accent."""
    css = _STYLE_CSS.read_text(encoding="utf-8")
    offenders = []
    for match in re.finditer(r"([^\n{}]+)\{([^{}]*)\}", css):
        body = match.group(2)
        if "var(--on-accent)" in body and (
            "--send-btn-bg" in body or "--send-btn-hover" in body
        ):
            offenders.append(match.group(1).strip())
    assert not offenders, offenders
