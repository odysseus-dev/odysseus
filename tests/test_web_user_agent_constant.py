"""The web scraping path routes its User-Agent through one constant.

Guards the dedup: web_fetch / web_search outbound UAs go through
WEB_FETCH_USER_AGENT, so a stale or bare Mozilla string cannot be re-inlined in
the search sources.
"""
from pathlib import Path

_SEARCH = Path(__file__).resolve().parent.parent / "services" / "search"


def test_search_sources_have_no_inline_mozilla_ua():
    offenders = [
        str(py.relative_to(_SEARCH.parent.parent))
        for py in _SEARCH.rglob("*.py")
        if "Mozilla/" in py.read_text(encoding="utf-8")
    ]
    assert not offenders, f"inline Mozilla UA found; use WEB_FETCH_USER_AGENT: {offenders}"
