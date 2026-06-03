"""Core search orchestrators: searxng_search_results, comprehensive_web_search, config, cache invalidation."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set
from urllib.parse import urlparse

from .analytics import (
    NetworkError,
    ParseError,
    RateLimitError,
    error_logger,
    _record_query,
)
from .cache import (
    SEARCH_CACHE_DIR,
    search_cache_index,
    generate_cache_key,
    cleanup_cache,
)
from .query import _cache_duration_for_query
from .ranking import rank_search_results
from .providers import (
    searxng_search_api,
    brave_search,
    duckduckgo_search,
    google_pse_search,
    tavily_search,
    serper_search,
    _get_search_settings,
    _get_provider_key,
    _get_result_count,
)
from .content import (
    fetch_webpage_content,
    extract_key_points,
    get_tldr,
    extract_quotes,
    extract_statistics,
)

logger = logging.getLogger(__name__)

# ========= CONFIG =========
SEARCH_CONFIG: Dict[str, Any] = {
    "primary_provider": "searxng",
}


def _is_secret_key(name: str) -> bool:
    """True for config keys that hold a credential (e.g. ``brave_api_key``)."""
    return name.endswith(("_api_key", "_key", "_token", "_secret"))


def get_search_config() -> Dict[str, Any]:
    """Get current search configuration including active provider info.

    Never returns stored API keys: callers — including the unauthenticated
    ``GET /api/search/config`` route — only need key *presence* via
    ``has_api_key``, not the secret itself (#1661).
    """
    config = SEARCH_CONFIG.copy()
    settings = _get_search_settings()
    provider = settings.get("search_provider", "searxng")
    config["active_provider"] = provider
    config["has_api_key"] = bool(_get_provider_key(provider))
    config["result_count"] = _get_result_count()
    if provider == "searxng":
        from .providers import _get_search_instance
        config["search_url"] = _get_search_instance()
    # Strip any string-valued credential so secrets never reach the response;
    # the boolean has_api_key flag (presence only) is preserved.
    return {
        k: v for k, v in config.items()
        if not (isinstance(v, str) and _is_secret_key(k))
    }


def update_search_config(api_key: str = None, **kwargs):
    """Merge non-secret search config into SEARCH_CONFIG.

    Provider API keys are intentionally NOT cached here. They are read on demand
    from settings/env via ``_get_provider_key`` (e.g. ``brave_search``), so the
    previous ``SEARCH_CONFIG["brave_api_key"] = api_key`` cache was never used
    for search and only leaked the decrypted key through ``get_search_config`` /
    ``GET /api/search/config`` (#1661). ``api_key`` is accepted for backward
    compatibility but no longer stored.
    """
    for k, v in kwargs.items():
        if not _is_secret_key(k):
            SEARCH_CONFIG[k] = v


def _call_provider(provider_name: str, query: str, count: int, time_filter: str = None) -> List[dict]:
    """Call a search provider by name. Returns list of results or empty list."""
    if provider_name == "searxng":
        return searxng_search_api(query, count, time_filter=time_filter)
    elif provider_name == "brave":
        return brave_search(query, count, time_filter)
    elif provider_name == "duckduckgo":
        return duckduckgo_search(query, count, time_filter)
    elif provider_name == "google_pse":
        return google_pse_search(query, count, time_filter)
    elif provider_name == "tavily":
        return tavily_search(query, count, time_filter)
    elif provider_name == "serper":
        return serper_search(query, count, time_filter)
    return []


# If the self-hosted SearXNG instance is up but all enabled engines return
# empty, fall back to the no-key provider so "search X" still works on fresh
# installs. Users can override/disable with `search_fallback_chain`.
_FALLBACK_ORDER = ["duckduckgo"]


def _build_provider_chain(primary: str) -> List[str]:
    """Build ordered list: primary first, then configured/default fallbacks."""
    chain = [primary]
    settings = _get_search_settings()
    user_chain = settings.get("search_fallback_chain") or []
    if isinstance(user_chain, str):
        user_chain = [s.strip() for s in user_chain.split(",") if s.strip()]
    fallbacks = user_chain if user_chain else _FALLBACK_ORDER
    for fb in fallbacks:
        if fb and fb != primary and fb not in chain and fb != "disabled":
            chain.append(fb)
    return chain


# ----------------------------------------------------------------------
# Unified search with caching and retry
# ----------------------------------------------------------------------
def searxng_search_results(query: str, count: int = 10, time_filter: str = None) -> list[dict]:
    """Perform a web search using configured provider with caching and retry."""
    settings = _get_search_settings()
    search_provider = settings.get("search_provider", "searxng")
    result_count = _get_result_count()
    # Use configured count if caller used default
    if count == 10:
        count = result_count

    cache_key = generate_cache_key(f"{query}|{count}|{time_filter}")
    cache_file = SEARCH_CACHE_DIR / f"{cache_key}.cache"

    # Check cache
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            expiry_raw = cached_data.get("expiry")
            expiry = datetime.fromisoformat(expiry_raw) if expiry_raw else None
            if expiry and datetime.now() < expiry:
                logger.debug(f"Search cache hit for query: {query}")
                results = cached_data["data"]
                _record_query(query, bool(results), cache_hit=True)
                return results
            else:
                cache_file.unlink(missing_ok=True)
                search_cache_index.pop(cache_key, None)
        except Exception as e:
            logger.warning(f"Failed to read search cache for {query}: {e}")
            cache_file.unlink(missing_ok=True)
            search_cache_index.pop(cache_key, None)

    logger.debug(f"Search cache miss for query: {query}")

    if search_provider == "disabled":
        logger.info("Search is disabled via admin settings")
        return []

    provider_chain = _build_provider_chain(search_provider)

    results: List[dict] = []
    for provider_name in provider_chain:
        for attempt in range(2):
            try:
                logger.info(f"Attempting {provider_name} search (attempt {attempt + 1})")
                results = _call_provider(provider_name, query, count, time_filter)
                if results:
                    logger.info(f"{provider_name} search succeeded with {len(results)} results")
                    break
            except (NetworkError, ParseError, RateLimitError) as e:
                error_logger.error(f"{provider_name} search error (attempt {attempt + 1}): {e}")
            except Exception as e:
                error_logger.error(f"Unexpected error during {provider_name} search (attempt {attempt + 1}): {e}")
        if results:
            break

    success = bool(results)
    _record_query(query, success, cache_hit=False)

    if success:
        results = rank_search_results(query, results)
        try:
            expiry = datetime.now() + _cache_duration_for_query(query)
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "expiry": expiry.isoformat(),
                "data": results,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)
            search_cache_index[cache_key] = datetime.now()
            cleanup_cache(SEARCH_CACHE_DIR, search_cache_index, timedelta(hours=1))
        except Exception as e:
            logger.warning(f"Failed to write search cache for {query}: {e}")

    if not success:
        logger.error(f"All search providers failed for query: {query}")

    return results


# ----------------------------------------------------------------------
# Cache invalidation
# ----------------------------------------------------------------------
def invalidate_search_cache(query: Optional[str] = None) -> None:
    """Invalidate cached search results. None clears all, otherwise just the given query."""
    if query is None:
        for file in SEARCH_CACHE_DIR.glob("*.cache"):
            try:
                file.unlink(missing_ok=True)
            except Exception as e:
                error_logger.warning(f"Failed to delete cache file {file}: {e}")
        search_cache_index.clear()
        logger.info("All search cache entries have been cleared.")
    else:
        # Match the key the write path stores: searxng_search_results replaces
        # the caller's default count with the configured _get_result_count()
        # (default 5), so a hardcoded "|10|None" never matched a real entry.
        cache_key = generate_cache_key(f"{query}|{_get_result_count()}|None")
        cache_file = SEARCH_CACHE_DIR / f"{cache_key}.cache"
        if cache_file.exists():
            try:
                cache_file.unlink(missing_ok=True)
                search_cache_index.pop(cache_key, None)
                logger.info(f"Cache entry for query '{query}' has been invalidated.")
            except Exception as e:
                error_logger.warning(f"Failed to delete cache file for query '{query}': {e}")
        else:
            logger.info(f"No cache entry found for query '{query}'.")


# ----------------------------------------------------------------------
# Comprehensive web search (with advanced filtering)
# ----------------------------------------------------------------------
def comprehensive_web_search(
    query: str,
    max_pages: int = 3,
    max_workers: int = 4,
    time_filter: str = None,
    domain_whitelist: Optional[Set[str]] = None,
    domain_blacklist: Optional[Set[str]] = None,
    content_type: Optional[str] = None,
    language: Optional[str] = None,
    min_content_length: int = 0,
    return_sources: bool = False,
):
    """Perform comprehensive web search with content fetching and advanced filtering."""
    logger.info(f"Starting comprehensive search for: {query}")
    if time_filter:
        logger.info(f"Applying time filter: {time_filter}")

    settings = _get_search_settings()
    search_provider = settings.get("search_provider", "searxng")
    result_count = _get_result_count()

    if search_provider == "disabled":
        logger.info("Search is disabled via admin settings")
        msg = "Web search is disabled by the administrator."
        return (msg, []) if return_sources else msg

    # Use configured result count (at least max_pages for content fetching)
    fetch_count = max(result_count, max_pages)

    provider_chain = _build_provider_chain(search_provider)

    search_results = []
    provider_attempts = {}
    for provider_name in provider_chain:
        last_err = None
        empty = False
        for attempt in range(2):
            try:
                search_results = _call_provider(provider_name, query, fetch_count, time_filter)
                if search_results:
                    provider_attempts[provider_name] = f"ok ({len(search_results)})"
                    logger.info(f"Comprehensive search: {provider_name} returned {len(search_results)} results")
                    break
                empty = True
            except Exception as e:
                last_err = e
                logger.warning(f"Comprehensive search: {provider_name} attempt {attempt + 1} failed: {e}")
        if search_results:
            break
        if last_err is not None:
            provider_attempts[provider_name] = f"error: {last_err}"
        elif empty:
            provider_attempts[provider_name] = "empty"

    if not search_results:
        tally = ", ".join(f"{p}:{r}" for p, r in provider_attempts.items()) or "no providers configured"
        any_errors = any(r.startswith("error") for r in provider_attempts.values())
        if any_errors:
            msg = f"Web search failed — all providers errored or returned empty. Tried: {tally}"
        else:
            msg = (
                f"No search results found. Tried: {tally}. "
                "All providers returned empty — possibly a niche query or upstream rate-limiting; "
                "rephrasing or using the browser tool for a specific URL may help."
            )
        logger.warning(msg)
        return (msg, []) if return_sources else msg

    search_results = rank_search_results(query, search_results)

    # URL filter helper
    def url_passes_filters(url: str) -> bool:
        try:
            netloc = urlparse(url).netloc.lower()
        except Exception:
            return False
        if domain_whitelist is not None and netloc not in domain_whitelist:
            return False
        if domain_blacklist is not None and netloc in domain_blacklist:
            return False
        if content_type:
            ct = content_type.lower()
            if ct == "article":
                if not any(k in url.lower() for k in ("article", "blog", "news", "post")):
                    return False
            elif ct == "forum":
                if not any(k in url.lower() for k in ("forum", "discussion", "thread", "topic")):
                    return False
            elif ct == "academic":
                if not any(k in url.lower() for k in ("pdf", "doi", "scholar", "arxiv", "journal", "research")):
                    return False
        if language:
            lang_pat = language.lower()
            if not (f"/{lang_pat}/" in url.lower() or f"?lang={lang_pat}" in url.lower() or f"&lang={lang_pat}" in url.lower()):
                return False
        return True

    filtered_urls = [r["url"] for r in search_results[:max_pages] if url_passes_filters(r["url"])]
    if not filtered_urls:
        logger.warning("All URLs filtered out by advanced criteria")
        msg = "No suitable results after applying filters."
        return (msg, []) if return_sources else msg

    # Build sources list for the frontend (before content fetching)
    _source_list = [
        {"url": r.get("url", ""), "title": r.get("title", "")}
        for r in search_results if r.get("url")
    ]

    # Map each URL to its [i] number in the sources list so fetched content
    # blocks can be labeled with the SAME index the model cites.
    _url_index = {
        r["url"]: i for i, r in enumerate(search_results, 1) if r.get("url")
    }

    # Fetch content in parallel
    fetched_content = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(fetch_webpage_content, url, 8, retry_attempt=0): url
            for url in filtered_urls
        }
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                result = future.result()
                if result["success"] and result["content"] and len(result["content"]) >= min_content_length:
                    # Remember which source this fetch belongs to: redirects
                    # can change result["url"] and completion order is
                    # arbitrary, so the block label cannot be recomputed later.
                    result["source_index"] = _url_index.get(url)
                    fetched_content.append(result)
            except Exception as e:
                logger.error(f"Exception while fetching {url}: {str(e)}")

    logger.info(f"Successfully fetched content from {len(fetched_content)} pages")

    # Format results
    output_parts = []

    if search_results:
        output_parts.append("```sources")
        for i, result in enumerate(search_results, 1):
            output_parts.append(f"[{i}] {result['title']}")
            output_parts.append(f"    {result['url']}")
            if result.get("age"):
                output_parts.append(f"    {result['age']}")
        output_parts.append("```")
        output_parts.append("")

    output_parts.append("=" * 70)
    output_parts.append("WEB SEARCH RESULTS AND FETCHED CONTENT")
    output_parts.append(f"Query: {query}")
    output_parts.append(f"Searched {len(search_results)} results, fetched {len(fetched_content)} pages")
    output_parts.append("=" * 70)
    output_parts.append("")

    output_parts.append("SEARCH RESULTS SUMMARY:")
    output_parts.append("-" * 50)
    for i, result in enumerate(search_results, 1):
        output_parts.append(f"\n[{i}] {result['title']}")
        output_parts.append(f"    URL: {result['url']}")
        output_parts.append(f"    Snippet: {result['snippet'][:200]}...")
        if result.get("age"):
            output_parts.append(f"    Age: {result['age']}")

    if fetched_content:
        output_parts.append("\n" + "=" * 70)
        output_parts.append("FETCHED PAGE CONTENT:")
        output_parts.append("-" * 50)

        # Emit blocks in source order, numbered with the same [i] as the
        # sources list, so [CONTENT 2] really is content from source [2].
        # Before this, blocks were numbered 1..N in fetch COMPLETION order,
        # which matched neither the sources list nor each other run to run.
        fetched_content.sort(key=lambda c: c.get("source_index") or len(search_results) + 1)
        for content in fetched_content:
            _idx = content.get("source_index")
            _label = f"[CONTENT {_idx}]" if _idx else "[CONTENT]"
            output_parts.append(f"\n{_label} From: {content['url']}")
            output_parts.append(f"Title: {content['title']}")
            output_parts.append("-" * 30)

            text = content["content"][:3000]
            if len(content["content"]) > 3000:
                text += "... [truncated]"
            output_parts.append(text)

            key_points = extract_key_points(content["content"])
            if key_points:
                output_parts.append("\nKey Points:")
                for pt in key_points[:5]:
                    output_parts.append(f"- {pt}")

            tldr = get_tldr(content["content"])
            if tldr:
                output_parts.append("\nTL;DR:")
                output_parts.append(tldr)

            quotes = extract_quotes(content["content"])
            if quotes:
                output_parts.append("\nImportant Quotes:")
                for q in quotes[:3]:
                    output_parts.append(f"\u201c{q}\u201d")

            stats = extract_statistics(content["content"])
            if stats:
                output_parts.append("\nData / Statistics:")
                for s in stats[:5]:
                    output_parts.append(f"- {s}")

            output_parts.append("")

    output_parts.append("=" * 70)
    output_parts.append("END OF WEB SEARCH RESULTS")
    output_parts.append("=" * 70)

    instructions = (
        "\n\nIMPORTANT INSTRUCTIONS:\n"
        "1. Use the above web search results and fetched content to answer the user's question\n"
        "2. Prioritize information from the FETCHED PAGE CONTENT section as it contains actual page data\n"
        "3. Cross-reference multiple sources when possible\n"
        "4. If the information is time-sensitive, pay attention to the age of the results\n"
        "5. Be explicit if the search results don't contain sufficient information to fully answer the question"
    )
    output_parts.append(instructions)

    result = "\n".join(output_parts)
    return (result, _source_list) if return_sources else result
