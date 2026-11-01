"""Tests must reason about the whole cascade, not one file of it.

The former ``static/style.css`` has been decomposed into an ordered cascade.
A test that names the deleted file as a runtime stylesheet silently loses the
split cascade. Python, JavaScript, MJS and HTML test sources are all scanned
recursively so nested browser tests cannot escape this guard.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).name

# The helper module and the manifest/snapshot tests are about the stylesheet
# set itself, so naming the file is the point rather than a mistake.
ALLOWED = {SELF, "test_static_stylesheet_manifest.py", "test_css_computed_style_snapshot.py"}

_DELETED_STYLE_LITERAL = re.compile(
    r'''["'][^"'\n]*static/style\.css[^"'\n]*["']'''
)
_SUFFIXES = {".py", ".js", ".mjs", ".html"}


def _test_sources():
    return [
        p
        for p in sorted((ROOT / "tests").rglob("*"))
        if p.is_file()
        and p.suffix in _SUFFIXES
        and p.name not in ALLOWED
    ]


def test_sources_are_discoverable() -> None:
    """Guard the guard: a layout change must not make this vacuous."""
    assert len(_test_sources()) > 100


def test_no_test_names_deleted_style_css_as_a_runtime_asset() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in _test_sources()
        if _DELETED_STYLE_LITERAL.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "tests must load the app stylesheet cascade instead of the deleted "
        f"static/style.css asset: {offenders}"
    )
