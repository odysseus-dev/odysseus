"""Core search orchestrators: searxng_search_results, comprehensive_web_search, config, cache invalidation."""

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set
from urllib.parse import urlparse
from src.constants import (
    ARXIV_API_URL,
    OPENALEX_API_URL,
    SCHOLARLY_LOOKUP_TOTAL_BUDGET,
)
from src.search_passages import search_excerpt

import httpx

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
    elif provider_name == "searxng_yep":
        return searxng_search_api(query, count, time_filter=time_filter, engines="yep")
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
    from .providers import provider_configured
    configured = [provider for provider in chain if provider_configured(provider)]
    for provider in set(chain) - set(configured):
        logger.warning("Skipping unconfigured search provider: %s", provider)
    if primary == "searxng" and "searxng_yep" not in configured:
        # The no-key DuckDuckGo fallback can be configured yet unavailable or
        # CAPTCHA-limited. Always retain a distinct engine on the private
        # metasearch instance before reporting retrieval failure.
        configured.insert(1, "searxng_yep")
    return configured


_SEARCH_QUERY_FILLER = {
    "what", "whats", "what's", "which", "when", "where", "year", "from",
    "any", "info", "information", "details", "update", "updates",
    "with", "this", "that", "search", "lookup", "look", "find", "tell",
    "about", "quick", "please", "pls", "official", "links", "source",
    "sources", "news", "headlines", "breaking", "latest", "current",
    "newest", "recent", "today", "now",
    "release", "releases", "version", "versions", "changelog", "github",
    "gitlab", "weather", "forecast", "forecasts", "tomorrow", "hourly",
    "daily", "temperature", "temperatures", "conditions", "rain", "raining",
    "chance", "precipitation",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "the", "and", "or", "but", "are", "was", "were", "does", "did",
    "can", "could", "should", "would", "will", "has", "have", "had",
    "for", "into", "onto", "near", "over", "under",
}

_SHORT_QUERY_SUBJECTS = {"ai", "ar", "eu", "uk", "us", "vr"}

_EMPTY_RESULT_RELAXATION_TERMS = {
    "find", "search", "lookup", "look", "online", "official", "source",
    "sources", "english", "download", "please", "latest", "current",
}


def _relaxed_query_after_empty(query: str) -> str:
    """Remove request scaffolding once an exact provider query returns nothing."""
    # Token-based relaxation cannot preserve search operators, quoted phrases,
    # or exclusions. Do not silently broaden an explicit source constraint.
    if re.search(r'\b\w+:|["\u201c\u201d]|(?:^|\s)-\S', str(query or "")):
        return ""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", str(query or ""))
    retained = [
        token for token in tokens
        if token.casefold() not in _EMPTY_RESULT_RELAXATION_TERMS
    ]
    relaxed = " ".join(retained).strip()
    return relaxed if len(retained) >= 2 and relaxed.casefold() != str(query or "").strip().casefold() else ""


def _empty_result_query_relaxations(query: str) -> list[str]:
    """Return bounded increasingly broad discovery queries for an empty SERP."""
    first = _relaxed_query_after_empty(query)
    candidates = [first] if first else []
    if first:
        document_terms = {
            "manual", "manuals", "guide", "guides", "instructions", "instruction",
            "operator", "owners", "owner", "pdf", "documentation", "docs",
        }
        entity_tokens = [
            token for token in first.split()
            if token.casefold() not in document_terms
        ]
        entity_query = " ".join(entity_tokens).strip()
        if len(entity_tokens) >= 2 and entity_query.casefold() != first.casefold():
            candidates.append(entity_query)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))

_WEATHER_QUERY_HINTS = {
    "weather", "forecast", "forecasts", "temperature", "temperatures",
    "rain", "raining", "precipitation", "humid", "humidity", "wind",
}
_WEATHER_RESULT_HINTS = {
    "weather", "forecast", "temperature", "temperatures", "rain",
    "precipitation", "humidity", "wind", "accuweather", "meteoblue",
    "weather-atlas", "weather25", "weather365", "easeweather",
}


def _meaningful_query_terms(query: str) -> list[str]:
    return [
        term
        for term in re.findall(r"[a-z0-9]+", str(query or "").lower())
        if (len(term) > 2 or term in _SHORT_QUERY_SUBJECTS)
        and not term.isdigit()
        and term not in _SEARCH_QUERY_FILLER
    ]


# Leading function/auxiliary words carry no entity signal. They are kept out
# of _SEARCH_QUERY_FILLER (which gates overall query meaningfulness) and
# applied only to the document-cue entity test below, where taking the *first*
# surviving token as the entity otherwise picks "how"/"i"/"best" and rejects
# every genuinely relevant result.
_QUERY_FUNCTION_WORDS = frozenset({
    "how", "to", "i", "we", "you", "your", "my", "our", "me", "us",
    "a", "an", "is", "are", "was", "were", "do", "does", "did", "can",
    "could", "should", "would", "will", "get", "getting", "got",
    "there", "here", "need", "needed", "want", "looking", "show", "give",
    "help", "best", "good", "top", "recommended", "some", "it", "its",
    "of", "in", "on", "at", "by", "or", "and", "be", "have", "has",
})


def _result_has_query_overlap(query: str, result: dict) -> bool:
    terms = _meaningful_query_terms(query)
    if not terms:
        return True
    text = " ".join(
        str(result.get(key) or "").lower()
        for key in ("title", "snippet", "url")
    )
    query_tokens = set(re.findall(r"[a-z0-9]+", str(query or "").lower()))
    if query_tokens & _WEATHER_QUERY_HINTS:
        return (
            any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)
            and any(marker in text for marker in _WEATHER_RESULT_HINTS)
        )
    result_tokens = set(re.findall(r"[a-z0-9]+", text))

    document_cues = {
        "manual", "manuals", "guide", "guides", "instructions", "instruction",
        "documentation", "docs", "pdf", "handbook",
    }
    if query_tokens & document_cues:
        entity_fillers = _SEARCH_QUERY_FILLER | document_cues | _QUERY_FUNCTION_WORDS | {
            "english", "operator", "owner", "owners", "user", "installation",
        }
        ordered_query_tokens = re.findall(r"[a-z0-9]+", str(query or "").lower())
        entity_terms = [
            token for token in ordered_query_tokens
            if token not in entity_fillers and not token.isdigit()
        ]
        # Product/manual lookups are especially vulnerable to homonyms. A
        # result matching only the generic product word and "manual" is not
        # evidence for the named brand/entity in the request.
        #
        # Test *any* entity term rather than specifically the first. Position
        # does not identify the entity: "how to configure nginx docs" leads
        # with a task verb, "best guide for sourdough" with a qualifier. A
        # result naming none of the entity terms is still rejected, which is
        # what keeps a Ford manual out of an IKEA BILLY lookup.
        if entity_terms and not (set(entity_terms) & result_tokens):
            return False
        model_numbers = {token for token in ordered_query_tokens if token.isdigit()}
        # Temporal qualifiers are not product identifiers. In particular,
        # query normalization may append "latest 2026" to a documentation
        # lookup; an evergreen official page need not put that year in its
        # title/snippet/URL. Retain actual product numbers (including years
        # used as model names without an explicit temporal qualifier).
        temporal_years = set(re.findall(
            r'\b(?:latest|current|updated|as\s+of)\s+(20\d{2})\b',
            str(query or ''), re.I,
        ))
        model_numbers -= temporal_years
        if model_numbers and not model_numbers.issubset(result_tokens):
            return False

    def lexical_root(word: str) -> str:
        for suffix in ("ation", "ition", "ence", "ance", "ment", "ents", "ent", "ant", "ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 6:
                return word[:-len(suffix)]
        return word

    result_roots = {lexical_root(token) for token in result_tokens}
    matched_terms = {
        term for term in terms
        if term in result_tokens or lexical_root(term) in result_roots
    }
    # A single broad token is not enough evidence for a detailed entity/event
    # query.  For example, SearXNG may answer "Sweden 78 year old British woman
    # deportation Brexit ..." with generic Sweden tourism pages.  Treat that as
    # an empty provider result so the configured fallback gets a chance.
    minimum_matches = 2 if len(set(terms)) >= 4 else 1
    return len(matched_terms) >= minimum_matches


def _filter_low_relevance_results(query: str, results: list[dict]) -> list[dict]:
    if not results:
        return []
    scoped = [result for result in results if _result_matches_site_scope(query, result)]
    relevant = [result for result in scoped if _result_has_query_overlap(query, result)]
    # Relevance matching is intentionally conservative and cannot understand
    # every inflection or language. Keep explicit site constraints strict, but
    # let ranking handle a provider page when the heuristic rejects every
    # otherwise in-scope result.
    return relevant or scoped


def _result_matches_site_scope(query: str, result: dict) -> bool:
    """Enforce explicit site constraints even when a provider ignores them."""
    scopes = re.findall(r'(?<!\S)(-?)site:([^\s()]+)', query, re.IGNORECASE)
    if not scopes:
        return True
    try:
        target = urlparse(str(result.get("url") or ""))
        if target.scheme not in {"https", "http"} or not target.hostname:
            return False
        host = target.hostname.lower().rstrip(".")
        included = []
        for excluded, scope in scopes:
            parsed = urlparse(scope if "://" in scope else "https://" + scope)
            domain = (parsed.hostname or "").lower().rstrip(".")
            matches = bool(domain) and (host == domain or host.endswith("." + domain))
            if parsed.path and parsed.path != "/":
                matches = matches and target.path.startswith(parsed.path)
            if excluded and matches:
                return False
            if not excluded:
                included.append(matches)
        return any(included) if included else True
    except ValueError:
        return False


_SCHOLARLY_QUERY_CUE_RE = re.compile(
    r"\b(?:paper|preprint|arxiv|proceedings|table\s+\d+|figure\s+\d+|"
    r"appendix\s+[a-z0-9]+|benchmark(?:s)?)\b",
    re.IGNORECASE,
)
_SCHOLARLY_TITLE_FILLER = _SEARCH_QUERY_FILLER | {
    "paper", "preprint", "arxiv", "proceedings", "table", "figure",
    "appendix", "authors", "author", "extract", "locate", "read",
}
_ARXIV_IDENTIFIER_RE = re.compile(
    r"(?i)(?:\barxiv\s*:\s*|\barxiv\.org/(?:abs|pdf|html)/)?"
    r"(?P<identifier>\d{4}\.\d{4,5}(?:v\d+)?)\b"
)
_FORMAL_PUBLICATION_CUE_RE = re.compile(
    r"\b(?:publish(?:ed|ing|cation)?|venue|conference|journal|proceedings|doi)\b",
    re.IGNORECASE,
)


def _exact_arxiv_identifier_results(query: str) -> list[dict]:
    """Return deterministic official landing pages for explicit arXiv IDs."""
    seen: set[str] = set()
    results: list[dict] = []
    for match in _ARXIV_IDENTIFIER_RE.finditer(str(query or "")):
        identifier = match.group("identifier")
        canonical = re.sub(r"v\d+$", "", identifier, flags=re.IGNORECASE)
        if canonical in seen:
            continue
        seen.add(canonical)
        results.append({
            "title": f"arXiv:{canonical} — exact identifier match",
            "url": f"https://arxiv.org/abs/{canonical}",
            "snippet": (
                "Official arXiv landing page resolved directly from the exact "
                "identifier in the query."
            ),
            "source": "arxiv",
        })
    return results


def _title_before_explicit_arxiv_identifier(query: str) -> str:
    """Extract a probable title that precedes an explicit arXiv identifier."""

    text = re.sub(r"\s+", " ", str(query or "")).strip()
    match = _ARXIV_IDENTIFIER_RE.search(text)
    if not match or not _FORMAL_PUBLICATION_CUE_RE.search(text):
        return ""
    candidate = text[:match.start()].strip(" \t,;:-'\"")
    candidate = re.sub(
        r"\barxiv(?:\.org)?(?:\s*:\s*|\s+(?:abs|pdf|html)\s*[/ :]*)?$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" \t,;:-'\"")
    candidate = re.sub(
        r"^(?:(?:please\s+)?(?:find|locate|search\s+for|look\s+up|verify|check)\s+)"
        r"(?:(?:the|this)\s+)?(?:paper\s+)?",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" \t,;:-'\"")
    return candidate if len(_normalized_title_terms(candidate)) >= 2 else ""


def _normalized_title_terms(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 1 and token not in _SCHOLARLY_TITLE_FILLER
    ]


def _is_distinctive_short_scholarly_title(value: str) -> bool:
    """Recognize compact model/report names without accepting generic phrases."""

    terms = _normalized_title_terms(value)
    if not 1 <= len(terms) <= 2:
        return False
    text = str(value or "").strip()
    return bool(
        re.search(r"\d", text)
        or re.search(r"\b[A-Z][A-Za-z0-9]*-[A-Z][A-Za-z0-9]*\b", text)
    )


def _scholarly_title_from_query(query: str) -> str:
    """Extract a probable paper title only from clearly scholarly searches."""

    text = re.sub(r"\s+", " ", str(query or "")).strip()
    if not text or not _SCHOLARLY_QUERY_CUE_RE.search(text):
        return ""

    quoted = [
        candidate.strip()
        for candidate in re.findall(r'["“”]([^"“”]{4,180})["“”]', text)
        if len(_normalized_title_terms(candidate)) >= 3
        or _is_distinctive_short_scholarly_title(candidate)
    ]
    if quoted:
        return max(quoted, key=lambda candidate: len(_normalized_title_terms(candidate)))

    before_paper = re.search(
        r"(?:^|\b(?:find|locate|read|from|about)\s+)(.{4,160}?)\s+"
        r"(?:paper|preprint)\b",
        text,
        re.IGNORECASE,
    )
    if before_paper:
        candidate = before_paper.group(1).strip(" ,:;-'")
        if (
            len(_normalized_title_terms(candidate)) >= 3
            or _is_distinctive_short_scholarly_title(candidate)
        ):
            return candidate

    before_locator = re.match(
        r"(.{2,80}?)\s+(?:table|figure)\s+\d+\b",
        text,
        re.IGNORECASE,
    )
    if before_locator:
        candidate = before_locator.group(1).strip(" ,:;-'\"")
        if _is_distinctive_short_scholarly_title(candidate):
            return candidate
    return ""


def _result_strongly_matches_title(title: str, result: dict) -> bool:
    wanted = set(_normalized_title_terms(title))
    found = set(_normalized_title_terms(str(result.get("title") or "")))
    if len(wanted) < 2 or not found:
        return False
    overlap = len(wanted & found) / len(wanted)
    return overlap >= (1.0 if len(wanted) == 2 else 0.8)


def _scholarly_user_agent() -> str:
    """Identify this build to the scholarly APIs using the real app version."""
    from src.constants import APP_VERSION

    return f"Odysseus/{APP_VERSION} scholarly-title-resolver"


_scholarly_deadline: ContextVar[Optional[float]] = ContextVar(
    "scholarly_deadline", default=None
)


@contextmanager
def _scholarly_budget():
    """Open one wall-clock budget shared by every hop of a lookup chain."""
    token = _scholarly_deadline.set(
        time.monotonic() + SCHOLARLY_LOOKUP_TOTAL_BUDGET
    )
    try:
        yield
    finally:
        _scholarly_deadline.reset(token)


MAX_SCHOLARLY_REDIRECTS = 3


def _scholarly_api_get(url: str, params: dict) -> Optional[httpx.Response]:
    """GET a scholarly metadata API under the shared outbound policy.

    Returns ``None`` when any destination URL fails the outbound check or the
    caller's budget is already spent, so callers degrade to their next source
    instead of raising. Bounded manual redirects ensure every hop passes
    through ``check_outbound_url`` before the destination is contacted.
    """
    from src.constants import SCHOLARLY_LOOKUP_TIMEOUT
    from src.url_safety import check_outbound_url

    current_url = url
    current_params: Optional[dict] = params

    for _ in range(MAX_SCHOLARLY_REDIRECTS + 1):
        ok, reason = check_outbound_url(current_url, block_private=True)
        if not ok:
            logger.warning("Scholarly lookup blocked for %s: %s", current_url, reason)
            return None

        timeout = SCHOLARLY_LOOKUP_TIMEOUT
        deadline = _scholarly_deadline.get()
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info("Scholarly lookup budget exhausted before %s", current_url)
                return None
            timeout = min(timeout, remaining)

        response = httpx.get(
            current_url,
            params=current_params,
            headers={"User-Agent": _scholarly_user_agent()},
            timeout=timeout,
            follow_redirects=False,
        )

        is_redirect = getattr(response, "is_redirect", False) or (
            getattr(response, "status_code", None) in (301, 302, 303, 307, 308)
        )
        if is_redirect:
            headers = getattr(response, "headers", {})
            location = headers.get("location")
            if not location:
                logger.warning(
                    "Scholarly redirect missing Location header from %s", current_url
                )
                return None
            current_url = str(httpx.URL(str(response.url)).join(location))
            current_params = None
            continue

        response.raise_for_status()
        return response

    logger.warning("Scholarly lookup exceeded max redirects from %s", url)
    return None


def _arxiv_title_results(title: str, count: int = 3) -> list[dict]:
    """Resolve a paper title through arXiv's public Atom API."""

    try:
        response = _scholarly_api_get(
            ARXIV_API_URL,
            {
                "search_query": f'ti:"{title}"',
                "start": 0,
                "max_results": max(1, min(int(count), 5)),
            },
        )
        if response is None:
            return []
        root = ET.fromstring(response.text)
    except Exception as exc:
        logger.info("arXiv title lookup failed for %r: %s", title, exc)
        return []

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    matches: list[dict] = []
    for entry in root.findall("atom:entry", namespace):
        result_title = " ".join(
            (entry.findtext("atom:title", default="", namespaces=namespace) or "").split()
        )
        if not _result_strongly_matches_title(title, {"title": result_title}):
            continue
        entry_id = (entry.findtext("atom:id", default="", namespaces=namespace) or "").strip()
        arxiv_id = entry_id.rstrip("/").rsplit("/", 1)[-1]
        if not arxiv_id:
            continue
        summary = " ".join(
            (entry.findtext("atom:summary", default="", namespaces=namespace) or "").split()
        )
        matches.append({
            "title": result_title,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "snippet": summary,
            "source": "arxiv",
        })
    return matches


def _openalex_title_results(title: str, count: int = 3) -> list[dict]:
    """Resolve an exact scholarly title through OpenAlex metadata."""

    try:
        # OpenAlex treats a literal question mark as query syntax and returns
        # HTTP 400 for otherwise valid titles such as "How Far ... GPT-4V?".
        search_title = re.sub(r"[?]+", " ", str(title or "")).strip()
        response = _scholarly_api_get(
            OPENALEX_API_URL,
            {
                "search": search_title,
                "per-page": max(1, min(int(count), 5)),
                "select": (
                    "display_name,doi,primary_location,publication_year,type"
                ),
            },
        )
        if response is None:
            return []
        payload = response.json()
    except Exception as exc:
        logger.info("OpenAlex title lookup failed for %r: %s", title, exc)
        return []

    matches: list[dict] = []
    for item in payload.get("results", []):
        result_title = str(item.get("display_name") or "").strip()
        if not _result_strongly_matches_title(title, {"title": result_title}):
            continue
        location = item.get("primary_location") or {}
        url = str(location.get("landing_page_url") or item.get("doi") or "").strip()
        if url.startswith("http://arxiv.org/"):
            url = "https://" + url[len("http://"):]
        if not url:
            continue
        snippet = "Exact scholarly-title match from OpenAlex metadata."
        venue = str(location.get("raw_source_name") or "").strip()
        year = item.get("publication_year")
        publication_type = str(item.get("type") or "").strip()
        version = str(location.get("version") or "").strip()
        formal_parts: list[str] = []
        if venue:
            formal_parts.append(f"{venue}, {year}" if year else venue)
        elif year:
            formal_parts.append(str(year))
        if publication_type:
            formal_parts.append(f"type: {publication_type}")
        if version:
            formal_parts.append(f"version: {version}")
        if formal_parts:
            snippet += f" Formal publication: {'; '.join(formal_parts)}."
        matches.append({
            "title": result_title,
            "url": url,
            "snippet": snippet,
            "source": "openalex",
        })
    return matches


def _scholarly_title_results(title: str, count: int = 3) -> list[dict]:
    """Retry a noisy scholarly query as a bare title, then use arXiv API.

    The three hops share one wall-clock budget so a slow upstream cannot hold a
    user-facing search open for the sum of every per-request timeout.
    """
    with _scholarly_budget():
        return _scholarly_title_results_inner(title, count)


def _scholarly_title_results_inner(title: str, count: int) -> list[dict]:
    try:
        simplified = searxng_search_api(title, count=max(3, count))
    except Exception as exc:
        logger.info("Simplified scholarly search failed for %r: %s", title, exc)
        simplified = []
    exact = [
        result for result in simplified
        if _result_strongly_matches_title(title, result)
    ]
    if exact:
        return exact[:count]
    openalex = _openalex_title_results(title, count)
    if openalex:
        return openalex
    return _arxiv_title_results(title, count)


def _direct_scholarly_title_results(title: str, count: int = 3) -> list[dict]:
    """Resolve a clear paper title without waiting on generic search providers."""

    # OpenAlex typically resolves titles in under a second and often returns
    # the official arXiv landing page. The arXiv API remains the fallback.
    with _scholarly_budget():
        openalex = _openalex_title_results(title, count)
        if openalex:
            return openalex
        return _arxiv_title_results(title, count)


def _augment_scholarly_results(query: str, results: list[dict], count: int) -> list[dict]:
    """Prepend an exact arXiv match when a scholarly SERP missed its title."""

    current = list(results or [])
    identifier_results = _exact_arxiv_identifier_results(query)
    if identifier_results:
        title = _title_before_explicit_arxiv_identifier(query)
        formal_results: list[dict] = []
        if title:
            formal_results = [
                item
                for item in _openalex_title_results(title, min(count, 3))
                if "arxiv.org/" not in str(item.get("url") or "").lower()
            ]
        exact_urls = {str(item["url"]) for item in identifier_results}
        formal_urls = {str(item.get("url") or "") for item in formal_results}
        return (
            formal_results
            + identifier_results
            + [
                item for item in current
                if str(item.get("url") or "") not in exact_urls | formal_urls
            ]
        )[:count]
    title = _scholarly_title_from_query(query)
    if not title:
        return current
    exact_current = [
        item for item in current
        if _result_strongly_matches_title(title, item)
    ]
    if exact_current:
        exact_ids = {id(item) for item in exact_current}
        return (exact_current + [item for item in current if id(item) not in exact_ids])[:count]
    arxiv_results = _scholarly_title_results(title, min(count, 3))
    if not arxiv_results:
        return current
    seen = {str(item.get("url") or "") for item in arxiv_results}
    return (arxiv_results + [item for item in current if str(item.get("url") or "") not in seen])[:count]


def _subject_first_weather_query(query: str) -> str:
    """Rewrite natural weather questions into the shape SearXNG handles best."""
    text = re.sub(r"\s+", " ", str(query or "")).strip(" ?")
    if not text:
        return text
    if not (set(re.findall(r"[a-z0-9]+", text.lower())) & _WEATHER_QUERY_HINTS):
        return text
    loc_match = re.search(
        r"\b(?:weather|forecast)\s+(?:in|for|at)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if not loc_match:
        loc_match = re.search(
            r"\b(?:weather|forecast)\b.*?\b(?:in|for|at)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
    if not loc_match:
        return text
    location = loc_match.group(1).strip(" ?.,")
    timing = ""
    timing_match = re.search(
        r"\b(today|tomorrow|tonight|this\s+week|next\s+week|now|current)\b",
        location,
        re.IGNORECASE,
    )
    if timing_match:
        timing = timing_match.group(1).lower()
        location = (
            location[: timing_match.start()] + location[timing_match.end():]
        ).strip(" ?.,")
    if not location:
        return text
    return re.sub(r"\s+", " ", f"{location} weather forecast {timing}").strip()


def _provider_friendly_query(query: str) -> str:
    """Convert generic question grammar to keyword order without changing its topic."""
    text = _subject_first_weather_query(query)
    match = re.fullmatch(
        r"(?:what|which)\s+(year|date|time)\s+(?:did|does|do|was|were|is|are)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return f"{match.group(2).strip()} {match.group(1).lower()}"
    # Search providers already receive recency separately. Remove a leading
    # conversational request shell so ranking is driven by the subject rather
    # than words such as "any", "latest", and "information".
    cleaned = re.sub(
        r"^(?:can|could|would)\s+you\s+(?:find|search|look\s+up)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(?:any\s+)?(?:latest|current|recent)?\s*"
        r"(?:news|info(?:rmation)?|updates?|details?)\s+(?:on|about)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    if cleaned.strip():
        return cleaned.strip()
    return text


# ----------------------------------------------------------------------
# Unified search with caching and retry
# ----------------------------------------------------------------------
def searxng_search_results(query: str, count: int = 10, time_filter: str = None) -> list[dict]:
    """Perform a web search using configured provider with caching and retry."""
    provider_query = _provider_friendly_query(query)
    settings = _get_search_settings()
    search_provider = settings.get("search_provider", "searxng")
    result_count = _get_result_count()
    # Use configured count if caller used default
    if count == 10:
        count = result_count

    # A named scholarly work has a deterministic metadata path. Resolve that
    # first instead of spending the full tool deadline retrying generic search
    # providers; the returned official URL lets the agent proceed to PDF tools.
    scholarly_title = _scholarly_title_from_query(provider_query)
    if scholarly_title and not time_filter:
        direct_results = [result for result in _direct_scholarly_title_results(scholarly_title, count)
                          if _result_matches_site_scope(provider_query, result)]
        if direct_results:
            _record_query(provider_query, True, cache_hit=False)
            return direct_results[:count]

    cache_key = generate_cache_key(f"{provider_query}|{count}|{time_filter}")
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
                # Ranking/relevance logic evolves independently from provider
                # results. Re-apply it on cache hits so stale cached ordering
                # does not preserve bad SERP choices after a harness fix.
                results = _filter_low_relevance_results(provider_query, results)
                if results:
                    results = rank_search_results(provider_query, results)
                results = _augment_scholarly_results(provider_query, results, count)
                if results:
                    _record_query(query, True, cache_hit=True)
                    return results
                logger.info(
                    "Search cache hit for %r became empty after relevance filtering; refetching",
                    provider_query,
                )
                cache_file.unlink(missing_ok=True)
                search_cache_index.pop(cache_key, None)
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
                results = _call_provider(provider_name, provider_query, count, time_filter)
                results = _filter_low_relevance_results(provider_query, results)
                if results:
                    logger.info(f"{provider_name} search succeeded with {len(results)} results")
                    break
                # A completed empty/unrelated result set is not a transport
                # failure. Advance to another provider rather than repeating
                # the exact request and spending the tool deadline twice.
                break
            except (NetworkError, ParseError, RateLimitError) as e:
                error_logger.error(f"{provider_name} search error (attempt {attempt + 1}): {e}")
            except Exception as e:
                error_logger.error(f"Unexpected error during {provider_name} search (attempt {attempt + 1}): {e}")
        if results:
            break

    if not results:
        for relaxed_query in _empty_result_query_relaxations(provider_query):
            logger.info(
                "Exact search returned no evidence for %r; retrying broadened query %r",
                provider_query, relaxed_query,
            )
            for provider_name in provider_chain:
                try:
                    results = _call_provider(provider_name, relaxed_query, count, time_filter)
                    results = _filter_low_relevance_results(relaxed_query, results)
                except Exception as exc:
                    error_logger.error("Relaxed %s search failed: %s", provider_name, exc)
                    results = []
                if results:
                    break
            if results:
                provider_query = relaxed_query
                break

    results = _augment_scholarly_results(provider_query, results, count)

    success = bool(results)
    _record_query(provider_query, success, cache_hit=False)

    if success:
        results = rank_search_results(provider_query, results)
        results = _augment_scholarly_results(provider_query, results, count)
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
            logger.warning(f"Failed to write search cache for {provider_query}: {e}")

    if not success:
        logger.error(f"All search providers failed for query: {provider_query}")

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
    provider_query = _provider_friendly_query(query)
    logger.info(f"Starting comprehensive search for: {provider_query}")
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
                search_results = _call_provider(provider_name, provider_query, fetch_count, time_filter)
                search_results = _filter_low_relevance_results(provider_query, search_results)
                if search_results:
                    provider_attempts[provider_name] = f"ok ({len(search_results)})"
                    logger.info(f"Comprehensive search: {provider_name} returned {len(search_results)} results")
                    break
                empty = True
                last_err = None
                break
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
        for relaxed_query in _empty_result_query_relaxations(provider_query):
            logger.info(
                "Comprehensive search empty for %r; retrying broadened query %r",
                provider_query, relaxed_query,
            )
            for provider_name in provider_chain:
                try:
                    search_results = _call_provider(
                        provider_name, relaxed_query, fetch_count, time_filter,
                    )
                    search_results = _filter_low_relevance_results(
                        relaxed_query, search_results,
                    )
                except Exception as exc:
                    provider_attempts[f"{provider_name}:relaxed"] = f"error: {exc}"
                    search_results = []
                if search_results:
                    provider_attempts[f"{provider_name}:relaxed"] = (
                        f"ok ({len(search_results)})"
                    )
                    provider_query = relaxed_query
                    break
                provider_attempts[f"{provider_name}:relaxed"] = "empty"
            if search_results:
                break

    search_results = _augment_scholarly_results(
        provider_query,
        search_results,
        fetch_count,
    )

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

    search_results = rank_search_results(provider_query, search_results)
    search_results = _augment_scholarly_results(
        provider_query,
        search_results,
        fetch_count,
    )

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
    output_parts.append(f"Query: {provider_query}")
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

            text = search_excerpt(content["content"], provider_query, 3000)
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
