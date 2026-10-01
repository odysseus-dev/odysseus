"""Read the app's CSS the way the browser does.

``static/style.css`` no longer holds every rule: panel styles live in separate
files that ``static/index.html`` loads eagerly, in a fixed order, right after
it. The cascade is the concatenation of those files in that order.

A test that asserts on a rule must therefore look at all of them. Reading
``static/style.css`` alone makes the test depend on which file a rule happens
to sit in today, so it breaks the next time a rule moves without anything
about the rendered page having changed.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "static"
_INDEX = _STATIC / "index.html"

# Only same-origin app stylesheets; vendored <link>s under static/lib are not
# part of the cascade these tests reason about.
_LINK = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']/static/([^"'?]+)""",
    re.I,
)


def stylesheet_paths() -> list[Path]:
    """Every app stylesheet, in the order index.html loads it."""
    html = _INDEX.read_text(encoding="utf-8")
    paths = [_STATIC / m.group(1) for m in _LINK.finditer(html)]
    if not paths:
        raise AssertionError(f"no app stylesheet <link> tags found in {_INDEX}")
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise AssertionError(f"index.html links stylesheets that do not exist: {missing}")
    return [p for p in paths if "lib/" not in p.as_posix().split("static/", 1)[-1]]


def app_css() -> str:
    """The whole cascade as one string, in load order."""
    return "\n".join(p.read_text(encoding="utf-8") for p in stylesheet_paths())
