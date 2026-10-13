"""Read the app's CSS the way the browser does.

The former ``static/style.css`` is now an ordered set of numbered fragments,
followed by the existing panel stylesheets. ``static/index.html`` loads the
complete cascade eagerly in the order the browser must apply it.

A test that asserts on a rule must therefore look at the complete cascade.
Reading one fragment alone ties the test to whichever file a rule happens to
sit in today, so it goes red the next time a rule moves without anything about
the rendered page having changed.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "static"
_INDEX = _STATIC / "index.html"

# Only same-origin app stylesheets. Vendored <link>s under static/lib are not
# part of the cascade these tests reason about.
_LINK = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']/static/([^"'?]+)([^"']*)["']""",
    re.I,
)


def _entries() -> list[tuple[Path, str]]:
    html = _INDEX.read_text(encoding="utf-8")
    out = []
    for m in _LINK.finditer(html):
        rel, query = m.group(1), m.group(2)
        if rel.startswith("lib/"):
            continue
        out.append((_STATIC / rel, "/static/" + rel + query))
    if not out:
        raise AssertionError(f"no app stylesheet <link> tags found in {_INDEX}")
    missing = [p for p, _u in out if not p.is_file()]
    if missing:
        raise AssertionError(f"index.html links stylesheets that do not exist: {missing}")
    return out


def stylesheet_paths() -> list[Path]:
    """Every app stylesheet on disk, in the order index.html loads it."""
    return [p for p, _u in _entries()]


def stylesheet_urls() -> list[str]:
    """The same stylesheets as request URLs, query string included."""
    return [u for _p, u in _entries()]


def stylesheet_link_tags() -> str:
    """The <link> tags for a synthetic page that needs the whole cascade."""
    return "".join(f'<link rel="stylesheet" href="{u}">' for u in stylesheet_urls())


def app_css() -> str:
    """The whole cascade as one string, in load order."""
    return "\n".join(p.read_text(encoding="utf-8") for p in stylesheet_paths())


def stylesheet_cache_version() -> str:
    """The single ``?v=`` token every app stylesheet link carries.

    The stylesheet is split across several files that must be busted together:
    shipping one fragment under a stale token serves a browser half of an old
    cascade and half of a new one. Tests ask for the shared version here instead of deriving it from one
    stylesheet filename, so they keep checking the invariant
    rather than a filename.
    """
    versions = set()
    for url in stylesheet_urls():
        m = re.search(r"\?v=([^&]+)$", url)
        if not m:
            raise AssertionError(f"app stylesheet has no cache-bust token: {url}")
        versions.add(m.group(1))
    if len(versions) != 1:
        raise AssertionError(
            f"app stylesheets disagree on their cache-bust token: {sorted(versions)}"
        )
    return versions.pop()
