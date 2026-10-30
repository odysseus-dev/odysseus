"""Primary-button labels clear WCAG AA in every built-in theme.

refresh.css paints text-bearing primary buttons with --accent-fill and
--accent-ink, which theme.js derives from each theme's accent through
static/js/color/contrast.js. A fixed white label on the darkened accent fell
to ~2.5:1 on bright accents (Monolith, Blueprint, Eclipse, Yoyo).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "js" / "color" / "contrast.js"
THEME_JS = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
REFRESH_CSS = (ROOT / "static" / "css" / "refresh.css").read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _builtin_accents():
    themes = THEME_JS.split("export const THEMES = {", 1)[1].split("\n};", 1)[0]
    found = dict(re.findall(r"^\s*'?([\w-]+)'?:\s*\{[^\n]*?red:'(#[0-9a-fA-F]{6})'", themes, re.MULTILINE))
    assert len(found) >= 20, found
    return found


def _button_colors(accents):
    source = (
        f"import {{ accentButtonColors, contrastRatio }} from {json.dumps(HELPER.as_uri())};"
        f"const out = {{}};"
        f"for (const [name, red] of Object.entries({json.dumps(accents)})) {{"
        f"  const c = accentButtonColors(red);"
        f"  out[name] = {{ fill: contrastRatio(c.fill, c.ink), hover: contrastRatio(c.hover, c.ink) }};"
        f"}}"
        f"console.log(JSON.stringify(out));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_primary_button_label_clears_aa_in_every_builtin_theme():
    ratios = _button_colors(_builtin_accents())
    failing = {name: r for name, r in ratios.items() if min(r.values()) < 4.5}
    assert not failing, failing


def test_bright_accents_from_the_review_are_covered():
    ratios = _button_colors({"monolith": "#b6e05c", "blueprint": "#e6b84a", "eclipse": "#46c2b3", "yoyo": "#e09d6c"})
    assert all(min(r.values()) >= 4.5 for r in ratios.values()), ratios


def test_primary_buttons_use_the_derived_label_color():
    primary = REFRESH_CSS.split(".email-send-btn {", 1)[1].split("}", 1)[0]
    assert "background: var(--accent-fill)" in primary
    assert "color: var(--accent-ink)" in primary
    assert "setProperty('--accent-ink'" in THEME_JS


def test_login_page_copy_matches_the_shared_helper():
    # login.html's synchronous head script cannot import contrast.js, so it
    # carries a copy; both must give the same button colors for every theme.
    login = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
    copy = login.split("  function accentButtonColors(accent) {", 1)[1].split("\n  try {", 1)[0]
    copy = "function accentButtonColors(accent) {" + copy
    accents = _builtin_accents()
    source = (
        f"import {{ accentButtonColors as shared }} from {json.dumps(HELPER.as_uri())};"
        f"{copy}\n"
        f"const out = {{}};"
        f"for (const [name, red] of Object.entries({json.dumps(accents)})) {{"
        f"  out[name] = [shared(red), accentButtonColors(red)];"
        f"}}"
        f"console.log(JSON.stringify(out));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    pairs = json.loads(proc.stdout.strip())
    assert {name: a for name, (a, b) in pairs.items() if a != b} == {}
    assert "background: var(--accent-fill," in login
    assert "color: var(--accent-ink, #fff)" in login
