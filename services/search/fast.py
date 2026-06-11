"""Local-only fast web search — snippet-first, with a tight lxml-parsed fetch
escalation only when the provider's result snippets are too thin to answer from.

Why this exists: ``comprehensive_web_search`` always fetches the full HTML of the
top results and parses it with BeautifulSoup's pure-Python parser, then hands the
model whole pages. For the common "look up a fact" query the provider's own
result snippets already contain the answer, so we skip fetching entirely; when
they don't, we fetch a few pages concurrently, parse with lxml (C speed), and
return a trimmed, query-relevant extract so a local model synthesizes fast.

Design — kept deliberately merge-friendly: this module is additive and
self-contained. The ONLY hook into existing code is one guarded call in
``src/agent_tools/web_tools.py``; any failure here returns an empty result so
the caller transparently falls back to ``comprehensive_web_search``. No new
dependencies (lxml is already required), no external services, and SSRF
protection is inherited by reusing the existing guarded fetch.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from typing import Tuple, Union

logger = logging.getLogger(__name__)

# Tuning — conservative so the fast path is never a downgrade.
_SNIPPET_MIN_CHARS = 120     # a snippet this long counts as "substantive"
_SNIPPETS_ENOUGH = 3         # this many substantive snippets => answer from snippets alone
_FETCH_TOP = 3               # pages fetched when escalating
_FETCH_TIMEOUT = 4           # seconds per page (tight)
_FETCH_WORKERS = 4
_EXTRACT_CHARS = 900         # per-page trimmed extract length

_WS_RE = re.compile(r"\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TOK_RE = re.compile(r"[a-z0-9]+")
_DROP_TAGS = ("script", "style", "noscript", "template", "svg",
              "nav", "footer", "header", "aside", "form")


def _tokens(s: str) -> set:
    return set(_TOK_RE.findall((s or "").lower()))


def _lxml_extract(html_text: str) -> str:
    """Fast main-text extraction using lxml's C parser. Best-effort: returns
    cleaned, whitespace-collapsed text, or '' on any failure."""
    if not html_text:
        return ""
    try:
        from lxml import html as lxml_html
        doc = lxml_html.fromstring(html_text)
        for tag in _DROP_TAGS:
            for el in doc.xpath(f"//{tag}"):
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
        main = doc.xpath("//main") or doc.xpath("//article")
        node = main[0] if main else doc
        return _WS_RE.sub(" ", node.text_content()).strip()
    except Exception as e:
        logger.debug("lxml extract failed: %s", e)
        return ""


def _relevant_extract(text: str, query: str, limit: int = _EXTRACT_CHARS) -> str:
    """Keep the query-relevant slice of a page — the most query-token-rich
    sentences, in their original order — capped at `limit` chars."""
    if len(text) <= limit:
        return text
    qt = _tokens(query)
    sents = [s.strip() for s in _SENT_SPLIT_RE.split(text) if len(s.strip()) > 40]
    if not sents or not qt:
        return text[:limit]
    order = {s: i for i, s in enumerate(sents)}
    ranked = sorted(sents, key=lambda s: len(qt & _tokens(s)), reverse=True)
    # Never pad with zero-overlap sentences — keep only query-relevant ones
    # (fall back to all if none overlap, e.g. a purely navigational page).
    relevant = [s for s in ranked if qt & _tokens(s)] or ranked
    chosen, total = [], 0
    for s in relevant:
        if total >= limit:
            break
        if total + len(s) + 1 > limit:
            # The single best sentence can exceed the budget — include a
            # truncation of it rather than dropping to a weaker one.
            if not chosen:
                chosen.append(s[:limit])
                total = limit
            continue
        chosen.append(s)
        total += len(s) + 1
    chosen.sort(key=lambda s: order.get(s, len(sents)))
    return " ".join(chosen) if chosen else text[:limit]


def _safe_fetch(url: str, timeout: int = _FETCH_TIMEOUT) -> str:
    """Fetch a page reusing the existing SSRF-guarded getter, then lxml-extract.
    Falls back to the full safe fetcher if internals move; '' on any failure.
    The SSRF guard rejecting a private/blocked URL surfaces here as an exception,
    which we treat as 'no content' — never a leak."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; OdysseusSearch/1.0)"}
    try:
        from services.search.content import _get_public_url
        resp = _get_public_url(url, headers=headers, timeout=timeout)
        return _lxml_extract(getattr(resp, "text", "") or "")
    except ImportError:
        try:
            from services.search.content import fetch_webpage_content
            r = fetch_webpage_content(url, timeout=timeout)
            return r.get("content", "") if isinstance(r, dict) and r.get("success") else ""
        except Exception as e:
            logger.debug("fallback fetch failed for %s: %s", url, e)
            return ""
    except Exception as e:
        logger.debug("fast fetch failed for %s: %s", url, e)
        return ""


def fast_web_search(
    query: str,
    max_pages: int = 5,
    time_filter: str = None,
    return_sources: bool = True,
) -> Union[Tuple[str, list], str]:
    """Snippet-first local web search. Returns (text, sources) exactly like
    ``comprehensive_web_search(return_sources=True)``. Returns ("", []) on no
    results or internal failure so the caller can fall back."""
    try:
        from services.search.core import searxng_search_results
        results = searxng_search_results(query, time_filter=time_filter) or []
    except Exception as e:
        logger.warning("fast_web_search: provider call failed: %s", e)
        results = []

    if not results:
        return ("", []) if return_sources else ""

    sources = [{"url": r.get("url", ""), "title": r.get("title", "")}
               for r in results if r.get("url")]

    top = results[:max(max_pages, 5)]
    url_index = {r.get("url"): i for i, r in enumerate(top, 1) if r.get("url")}

    lines, substantive = [], 0
    for i, r in enumerate(top, 1):
        snip = (r.get("snippet") or "").strip()
        if not snip:
            continue
        if len(snip) >= _SNIPPET_MIN_CHARS:
            substantive += 1
        lines.append(f"[{i}] {(r.get('title') or '').strip()} — {r.get('url', '')}\n{snip}")
    text = "\n\n".join(lines)

    # Escalate to a tight, parallel fetch only when snippets can't carry it.
    if substantive < _SNIPPETS_ENOUGH:
        urls = [r.get("url") for r in results[:_FETCH_TOP] if r.get("url")]
        extracts = {}
        if urls:
            with concurrent.futures.ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
                futs = {ex.submit(_safe_fetch, u): u for u in urls}
                for fut in concurrent.futures.as_completed(futs):
                    u = futs[fut]
                    try:
                        body = fut.result()
                    except Exception:
                        body = ""
                    if body:
                        extracts[u] = _relevant_extract(body, query)
        if extracts:
            blocks = [f"[{url_index.get(u, '?')}] {u}\n{body}" for u, body in extracts.items()]
            text = (text + "\n\n--- page extracts ---\n\n" + "\n\n".join(blocks)).strip()

    if not text:
        return ("", []) if return_sources else ""
    return (text, sources) if return_sources else text
