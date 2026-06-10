"""Search provider implementations: SearXNG, Brave, DuckDuckGo, Google PSE, Tavily, Serper, Kagi."""

import json
import logging
import os
import time
import warnings
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from src.constants import SEARXNG_INSTANCE
from .analytics import RateLimitError, error_logger
from .query import build_enhanced_query

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
DDG_RETRY_DELAY_SECONDS = 0.35

# Provider registry — maps setting value to (label, needs_key, needs_url)
PROVIDER_INFO = {
    "searxng":  ("SearXNG",           False, True),
    "brave":    ("Brave Search",      True,  False),
    "duckduckgo": ("DuckDuckGo",      False, False),
    "google_pse": ("Google PSE",      True,  False),
    "tavily":   ("Tavily",            True,  False),
    "serper":   ("Serper",            True,  False),
    "kagi":     ("Kagi",              True,  False),
    "serpapi":  ("SerpApi",           True,  False),
    "disabled": ("Disabled",          False, False),
}


@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    label: str
    required_settings: tuple[str, ...] = ()
    query_concurrency: str = "parallel"
    fallback_concurrency: int = 4
    status_when_empty: str = "empty"


@dataclass(frozen=True)
class ProviderAvailability:
    provider: str
    ok: bool
    reason: str = "ok"
    detail: str = ""


_PROVIDER_POLICIES = {
    "searxng": ProviderPolicy(
        name="searxng",
        label="SearXNG",
        query_concurrency="parallel",
        fallback_concurrency=2,
        status_when_empty="searxng returned no results",
    ),
    "brave": ProviderPolicy(
        name="brave",
        label="Brave Search",
        required_settings=("brave_api_key",),
        query_concurrency="parallel",
        fallback_concurrency=4,
        status_when_empty="brave returned no results",
    ),
    "duckduckgo": ProviderPolicy(
        name="duckduckgo",
        label="DuckDuckGo",
        query_concurrency="sequential",
        fallback_concurrency=1,
        status_when_empty="duckduckgo returned no results after retry and HTML fallback",
    ),
    "google_pse": ProviderPolicy(
        name="google_pse",
        label="Google PSE",
        required_settings=("google_pse_key", "google_pse_cx"),
        query_concurrency="parallel",
        fallback_concurrency=4,
        status_when_empty="google_pse returned no results",
    ),
    "tavily": ProviderPolicy(
        name="tavily",
        label="Tavily",
        required_settings=("tavily_api_key",),
        query_concurrency="parallel",
        fallback_concurrency=4,
        status_when_empty="tavily returned no results",
    ),
    "serper": ProviderPolicy(
        name="serper",
        label="Serper",
        required_settings=("serper_api_key",),
        query_concurrency="parallel",
        fallback_concurrency=4,
        status_when_empty="serper returned no results",
    ),
    "serpapi": ProviderPolicy(
        name="serpapi",
        label="SerpApi",
        required_settings=("serpapi_api_key",),
        query_concurrency="parallel",
        fallback_concurrency=4,
        status_when_empty="serpapi returned no results",
    ),
}


def get_provider_policy(provider: str) -> ProviderPolicy:
    """Return provider capability metadata for research/search orchestration."""
    if provider in _PROVIDER_POLICIES:
        return _PROVIDER_POLICIES[provider]
    label, needs_key, _needs_url = PROVIDER_INFO.get(provider, (provider or "unknown", False, False))
    required = (f"{provider}_api_key",) if needs_key and provider else ()
    return ProviderPolicy(
        name=provider,
        label=label,
        required_settings=required,
        status_when_empty=f"{provider} returned no results" if provider else "provider returned no results",
    )


def _provider_config_value(provider: str, field: str) -> str:
    settings = _get_search_settings()
    if field.endswith("_api_key") or field.endswith("_key"):
        return _get_provider_key(provider)
    if field == "google_pse_cx":
        return (settings.get("google_pse_cx") or "").strip() or os.environ.get("GOOGLE_PSE_CX", "").strip()
    if field == "search_url":
        return _get_search_instance()
    return (settings.get(field) or "").strip()


def get_provider_availability(provider: str) -> ProviderAvailability:
    """Return whether provider has required local config, without exposing secrets."""
    if provider == "disabled":
        return ProviderAvailability(provider=provider, ok=False, reason="disabled", detail="search provider is disabled")

    policy = get_provider_policy(provider)
    missing = [
        field for field in policy.required_settings
        if not _provider_config_value(provider, field)
    ]
    if missing:
        return ProviderAvailability(
            provider=provider,
            ok=False,
            reason="missing_config",
            detail="missing required setting(s): " + ", ".join(missing),
        )
    return ProviderAvailability(provider=provider, ok=True)


# ── Settings helpers ──

def _get_search_settings() -> dict:
    """Return search settings from admin config, falling back to env defaults."""
    try:
        from src.settings import load_settings
        return load_settings()
    except Exception:
        return {}


def _get_search_instance() -> str:
    """Return the active search API URL from admin settings, falling back to env var."""
    settings = _get_search_settings()
    url = (settings.get("search_url") or "").strip()
    if url:
        return url.rstrip("/")
    return SEARXNG_INSTANCE


def _get_provider_key(provider: str) -> str:
    """Return the API key for a specific provider, with legacy fallback."""
    settings = _get_search_settings()
    key_map = {
        "brave": "brave_api_key",
        "google_pse": "google_pse_key",
        "tavily": "tavily_api_key",
        "serper": "serper_api_key",
        "kagi": "kagi_api_key",
        "serpapi": "serpapi_api_key",
    }
    field = key_map.get(provider, "")
    if field:
        val = (settings.get(field) or "").strip()
        if val:
            return val
    # Legacy fallback: old shared search_api_key field
    legacy = (settings.get("search_api_key") or "").strip()
    if legacy:
        return legacy
    env_map = {
        "brave": "DATA_BRAVE_API_KEY",
        "google_pse": "GOOGLE_API_KEY",
        "tavily": "TAVILY_API_KEY",
        "serper": "SERPER_API_KEY",
        "kagi": "KAGI_API_KEY",
        "serpapi": "SERPAPI_API_KEY",
    }
    env_name = env_map.get(provider, "")
    return (os.environ.get(env_name) or "").strip() if env_name else ""


def _get_result_count() -> int:
    """Return configured result count, default 5."""
    settings = _get_search_settings()
    try:
        return int(settings.get("search_result_count", 5))
    except (ValueError, TypeError):
        return 5


# Canonical SafeSearch levels: "strict" (default), "moderate", "off".
# Each provider has its own knob name and value space -- see _safesearch_for(...).
_SAFESEARCH_LEVELS = ("strict", "moderate", "off")


def _get_safesearch_level() -> str:
    """Return configured SafeSearch level normalized to a canonical value."""
    settings = _get_search_settings()
    raw = (settings.get("search_safesearch") or "strict").strip().lower()
    if raw in _SAFESEARCH_LEVELS:
        return raw
    aliases = {
        "on": "strict", "high": "strict", "2": "strict",
        "medium": "moderate", "1": "moderate", "default": "moderate",
        "none": "off", "disabled": "off", "0": "off",
    }
    return aliases.get(raw, "strict")


def _safesearch_for(provider: str) -> Optional[str]:
    """Translate the canonical SafeSearch level into provider-specific values."""
    level = _get_safesearch_level()
    if provider == "searxng":
        return {"strict": "2", "moderate": "1", "off": "0"}[level]
    if provider == "brave":
        return level
    if provider == "duckduckgo_lib":
        return {"strict": "on", "moderate": "moderate", "off": "off"}[level]
    if provider == "duckduckgo_html":
        return {"strict": "1", "moderate": "-1", "off": "-2"}[level]
    if provider == "google_pse":
        return None if level == "off" else "active"
    if provider == "serper":
        return None if level == "off" else "active"
    return None


# ── SearXNG ──

_NEWS_HINTS = ("news", "nyheter", "headlines", "breaking", "latest", "today", "idag")

# Default general engines (google/duckduckgo/brave/startpage/wikipedia) are
# routinely rate-limited / CAPTCHA-blocked on this instance and return nothing.
# Pin engines that actually respond so non-news queries get results without any
# third-party API fallback. Override via SEARXNG_GENERAL_ENGINES.
_GENERAL_ENGINES = os.environ.get("SEARXNG_GENERAL_ENGINES", "bing,mojeek,presearch")


def searxng_search_api(query: str, count: Optional[int] = None, categories: str = "general",
                       time_filter: Optional[str] = None) -> List[dict]:
    """Search using SearXNG JSON API. Returns list of {title, url, snippet}."""
    count = count if count is not None else _get_result_count()
    instance = _get_search_instance()
    api_key = ""
    headers = {"User-Agent": "Mozilla/5.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # News/fresh queries do badly in the 'general' category — it favours
    # encyclopedic/tourism pages, ignores recency, and (with no language pin)
    # bleeds in foreign-language results. When the agent layer detected
    # freshness (time_filter) or the query reads like a news lookup, switch to
    # the 'news' category, constrain recency, and pin language to English so a
    # search like "Canada latest news" returns actual news instead of Wikipedia.
    # Pin English for ALL searches — without it, SearXNG geolocates / mixes
    # languages and brand-ambiguous terms bleed in foreign SEO pages (e.g.
    # "Odyssey" → Honda Japan, "Trojan" → Japanese malware blogs, "Polyphemus"
    # → Chinese math forums). The news path already did this; general didn't.
    params = {
        "q": query,
        "format": "json",
        "language": "en",
        "safesearch": _safesearch_for("searxng"),
    }
    q_lc = query.lower()
    is_news = time_filter is not None or any(h in q_lc for h in _NEWS_HINTS)
    if is_news and categories == "general":
        params["categories"] = "news"
        if time_filter in ("day", "week", "month", "year"):
            # 'day' is too sparse on most SearXNG news engines — widen to a week
            # so there's enough volume; the news category already biases recent.
            params["time_range"] = "week" if time_filter in ("day", "week") else time_filter
    else:
        params["categories"] = categories
        # Route general queries to engines that aren't blocked (default general
        # set returns 0 on this instance — see _GENERAL_ENGINES).
        if categories == "general" and _GENERAL_ENGINES:
            params["engines"] = _GENERAL_ENGINES
    try:
        def _parse_results(results):
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in results[:count]
                if r.get("url")
            ]

        def _run(search_params):
            response = httpx.get(
                f"{instance}/search",
                params=search_params,
                headers=headers or None,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            return _parse_results(data.get("results", [])), data

        active_params = params
        parsed, data = _run(active_params)
        if not parsed and is_news and categories == "general":
            # Some self-hosted SearXNG configs have no working news engines.
            # Fall back to the known-good general engines before reporting an
            # empty search, otherwise common queries like "Canada news" fail.
            fallback = {
                "q": query,
                "format": "json",
                "language": "en",
                "categories": "general",
                "safesearch": _safesearch_for("searxng"),
            }
            if _GENERAL_ENGINES:
                fallback["engines"] = _GENERAL_ENGINES
            logger.info(
                "SearXNG news search returned 0 results for %r; retrying general engines",
                query,
            )
            active_params = fallback
            parsed, data = _run(active_params)
        if not parsed and active_params.get("language"):
            fallback = dict(active_params)
            fallback.pop("language", None)
            logger.info(
                "SearXNG language-pinned search returned 0 results for %r; retrying without language",
                query,
            )
            active_params = fallback
            parsed, data = _run(active_params)
        if not parsed and active_params.get("engines"):
            fallback = dict(active_params)
            fallback.pop("engines", None)
            logger.info(
                "SearXNG pinned engines returned 0 results for %r; retrying default engines",
                query,
            )
            parsed, data = _run(fallback)
        logger.info(f"SearXNG JSON API returned {len(parsed)} results for: {query}")
        if not parsed:
            unresponsive = data.get("unresponsive_engines") if isinstance(data, dict) else None
            if unresponsive:
                logger.info(f"SearXNG unresponsive engines for {query!r}: {unresponsive}")
        return parsed
    except Exception as e:
        logger.warning(f"SearXNG JSON API search failed: {e}")
        html_results = searxng_search(query, max_results=count)
        if html_results:
            logger.info(f"SearXNG HTML fallback returned {len(html_results)} results for: {query}")
        return html_results


def searxng_search(query, max_results=10):
    """Search using SearXNG instance - parsing HTML."""
    instance = _get_search_instance()
    api_key = ""
    req_headers = {"User-Agent": "Mozilla/5.0"}
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.get(
            f"{instance}/search",
            params={"q": query, "safesearch": _safesearch_for("searxng")},
            headers=req_headers,
            timeout=10,
        )
        if response.is_success:
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for article in soup.select("article.result")[:max_results]:
                title_elem = article.select_one("h3 a")
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                url = title_elem.get("href", "")
                snippet_elem = article.select_one("p.content")
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                results.append({"title": title, "url": url, "snippet": snippet})
            logger.info(f"SearXNG search (HTML) returned {len(results)} results")
            return results
    except Exception as e:
        logger.error(f"SearXNG search failed: {e}")
    return []


# ── Brave ──

def brave_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Brave API with key from admin settings or env var."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("brave") or os.environ.get("DATA_BRAVE_API_KEY") or ""
    return _brave_search_impl(query, count, time_filter, search_config={"brave_api_key": api_key})


def _brave_search_impl(query: str, count: int, time_filter: Optional[str] = None, search_config: dict = None) -> List[dict]:
    """Core Brave API call. Returns a list of result dicts or an empty list on failure."""
    enhanced_query = build_enhanced_query(query, time_filter)
    config = search_config or {}

    brave_api_key = config.get("brave_api_key")
    if not brave_api_key:
        brave_api_key = os.environ.get("DATA_BRAVE_API_KEY")

    if not brave_api_key:
        logger.warning("Brave API key not found, returning empty results for fallback")
        return []

    headers = {"X-Subscription-Token": brave_api_key, "Accept": "application/json"}
    params = {
        "q": enhanced_query,
        "count": count,
        "safesearch": _safesearch_for("brave"),
    }
    if time_filter:
        time_map = {"day": "day", "week": "week", "month": "month", "year": "year"}
        if time_filter in time_map:
            params["freshness"] = time_map[time_filter]

    logger.info(f"Executing Brave search with query: {enhanced_query}")
    try:
        response = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Brave rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError during Brave search: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Brave API response: {e}")
        return []

    results = []
    if "web" in data and "results" in data["web"]:
        for item in data["web"]["results"][:count]:
            url = item.get("url", "")
            if not url:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("description", "") or item.get("content", ""),
                "age": item.get("date", "") if item.get("date") else "",
            })

    logger.info(f"Brave search returned {len(results)} results")
    return results


# ── DuckDuckGo (free, no key) ──

def _is_duckduckgo_host(host: str) -> bool:
    """True only for duckduckgo.com and its subdomains."""
    host = (host or "").lower()
    return host == "duckduckgo.com" or host.endswith(".duckduckgo.com")


def _resolve_ddg_redirect(raw: str) -> str:
    """Resolve a DuckDuckGo /l/?uddg= redirect URL to its destination."""
    if not raw:
        return raw
    resolved = raw
    if resolved.startswith("//"):
        resolved = "https:" + resolved
    elif resolved.startswith("/"):
        resolved = urljoin("https://html.duckduckgo.com", resolved)
    try:
        parsed = urlparse(resolved)
        if _is_duckduckgo_host(parsed.hostname) and parsed.path.rstrip("/") == "/l":
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return qs["uddg"][0]
    except Exception:
        pass
    return resolved


def _is_duckduckgo_rename_warning(warning: warnings.WarningMessage) -> bool:
    message = str(warning.message)
    return (
        issubclass(warning.category, RuntimeWarning)
        and "duckduckgo_search" in message
        and "ddgs" in message
    )


def _build_ddgs_client(DDGS):
    with warnings.catch_warnings(record=True) as caught:
        ddgs = DDGS()
    for warning in caught:
        if _is_duckduckgo_rename_warning(warning):
            continue
        warnings.warn(
            warning.message,
            warning.category,
            stacklevel=2,
        )
    return ddgs


def duckduckgo_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using DuckDuckGo via the duckduckgo-search library. No API key needed."""
    count = count if count is not None else _get_result_count()
    def _html_fallback() -> List[dict]:
        def _link_result(link, snippet: str = "") -> Optional[dict]:
            url = _resolve_ddg_redirect(link.get("href", ""))
            if not url:
                return None
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return None
            title = link.get_text(" ", strip=True)
            if not title:
                return None
            return {"title": title, "url": url, "snippet": snippet}

        def _parse_html_results(html: str) -> List[dict]:
            soup = BeautifulSoup(html, "html.parser")
            parsed = []
            for result in soup.select(".result")[:count]:
                link = result.select_one(".result__a")
                if not link:
                    continue
                snippet_el = result.select_one(".result__snippet")
                item = _link_result(
                    link,
                    snippet_el.get_text(" ", strip=True) if snippet_el else "",
                )
                if item:
                    parsed.append(item)
            return parsed

        def _parse_lite_results(html: str) -> List[dict]:
            soup = BeautifulSoup(html, "html.parser")
            parsed = []
            seen = set()
            for link in soup.select("a"):
                item = _link_result(link)
                if not item or item["url"] in seen:
                    continue
                seen.add(item["url"])
                parsed.append(item)
                if len(parsed) >= count:
                    break
            return parsed

        try:
            data = {"q": query, "kp": _safesearch_for("duckduckgo_html")}
            response = httpx.post(
                "https://html.duckduckgo.com/html/",
                data=data,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            parsed = _parse_html_results(response.text)
            logger.info(
                f"DuckDuckGo HTML search returned {len(parsed)} results "
                f"(status {getattr(response, 'status_code', 'unknown')})"
            )
            if parsed:
                return parsed

            lite_response = httpx.post(
                "https://lite.duckduckgo.com/lite/",
                data=data,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            lite_response.raise_for_status()
            parsed = _parse_lite_results(lite_response.text)
            logger.info(
                f"DuckDuckGo Lite search returned {len(parsed)} results "
                f"(status {getattr(lite_response, 'status_code', 'unknown')})"
            )
            return parsed
        except Exception as e:
            logger.warning(f"DuckDuckGo HTML search failed: {e}")
            return []

    try:
        from ddgs import DDGS
    except ImportError:
        logger.warning("duckduckgo-search package not installed; using HTML fallback")
        return _html_fallback()

    timelimit = None
    if time_filter:
        time_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        timelimit = time_map.get(time_filter)

    def _library_search_once() -> List[dict]:
        ddgs = _build_ddgs_client(DDGS)
        raw = ddgs.text(
            query,
            max_results=count,
            timelimit=timelimit,
            safesearch=_safesearch_for("duckduckgo_lib"),
        )
        results = []
        for item in raw:
            url = item.get("href", "")
            if not url:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("body", ""),
            })
        return results

    try:
        for attempt in range(2):
            results = _library_search_once()
            logger.info(
                f"DuckDuckGo search returned {len(results)} results "
                f"(attempt {attempt + 1})"
            )
            if results:
                return results
            if attempt == 0:
                logger.info("DuckDuckGo returned 0 results; retrying once before HTML fallback")
                if DDG_RETRY_DELAY_SECONDS > 0:
                    time.sleep(DDG_RETRY_DELAY_SECONDS)
        logger.warning("DuckDuckGo returned 0 results after retry; using HTML fallback")
        return _html_fallback()
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return _html_fallback()


# ── Google Programmable Search Engine ──

def google_pse_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Google PSE (Custom Search JSON API).

    Requires two keys in settings:
      - search_api_key: Google API key
      - google_pse_cx: Programmable Search Engine ID (cx)
    Or env vars GOOGLE_API_KEY and GOOGLE_PSE_CX.
    """
    count = count if count is not None else _get_result_count()
    settings = _get_search_settings()
    api_key = _get_provider_key("google_pse") or os.environ.get("GOOGLE_API_KEY", "")
    cx = (settings.get("google_pse_cx") or "").strip() or os.environ.get("GOOGLE_PSE_CX", "")

    if not api_key or not cx:
        logger.warning("Google PSE: missing API key or CX ID")
        return []

    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": min(count, 10),  # Google PSE max is 10 per request
    }
    safe = _safesearch_for("google_pse")
    if safe:
        params["safe"] = safe
    if time_filter:
        # dateRestrict: d[number], w[number], m[number], y[number]
        time_map = {"day": "d1", "week": "w1", "month": "m1", "year": "y1"}
        if time_filter in time_map:
            params["dateRestrict"] = time_map[time_filter]

    try:
        response = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Google PSE rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Google PSE search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Google PSE returned invalid JSON: {e}")
        return []

    results = []
    for item in data.get("items", [])[:count]:
        url = item.get("link", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
        })

    logger.info(f"Google PSE returned {len(results)} results")
    return results


# ── Tavily ──

def tavily_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Tavily API. Requires search_api_key or TAVILY_API_KEY env var."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("tavily") or os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        logger.warning("Tavily: no API key configured")
        return []

    payload = {
        "query": query,
        "max_results": count,
        "include_answer": False,
    }
    if time_filter:
        time_map = {"day": "day", "week": "week", "month": "month", "year": "year"}
        if time_filter in time_map:
            payload["days"] = {"day": 1, "week": 7, "month": 30, "year": 365}[time_filter]

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Tavily rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Tavily search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Tavily returned invalid JSON: {e}")
        return []

    results = []
    for item in data.get("results", [])[:count]:
        url = item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("content", ""),
            "age": item.get("published_date", ""),
        })

    logger.info(f"Tavily returned {len(results)} results")
    return results


# ── Serper.dev ──

def serper_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using Serper.dev API. Requires search_api_key or SERPER_API_KEY env var."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("serper") or os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("Serper: no API key configured")
        return []

    payload = {
        "q": query,
        "num": count,
    }
    safe = _safesearch_for("serper")
    if safe:
        payload["safe"] = safe
    if time_filter:
        time_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
        if time_filter in time_map:
            payload["tbs"] = time_map[time_filter]

    try:
        response = httpx.post(
            "https://google.serper.dev/search",
            json=payload,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Serper rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Serper search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Serper returned invalid JSON: {e}")
        return []

    results = []
    for item in data.get("organic", [])[:count]:
        url = item.get("link", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
            "age": item.get("date", ""),
        })

    logger.info(f"Serper returned {len(results)} results")
    return results


# ── Kagi ──

def kagi_search(query: str, count: int = 10, time_filter: Optional[str] = None) -> List[dict]:
    """Search using the Kagi Search API v1 (POST /search, Bearer auth).

    Kagi's Search API is billed per query (no free tier). Honors the configured
    SafeSearch level via the boolean ``safe_search`` flag (Kagi has no middle
    tier, so anything other than "off" enables it) and ``time_filter`` via
    ``lens.time_relative`` (day/week/month) or ``filters.after`` for "year".
    Results live under ``data.search`` with ``time`` as the freshness field.
    """
    api_key = _get_provider_key("kagi") or os.environ.get("KAGI_API_KEY", "")
    if not api_key:
        logger.warning("Kagi: no API key configured")
        return []

    payload: dict = {
        "query": query,
        "limit": count,
        "safe_search": _get_safesearch_level() != "off",
    }
    if time_filter in ("day", "week", "month"):
        payload["lens"] = {"time_relative": time_filter}
    elif time_filter == "year":
        # Kagi's time_relative enum stops at "month"; map "year" to an absolute
        # lower bound so year-scoped queries still constrain recency.
        from datetime import datetime, timedelta
        after = (datetime.now() - timedelta(days=365)).date().isoformat()
        payload["filters"] = {"after": after}

    try:
        response = httpx.post(
            "https://kagi.com/api/v1/search",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("Kagi rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"Kagi search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"Kagi returned invalid JSON: {e}")
        return []

    results = []
    search_items = (data.get("data") or {}).get("search", []) if isinstance(data, dict) else []
    for item in search_items[:count]:
        url = item.get("url", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
            "age": item.get("time", ""),
        })

    logger.info(f"Kagi returned {len(results)} results")
    return results


def serpapi_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using SerpApi JSON API."""
    count = count if count is not None else _get_result_count()
    api_key = _get_provider_key("serpapi") or os.environ.get("SERPAPI_API_KEY", "")
    if not api_key:
        logger.warning("SerpApi: no API key configured")
        return []

    params = {
        "engine": "google_light",
        "q": query,
        "api_key": api_key,
    }
    if time_filter:
        time_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
        if time_filter in time_map:
            params["tbs"] = time_map[time_filter]

    try:
        response = httpx.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429:
            raise RateLimitError("SerpApi rate limit hit")
        response.raise_for_status()
    except httpx.RequestError as e:
        error_logger.error(f"SerpApi search failed: {e}")
        return []
    except RateLimitError as e:
        error_logger.error(str(e))
        return []

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        error_logger.error(f"SerpApi returned invalid JSON: {e}")
        return []

    results = []
    organic = data.get("organic_results", []) if isinstance(data, dict) else []
    for item in organic[:count]:
        url = item.get("link", "")
        if not url:
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", "") or "",
            "age": item.get("date", "") or "",
        })

    logger.info(f"SerpApi returned {len(results)} results")
    return results
