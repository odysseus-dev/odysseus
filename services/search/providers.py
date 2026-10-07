"""Search provider implementations: SearXNG, Brave, DuckDuckGo, Google PSE, Tavily, Serper."""

import json
import logging
import os
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from src.constants import SEARXNG_INSTANCE, REQUEST_TIMEOUT, WEB_FETCH_USER_AGENT
from .analytics import ProviderUnavailableError, RateLimitError, error_logger
from .query import build_enhanced_query

logger = logging.getLogger(__name__)

# Provider registry — maps setting value to (label, needs_key, needs_url)
PROVIDER_INFO = {
    "searxng":  ("SearXNG",           False, True),
    "brave":    ("Brave Search",      True,  False),
    "duckduckgo": ("DuckDuckGo",      False, False),
    "google_pse": ("Google PSE",      True,  False),
    "tavily":   ("Tavily",            True,  False),
    "serper":   ("Serper",            True,  False),
    "disabled": ("Disabled",          False, False),
}


# ── Settings helpers ──

def _get_search_settings() -> dict:
    """Return search settings from admin config, falling back to env defaults."""
    try:
        from src.settings import load_settings
        settings = dict(load_settings())
    except Exception:
        settings = {}
    # Headless/native deployments do not necessarily have an admin settings
    # database.  Require an explicit Odysseus-prefixed override so ordinary UI
    # configuration remains authoritative by default.
    env_provider = os.environ.get("ODYSSEUS_SEARCH_PROVIDER", "").strip().lower()
    if env_provider:
        settings["search_provider"] = env_provider
    return settings


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
        # DATA_BRAVE_API_KEY is the historical Odysseus name; BRAVE_API_KEY is
        # the standard name used by headless runners and the Brave SDK.
        "brave": ("DATA_BRAVE_API_KEY", "BRAVE_API_KEY"),
        "google_pse": ("GOOGLE_API_KEY",),
        "tavily": ("TAVILY_API_KEY",),
        "serper": ("SERPER_API_KEY",),
    }
    for env_name in env_map.get(provider, ()):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return ""


def _get_result_count() -> int:
    """Return configured result count, default 5."""
    settings = _get_search_settings()
    try:
        return int(settings.get("search_result_count", 5))
    except (ValueError, TypeError):
        return 5


def provider_configured(provider: str) -> bool:
    """Configuration readiness only; a configured engine can still fail upstream."""
    if provider in {"searxng", "searxng_yep", "duckduckgo"}:
        return True
    if provider not in {"brave", "google_pse", "tavily", "serper"}:
        return False
    if not _get_provider_key(provider):
        return False
    if provider == "google_pse":
        return bool(_get_search_settings().get("google_pse_cx") or os.environ.get("GOOGLE_PSE_CX"))
    return True


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

_NEWS_HINTS = (
    "news", "nyheter", "headlines", "breaking", "idag",
    "current events", "what's happening", "what is happening",
)
_NEWS_EVENT_HINT_RE = re.compile(
    r"\b(?:deport(?:ation|ed|ing)?|arrest(?:ed|s)?|election(?:s)?|"
    r"evacuat(?:e|ed|ion)|flood(?:ing|s|ed)?|sanction(?:s|ed)?)\b",
    re.IGNORECASE,
)
_SOFTWARE_RELEASE_HINTS = (
    "github",
    "gitlab",
    "release",
    "releases",
    "version",
    "versions",
    "changelog",
    "change log",
    "pypi",
    "npm",
    "package",
)

# Verified with the pinned September SearXNG adapters. Bing can return unrelated
# pages as successful results; do not prefer it over working general engines.
# Deployments can override this via SEARXNG_GENERAL_ENGINES.
_GENERAL_ENGINES = os.environ.get("SEARXNG_GENERAL_ENGINES", "google,brave,duckduckgo")


def searxng_search_api(query: str, count: Optional[int] = None, categories: str = "general",
                       time_filter: Optional[str] = None, *, engines: Optional[str] = None) -> List[dict]:
    """Search using SearXNG JSON API. Returns list of {title, url, snippet}."""
    count = count if count is not None else _get_result_count()
    instance = _get_search_instance()
    api_key = ""
    headers = {"User-Agent": WEB_FETCH_USER_AGENT}
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
    # Fresh software-version queries are usually better served by general
    # search or canonical project pages than by the news vertical. For example
    # "latest ollama release version github" can return a sparse news result
    # that gets filtered as irrelevant, while general engines find GitHub.
    is_software_release_query = any(h in q_lc for h in _SOFTWARE_RELEASE_HINTS)
    is_news = (
        not is_software_release_query
        and (
            any(h in q_lc for h in _NEWS_HINTS)
            or bool(_NEWS_EVENT_HINT_RE.search(query))
            or (
                bool(re.search(r'\bdevelopments\b', query, re.I))
                and bool(re.search(r'\b(?:latest|recent|today|this\s+(?:week|month)|past\s+(?:week|month))\b', query, re.I))
            )
        )
    )
    if is_news and categories == "general":
        params["categories"] = "news"
        if time_filter in ("day", "week", "month", "year"):
            params["time_range"] = time_filter
    else:
        params["categories"] = categories
        # Freshness and source category are independent: current manuals,
        # comparisons and documentation still belong in general search.
        if time_filter in ("day", "week", "month", "year"):
            params["time_range"] = time_filter
        # Route general queries to engines that aren't blocked (default general
        # set returns 0 on this instance — see _GENERAL_ENGINES).
        if categories == "general" and _GENERAL_ENGINES:
            params["engines"] = _GENERAL_ENGINES
            # SearXNG unions category engines with explicit engines. Omitting
            # categories makes this a real engine selection rather than also
            # invoking the default engines that may be CAPTCHA-blocked.
            params.pop("categories", None)
    if engines:
        params["engines"] = engines
        params.pop("categories", None)
    try:
        engine_errors = []

        def _parse_results(results, infoboxes=None):
            buckets = {}

            def _add(row, url, title, snippet, fallback_engine=""):
                if not isinstance(url, str) or not url:
                    return
                engine = row.get("engine")
                if not isinstance(engine, str) or not engine:
                    engines = row.get("engines")
                    engine = engines[0] if isinstance(engines, list) and engines else ""
                if not isinstance(engine, str) or not engine:
                    engine = fallback_engine
                buckets.setdefault(engine, []).append({
                    "title": title, "url": url, "snippet": snippet,
                    "provider": "searxng",
                    "engines": row.get("engines") or ([engine] if engine else []),
                    "published_date": row.get("publishedDate"),
                    "query": query,
                })

            for row in results or []:
                if isinstance(row, dict):
                    _add(row, row.get("url"), row.get("title", ""), row.get("content", ""))
            for box in infoboxes or []:
                if not isinstance(box, dict):
                    continue
                links = box.get("urls")
                if not isinstance(links, list) or not links:
                    links = [{"url": box.get("url") or box.get("id")}]
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    url = link.get("url")
                    if not isinstance(url, str):
                        continue
                    try:
                        parsed_url = urlparse(url)
                    except ValueError:
                        continue
                    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                        continue
                    title = box.get("infobox") or box.get("title") or link.get("title") or url
                    _add(box, url, title, box.get("content", ""), "infobox")

            # SearXNG can return entire engine groups consecutively. Taking
            # the first count rows would hide every later engine's sources.
            # One bucket without metadata retains the original result order.
            parsed, seen = [], set()
            active = [iter(rows) for rows in buckets.values()]
            while active and len(parsed) < count:
                following = []
                for rows in active:
                    for row in rows:
                        if row["url"] not in seen:
                            seen.add(row["url"])
                            parsed.append(row)
                            following.append(rows)
                            break
                    if len(parsed) >= count:
                        break
                active = following
            return parsed

        def _run(search_params):
            response = httpx.get(
                f"{instance}/search",
                params=search_params,
                headers=headers or None,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            parsed = _parse_results(data.get("results", []), data.get("infoboxes", []))
            if not parsed:
                for error in data.get("unresponsive_engines", []) or []:
                    if not isinstance(error, (list, tuple)) or len(error) < 2:
                        continue
                    engine = " ".join(str(error[0]).split())[:80]
                    reason = " ".join(str(error[1]).split())[:180]
                    detail = f"{engine}: {reason}"
                    if detail not in engine_errors:
                        engine_errors.append(detail)
            return parsed, data

        active_params = params
        parsed, data = _run(active_params)
        if not parsed and active_params.get("categories") == "news":
            # Some self-hosted SearXNG configs have no working news engines.
            # Fall back to the known-good general engines before reporting an
            # empty search, otherwise common queries like "Canada news" fail.
            fallback = {**active_params, "categories": "general"}
            if _GENERAL_ENGINES:
                fallback["engines"] = _GENERAL_ENGINES
                fallback.pop("categories", None)
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
            fallback["categories"] = categories
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
            if engine_errors:
                raise ProviderUnavailableError(
                    "SearXNG returned no results; engine errors: "
                    + "; ".join(engine_errors[:6])
                )
        return parsed
    except ProviderUnavailableError:
        raise
    except Exception as e:
        logger.warning(f"SearXNG JSON API search failed: {e}")
        html_results = searxng_search(query, max_results=count, search_params=active_params)
        if html_results:
            logger.info(f"SearXNG HTML fallback returned {len(html_results)} results for: {query}")
        return html_results


def searxng_search(query, max_results=10, *, search_params=None):
    """Search using SearXNG instance - parsing HTML."""
    instance = _get_search_instance()
    api_key = ""
    req_headers = {"User-Agent": WEB_FETCH_USER_AGENT}
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    # Transport fallback must not change the user's retrieval constraints.
    # In particular omit only JSON formatting, not publication time/category.
    params = {key: value for key, value in (search_params or {}).items()
              if key in {'categories', 'engines', 'language', 'time_range'}}
    params.update({'q': query, 'safesearch': _safesearch_for('searxng')})
    try:
        response = httpx.get(
            f"{instance}/search",
            params=params,
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


def duckduckgo_search(query: str, count: Optional[int] = None, time_filter: Optional[str] = None) -> List[dict]:
    """Search using DuckDuckGo via the duckduckgo-search library. No API key needed."""
    count = count if count is not None else _get_result_count()
    def _html_fallback() -> List[dict]:
        try:
            response = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query, "kp": _safesearch_for("duckduckgo_html")},
                headers={"User-Agent": WEB_FETCH_USER_AGENT},
                # This is a last-resort compatibility path when the declared
                # optional ``ddgs`` dependency is absent. Keep it short so a
                # blocked public endpoint cannot consume the full search SLA
                # across provider and query-relaxation retries.
                timeout=min(REQUEST_TIMEOUT, 5),
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            parsed = []
            for result in soup.select(".result")[:count]:
                link = result.select_one(".result__a")
                if not link:
                    continue
                url = _resolve_ddg_redirect(link.get("href", ""))
                if not url:
                    continue
                snippet_el = result.select_one(".result__snippet")
                parsed.append({
                    "title": link.get_text(" ", strip=True),
                    "url": url,
                    "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                })
            logger.info(f"DuckDuckGo HTML search returned {len(parsed)} results")
            if not parsed:
                challenge = soup.select_one("#challenge-form, #anomaly-form, .anomaly-modal")
                if challenge is not None:
                    raise ProviderUnavailableError(
                        "DuckDuckGo requires a CAPTCHA or bot verification"
                    )
                if response.status_code == 202:
                    raise ProviderUnavailableError(
                        "DuckDuckGo returned HTTP 202 instead of search results"
                    )
            return parsed
        except ProviderUnavailableError:
            raise
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

    try:
        ddgs = DDGS()
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
        logger.info(f"DuckDuckGo search returned {len(results)} results")
        return results or _html_fallback()
    except ProviderUnavailableError:
        raise
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
