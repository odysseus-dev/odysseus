"""Pin the CSS responsive-override analyzer in scripts/check-css-overrides.mjs.

Driven through `node --input-type=module` so we exercise the real parser without
a JS test runner (same approach as test_compare_js.py / test_reply_recipients_js.py).
Skips when `node` is not installed rather than failing.

The analyzer makes desktop/mobile paired rules in static/style.css discoverable:
which selectors are styled at the base layer and then overridden inside a
`@media` block, plus breakpoints that are spelled inconsistently.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check-css-overrides.mjs"
_HAS_NODE = shutil.which("node") is not None

_SAMPLE = """
/* a stray @media (max-width: 999px) inside a comment must be ignored */
.btn { color: red; }
.btn::after { content: "}"; }
@media (max-width: 768px) {
  .btn { color: blue; }
  .mobile-only { display: block; }
}
@media (max-width:768px) {
  .btn { color: green; }
}
@keyframes spin { from { opacity: 0; } to { opacity: 1; } }
@media print {
  .btn { color: black; }
}
"""


def _analyze(css: str) -> dict:
    # `as_uri()` -> file:// URL so the ESM import resolves on Windows too
    # (a bare C:/... path raises ERR_UNSUPPORTED_ESM_URL_SCHEME).
    js = f"""
    import {{ analyzeCss }} from '{_SCRIPT.as_uri()}';
    console.log(JSON.stringify(analyzeCss({json.dumps(css)})));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_detects_base_plus_media_override():
    a = _analyze(_SAMPLE)
    paired = {p["selector"]: p for p in a["paired"]}
    assert ".btn" in paired, "selector styled at base AND under @media should be paired"
    queries = {o["query"] for o in paired[".btn"]["overrides"]}
    assert "(max-width: 768px)" in queries
    assert "print" in queries
    assert paired[".btn"]["baseLines"], "paired selector should record its base line(s)"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_mobile_only_vs_base_only():
    a = _analyze(_SAMPLE)
    mobile_only = {m["selector"] for m in a["mobileOnly"]}
    paired = {p["selector"] for p in a["paired"]}
    # Defined only inside @media -> mobile-only, not paired.
    assert ".mobile-only" in mobile_only
    assert ".mobile-only" not in paired
    # Base-only selector (with a "}" in a string value) is neither.
    assert ".btn::after" not in mobile_only
    assert ".btn::after" not in paired


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_flags_inconsistent_breakpoint_spelling():
    a = _analyze(_SAMPLE)
    assert a["stats"]["inconsistentBreakpoints"] == 1
    inc = a["inconsistentBreakpoints"][0]
    assert inc["normalized"] == "(max-width:768px)"
    forms = {f["form"] for f in inc["forms"]}
    assert forms == {"(max-width: 768px)", "(max-width:768px)"}


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_ignores_comments_and_keyframe_stops():
    a = _analyze(_SAMPLE)
    # The breakpoint hidden in a comment must not be parsed as real.
    assert all("999px" not in b["query"] for b in a["breakpoints"])
    # @keyframes stops ("from"/"to") are not selectors.
    selectors = {p["selector"] for p in a["paired"]} | {m["selector"] for m in a["mobileOnly"]}
    assert "from" not in selectors and "to" not in selectors


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_repo_stylesheet_has_consistent_breakpoints():
    """The bundled stylesheet should pass the CI breakpoint-consistency check."""
    css = (_REPO / "static" / "style.css").read_text(encoding="utf-8")
    a = _analyze(css)
    assert a["inconsistentBreakpoints"] == [], (
        "static/style.css has inconsistently spelled breakpoints; "
        "run `npm run css:overrides` and normalize them"
    )
