"""Stylesheet asset manifest: shipped HTML and the service worker must agree.

``specs/frontend.md`` records that nothing validates the service-worker
precache against what ``index.html`` actually loads, and that a static asset
manifest regression should confirm referenced files exist. Both matter more
once ``static/style.css`` starts being split: every new stylesheet has to be
registered in ``static/index.html`` *and* in the ``static/sw.js`` precache,
with the same ``?v=`` cache-bust string in both. Today those two strings are
hardcoded independently, so a split that updates one and not the other ships an
offline cache that serves a stale or missing stylesheet - invisible until
someone loads the app without a network.

This test reads the shipped files rather than driving a browser: the artifact
under test *is* the URL string in two files, and a browser would only see one
of them at a time.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "static" / "index.html"
SERVICE_WORKER = ROOT / "static" / "sw.js"

_STYLESHEET_LINK = re.compile(
    r"""<link\b[^>]*\brel=["']stylesheet["'][^>]*>""", re.IGNORECASE)
_HREF = re.compile(r"""\bhref=["']([^"']+)["']""", re.IGNORECASE)


def _stylesheet_hrefs(html_path):
    """Every href from a <link rel="stylesheet"> in one HTML file."""
    html = html_path.read_text(encoding="utf-8")
    hrefs = []
    for tag in _STYLESHEET_LINK.findall(html):
        match = _HREF.search(tag)
        if match:
            hrefs.append(match.group(1))
    return hrefs


def _precache_entries(name):
    """The quoted URLs of one precache array in sw.js.

    Entries built by spreading another list (``...KATEX_FONTS``) are skipped:
    they carry no literal URL at this level, and the fonts they add are not
    stylesheets.
    """
    source = SERVICE_WORKER.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = \[(.*?)\n\];", source, re.DOTALL)
    assert match, f"{name} array not found in static/sw.js"
    return re.findall(r"""['"](/[^'"]+)['"]""", match.group(1))


def _local_path(url):
    """Map an app-absolute URL to the file it is served from."""
    return ROOT / url.split("?", 1)[0].lstrip("/")


# HTML outside static/ that still links app stylesheets by URL. The snapshot
# bench is one: it is not shipped, but a dead link there renders the bench
# unstyled and every computed-style measurement taken from it is worthless.
_UNSHIPPED_HTML = ("tests/css_snapshot/bench.html",)


def test_every_stylesheet_referenced_by_shipped_html_exists():
    checked = 0
    html_files = sorted((ROOT / "static").glob("*.html"))
    html_files += [ROOT / rel for rel in _UNSHIPPED_HTML]
    for html_path in html_files:
        for href in _stylesheet_hrefs(html_path):
            if not href.startswith("/"):
                continue  # external or relative-to-page; not ours to resolve
            assert _local_path(href).is_file(), (
                f"{html_path.relative_to(ROOT)} links {href}, "
                "which does not exist on disk"
            )
            checked += 1
    assert checked, "no app stylesheet links found - the parser or the markup moved"


def test_every_precached_stylesheet_exists():
    for name in ("PRECACHE", "PANEL_PRECACHE"):
        for url in _precache_entries(name):
            if not url.endswith(".css") and ".css?" not in url:
                continue
            assert _local_path(url).is_file(), (
                f"sw.js {name} precaches {url}, which does not exist on disk"
            )


def test_index_stylesheets_are_precached_with_the_same_cache_bust_string():
    """The precache must match the request the browser actually makes.

    A service-worker entry only matches a fetch when the URL is identical,
    query string included, so a stylesheet whose ``?v=`` differs between the
    two files is precached under a URL nothing ever requests.
    """
    precached = set(_precache_entries("PRECACHE"))
    precached_paths = {url.split("?", 1)[0]: url for url in precached}

    for href in _stylesheet_hrefs(INDEX_HTML):
        if not href.startswith("/static/"):
            continue
        path = href.split("?", 1)[0]
        assert path in precached_paths, (
            f"index.html loads {href} but sw.js PRECACHE has no entry for {path}; "
            "the app shell would not be available offline"
        )
        assert precached_paths[path] == href, (
            f"cache-bust mismatch for {path}: index.html requests {href!r}, "
            f"sw.js precaches {precached_paths[path]!r}"
        )
